"""
On-demand subtitle requests — "I want Greek subtitles for this, now".

MorpiceHub (the Plex front-end) calls this when a viewer asks for a subtitle
that does not exist yet. Everything downstream is the ordinary pipeline; this
module only decides, per episode, which entry point that episode needs:

  target already beside the file, or muxed in  -> nothing to do  ("available")
  source sidecar on disk (imported by the sync) -> translation queue
  no sidecar, but a usable muxed text track     -> extraction lane, chained into translation
  nothing to translate from                     -> "unavailable", with the reason

To make that decision it syncs only the requested episodes — importing a whole
500-episode series to translate one episode would be all cost. A brand-new
project comes out of that sync exactly as a scheduled sync would create it, and
its context guide and glossary are built by the first translation it runs
(``ensure_context`` -> services.project_bootstrap).

Requests are persisted (who asked, for what, what each episode got at request
time) but progress is always derived live from the queue and episode metadata —
see ``render``. There is no status column to drift away from what the worker did.
"""

import asyncio
import logging
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from services.autopilot import media_inspect
from utils import on_demand_store, paths, storage
from utils.language_codes import LANG_CODES, to_code, variants_for
from utils.translation_queue import (
    KIND_EXTRACTION,
    KIND_TRANSLATION,
    PRIORITY_BACKLOG,
    PRIORITY_MANUAL,
    TranslationQueue,
)

logger = logging.getLogger(__name__)

# Episode states a request can report.
ACTIVE_STATES = {"queued", "extracting", "translating", "paused"}
DONE_STATES = {"available", "completed"}

# Bounded like the sync's own probes: ffprobe is a header read, but a season of
# them unbounded over SMB still saturates the link.
_PROBE_CONCURRENCY = 4
# A lookup probes containers only for small selections; beyond this it reports
# what the filesystem shows and leaves the probe to request time.
_LOOKUP_PROBE_LIMIT = 30


class OnDemandError(Exception):
    """A request that cannot proceed, with an HTTP status and a stable code the
    client can branch on (and localise) instead of parsing the message."""

    def __init__(self, status_code: int, code: str, message: str, extra: Optional[Dict] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}


@dataclass
class MediaQuery:
    media_type: str                       # "movie" | "series"
    tmdb_id: Optional[int] = None
    tvdb_id: Optional[int] = None
    imdb_id: Optional[str] = None
    radarr_id: Optional[int] = None
    sonarr_id: Optional[int] = None
    title: Optional[str] = None
    year: Optional[int] = None
    season: Optional[int] = None
    episodes: List[int] = field(default_factory=list)
    language: Optional[str] = None


@dataclass
class _Unit:
    """One episode (or the movie) a request covers."""
    episode_name: str
    season: Optional[int]
    episode: Optional[int]
    title: str
    has_file: bool
    import_metadata: Dict
    raw_media_path: str = ""


# ---------------------------------------------------------------------------
# Resolution: which library item, which episodes, which language
# ---------------------------------------------------------------------------

def _engine(config: Dict):
    from routers.integrations import _build_arr_engine
    # Never let a targeted pass fan out into probing the rest of the show: the
    # requested episodes are probed explicitly, with this request's own options.
    return _build_arr_engine(config, extract_embedded_ass=False)


def _language(config: Dict, requested: Optional[str]) -> Tuple[str, str]:
    """Resolve the requested language to (ISO code, display name)."""
    code = to_code(requested or config.get("default_target_language") or "Greek")
    name = next((n for n, c in LANG_CODES.items() if c == code), requested or code)
    return code, name


def _normalise_title(value: Optional[str]) -> str:
    text = unicodedata.normalize("NFKD", value or "").lower()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^\w]+", " ", text).strip()


def _match_library(items: List[Dict], q: MediaQuery) -> Optional[Dict]:
    """Pick one library item by external id, falling back to title (+year)."""
    for item in items:
        if q.imdb_id and item.get("imdbId") and item.get("imdbId") == q.imdb_id:
            return item
        if q.tmdb_id and item.get("tmdbId") and str(item.get("tmdbId")) == str(q.tmdb_id):
            return item
        if q.tvdb_id and item.get("tvdbId") and str(item.get("tvdbId")) == str(q.tvdb_id):
            return item

    if not q.title:
        return None
    wanted = _normalise_title(q.title)
    hits = [
        item for item in items
        if _normalise_title(item.get("title")) == wanted
        or any(_normalise_title(a.get("title")) == wanted for a in item.get("alternateTitles") or [])
    ]
    if q.year:
        hits = [h for h in hits if h.get("year") == q.year] or hits
    if len(hits) > 1 and not q.year:
        raise OnDemandError(
            409, "ambiguous",
            f"Several library titles match '{q.title}'. Pass a year or an id.",
            {"candidates": [{"title": h.get("title"), "year": h.get("year")} for h in hits[:5]]},
        )
    return hits[0] if hits else None


async def _resolve_movie(engine, q: MediaQuery) -> Optional[Dict]:
    from integrations.radarr import find_movie_by_tmdb, get_all_movies, get_movie
    cfg = engine.radarr
    if not (cfg and cfg.enabled and cfg.api_key):
        raise OnDemandError(503, "radarr_not_configured", "Radarr is not configured in Omnisub.")
    if q.tmdb_id:
        if movie := await find_movie_by_tmdb(cfg, int(q.tmdb_id)):
            return movie
    if q.radarr_id:
        if movie := await get_movie(cfg, int(q.radarr_id)):
            return movie
    if q.imdb_id or q.title:
        return _match_library(await get_all_movies(cfg), q)
    return None


async def _resolve_series(engine, q: MediaQuery) -> Optional[Dict]:
    from integrations.sonarr import find_series_by_tvdb, get_all_series, get_series
    cfg = engine.sonarr
    if not (cfg and cfg.enabled and cfg.api_key):
        raise OnDemandError(503, "sonarr_not_configured", "Sonarr is not configured in Omnisub.")
    if q.tvdb_id:
        if series := await find_series_by_tvdb(cfg, int(q.tvdb_id)):
            return series
    if q.sonarr_id:
        if series := await get_series(cfg, int(q.sonarr_id)):
            return series
    if q.imdb_id or q.tmdb_id or q.title:
        return _match_library(await get_all_series(cfg), q)
    return None


def _select_episodes(episodes: List[Dict], season: Optional[int], numbers: List[int]) -> List[Dict]:
    if numbers and season is None:
        raise OnDemandError(400, "season_required", "Episode numbers need a season number.")
    if season is None:
        # The whole show: every downloaded regular episode. Specials only on request.
        chosen = [e for e in episodes if e.get("hasFile") and (e.get("seasonNumber") or 0) > 0]
    elif numbers:
        wanted = set(numbers)
        chosen = [
            e for e in episodes
            if e.get("seasonNumber") == season and e.get("episodeNumber") in wanted
        ]
    else:
        chosen = [e for e in episodes if e.get("seasonNumber") == season and e.get("hasFile")]
    return sorted(chosen, key=lambda e: (e.get("seasonNumber") or 0, e.get("episodeNumber") or 0))


async def _movie_units(engine, q: MediaQuery) -> Tuple[Dict, str, List[_Unit]]:
    from integrations.media_sync_engine import MediaSyncEngine, movie_project_name
    movie = await _resolve_movie(engine, q)
    if not movie:
        raise OnDemandError(404, "not_in_library", "This movie is not in the Radarr library.")
    adopted = await asyncio.get_running_loop().run_in_executor(
        None, MediaSyncEngine._find_project_by_radarr_id, storage, movie.get("id")
    )
    project_name = movie_project_name(movie, adopted=adopted)
    movie_file = movie.get("movieFile") or {}
    unit = _Unit(
        episode_name=project_name,
        season=None,
        episode=None,
        title=movie.get("title") or project_name,
        has_file=bool(movie.get("hasFile") and movie_file.get("path")),
        import_metadata={"arr_radarr_id": movie.get("id")},
        raw_media_path=movie_file.get("path") or "",
    )
    return movie, project_name, [unit]


async def _series_units(engine, q: MediaQuery, *, require_selection: bool) -> Tuple[Dict, str, List[_Unit], List[Dict]]:
    from integrations.media_sync_engine import make_episode_name, make_project_name
    from integrations.sonarr import get_episode_files, get_episodes
    series = await _resolve_series(engine, q)
    if not series:
        raise OnDemandError(404, "not_in_library", "This series is not in the Sonarr library.")
    episodes = await get_episodes(engine.sonarr, series["id"])
    if not episodes:
        raise OnDemandError(502, "arr_error", "Sonarr returned no episodes for this series.")

    chosen = _select_episodes(episodes, q.season, q.episodes)
    if not chosen and require_selection:
        raise OnDemandError(404, "no_episodes", "No matching downloaded episodes were found.")

    files = {}
    if any(e.get("hasFile") for e in chosen):
        files = {f.get("id"): f.get("path", "") for f in (await get_episode_files(engine.sonarr, series["id"]) or [])}

    units = []
    for ep in chosen:
        season, number = ep.get("seasonNumber", 0), ep.get("episodeNumber", 0)
        raw_path = ((ep.get("episodeFile") or {}).get("path")) or files.get(ep.get("episodeFileId"), "")
        units.append(_Unit(
            episode_name=make_episode_name(season, number),
            season=season,
            episode=number,
            title=ep.get("title") or "",
            has_file=bool(ep.get("hasFile") and raw_path),
            import_metadata={
                "arr_sonarr_episode_id": ep.get("id"),
                "episode_title": ep.get("title") or "",
                "season": season,
                "episode": number,
            },
            raw_media_path=raw_path,
        ))
    return series, make_project_name(series.get("title", "Unknown")), units, episodes


# ---------------------------------------------------------------------------
# Inspection: what does this media file already have?
# ---------------------------------------------------------------------------

def _scanner_for(engine, lang_code: str):
    """The engine's scanner when it already targets this language, else one that does."""
    return media_inspect.scanner_for(engine, lang_code)


def _inspect_media(engine, media_path: str, lang_code: str, probe: bool,
                   dir_index: Optional[dict] = None) -> Dict:
    """Filesystem (+ optional container probe) facts for one media file. Blocking."""
    return media_inspect.inspect(
        media_path, target_code=lang_code, source_code=engine.source_lang_code,
        probe=probe, engine=engine, dir_index=dir_index,
    )


def _lang_key(project_meta: Optional[Dict], lang_code: str) -> str:
    """Queue ``lang`` for this language: '' when it is the project's primary target."""
    primary = to_code((project_meta or {}).get("target_language") or "")
    return "" if not project_meta or primary == lang_code else lang_code


def _short_error(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    first = str(raw).strip().splitlines()[0] if str(raw).strip() else ""
    return first[:200] or None


# ---------------------------------------------------------------------------
# Live status
# ---------------------------------------------------------------------------

def _live_state(queue: TranslationQueue, project_name: str, entry: Dict, lang_key: str) -> Dict:
    """Current state of one requested episode, derived from queue + metadata."""
    initial = entry.get("initial_state")
    state, detail, progress = initial, entry.get("detail"), None
    if initial == "available":
        return {"state": state, "detail": detail, "progress": None}

    ep_name = entry["episode_name"]
    try:
        ep_meta = storage.load_episode_metadata(project_name, ep_name)
    except ValueError:
        ep_meta = None
    meta_lang = lang_key or None
    has_target = bool(ep_meta) and storage.episode_has_target(ep_meta, meta_lang) \
        and not storage.episode_translation_is_stale(ep_meta, meta_lang)

    tr = queue.find_item(project_name, ep_name, lang_key, KIND_TRANSLATION)
    ex = queue.find_item(project_name, ep_name, "", KIND_EXTRACTION)

    if has_target and (tr is None or tr["status"] == "completed"):
        target_path = (
            ((ep_meta.get("arr_targets") or {}).get(lang_key) or {}).get("target_path")
            if lang_key else ep_meta.get("arr_target_path")
        )
        return {"state": "completed", "detail": Path(target_path).name if target_path else None, "progress": 100}

    if ex and ex["status"] in ("pending", "running"):
        detail = _short_error(ex.get("last_error")) if ex.get("next_retry_at") else None
        return {"state": "extracting", "detail": detail, "progress": None}

    if tr:
        status = tr["status"]
        if status == "pending":
            state = "queued"
            detail = _short_error(tr.get("last_error")) if tr.get("next_retry_at") else None
        elif status == "running":
            state, detail = "translating", None
            try:
                from utils.jobs_manager import jobs
                job = jobs.get(f"bg_{tr['id']}")
                if job is not None:
                    progress = round(float(job.progress or 0))
            except Exception:
                pass
        elif status == "paused_daily_limit":
            state, detail = "paused", "daily_quota"
        elif status == "failed":
            state, detail = "failed", _short_error(tr.get("last_error"))
        elif status == "cancelled":
            state, detail = "cancelled", None
        elif status == "completed":
            state, detail, progress = "completed", None, 100
        return {"state": state, "detail": detail, "progress": progress}

    if ex and ex["status"] == "failed":
        return {"state": "failed", "detail": _short_error(ex.get("last_error")), "progress": None}
    if ex and ex["status"] == "completed":
        # Extracted; the chained translation is being enqueued.
        return {"state": "queued", "detail": None, "progress": None}
    return {"state": state, "detail": detail, "progress": progress}


def _rollup(states: List[str]) -> str:
    if any(s in ACTIVE_STATES for s in states):
        return "in_progress"
    done = sum(1 for s in states if s in DONE_STATES)
    if states and done == len(states):
        return "completed"
    if done:
        return "partial"
    if any(s in ("failed", "cancelled") for s in states):
        return "failed"
    return "unavailable"


def render(record: Dict) -> Dict:
    """A stored request with every episode's live state."""
    queue = TranslationQueue(paths.DB_FILE)
    episodes = []
    for entry in record.get("episodes") or []:
        live = _live_state(queue, record["project_name"], entry, record.get("lang_key") or "")
        episodes.append({
            "episode_name": entry["episode_name"],
            "season": entry.get("season"),
            "episode": entry.get("episode"),
            "title": entry.get("title"),
            **live,
        })
    states = [e["state"] for e in episodes]
    return {
        "id": record["id"],
        "created_at": record["created_at"],
        "requested_by": record.get("requested_by"),
        "client": record.get("client"),
        "client_ref": record.get("client_ref"),
        "media_type": record["media_type"],
        "title": record["title"],
        "year": record.get("year"),
        "project_name": record["project_name"],
        "language": record["language"],
        "status": _rollup(states),
        "summary": dict(Counter(states)),
        "episodes": episodes,
    }


def get_request(request_id: str) -> Optional[Dict]:
    record = on_demand_store.get(request_id)
    return render(record) if record else None


def list_requests(limit: int = 50) -> List[Dict]:
    return [render(r) for r in on_demand_store.list_recent(limit)]


# ---------------------------------------------------------------------------
# Lookup (read-only)
# ---------------------------------------------------------------------------

async def lookup(q: MediaQuery) -> Dict:
    """What subtitles exist for a title, and what a request would do. No side effects."""
    config = storage.load_global_config()
    engine = _engine(config)
    lang_code, lang_name = _language(config, q.language)
    loop = asyncio.get_running_loop()

    if q.media_type == "movie":
        movie, project_name, units = await _movie_units(engine, q)
        item = {"title": movie.get("title"), "year": movie.get("year")}
        all_episodes = []
    else:
        series, project_name, units, all_episodes = await _series_units(engine, q, require_selection=False)
        item = {"title": series.get("title"), "year": series.get("year")}

    project_meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
    lang_key = _lang_key(project_meta, lang_code)
    base = {
        "found": True,
        "media_type": q.media_type,
        **item,
        "language": lang_code,
        "language_name": lang_name,
        "project": {
            "name": project_name,
            "exists": bool(project_meta),
            "has_glossary": bool(((project_meta or {}).get("glossary") or {}).get("terms")),
            "has_context": bool(((project_meta or {}).get("context_guide") or "").strip()),
        },
        "extraction_enabled": bool(config.get("embedded_extraction_enabled", False)),
    }

    # A series asked about as a whole: per-season counts from the filesystem only.
    if q.media_type != "movie" and q.season is None and not q.episodes:
        seasons: Dict[int, Dict] = {}
        for ep in all_episodes:
            number = ep.get("seasonNumber") or 0
            if number <= 0:
                continue
            seasons.setdefault(number, {"season": number, "episodes": 0, "downloaded": 0, "with_subtitle": 0})
            seasons[number]["episodes"] += 1
        indexes: Dict[str, dict] = {}

        def _count():
            for unit in units:
                media_path = engine.resolver.resolve(unit.raw_media_path)
                parent = str(Path(media_path).parent)
                if parent not in indexes:
                    indexes[parent] = _scanner_for(engine, lang_code).scan_directory_index(parent)
                info = _inspect_media(engine, media_path, lang_code, probe=False, dir_index=indexes[parent])
                row = seasons.get(unit.season)
                if row is None:
                    continue
                row["downloaded"] += 1
                if info["has_target"]:
                    row["with_subtitle"] += 1

        await loop.run_in_executor(None, _count)
        return {**base, "seasons": [seasons[k] for k in sorted(seasons)], "episodes": []}

    probe = len([u for u in units if u.has_file]) <= _LOOKUP_PROBE_LIMIT
    queue = TranslationQueue(paths.DB_FILE)
    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def describe(unit: _Unit) -> Dict:
        row = {
            "episode_name": unit.episode_name, "season": unit.season,
            "episode": unit.episode, "title": unit.title, "has_file": unit.has_file,
        }
        if not unit.has_file:
            return {**row, "state": "not_downloaded", "detail": None, "source": None}

        # Something already on its way wins over what the disk shows.
        live = _live_state(queue, project_name, {"episode_name": unit.episode_name, "initial_state": None}, lang_key)
        if live["state"] in ACTIVE_STATES:
            return {**row, "state": "in_progress", "detail": live["state"], "progress": live["progress"], "source": None}

        media_path = engine.resolver.resolve(unit.raw_media_path)
        async with sem:
            info = await loop.run_in_executor(None, _inspect_media, engine, media_path, lang_code, probe)
        if info["has_target"]:
            return {**row, "state": "available", "detail": info["target_file"], "source": None}
        if info["embedded_target"]:
            return {**row, "state": "available", "detail": f"embedded_{info['embedded_target']}", "source": None}
        if not info["reachable"]:
            return {**row, "state": "unavailable", "detail": "media_unreachable", "source": None}
        if info["has_source"]:
            return {**row, "state": "requestable", "detail": None, "source": "sidecar"}
        if info["embedded_source"]:
            return {**row, "state": "requestable", "detail": None, "source": "embedded"}
        if info["embedded_source"] is None and not probe:
            return {**row, "state": "requestable", "detail": None, "source": "unknown"}
        return {**row, "state": "unavailable", "detail": info["reason"], "source": None}

    rows = await asyncio.gather(*[describe(u) for u in units])
    return {**base, "episodes": list(rows), "summary": dict(Counter(r["state"] for r in rows))}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

async def create_request(q: MediaQuery, requested_by: Optional[str] = None,
                         client: Optional[str] = None, client_ref: Optional[str] = None) -> Dict:
    """Sync the requested episodes, route each one into the pipeline, record the request."""
    from integrations.media_sync_engine import SyncResult
    from utils.srt_parser import parse_srt

    config = storage.load_global_config()
    engine = _engine(config)
    lang_code, lang_name = _language(config, q.language)
    loop = asyncio.get_running_loop()
    request_id = uuid.uuid4().hex
    try:
        max_episodes = max(1, int(config.get("on_demand_max_episodes", 30) or 30))
    except (TypeError, ValueError):
        max_episodes = 30

    result = SyncResult()
    existing = set(await loop.run_in_executor(None, storage.list_projects))
    try:
        if q.media_type == "movie":
            movie, project_name, units = await _movie_units(engine, q)
            title, year = movie.get("title") or project_name, movie.get("year")
            if units[0].has_file:
                await engine._sync_movie(movie, project_name, result, storage, parse_srt, existing)
        else:
            series, project_name, units, _ = await _series_units(engine, q, require_selection=True)
            title, year = series.get("title") or project_name, series.get("year")
            if len(units) > max_episodes:
                raise OnDemandError(
                    400, "too_many_episodes",
                    f"{len(units)} episodes requested; the limit per request is {max_episodes}.",
                    {"limit": max_episodes, "requested": len(units)},
                )
            wanted = {u.episode_name for u in units if u.has_file}
            if wanted:
                await engine._sync_series(
                    series, project_name, result, storage, parse_srt, existing, episode_filter=wanted
                )
    except OnDemandError:
        raise
    except PermissionError as e:
        raise OnDemandError(502, "arr_error", str(e))

    project_meta = await loop.run_in_executor(None, _reenable_project, project_name)
    lang_key = _lang_key(project_meta, lang_code)
    translation_options = {
        "target_language_name": lang_name,
        # The first translation in a fresh project builds its context guide and
        # glossary before translating (services.project_bootstrap).
        "ensure_context": True,
        "on_demand_request_id": request_id,
        "requested_by": requested_by,
    }

    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
    entries = await asyncio.gather(*[
        _route_unit(engine, config, project_name, unit, lang_code, lang_key, translation_options, result, sem)
        for unit in units
    ])

    record = on_demand_store.create({
        "id": request_id,
        "requested_by": requested_by,
        "client": client,
        "client_ref": client_ref,
        "media_type": q.media_type,
        "title": title,
        "year": year,
        "project_name": project_name,
        "language": lang_code,
        "lang_key": lang_key,
        "episodes": list(entries),
    })
    dispositions = Counter(e["initial_state"] for e in entries)
    logger.info(
        f"On-demand {lang_code} subtitle request {request_id} from {requested_by or client or 'unknown'} "
        f"for {project_name}: {dict(dispositions)}"
    )
    return render(record)


def _reenable_project(project_name: str) -> Optional[Dict]:
    """A title someone just asked for is, by definition, back in the library.

    A title the owner set to Ignored stays ignored: the request's own episodes are
    still queued (an explicit request is not automatic work), nothing else is."""
    meta = storage.load_project_metadata(project_name)
    if meta and meta.get("arr_disabled") and not meta.get("ignored_by_user"):
        meta["arr_disabled"] = False
        meta.pop("arr_disabled_at", None)
        storage.save_project_metadata(project_name, meta)
    return meta


async def _route_unit(engine, config: Dict, project_name: str, unit: _Unit, lang_code: str,
                      lang_key: str, translation_options: Dict, result, sem: asyncio.Semaphore,
                      priority: int = PRIORITY_MANUAL) -> Dict:
    """Send one episode down the right entry point; return its stored disposition.

    Shared by on-demand requests and "Translate missing" (``translate_missing``): the
    episode must already have an entry (the filtered sync creates it from the media file).
    """
    from services.queue_service import enqueue_translation

    entry = {
        "episode_name": unit.episode_name, "season": unit.season,
        "episode": unit.episode, "title": unit.title,
    }
    if not unit.has_file:
        return {**entry, "initial_state": "not_downloaded", "detail": None}

    loop = asyncio.get_running_loop()
    ep_meta = await loop.run_in_executor(None, storage.load_episode_metadata, project_name, unit.episode_name)
    media_path = (ep_meta or {}).get("arr_media_path")
    if not ep_meta or not media_path:
        return {**entry, "initial_state": "unavailable", "detail": "media_unreachable"}

    async with sem:
        info = await loop.run_in_executor(None, _inspect_media, engine, media_path, lang_code, True)

    if info["has_target"]:
        return {**entry, "initial_state": "available", "detail": info["target_file"]}
    if info["embedded_target"]:
        return {**entry, "initial_state": "available", "detail": f"embedded_{info['embedded_target']}"}

    has_imported_source = await loop.run_in_executor(
        None, storage.original_subtitle_exists, project_name, unit.episode_name
    )
    if has_imported_source:
        enqueue_translation(project_name, unit.episode_name, priority, dict(translation_options), lang=lang_key)
        return {**entry, "initial_state": "queued", "detail": None}

    if not info["embedded_source"]:
        return {**entry, "initial_state": "unavailable", "detail": info["reason"] or "no_subtitles"}

    if config.get("embedded_extraction_enabled", False):
        queued = await loop.run_in_executor(
            None, engine._probe_and_enqueue_extraction,
            project_name, unit.episode_name, media_path, unit.import_metadata,
            priority, result, None,
            {"translate_after": {"options": dict(translation_options), "lang": lang_key}},
        )
        if queued:
            return {**entry, "initial_state": "extracting", "detail": None}
        return {**entry, "initial_state": "unavailable", "detail": info["reason"] or "no_subtitles"}

    # The extraction lane is switched off; the translation worker extracts a muxed
    # track inline when an episode has no source (translation_item_worker).
    enqueue_translation(project_name, unit.episode_name, priority, dict(translation_options), lang=lang_key)
    return {**entry, "initial_state": "queued", "detail": None}


# ---------------------------------------------------------------------------
# "Translate missing" for one library title
# ---------------------------------------------------------------------------

_MAX_REPORTED_UNAVAILABLE = 50


async def translate_missing(project_name: str, priority: int = PRIORITY_BACKLOG) -> Dict:
    """Queue every episode of a library title that has no translation yet.

    Episodes whose source subtitle is already imported go straight to the queue
    (``enqueue_missing_for_project``). For a Sonarr/Radarr title, the episodes OmbiSub has
    never imported (no sidecar was ever there) get an entry created from the media file and
    are routed exactly like an on-demand request: a muxed text track goes to the extraction
    lane chained into translation, or to the translation queue when the lane is off (the
    runner and the model worker extract inline). Nothing usable is reported with its reason.
    The extraction and translation are queued for the title's primary language.

    Returns ``enqueued_count`` (queue items created) plus the counts ``queued_for_translation``,
    ``queued_for_extraction``, ``no_usable_source`` and ``already_available``;
    ``unavailable`` lists the episodes without a usable source and ``error`` says why the
    Sonarr/Radarr part could not run (the imported episodes are queued regardless).
    """
    from integrations.media_sync_engine import SyncResult
    from services.queue_service import enqueue_missing_for_project, translation_options_for
    from utils.srt_parser import parse_srt

    loop = asyncio.get_running_loop()
    meta = await loop.run_in_executor(None, storage.load_project_metadata, project_name)
    if not meta:
        raise OnDemandError(404, "project_not_found", f"Project '{project_name}' not found.")

    queued = await loop.run_in_executor(None, enqueue_missing_for_project, project_name, priority)
    report: Dict = {
        "status": "success", "project": project_name,
        "queued_for_translation": len([q for q in queued if q]), "queued_for_extraction": 0,
        "no_usable_source": 0, "already_available": 0, "unavailable": [],
    }

    is_movie = (meta.get("arr_media_type") or meta.get("type")) == "movie"
    has_arr_id = meta.get("arr_radarr_id") if is_movie else meta.get("arr_sonarr_id")
    if meta.get("arr_disabled") or not (meta.get("arr_source") or has_arr_id):
        report["enqueued_count"] = report["queued_for_translation"]
        return report

    config = storage.load_global_config()
    engine = _engine(config)
    try:
        if is_movie:
            query = MediaQuery(media_type="movie", radarr_id=meta.get("arr_radarr_id"), title=meta.get("show_name"))
            movie, _, units = await _movie_units(engine, query)
            for unit in units:
                unit.episode_name = project_name
        else:
            query = MediaQuery(media_type="series", sonarr_id=meta.get("arr_sonarr_id"), title=meta.get("show_name"))
            series, _, units, _ = await _series_units(engine, query, require_selection=False)
        pending = []
        for unit in units:
            if unit.has_file and not await loop.run_in_executor(
                    None, storage.original_subtitle_exists, project_name, unit.episode_name):
                pending.append(unit)

        entries = []
        if pending:
            sync_result = SyncResult()
            existing = set(await loop.run_in_executor(None, storage.list_projects))
            if is_movie:
                await engine._sync_movie(movie, project_name, sync_result, storage, parse_srt, existing)
            else:
                await engine._sync_series(series, project_name, sync_result, storage, parse_srt, existing,
                                          episode_filter={u.episode_name for u in pending})
            fresh = await loop.run_in_executor(None, storage.load_project_metadata, project_name) or meta
            lang_code, lang_name = _language(config, fresh.get("target_language"))
            lang_key = _lang_key(fresh, lang_code)
            options = {**{k: v for k, v in translation_options_for(project_name).items() if v is not None},
                       "target_language_name": lang_name, "ensure_context": True}
            sem = asyncio.Semaphore(_PROBE_CONCURRENCY)
            entries = await asyncio.gather(*[
                _route_unit(engine, config, project_name, unit, lang_code, lang_key, options, sync_result, sem,
                            priority)
                for unit in pending
            ])
    except OnDemandError as e:
        report["error"] = e.message
    except Exception as e:
        logger.warning(f"Translate missing for '{project_name}' could not read Sonarr/Radarr: {e}")
        report["error"] = f"Sonarr/Radarr could not be read: {e}"
    else:
        for entry in entries:
            state = entry["initial_state"]
            if state == "queued":
                report["queued_for_translation"] += 1
            elif state == "extracting":
                report["queued_for_extraction"] += 1
            elif state == "available":
                report["already_available"] += 1
            else:
                report["no_usable_source"] += 1
                if len(report["unavailable"]) < _MAX_REPORTED_UNAVAILABLE:
                    report["unavailable"].append({"episode": entry["episode_name"], "reason": entry.get("detail")})
    report["enqueued_count"] = report["queued_for_translation"] + report["queued_for_extraction"]
    return report


def capabilities() -> Dict:
    """What this Omnisub can do for on-demand requests — the client's health check."""
    from integrations import embedded_subs
    config = storage.load_global_config()
    code, name = _language(config, None)
    return {
        "sonarr": bool(config.get("sonarr_enabled") and config.get("sonarr_api_key")),
        "radarr": bool(config.get("radarr_enabled") and config.get("radarr_api_key")),
        "extraction_enabled": bool(config.get("embedded_extraction_enabled", False)),
        "ffmpeg": bool(embedded_subs.resolve_tools(config)),
        "default_language": code,
        "default_language_name": name,
        "max_episodes": int(config.get("on_demand_max_episodes", 30) or 30),
    }
