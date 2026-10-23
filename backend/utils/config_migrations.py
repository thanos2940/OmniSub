"""One-time changes to the stored settings (``config.json``).

Each migration runs once: its id is recorded in the ``config_migrations`` list of the config, and
``apply_config_migrations`` (called at startup) skips the ones already there. A migration gets the raw
stored config, changes it in place and returns a short description of what it did, "" when the config
needed nothing (recorded all the same, so it never runs again), or None when it cannot decide yet (not
recorded: it runs again at the next start).
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from utils.storage_modules import config_store

logger = logging.getLogger(__name__)

APPLIED_KEY = "config_migrations"


def _agent_engine(cfg: Dict) -> Optional[str]:
    """OmbiSub translates through an agent CLI (agy) first. An install still on the Gemini worker is
    switched to the agent engine when an agent CLI is installed; the worker pause it kept to stop
    Gemini from translating is lifted, since with the agent engine it would stop the runner too."""
    if cfg.get("translation_engine", "model") == "agent":
        return ""
    from services import agent_cli
    if not any(agent_cli.engine_command(e) for e in ("antigravity", "claude")):
        return None  # no agent CLI yet: decide again once one is installed
    cfg["translation_engine"] = "agent"
    lifted = bool(cfg.pop("queue_worker_paused", False))
    return "translation engine set to agent" + ("; translation pause lifted" if lifted else "")


def _autopilot_only(cfg: Dict) -> str:
    """The Autopilot replaced the webhook auto-translate mode. An install that had the old mode on gets
    the Autopilot instead (new imports only: no backlog); the old keys go."""
    legacy = cfg.pop("arr_auto_translate", None)
    cfg.pop("arr_non_anime_delay_minutes", None)
    if legacy in ("smart", True, "true", "True") and not cfg.get("autopilot_enabled"):
        cfg["autopilot_enabled"] = True
        cfg["autopilot_backlog_mode"] = "off"
        if not cfg.get("autopilot_enabled_at"):
            from services.autopilot.store import iso_utc
            cfg["autopilot_enabled_at"] = iso_utc(datetime.now(timezone.utc))
        return "legacy auto-translate replaced by the Autopilot (new imports only)"
    return "legacy auto-translate settings removed" if legacy is not None else ""


def _update_channel(cfg: Dict) -> str:
    """"releases" was the stored default before the main channel existed; nothing was ever released, so
    such an install could never update. "auto" follows main until the repository has release tags."""
    if cfg.get("update_channel") == "releases":
        cfg["update_channel"] = "auto"
        return "update channel set to auto"
    return ""


# The sampling values OmbiSub used to write by default (into the settings and into every new project).
_OLD_SAMPLING_DEFAULTS = {"temperature": 0.3, "top_k": 64, "top_p": 0.95}


def _google_sampling_defaults(cfg: Dict) -> str:
    """Temperature, top-k and top-p now follow Google's defaults for each model unless set by hand. The
    values OmbiSub stored only because they were its old defaults are removed, from the settings and from
    every project; any other value was chosen by someone and stays."""
    removed = [k for k, v in _OLD_SAMPLING_DEFAULTS.items() if k in cfg and cfg[k] == v]
    for k in removed:
        cfg.pop(k)
    cfg.pop("max_output_tokens", None)  # a setting nothing ever read
    from utils.storage_modules import project_store
    projects = 0
    for name in project_store.list_projects():
        meta = project_store.load_project_metadata(name) or {}
        settings = meta.get("settings")
        if not isinstance(settings, dict):
            continue
        stale = [k for k, v in _OLD_SAMPLING_DEFAULTS.items() if k in settings and settings[k] in (v, None)]
        if stale:
            for k in stale:
                settings.pop(k)
            project_store.save_project_metadata(name, meta)
            projects += 1
    if not removed and not projects:
        return ""
    return f"sampling follows Google's defaults (cleared {', '.join(removed) or 'nothing'} in settings, {projects} project(s))"


MIGRATIONS: List[Tuple[str, Callable[[Dict], Optional[str]]]] = [
    ("2026-10-19-agent-engine", _agent_engine),
    ("2026-10-19-autopilot-only", _autopilot_only),
    ("2026-10-19-update-channel", _update_channel),
    ("2026-10-23-google-sampling-defaults", _google_sampling_defaults),
]


def apply_config_migrations() -> List[str]:
    """Run the migrations this config has not had yet; returns the descriptions of what changed."""
    with config_store._config_lock:
        cfg = config_store._load_raw_config()
        applied = list(cfg.get(APPLIED_KEY) or [])
        pending = [(mid, fn) for mid, fn in MIGRATIONS if mid not in applied]
        if not pending:
            return []
        changes: List[str] = []
        for mid, fn in pending:
            try:
                note = fn(cfg)
            except Exception as e:  # a migration that cannot run now is tried again at the next start
                logger.warning(f"Config migration {mid} failed: {e}")
                continue
            if note is None:
                continue
            applied.append(mid)
            if note:
                changes.append(f"{mid}: {note}")
        if len(applied) == len(cfg.get(APPLIED_KEY) or []):
            return []  # only migrations that wait for later: nothing to write
        cfg[APPLIED_KEY] = applied
        config_store._atomic_write_json(config_store._get_config_file(), cfg)
        config_store.GLOBAL_CONFIG.clear()
        config_store.GLOBAL_CONFIG.update(cfg)
    for change in changes:
        logger.info(f"Config migration {change}")
    return changes
