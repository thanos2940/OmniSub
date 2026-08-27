"""Regenerate backend/config.example.json from the SettingsRequest schema defaults.

Run from backend/: ../.venv/Scripts/python.exe -m scripts.dump_default_config
"""
import json
from pathlib import Path

from routers.schemas import SettingsRequest

# Fields that hold credentials/secrets — never bake real-looking defaults into the
# example file, even though the schema defaults are already empty strings.
_SECRET_FIELDS = {
    "api_key_obfuscated", "sonarr_api_key", "radarr_api_key",
    "webhook_secret", "discord_webhook_url", "auth_password_hash", "api_key",
}


def main() -> None:
    defaults = SettingsRequest().model_dump(exclude_unset=False)
    for key in _SECRET_FIELDS:
        if key in defaults:
            defaults[key] = ""

    out_path = Path(__file__).resolve().parent.parent / "config.example.json"
    out_path.write_text(json.dumps(defaults, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {out_path} ({len(defaults)} keys)")


if __name__ == "__main__":
    main()
