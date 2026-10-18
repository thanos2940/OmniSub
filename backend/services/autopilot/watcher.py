"""Follows Sonarr and Radarr like Bazarr does (docs/PLAN_autopilot.md 4.4).

Three ways in, all landing in the same store and deduplicated by ``(source, arr_file_id)``:
history polling (``poll_once``), the library walk (``reconcile_once``) and webhooks
(``ingest_webhook``). Each source fails and backs off on its own.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from integrations import radarr as radarr_api
from integrations import sonarr as sonarr_api
from services.autopilot.config import AutopilotSettings
from services.autopilot.store import AutopilotStore, iso_utc, parse_iso
from utils import storage

logger = logging.getLogger(__name__)

SOURCES = ("sonarr", "radarr")
EVENT_TYPES = {
    "sonarr": ("downloadFolderImported", "episodeFileRenamed", "episodeFileDeleted"),
    "radarr": ("downloadFolderImported", "movieFileRenamed", "movieFileDeleted"),
}
_IMPORTED = "downloadFolderImported"
_RENAMED = ("episodefilerenamed", "moviefilerenamed")
_DELETED = ("episodefiledeleted", "moviefiledeleted")

BACKOFF_SECONDS = (60, 120, 300, 600)
# A record that keeps failing to resolve (say, its episode was removed from Sonarr) is
# given up on after this many polls instead of blocking the cursor forever.
MAX_RECORD_ATTEMPTS = 5
MEDIA_INFO_TTL_SECONDS = 300
LIVE_STATES = ("waiting", "checking", "extracting", "translating", "no_source", "failed")
CANCEL_STATES = ("waiting", "checking", "extracting", "translating")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _lowest_episode(episodes: List[Dict]) -> Dict:
    return min(episodes, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))


class _SourceState:
    def __init__(self) -> None:
        self.configured = False
        self.ok: Optional[bool] = None
        self.since: Optional[str] = None
        self.error: Optional[str] = None
        self.fail_count = 0
        self.next_try: Optional[datetime] = None


class ArrWatcher:
    def __init__(self, store: AutopilotStore, *, config_provider: Optional[Callable[[], Dict]] = None,
                 clock: Optional[Callable[[], datetime]] = None):
        self.store = store
        self._config_provider = config_provider
        self._clock = clock or _utcnow
        self._state = {s: _SourceState() for s in SOURCES}

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    def now(self) -> datetime:
        return self._clock()

    def _cfg(self) -> Dict:
        provider = self._config_provider or storage.load_global_config
        try:
            return provider() or {}
        except Exception as e:
            logger.warning(f"Could not load the global config: {e}")
            return {}

    @staticmethod
    def arr_config(source: str, cfg: Dict):
        """The Sonarr/Radarr client config when that source is enabled and has a key, else None."""
        if source == "sonarr":
            arr = sonarr_api.SonarrConfig(
                base_url=cfg.get("sonarr_url", "http://localhost:8989"),
                api_key=cfg.get("sonarr_api_key", ""),
                enabled=cfg.get("sonarr_enabled", False),
            )
        else:
            arr = radarr_api.RadarrConfig(
                base_url=cfg.get("radarr_url", "http://localhost:7878"),
                api_key=cfg.get("radarr_api_key", ""),
                enabled=cfg.get("radarr_enabled", False),
            )
        return arr if arr.enabled and arr.api_key else None

    @staticmethod
    def _resolve_path(cfg: Dict, raw_path: str) -> str:
        from integrations.path_resolver import PathResolver
        return PathResolver(cfg.get("arr_path_mappings", [])).resolve(raw_path) if raw_path else raw_path

    # ------------------------------------------------------------------
    # Health / backoff
    # ------------------------------------------------------------------

    def _record_ok(self, source: str) -> None:
        st = self._state[source]
        if st.ok is not True:
            st.since = iso_utc(self.now())
        st.ok, st.error, st.fail_count, st.next_try = True, None, 0, None

    def _record_failure(self, source: str, error: Any) -> None:
        st = self._state[source]
        if st.ok is not False:
            st.since = iso_utc(self.now())
        st.ok = False
        st.error = str(error)[:300] or type(error).__name__
        st.fail_count += 1
        delay = BACKOFF_SECONDS[min(st.fail_count, len(BACKOFF_SECONDS)) - 1]
        st.next_try = self.now() + timedelta(seconds=delay)
        logger.warning(f"{source} unreachable ({st.error}); next attempt in {delay}s")

    def health(self) -> Dict[str, Dict]:
        """Per source: ``{configured, ok, since, error, cursor}`` (``ok`` is None before the first call)."""
        cfg = self._cfg()
        out = {}
        for source in SOURCES:
            st = self._state[source]
            configured = self.arr_config(source, cfg) is not None
            out[source] = {
                "configured": configured,
                "ok": st.ok if configured else None,
                "since": st.since if configured else None,
                "error": st.error if configured else None,
                "cursor": self.store.get_cursor(source),
            }
        return out

    def _backing_off(self, source: str) -> bool:
        nxt = self._state[source].next_try
        return nxt is not None and self.now() < nxt

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def poll_once(self) -> Dict:
        """Poll both sources' history. One source failing never stops the other."""
        cfg = self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        summary: Dict[str, Dict] = {}
        for source in SOURCES:
            arr = self.arr_config(source, cfg)
            self._state[source].configured = arr is not None
            if arr is None:
                summary[source] = {"skipped": "not_configured"}
                continue
            if self._backing_off(source):
                summary[source] = {"skipped": "backoff", "error": self._state[source].error}
                continue
            try:
                summary[source] = await self._poll_source(source, arr, cfg, settings)
            except Exception as e:
                self._record_failure(source, e)
                summary[source] = {"ok": False, "error": self._state[source].error}
            else:
                self._record_ok(source)
        return summary

    async def _poll_source(self, source: str, arr, cfg: Dict, settings: AutopilotSettings) -> Dict:
        cursor = self.store.get_cursor(source)
        if not cursor or not parse_iso(cursor.get("date")):
            self.store.set_cursor(source, {"date": iso_utc(self.now()), "ids": []})
            return {"ok": True, "initialized": True}

        fetch = sonarr_api.get_history_since if source == "sonarr" else radarr_api.get_history_since
        records: Dict[Any, Dict] = {}
        for event_type in EVENT_TYPES[source]:
            rows = await fetch(arr, cursor["date"], event_type)
            if rows is None:
                raise RuntimeError(f"{source} history request failed ({event_type})")
            for rec in rows:
                records.setdefault(rec.get("id"), rec)

        seen_ids = set(cursor.get("ids") or [])
        previous_retry = cursor.get("retry") or {}
        cursor_date = parse_iso(cursor["date"])

        ordered = []
        for rid, rec in records.items():
            when = parse_iso(rec.get("date"))
            if when is None or rid is None or rid in seen_ids:
                continue
            ordered.append((when, rid, rec))
        ordered.sort(key=lambda t: (t[0], t[1]))

        summary = {"ok": True, "records": len(ordered), "created": 0, "renamed": 0, "gone": 0}
        last_date, last_ids = cursor_date, set(seen_ids)
        retry: Dict = {}
        for when, rid, rec in ordered:
            try:
                outcome = await self._handle_record(source, arr, rec, cfg, settings, when)
            except Exception as e:
                tries = previous_retry.get("n", 0) + 1 if previous_retry.get("id") == rid else 1
                if tries < MAX_RECORD_ATTEMPTS:
                    # Stop here and keep the cursor in front of this record: it is tried again next poll.
                    logger.warning(f"{source} history record {rid} could not be resolved yet ({e}); will retry")
                    retry = {"id": rid, "n": tries}
                    summary["deferred"] = str(e)
                    break
                logger.error(f"{source} history record {rid} skipped after {tries} attempts: {e}")
                outcome = "skipped"
            if outcome in summary:
                summary[outcome] += 1
            if when > last_date:
                last_date, last_ids = when, set()
            last_ids.add(rid)
        self.store.set_cursor(source, self._cursor_value(last_date, last_ids, retry))
        return summary

    @staticmethod
    def _cursor_value(date: datetime, ids: set, retry: Dict) -> Dict:
        value = {"date": date.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "ids": sorted(ids)}
        if retry:
            value["retry"] = retry
        return value

    async def _handle_record(self, source: str, arr, rec: Dict, cfg: Dict, settings: AutopilotSettings,
                             when: datetime) -> str:
        kind = str(rec.get("eventType") or "")
        if kind == _IMPORTED:
            detection = await (self._sonarr_detection if source == "sonarr" else self._radarr_detection)(
                arr, rec, cfg)
            if detection is None:
                return "ignored"
            _, created = self.store.upsert_detection(
                **detection, detected_at=when, grace_seconds=settings.grace_seconds)
            return "created" if created else "duplicate"
        if kind.lower() in _RENAMED:
            return await self._handle_rename(source, arr, rec, cfg)
        if kind.lower() in _DELETED:
            return await self._handle_delete(source, arr, rec)
        return "ignored"

    # ---- imported ----

    async def _sonarr_detection(self, arr, rec: Dict, cfg: Dict) -> Optional[Dict]:
        ep = rec.get("episode") or {}
        episode_id = rec.get("episodeId") or ep.get("id")
        if not episode_id:
            return None
        if ep.get("episodeFileId") is None or ep.get("seasonNumber") is None:
            ep = await sonarr_api.get_episode(arr, episode_id)
            if ep is None:
                raise RuntimeError(f"episode {episode_id} could not be fetched")
        file_id = ep.get("episodeFileId") or 0
        if not file_id:
            return None  # the file is already gone again
        data = rec.get("data") or {}
        file = ep.get("episodeFile")
        if not (file and file.get("path")):
            file = await sonarr_api.get_episode_file(arr, file_id)
        raw_path = (file or {}).get("path") or data.get("importedPath")
        if not raw_path:
            raise RuntimeError(f"episode file {file_id} has no path")

        series = rec.get("series") or {}
        series_id = rec.get("seriesId") or series.get("id") or ep.get("seriesId")
        if not series.get("title"):
            series = await sonarr_api.get_series(arr, series_id) if series_id else None
            if not series:
                raise RuntimeError(f"series {series_id} could not be fetched")
        return self._sonarr_fields(cfg, series, ep, episode_id, series_id, file_id, raw_path)

    def _sonarr_fields(self, cfg: Dict, series: Dict, ep: Dict, episode_id, series_id, file_id, raw_path) -> Dict:
        from integrations.media_sync_engine import make_episode_name, make_project_name
        season, number = ep.get("seasonNumber") or 0, ep.get("episodeNumber") or 0
        return dict(
            source="sonarr", kind="episode", arr_item_id=episode_id, arr_parent_id=series_id,
            arr_file_id=file_id, media_path=self._resolve_path(cfg, raw_path),
            project_name=make_project_name(series.get("title") or "Unknown"),
            episode_name=make_episode_name(season, number), title=series.get("title"),
            season=season, episode=number,
        )

    async def _radarr_detection(self, arr, rec: Dict, cfg: Dict) -> Optional[Dict]:
        movie = dict(rec.get("movie") or {})
        movie_id = rec.get("movieId") or movie.get("id")
        if not movie_id:
            return None
        mf = movie.get("movieFile")
        if not (mf and mf.get("path")):
            file_id = (mf or {}).get("id") or movie.get("movieFileId")
            if not file_id:
                fresh = await radarr_api.get_movie(arr, movie_id)
                if fresh is None:
                    raise RuntimeError(f"movie {movie_id} could not be fetched")
                movie = {**movie, **fresh}
                mf = fresh.get("movieFile")
                file_id = (mf or {}).get("id") or fresh.get("movieFileId")
            if file_id and not (mf and mf.get("path")):
                mf = await radarr_api.get_movie_file(arr, file_id)
                if mf is None:
                    data_path = (rec.get("data") or {}).get("importedPath")
                    if not data_path:
                        raise RuntimeError(f"movie file {file_id} could not be fetched")
                    mf = {"id": file_id, "path": data_path}
        if not (mf and mf.get("path")):
            return None
        if not movie.get("title"):
            fresh = await radarr_api.get_movie(arr, movie_id)
            if fresh is None:
                raise RuntimeError(f"movie {movie_id} could not be fetched")
            movie = {**fresh, **movie}
        movie["movieFile"] = mf
        return await asyncio.get_running_loop().run_in_executor(
            None, self._radarr_fields, cfg, movie, mf)

    def _radarr_fields(self, cfg: Dict, movie: Dict, mf: Dict) -> Dict:
        from integrations.media_sync_engine import MediaSyncEngine, movie_project_name
        adopted = MediaSyncEngine._find_project_by_radarr_id(storage, movie.get("id"))
        project = movie_project_name(movie, adopted=adopted)
        return dict(
            source="radarr", kind="movie", arr_item_id=movie.get("id"), arr_parent_id=None,
            arr_file_id=mf.get("id"), media_path=self._resolve_path(cfg, mf.get("path")),
            project_name=project, episode_name=project, title=movie.get("title"), season=None, episode=None,
        )

    # ---- renamed / deleted ----

    async def _current_file(self, source: str, arr, rec: Dict) -> Tuple[Optional[int], Optional[str], Optional[int]]:
        """``(arr_item_id, current file id or None, current raw path or None)`` for a rename/delete record."""
        if source == "sonarr":
            episode_id = rec.get("episodeId") or (rec.get("episode") or {}).get("id")
            if not episode_id:
                return None, None, None
            ep = await sonarr_api.get_episode(arr, episode_id)
            if ep is None:
                raise RuntimeError(f"episode {episode_id} could not be fetched")
            file_id = ep.get("episodeFileId") or 0
            if not file_id:
                return episode_id, None, None
            file = ep.get("episodeFile")
            if not (file and file.get("path")):
                file = await sonarr_api.get_episode_file(arr, file_id)
            return episode_id, file_id, (file or {}).get("path")
        movie_id = rec.get("movieId") or (rec.get("movie") or {}).get("id")
        if not movie_id:
            return None, None, None
        movie = await radarr_api.get_movie(arr, movie_id)
        if movie is None:
            raise RuntimeError(f"movie {movie_id} could not be fetched")
        mf = movie.get("movieFile") or {}
        return movie_id, (mf.get("id") or None), mf.get("path")

    async def _handle_rename(self, source: str, arr, rec: Dict, cfg: Dict) -> str:
        item_id, file_id, raw_path = await self._current_file(source, arr, rec)
        if not item_id or not file_id or not raw_path:
            return "ignored"
        new_path = self._resolve_path(cfg, raw_path)
        renamed = 0
        for item in self.store.find_for_media(source, item_id):
            if item["arr_file_id"] == file_id and item["state"] not in ("superseded", "gone"):
                renamed += await self._apply_rename(item, new_path)
        return "renamed" if renamed else "ignored"

    async def _apply_rename(self, item: Dict, new_path: str) -> int:
        if item["media_path"] == new_path:
            return 0
        old_path = item["media_path"]
        moved = await asyncio.get_running_loop().run_in_executor(None, self.rename_sidecars, old_path, new_path)
        outputs = list(item.get("outputs") or [])
        mapping = dict(moved)
        outputs = [mapping.get(o, o) for o in outputs]
        self.store.transition(item["id"], item["state"], message=f"media file renamed to {Path(new_path).name}",
                              media_path=new_path, outputs=outputs)
        return 1

    @staticmethod
    def rename_sidecars(old_path: str, new_path: str) -> List[Tuple[str, str]]:
        """Follow a video rename with its Greek sidecars (``<old stem>.el.srt`` -> ``<new stem>.el.srt``).

        Never overwrites; returns the ``(old, new)`` pairs that moved.
        """
        from services.autopilot.media_inspect import find_language_sidecars
        old, new = Path(old_path), Path(new_path)
        if old.stem == new.stem or not old.parent.exists():
            return []
        moved = []
        for sidecar in find_language_sidecars(old_path, "el"):
            src = Path(sidecar)
            target = new.parent / (new.stem + src.name[len(old.stem):])
            if target.exists() or not src.exists():
                continue
            try:
                os.replace(src, target)
                moved.append((str(src), str(target)))
            except OSError as e:
                logger.warning(f"Could not rename {src.name} to {target.name}: {e}")
        return moved

    async def _handle_delete(self, source: str, arr, rec: Dict) -> str:
        item_id, file_id, _ = await self._current_file(source, arr, rec)
        if not item_id:
            return "ignored"
        gone = 0
        for item in self.store.find_for_media(source, item_id):
            if item["state"] in LIVE_STATES and item["arr_file_id"] != file_id:
                self._mark_gone(item, "file deleted in the arr")
                gone += 1
        return "gone" if gone else "ignored"

    def _mark_gone(self, item: Dict, message: str) -> None:
        was_active = item["state"] in CANCEL_STATES
        self.store.transition(item["id"], "gone", reason="file_deleted", message=message)
        if was_active:
            self._cancel_queue_rows(item)

    def _cancel_queue_rows(self, item: Dict) -> None:
        """Cancel the queue rows of an episode no live item of ours still waits for."""
        project, episode = item.get("project_name"), item.get("episode_name")
        if not project or not episode:
            return
        for other in self.store.find_for_media(item["source"], item["arr_item_id"]):
            if other["id"] != item["id"] and other["state"] in CANCEL_STATES:
                return
        try:
            from utils.translation_queue import KIND_EXTRACTION, KIND_TRANSLATION, TranslationQueue
            queue = TranslationQueue()
            for kind in (KIND_EXTRACTION, KIND_TRANSLATION):
                row = queue.find_item(project, episode, "", kind)
                if row and row["status"] in ("pending", "running", "paused_daily_limit"):
                    queue.cancel(row["id"])
        except Exception as e:
            logger.warning(f"Could not cancel queue rows for {project}/{episode}: {e}")

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def ingest_webhook(self, source: str, payload: Dict) -> Optional[Dict]:
        """Feed a Sonarr/Radarr webhook payload into the store (same dedupe as polling)."""
        if source not in SOURCES or not isinstance(payload, dict):
            return None
        event = str(payload.get("eventType") or "")
        cfg = self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        try:
            if source == "sonarr":
                return self._ingest_sonarr(event, payload, cfg, settings)
            return self._ingest_radarr(event, payload, cfg, settings)
        except Exception as e:
            logger.error(f"Autopilot could not ingest the {source} webhook ({event}): {e}", exc_info=True)
            return None

    def _ingest_sonarr(self, event: str, payload: Dict, cfg: Dict, settings: AutopilotSettings) -> Optional[Dict]:
        series = payload.get("series") or {}
        episodes = payload.get("episodes") or []
        file = payload.get("episodeFile") or {}
        if not episodes or not file.get("path"):
            return None
        ep = _lowest_episode(episodes)
        if event == "Download":
            fields = self._sonarr_fields(cfg, series, ep, ep.get("id"), series.get("id") or ep.get("seriesId"),
                                         file.get("id"), file["path"])
            item, _ = self.store.upsert_detection(
                **fields, detected_at=self.now(), grace_seconds=settings.grace_seconds)
            return item
        if event == "EpisodeFileRenamed":
            return self._webhook_rename("sonarr", ep.get("id"), file, cfg)
        if event == "EpisodeFileDelete":
            return self._webhook_delete("sonarr", ep.get("id"), file.get("id"))
        return None

    def _ingest_radarr(self, event: str, payload: Dict, cfg: Dict, settings: AutopilotSettings) -> Optional[Dict]:
        movie = dict(payload.get("movie") or {})
        file = payload.get("movieFile") or {}
        if not file.get("path"):
            return None
        movie["movieFile"] = file
        if event == "Download":
            fields = self._radarr_fields(cfg, movie, file)
            item, _ = self.store.upsert_detection(
                **fields, detected_at=self.now(), grace_seconds=settings.grace_seconds)
            return item
        if event == "MovieFileRenamed":
            return self._webhook_rename("radarr", movie.get("id"), file, cfg)
        if event == "MovieFileDelete":
            return self._webhook_delete("radarr", movie.get("id"), file.get("id"))
        return None

    def _webhook_rename(self, source: str, item_id, file: Dict, cfg: Dict) -> Optional[Dict]:
        new_path = self._resolve_path(cfg, file.get("path"))
        result = None
        for item in self.store.find_for_media(source, item_id):
            if item["arr_file_id"] == file.get("id") and item["state"] not in ("superseded", "gone") \
                    and item["media_path"] != new_path:
                moved = self.rename_sidecars(item["media_path"], new_path)
                mapping = dict(moved)
                result = self.store.transition(
                    item["id"], item["state"], message=f"media file renamed to {Path(new_path).name}",
                    media_path=new_path, outputs=[mapping.get(o, o) for o in item.get("outputs") or []])
        return result

    def _webhook_delete(self, source: str, item_id, file_id) -> Optional[Dict]:
        result = None
        for item in self.store.find_for_media(source, item_id):
            if item["state"] in LIVE_STATES and item["arr_file_id"] == file_id:
                self._mark_gone(item, "file deleted in the arr")
                result = self.store.get(item["id"])
        return result

    # ------------------------------------------------------------------
    # Reconcile
    # ------------------------------------------------------------------

    def _enabled_at(self, settings: AutopilotSettings) -> datetime:
        """When the autopilot was switched on; files imported before are backlog."""
        enabled = parse_iso(settings.enabled_at)
        if enabled:
            return enabled
        fallback = self.store.get_cursor("enabled_at_fallback")
        if fallback and parse_iso(fallback.get("value")):
            return parse_iso(fallback["value"])
        now = self.now()
        self.store.set_cursor("enabled_at_fallback", {"value": iso_utc(now)})
        return now

    def _backlog_quota(self, settings: AutopilotSettings) -> int:
        if settings.backlog_mode == "off":
            return 0
        day = self.now().strftime("%Y-%m-%d")
        used = self.store.get_cursor("backlog") or {}
        count = used.get("count", 0) if used.get("day") == day else 0
        return max(0, settings.backlog_daily_limit - count)

    def _backlog_used(self, added: int) -> None:
        if not added:
            return
        day = self.now().strftime("%Y-%m-%d")
        used = self.store.get_cursor("backlog") or {}
        count = used.get("count", 0) if used.get("day") == day else 0
        self.store.set_cursor("backlog", {"day": day, "count": count + added})

    async def reconcile_once(self) -> Dict:
        """Walk the libraries: queue files that have no item and no Greek (new ones, plus backlog)."""
        cfg = self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        enabled_at = self._enabled_at(settings)
        candidates: List[Dict] = []  # older files, backlog only
        summary: Dict[str, Any] = {"created": 0, "requeued": 0, "backlog": 0}
        for source in SOURCES:
            arr = self.arr_config(source, cfg)
            self._state[source].configured = arr is not None
            if arr is None:
                continue
            try:
                walker = self._walk_sonarr if source == "sonarr" else self._walk_radarr
                found = await walker(arr, cfg, settings, enabled_at, candidates)
            except Exception as e:
                self._record_failure(source, e)
                summary[source] = {"ok": False, "error": self._state[source].error}
                continue
            self._record_ok(source)
            summary[source] = {"ok": True, **found}
            summary["created"] += found["created"]
            summary["requeued"] += found["requeued"]
        summary["backlog"] = await self._add_backlog(candidates, settings)
        return summary

    async def _greek_present(self, media_path: str) -> bool:
        from services.autopilot.media_inspect import find_language_sidecars
        loop = asyncio.get_running_loop()
        return bool(await loop.run_in_executor(None, find_language_sidecars, media_path, "el"))

    async def _consider(self, source: str, fields: Dict, added: Optional[datetime], cfg: Dict,
                        settings: AutopilotSettings, enabled_at: datetime, candidates: List[Dict],
                        counts: Dict) -> None:
        """Queue one library file now, remember it as backlog, or leave it alone."""
        known = [i for i in self.store.find_for_media(source, fields["arr_item_id"])
                 if i["arr_file_id"] == fields["arr_file_id"]]
        if known:
            item = known[0]
            if item["state"] == "no_source" and await self._has_external_source(fields["media_path"]):
                self.store.transition(item["id"], "waiting", message="a source subtitle appeared",
                                      due_at=self.now(), attempts=0)
                counts["requeued"] += 1
            return
        is_new = added is not None and added >= enabled_at
        if not is_new and settings.backlog_mode == "off":
            return
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(None, os.path.exists, fields["media_path"]):
            return
        if is_new:
            if await self._greek_present(fields["media_path"]):
                return
            _, created = self.store.upsert_detection(
                **fields, detected_at=added, grace_seconds=0)
            counts["created"] += int(created)
        else:
            candidates.append({"fields": fields, "added": added})

    async def _has_external_source(self, media_path: str) -> bool:
        from integrations.subtitle_scanner import SubtitleScannerService
        scan = await asyncio.get_running_loop().run_in_executor(
            None, SubtitleScannerService("en", "el").scan_media_file, media_path)
        return bool(scan.has_source_sub)

    async def _walk_sonarr(self, arr, cfg: Dict, settings: AutopilotSettings, enabled_at: datetime,
                           candidates: List[Dict]) -> Dict:
        counts = {"created": 0, "requeued": 0}
        for series in await sonarr_api.get_all_series(arr):
            if settings.monitored_only and not series.get("monitored", True):
                continue
            stats = series.get("statistics")
            if stats is not None and not stats.get("episodeFileCount"):
                continue
            episodes = await sonarr_api.get_episodes(arr, series["id"])
            files = await sonarr_api.get_episode_files(arr, series["id"])
            if not episodes or files is None:
                continue
            by_id = {f.get("id"): f for f in files}
            used = set()
            for ep in sorted(episodes, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0)):
                file_id = ep.get("episodeFileId") or 0
                file = by_id.get(file_id)
                if not file or file_id in used or not file.get("path"):
                    continue
                if settings.monitored_only and not ep.get("monitored", True):
                    continue
                used.add(file_id)
                fields = self._sonarr_fields(cfg, series, ep, ep.get("id"), series["id"], file_id, file["path"])
                await self._consider("sonarr", fields, parse_iso(file.get("dateAdded")), cfg, settings,
                                     enabled_at, candidates, counts)
        return counts

    async def _walk_radarr(self, arr, cfg: Dict, settings: AutopilotSettings, enabled_at: datetime,
                           candidates: List[Dict]) -> Dict:
        counts = {"created": 0, "requeued": 0}
        loop = asyncio.get_running_loop()
        for movie in await radarr_api.get_all_movies(arr):
            mf = movie.get("movieFile") or {}
            if not movie.get("hasFile", bool(mf)) or not mf.get("path"):
                continue
            if settings.monitored_only and not movie.get("monitored", True):
                continue
            fields = await loop.run_in_executor(None, self._radarr_fields, cfg, movie, mf)
            await self._consider("radarr", fields, parse_iso(mf.get("dateAdded")), cfg, settings,
                                 enabled_at, candidates, counts)
        return counts

    async def _add_backlog(self, candidates: List[Dict], settings: AutopilotSettings) -> int:
        quota = self._backlog_quota(settings)
        if not quota or not candidates:
            return 0
        floor = datetime.min.replace(tzinfo=timezone.utc)
        candidates.sort(key=lambda c: c["added"] or floor, reverse=settings.backlog_mode == "newest")
        added = 0
        for cand in candidates:
            if added >= quota:
                break
            if await self._greek_present(cand["fields"]["media_path"]):
                continue
            _, created = self.store.upsert_detection(
                **cand["fields"], detected_at=self.now(), grace_seconds=0)
            added += int(created)
        self._backlog_used(added)
        return added


# ----------------------------------------------------------------------
# Series / movie facts for the skip rules
# ----------------------------------------------------------------------

class ArrMediaInfo:
    """Default ``media_info`` of the pipeline: facts from Sonarr/Radarr, cached for a few minutes.

    Returns ``{title, original_language, tags (names), monitored, series_type}``; ``{}`` when the
    source is not configured. Raises when the arr cannot be reached (the pipeline retries).
    """

    def __init__(self, config_provider: Optional[Callable[[], Dict]] = None,
                 ttl_seconds: float = MEDIA_INFO_TTL_SECONDS):
        self._config_provider = config_provider
        self._ttl = ttl_seconds
        self._cache: Dict[Tuple, Tuple[float, Any]] = {}

    async def _cached(self, key: Tuple, fetch):
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        value = await fetch()
        if value is not None:
            self._cache[key] = (time.monotonic(), value)
        return value

    async def _tag_names(self, source: str, arr, ids) -> List[str]:
        if not ids:
            return []
        api = sonarr_api if source == "sonarr" else radarr_api
        tags = await self._cached((source, "tags"), lambda: api.get_tags(arr)) or []
        labels = {t.get("id"): str(t.get("label") or "") for t in tags}
        return [labels[i] for i in ids if i in labels]

    async def __call__(self, item: Dict) -> Dict:
        provider = self._config_provider or storage.load_global_config
        cfg = provider() or {}
        source = item["source"]
        arr = ArrWatcher.arr_config(source, cfg)
        if arr is None:
            return {}
        if source == "sonarr":
            series_id = item.get("arr_parent_id")
            series = await self._cached(("series", series_id), lambda: sonarr_api.get_series(arr, series_id)) \
                if series_id else None
            if series is None:
                raise RuntimeError(f"Sonarr series {series_id} could not be fetched")
            ep = await self._cached(("episode", item["arr_item_id"]),
                                    lambda: sonarr_api.get_episode(arr, item["arr_item_id"]))
            monitored = bool(series.get("monitored", True)) and bool((ep or {}).get("monitored", True))
            return {
                "title": series.get("title"),
                "original_language": (series.get("originalLanguage") or {}).get("name"),
                "tags": await self._tag_names(source, arr, series.get("tags")),
                "monitored": monitored,
                "series_type": series.get("seriesType"),
            }
        movie_id = item["arr_item_id"]
        movie = await self._cached(("movie", movie_id), lambda: radarr_api.get_movie(arr, movie_id))
        if movie is None:
            raise RuntimeError(f"Radarr movie {movie_id} could not be fetched")
        return {
            "title": movie.get("title"),
            "original_language": (movie.get("originalLanguage") or {}).get("name"),
            "tags": await self._tag_names(source, arr, movie.get("tags")),
            "monitored": bool(movie.get("monitored", True)),
            "series_type": None,
        }
