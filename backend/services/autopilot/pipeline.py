"""Processes autopilot items: skip rules, Greek check, source choice, routing, tracking (plan 4.5).

``process_due`` takes items whose grace period is over through the checks and routes them to
the extraction / translation queue; ``track_active`` follows the queue rows of routed items
until the Greek files exist on disk. Nothing here translates: an engine consumes the queue.
"""

import asyncio
import codecs
import inspect as _inspect
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.autopilot import media_inspect
from services.autopilot.config import AutopilotSettings
from services.autopilot.store import AutopilotStore, parse_iso
from utils import paths, storage
from utils.language_codes import output_filename_from_original
from utils.translation_queue import KIND_EXTRACTION, KIND_TRANSLATION, PRIORITY_WEBHOOK, TranslationQueue

logger = logging.getLogger(__name__)

TARGET_CODE = "el"
RETRY_DELAYS = (300, 1800, 7200)       # 5 min, 30 min, 2 h
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1   # the failure after the last delay parks the item
MEDIA_MISSING_DELAY = 60
MEDIA_MISSING_ATTEMPTS = 15            # a file that is still moving: one try a minute for 15 minutes
LOST_JOB_SECONDS = 30 * 60             # no queue row this long after routing -> route again
HANDOFF_SECONDS = 120                  # extraction done but the translation row not there yet
FRESH_SLACK_SECONDS = 2.0              # filesystem timestamp granularity

_REASON_MAP = {
    "image_subtitles_only": "image_only",
    "no_subtitles": "no_subtitles",
    "ffmpeg_unavailable": "ffmpeg_missing",
    "media_unreachable": "media_missing",
}
_TEXT_SIDECARS = (".srt", ".ass", ".ssa", ".vtt")
_ASS_EXTS = (".ass", ".ssa")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: Optional[datetime]) -> Optional[float]:
    return dt.timestamp() if dt else None


class AutopilotPipeline:
    def __init__(self, store: AutopilotStore, *, engine_factory: Optional[Callable[[], Any]] = None,
                 media_info: Optional[Callable[[Dict], Any]] = None,
                 clock: Optional[Callable[[], datetime]] = None,
                 on_event: Optional[Callable[..., Any]] = None,
                 config_provider: Optional[Callable[[], Dict]] = None):
        self.store = store
        self._config_provider = config_provider
        self._engine_factory = engine_factory or self._default_engine
        if media_info is None:
            from services.autopilot.watcher import ArrMediaInfo
            media_info = ArrMediaInfo(config_provider)
        self._media_info = media_info
        self._clock = clock or store.now
        self._on_event = on_event
        self._inflight: set = set()

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def now(self) -> datetime:
        return self._clock()

    def _cfg(self) -> Dict:
        return (self._config_provider or storage.load_global_config)() or {}

    def _default_engine(self):
        from routers.integrations import _build_arr_engine
        return _build_arr_engine(self._cfg(), extract_embedded_ass=True)

    @staticmethod
    async def _blocking(fn, *args):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *args)

    async def _emit(self, kind: str, item: Dict, **data) -> None:
        """Tell the integration layer (Plex, ownership, Discord); never lets it break the pipeline."""
        if self._on_event is None:
            return
        try:
            result = self._on_event(kind, item, **data)
            if _inspect.isawaitable(result):
                await result
        except Exception as e:
            logger.warning(f"Autopilot on_event({kind}) failed for {item.get('id')}: {e}")

    def _register(self, path: str, item: Dict) -> None:
        try:
            from services.autopilot import ownership
            ownership.register_file(path, item.get("project_name"), item.get("episode_name"))
        except Exception as e:
            logger.debug(f"Could not register {path} as written by OmbiSub: {e}")

    # ------------------------------------------------------------------
    # Due items
    # ------------------------------------------------------------------

    async def process_due(self, limit: int = 10) -> int:
        """Process the items whose time has come. Returns how many were handled."""
        handled = 0
        for item in self.store.due_items(self.now(), limit):
            if item["id"] in self._inflight:
                continue
            try:
                await self.process_item(item)
            except Exception as e:
                logger.error(f"Autopilot item {item['id']} crashed: {e}", exc_info=True)
            handled += 1
        return handled

    async def process_item(self, item: Dict) -> Dict:
        """Run steps 1-5 of the plan for one item; returns the updated item."""
        item_id = item["id"]
        if item_id in self._inflight:
            return item
        current = self.store.get(item_id)
        if current is None or current["state"] not in ("waiting", "failed"):
            return current or item
        self._inflight.add(item_id)
        try:
            item = self.store.transition(item_id, "checking", message="checking")
            try:
                return await self._check(item)
            except Exception as e:
                logger.error(f"Autopilot check failed for {item_id}: {e}", exc_info=True)
                return await self._fail_attempt(item, f"{type(e).__name__}: {e}")
        finally:
            self._inflight.discard(item_id)

    async def _check(self, item: Dict) -> Dict:
        cfg = self._cfg()
        settings = AutopilotSettings.from_config(cfg)
        media_path = item["media_path"]

        # 1. The file may still be moving into place.
        if not await self._blocking(os.path.exists, media_path):
            updated = self.store.schedule_retry(
                item["id"], f"media file not found: {media_path}", MEDIA_MISSING_DELAY, MEDIA_MISSING_ATTEMPTS)
            if updated["state"] == "failed":
                updated = self.store.transition(updated["id"], "failed", reason="media_missing",
                                                detail=updated["detail"], message="media file never appeared")
                await self._emit("failed", updated, error=updated["detail"], reason="media_missing")
            return updated

        # 1b. An upgrade under a new name leaves our Greek files of the replaced video behind.
        try:
            await self._blocking(self._clean_replaced, item)
        except Exception as e:  # tidying up must never cost the new file its subtitles
            logger.warning(f"Could not clean up after the video {item['id']} replaced: {e}")

        # 2. Skip rules.
        reason = await self._skip_reason(item, settings)
        if reason:
            updated = self.store.transition(item["id"], "skipped", reason=reason, message=f"skipped: {reason}")
            await self._emit("skipped", updated, reason=reason)
            return updated

        source_code = cfg.get("arr_source_language") or "en"

        # 3. Greek check: sidecars first (no probe needed), then the container. A Greek file
        # OmbiSub wrote itself (an upgrade that kept the file name, an interrupted earlier
        # attempt) is not "Greek present": only foreign files stand the autopilot down.
        ep_meta = await self._episode_meta(item)
        info = await self._blocking(media_inspect.inspect, media_path, TARGET_CODE, source_code, False)
        foreign = await self._blocking(self._foreign_paths, info["target_paths"], ep_meta)
        if foreign:
            return await self._greek_sidecar(item, foreign, settings)

        engine = self._engine_factory()
        info = await self._blocking(media_inspect.inspect, media_path, TARGET_CODE, source_code, True, engine,
                                    None, bool(info["target_paths"]))
        foreign = await self._blocking(self._foreign_paths, info["target_paths"], ep_meta)
        if foreign:
            return await self._greek_sidecar(item, foreign, settings)
        if info["embedded_target_kind"] and settings.count_embedded_greek:
            kind = info["embedded_target_kind"]
            updated = self.store.transition(item["id"], "has_greek", reason=f"embedded_greek_{kind}",
                                            message=f"embedded Greek {kind} track found")
            await self._emit("has_greek", updated, reason=updated["reason"])
            return updated

        choice = info.get("source_choice")
        if choice and await self._blocking(self._reuse_existing, item, engine, info, choice):
            meta = await self._blocking(storage.load_episode_metadata, item["project_name"], item["episode_name"])
            outputs = [str(p) for p in self._expected_outputs(item, meta, choice["kind"])]
            return await self._finish(item, outputs, "re-exported the existing translation")

        # 4. Source choice.
        if not choice:
            no_source = _REASON_MAP.get(info.get("reason"), info.get("reason") or "no_source")
            updated = self.store.transition(item["id"], "no_source", reason=no_source,
                                            message=f"no usable source subtitle ({no_source})")
            await self._emit("no_source", updated, reason=no_source)
            return updated

        # 5. Route.
        return await self._route(item, engine, choice, cfg)

    def _clean_replaced(self, item: Dict) -> List[str]:
        """Delete the Greek files OmbiSub wrote for a video this one replaced (blocking).

        Sonarr/Radarr delete the old video on an upgrade, but our ``.el.ass``/``.el.srt`` under
        its name would stay behind as orphans. Only superseded items whose video is gone are
        cleaned, and only files the ownership registry says are ours and unchanged.
        """
        from services.autopilot import ownership
        if item.get("arr_item_id") is None:
            return []
        media = Path(item["media_path"])
        new_prefix = (media.stem + ".").lower()
        removed: List[str] = []
        for old in self.store.find_for_media(item["source"], item["arr_item_id"]):
            if old["id"] == item["id"] or old["state"] != "superseded" or not old.get("outputs"):
                continue
            old_media = Path(old["media_path"])
            if old_media == media or old_media.exists():
                continue
            gone = []
            for out in old["outputs"]:
                path = Path(out)
                if path.parent == media.parent and path.name.lower().startswith(new_prefix):
                    continue  # also named after the new video: not an orphan
                try:
                    if path.is_file() and ownership.is_ours(path):
                        path.unlink()
                        ownership.forget(path)
                        gone.append(path.name)
                except OSError as e:
                    logger.warning(f"Could not remove the replaced subtitle {path}: {e}")
            if gone:
                removed += gone
                self.store.transition(old["id"], "superseded", outputs=[],
                                      message=f"removed its Greek files: {', '.join(gone)}")
                logger.info(f"Autopilot: removed {', '.join(gone)} (the video was replaced by {media.name}).")
        return removed

    async def _episode_meta(self, item: Dict) -> Optional[Dict]:
        if not (item.get("project_name") and item.get("episode_name")):
            return None
        try:
            return await self._blocking(storage.load_episode_metadata, item["project_name"], item["episode_name"])
        except Exception as e:
            logger.debug(f"No episode metadata for {item.get('id')}: {e}")
            return None

    @staticmethod
    def _foreign_paths(found: List[str], meta: Optional[Dict]) -> List[str]:
        """The Greek sidecars OmbiSub did not write."""
        return [p for p in found if _ownership_check(p, meta) is not True]

    async def _skip_reason(self, item: Dict, settings: AutopilotSettings) -> Optional[str]:
        meta = await self._blocking(storage.load_project_metadata, item["project_name"]) \
            if item.get("project_name") else None
        if meta:
            prio_fn = getattr(storage, "resolve_project_priority", None)
            tier = prio_fn(meta) if callable(prio_fn) else ("ignored" if meta.get("arr_disabled") else meta.get("translation_priority", "normal"))
            if tier == "ignored" or meta.get("arr_disabled") or meta.get("translation_priority") == "ignored":
                return "arr_disabled"
        facts = self._media_info(item)
        if _inspect.isawaitable(facts):
            facts = await facts
        facts = facts or {}
        if settings.skip_original_greek and _is_greek_language(facts.get("original_language")):
            return "original_greek"
        tags = {str(t).strip().lower() for t in facts.get("tags") or []}
        if tags & set(settings.skip_tags):
            return "skip_tag"
        if settings.monitored_only and facts.get("monitored") is False:
            return "unmonitored"
        return None

    async def _greek_sidecar(self, item: Dict, target_paths: List[str], settings: AutopilotSettings) -> Dict:
        repaired: List[str] = []
        if settings.repair_foreign_encoding:
            for path in target_paths:
                if Path(path).suffix.lower() not in _TEXT_SIDECARS:
                    continue
                try:
                    if await self._blocking(self._repair_sidecar, item, path):
                        repaired.append(path)
                except Exception as e:
                    logger.warning(f"Could not repair the encoding of {path}: {e}")
        names = ", ".join(Path(p).name for p in target_paths)
        detail = names + (f" (repaired: {', '.join(Path(p).name for p in repaired)})" if repaired else "")
        updated = self.store.transition(item["id"], "has_greek", reason="greek_sidecar", detail=detail,
                                        message=f"Greek subtitle present: {detail}")
        if repaired:
            await self._emit("foreign_repaired", updated, paths=repaired)
        await self._emit("has_greek", updated, reason="greek_sidecar", paths=target_paths)
        return updated

    # ------------------------------------------------------------------
    # Foreign sidecar encoding repair
    # ------------------------------------------------------------------

    def _repair_sidecar(self, item: Dict, path: str) -> bool:
        """Fix a garbled Greek sidecar in place (blocking). The original is kept in the backups folder."""
        from utils import text_repair
        raw = Path(path).read_bytes()
        legacy = False
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            text, encoding = raw.decode("utf-16"), "utf-16"
        else:
            try:
                text = raw.decode("utf-8-sig")
                encoding = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
            except UnicodeDecodeError:
                text, encoding, legacy = text_repair.decode_subtitle_bytes(raw), "utf-8-sig", True
        new, fixed = text_repair.repair_subtitle_text(text, known=False)
        if legacy and text_repair.has_greek(new):
            pass  # a single-byte Greek file: Plex and players expect UTF-8
        elif new == text:
            return False

        backup_dir = Path(paths.DATA_DIR) / "autopilot" / "backups" / item["id"]
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / Path(path).name)
        tmp = Path(path).with_name(f"{Path(path).name}.omnisub-tmp-{uuid.uuid4().hex[:8]}")
        try:
            tmp.write_bytes(new.encode(encoding))
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        logger.info(f"Repaired the encoding of {path} ({fixed} cues); original kept in {backup_dir}")
        return True

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------

    @staticmethod
    def _expected_outputs(item: Dict, meta: Optional[Dict], kind: Optional[str] = None) -> List[Path]:
        """The Greek files that must exist for the item: ``.el.ass`` + ``.el.srt`` for ASS, ``.el.srt`` for SRT."""
        media = Path(item["media_path"])
        kind = kind or item.get("source_kind") or ""
        ext = Path((meta or {}).get("original_filename") or "").suffix.lower()
        if ext not in _TEXT_SIDECARS:
            ext = ".ass" if kind.endswith("_ass") else ".srt"
        names = [output_filename_from_original(f"{media.stem}{ext}", TARGET_CODE)]
        if ext in _ASS_EXTS:
            names.append(output_filename_from_original(f"{media.stem}.srt", TARGET_CODE))
        return [media.parent / n for n in names]

    def _reuse_existing(self, item: Dict, engine, info: Dict, choice: Dict) -> bool:
        """Re-export a translation OmbiSub already holds for this exact source (blocking)."""
        from integrations.media_sync_engine import MediaSyncEngine
        project, episode = item.get("project_name"), item.get("episode_name")
        if not project or not episode:
            return False
        meta = storage.load_episode_metadata(project, episode)
        fingerprint = (meta or {}).get("arr_sub_fingerprint")
        if not fingerprint or storage.episode_translation_is_stale(meta):
            return False
        if not any(MediaSyncEngine._fingerprint(p) == fingerprint for p in info.get("source_paths") or []):
            return False
        scan = engine.scanner.scan_media_file(item["media_path"])
        # The Greek files beside the video are OmbiSub's own (the check ruled foreign ones out):
        # re-export over them rather than treating them as someone else's subtitle.
        scan.has_target_sub = False
        if not engine._reuse_and_export_existing_translation(project, episode, item["media_path"], scan, storage, meta):
            return False
        meta = storage.load_episode_metadata(project, episode)
        expected = self._expected_outputs(item, meta, choice["kind"])
        if not all(p.exists() for p in expected):
            try:  # the ASS companion: the exporter writes both formats
                from services.translation.export_formatter import auto_export_translated_subtitle
                rows = (storage.load_episode(project, episode) or {}).get("data") or []
                auto_export_translated_subtitle(project, episode, rows, meta)
            except Exception as e:
                logger.warning(f"Top-up export failed for {project}/{episode}: {e}")
        return all(p.exists() for p in expected)

    async def _finish(self, item: Dict, outputs: List[str], message: str) -> Dict:
        for path in outputs:
            await self._blocking(self._register, path, item)
        updated = self.store.transition(item["id"], "done", message=message, outputs=outputs)
        await self._emit("done", updated, outputs=outputs)
        return updated

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _route(self, item: Dict, engine, choice: Dict, cfg: Dict) -> Dict:
        kind = choice["kind"]
        project, episode = item["project_name"], item["episode_name"]
        facts = self._media_info(item)
        if _inspect.isawaitable(facts):
            facts = await facts
        facts = facts or {}
        opts = await self._blocking(self._translation_options, project)

        if kind.startswith("embedded"):
            if not cfg.get("embedded_extraction_enabled", True):
                updated = self.store.transition(
                    item["id"], "failed", reason="extraction_disabled",
                    detail="embedded_extraction_enabled is off in the settings",
                    message="cannot extract the embedded subtitle: the extraction lane is disabled")
                await self._emit("failed", updated, error=updated["detail"], reason="extraction_disabled")
                return updated
            queued = await self._blocking(self._queue_extraction, item, engine, opts, facts)
            if not queued:
                return await self._fail_attempt(item, "the embedded subtitle extraction could not be queued")
            state, message = "extracting", f"queued extraction of the embedded {kind[-3:].upper()} track"
        else:
            error = await self._import_external(item, engine, choice["path"], opts)
            if error:
                return await self._fail_attempt(item, error)
            state, message = "translating", f"queued translation from {Path(choice['path']).name}"

        return self.store.transition(item["id"], state, message=message, source_kind=kind,
                                     routed_at=self.now(), outputs=[])

    @staticmethod
    def _translation_options(project: str) -> Dict:
        from services.queue_service import translation_options_for
        opts = translation_options_for(project)
        meta = storage.load_project_metadata(project)
        if not meta or not meta.get("context_guide"):
            opts["ensure_context"] = True  # a fresh project builds its guide before the first translation
        return opts

    def _import_metadata(self, item: Dict, facts: Dict) -> Dict:
        if item["kind"] == "movie":
            return {"arr_radarr_id": item["arr_item_id"]}
        return {
            "arr_sonarr_series_id": item.get("arr_parent_id"),
            "arr_sonarr_episode_id": item["arr_item_id"],
            "season": item.get("season"),
            "episode": item.get("episode"),
        }

    def _queue_extraction(self, item: Dict, engine, opts: Dict, facts: Dict) -> bool:
        from integrations.media_sync_engine import SyncResult
        project, episode = item["project_name"], item["episode_name"]
        queue = TranslationQueue()
        stale = queue.find_item(project, episode, "", KIND_TRANSLATION)
        if stale and stale["status"] in ("completed", "failed", "cancelled"):
            queue.delete_item(stale["id"])  # a record of an earlier file; it would pass for this one's result
        media_type = "movie" if item["kind"] == "movie" else "series"
        project_meta = engine._new_project_metadata(item.get("title") or project, media_type)
        if facts.get("series_type"):
            project_meta["series_type"] = facts["series_type"]

        prio = PRIORITY_WEBHOOK
        if project:
            pmeta = storage.load_project_metadata(project) or {}
            prio_fn = getattr(storage, "resolve_project_priority", None)
            tier = prio_fn(pmeta) if callable(prio_fn) else ("ignored" if pmeta.get("arr_disabled") else pmeta.get("translation_priority", "normal"))
            if tier == "high":
                prio = -1
            elif tier == "low":
                prio = 5

        return bool(engine._probe_and_enqueue_extraction(
            project, episode, item["media_path"], self._import_metadata(item, facts), prio,
            SyncResult(), project_meta,
            {"translate_after": {"options": opts, "lang": ""}},
        ))

    async def _import_external(self, item: Dict, engine, source_path: str, opts: Dict) -> Optional[str]:
        """Import the English sidecar as the episode's source and queue its translation. Returns an error text."""
        from integrations.media_sync_engine import SyncResult
        from services.queue_service import enqueue_translation
        from utils.srt_parser import parse_srt
        project, episode = item["project_name"], item["episode_name"]
        scan = await self._blocking(engine.scanner.scan_media_file, item["media_path"])
        if not scan.has_source_sub:
            return "the English sidecar disappeared"
        await engine._import_episode_file(
            project_name=project, series_title=item.get("title") or project, episode_name=episode,
            media_path=item["media_path"], scan=scan, ep_meta=self._import_metadata(item, {}),
            result=SyncResult(), storage=storage, parse_srt=parse_srt,
            media_type="movie" if item["kind"] == "movie" else "series",
        )
        if not await self._blocking(storage.original_subtitle_exists, project, episode):
            return f"importing {Path(source_path).name} did not create the episode"

        prio = PRIORITY_WEBHOOK
        if project:
            pmeta = await self._blocking(storage.load_project_metadata, project) or {}
            prio_fn = getattr(storage, "resolve_project_priority", None)
            tier = prio_fn(pmeta) if callable(prio_fn) else ("ignored" if pmeta.get("arr_disabled") else pmeta.get("translation_priority", "normal"))
            if tier == "high":
                prio = -1
            elif tier == "low":
                prio = 5

        if not await self._blocking(enqueue_translation, project, episode, prio, opts):
            return "the translation could not be queued"
        return None

    # ------------------------------------------------------------------
    # Tracking
    # ------------------------------------------------------------------

    async def track_active(self) -> int:
        """Follow routed items through the queue. Returns how many items changed state."""
        changed = 0
        for item in self.store.active_items():
            if item["id"] in self._inflight:
                continue
            self._inflight.add(item["id"])
            try:
                if await self._track(item):
                    changed += 1
            except Exception as e:
                logger.error(f"Autopilot tracking of {item['id']} crashed: {e}", exc_info=True)
            finally:
                self._inflight.discard(item["id"])
        return changed

    async def _track(self, item: Dict) -> bool:
        queue = TranslationQueue()
        project, episode = item["project_name"], item["episode_name"]
        routed = parse_iso(item.get("routed_at")) or self.now()
        age = (self.now() - routed).total_seconds()

        extraction = await self._blocking(queue.find_item, project, episode, "", KIND_EXTRACTION)
        translation = await self._blocking(queue.find_item, project, episode, "", KIND_TRANSLATION)
        if translation and translation["status"] == "completed" and self._finished_before(translation, routed):
            translation = None  # the record of an earlier file

        if item["state"] == "extracting":
            if translation is not None and (extraction is None or extraction["status"] == "completed"):
                item = self.store.transition(item["id"], "translating", message="extracted, translating")
            else:
                return await self._track_extraction(item, extraction, age)

        return await self._track_translation(item, translation, age)

    @staticmethod
    def _finished_before(row: Dict, routed: datetime) -> bool:
        done = parse_iso(row.get("completed_at"))
        return bool(done and done < routed - timedelta(seconds=FRESH_SLACK_SECONDS))

    async def _track_extraction(self, item: Dict, row: Optional[Dict], age: float) -> bool:
        if row is None:
            if age > LOST_JOB_SECONDS:
                await self._fail_attempt(item, "the extraction job vanished from the queue", delay=0)
                return True
            return False
        status = row["status"]
        if status in ("failed", "cancelled"):
            await self._fail_attempt(item, f"extraction {status}: {_short(row.get('last_error'))}")
            return True
        if status == "completed":
            done = parse_iso(row.get("completed_at")) or self.now()
            if (self.now() - done).total_seconds() > HANDOFF_SECONDS:
                await self._fail_attempt(item, "the translation was never queued after the extraction", delay=0)
                return True
        return False  # pending, running or paused: waiting is not a failure

    async def _track_translation(self, item: Dict, row: Optional[Dict], age: float) -> bool:
        if row is None:
            if age > LOST_JOB_SECONDS:
                await self._fail_attempt(item, "the translation job vanished from the queue", delay=0)
                return True
            return False
        status = row["status"]
        if status in ("failed", "cancelled"):
            await self._fail_attempt(item, f"translation {status}: {_short(row.get('last_error'))}")
            return True
        if status != "completed":
            return False  # pending, running, paused for quota...: the engine's business
        return await self._verify(item)

    async def _verify(self, item: Dict) -> bool:
        meta = await self._blocking(storage.load_episode_metadata, item["project_name"], item["episode_name"])
        expected = self._expected_outputs(item, meta)
        routed = _ts(parse_iso(item.get("routed_at"))) or 0.0

        # An ASS episode whose project holds a translated SRT sibling gets its .el.srt from that
        # sibling's own export: the file must exist and be ours, but need not be rewritten now.
        stale_ok = set()
        if len(expected) > 1 and await self._blocking(self._srt_sibling_translated, item):
            stale_ok = {expected[-1]}

        def good(path: Path) -> bool:
            """Written since the routing, and by OmbiSub (a fresh foreign file is not our output)."""
            try:
                if path not in stale_ok and path.stat().st_mtime < routed - FRESH_SLACK_SECONDS:
                    return False
                if not path.is_file():
                    return False
            except OSError:
                return False
            return _ownership_check(path, meta) is not False

        missing = [p for p in expected if not await self._blocking(good, p)]
        if not missing:
            await self._finish(item, [str(p) for p in expected], "Greek subtitles written")
            return True

        media_path = item["media_path"]
        found = await self._blocking(media_inspect.find_language_sidecars, media_path, TARGET_CODE)
        if found and self._has_foreign(found, meta):
            # The exporter refused to overwrite it (translation_warning on the episode): it wins.
            names = ", ".join(Path(p).name for p in found)
            updated = self.store.transition(
                item["id"], "has_greek", reason="appeared_during_translation", detail=names,
                message=f"another Greek subtitle appeared while translating: {names}")
            await self._emit("has_greek", updated, reason="appeared_during_translation", paths=found)
            return True
        await self._fail_attempt(item, "translation finished but the Greek files are missing: "
                                       + ", ".join(p.name for p in missing))
        return True

    @staticmethod
    def _srt_sibling_translated(item: Dict) -> bool:
        from services.translation.export_formatter import _has_translated_srt_sibling
        return _has_translated_srt_sibling(item["project_name"], item["episode_name"], TARGET_CODE)

    @staticmethod
    def _has_foreign(found: List[str], meta: Optional[Dict]) -> bool:
        """True when a Greek file beside the video is not one OmbiSub wrote."""
        return any(_ownership_check(p, meta) is not True for p in found)

    async def _fail_attempt(self, item: Dict, error: str, delay: Optional[float] = None) -> Dict:
        """Count a failure: retry with backoff, or park the item as failed when attempts are spent."""
        attempts = int(item.get("attempts") or 0)
        if delay is None:
            delay = RETRY_DELAYS[min(attempts, len(RETRY_DELAYS) - 1)]
        updated = self.store.schedule_retry(item["id"], error, delay, MAX_ATTEMPTS)
        if updated["state"] == "failed":
            await self._emit("failed", updated, error=error, reason=updated.get("reason"))
        return updated


def _ownership_check(path, meta: Optional[Dict]) -> Optional[bool]:
    """``ownership.is_ours`` -- None when the registry is unavailable (callers decide the fallback)."""
    try:
        from services.autopilot import ownership
        return bool(ownership.is_ours(path, meta))
    except Exception as e:
        logger.debug(f"Ownership check unavailable for {path}: {e}")
        return None


def _short(text: Optional[str]) -> str:
    first = (text or "").strip().splitlines()
    return first[0][:200] if first else ""


def _is_greek_language(value: Optional[str]) -> bool:
    text = (value or "").strip().lower()
    return bool(text) and (text.startswith("greek") or text in ("el", "ell", "gre", "ελληνικά"))
