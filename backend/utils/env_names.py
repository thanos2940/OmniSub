"""Omnisub's environment variables: ``OMNISUB_<NAME>``, with the ``OMBISUB_<NAME>`` spelling of the versions before
the rename still read (a supervisor started by an older version keeps setting it until it is restarted)."""
import os
from typing import Optional


def env(name: str) -> Optional[str]:
    return os.environ.get(f"OMNISUB_{name}") or os.environ.get(f"OMBISUB_{name}") or None


def set_env(name: str, value: str) -> None:
    """Set both spellings, for child processes of either version."""
    os.environ[f"OMNISUB_{name}"] = value
    os.environ[f"OMBISUB_{name}"] = value
