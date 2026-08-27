"""Guard: every global-config key the backend reads must be declared in
SettingsRequest, so it has a canonical default and is reachable from the UI.

Regression guard for the class of bug where the pipeline read ``condense_enabled``
while the schema/UI only knew ``condense_violations`` — making the feature
unreachable from Settings (v2 plan, Phase 0.2).
"""

import re
from pathlib import Path

from routers.schemas import SettingsRequest

BACKEND = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["services", "utils", "routers", "adk_agents"]
SCAN_FILES = ["main.py"]

# Keys that are genuinely NOT user settings (runtime toggles, internal state,
# nested config sections, or rate-limiter bucket names).
ALLOWLIST = {
    "queue_worker_paused",   # runtime pause toggle (pause/resume endpoints)
    "api_key_obfuscated",    # internal display helper
    "rate_limits",           # nested dict section, edited via its own endpoint
    "default",               # rate-limiter bucket key, not a setting
    # Auth fields (docs/PLAN_auth_security.md) are deliberately NOT in
    # SettingsRequest: they must never be settable via the generic POST
    # /settings body (that would let an unauthenticated caller plant
    # credentials or overwrite the API key). They're written only by
    # routers/auth.py and read by its middleware in main.py.
    "auth_enabled",
    "auth_username",
    "auth_password_hash",
    "api_key",
}

KEY_RE = re.compile(r'(?:global_config|config|cfg)\.get\(\s*"([a-z][a-z0-9_]*)"')


def _iter_source_files():
    for d in SCAN_DIRS:
        yield from (BACKEND / d).rglob("*.py")
    for f in SCAN_FILES:
        yield BACKEND / f


def test_all_config_keys_declared_in_settings_schema():
    declared = set(SettingsRequest().model_dump().keys()) | ALLOWLIST
    offenders = {}
    for path in _iter_source_files():
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for key in KEY_RE.findall(text):
            if key not in declared:
                offenders.setdefault(key, []).append(path.name)

    assert not offenders, (
        "Global-config keys read in code but missing from SettingsRequest "
        "(add them to the schema or to ALLOWLIST if they are runtime-only):\n"
        + "\n".join(f"  {k}  ({', '.join(sorted(set(v)))})" for k, v in sorted(offenders.items()))
    )


def test_condense_key_is_condense_enabled():
    """The condensation pass reads condense_enabled — the schema must match."""
    fields = SettingsRequest().model_dump()
    assert "condense_enabled" in fields
    assert "condense_violations" not in fields
