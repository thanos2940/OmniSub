"""Omnisub was called OmbiSub: what an install of that time left behind keeps working after the rename."""
import os

from utils import instance_lock
from utils.env_names import env, set_env


def test_an_old_server_and_a_new_one_never_share_a_data_folder(tmp_path):
    (tmp_path / "ombisub_instance.lock").touch()          # the folder had an old version
    assert instance_lock.acquire(tmp_path)
    try:
        import subprocess, sys
        # Another process taking the legacy lock (as the old version does) is refused while this one runs.
        code = ("import fcntl, os, sys; fd = os.open(sys.argv[1], os.O_RDWR); "
                "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)")
        if os.name != "nt":
            r = subprocess.run([sys.executable, "-c", code, str(tmp_path / "ombisub_instance.lock")])
            assert r.returncode != 0
    finally:
        instance_lock.release(tmp_path)


def test_a_new_install_gets_no_file_with_the_old_name(tmp_path):
    assert instance_lock.acquire(tmp_path)
    instance_lock.release(tmp_path)
    assert not (tmp_path / "ombisub_instance.lock").exists()
    assert (tmp_path / "omnisub_instance.lock").exists()


def test_old_environment_names_are_still_read(monkeypatch):
    monkeypatch.delenv("OMNISUB_SUPERVISOR", raising=False)
    monkeypatch.setenv("OMBISUB_SUPERVISOR", "tray")     # set by a supervisor started before the rename
    assert env("SUPERVISOR") == "tray"
    monkeypatch.setenv("OMNISUB_SUPERVISOR", "none")
    assert env("SUPERVISOR") == "none"


def test_set_env_writes_both_spellings(monkeypatch):
    monkeypatch.delenv("OMNISUB_PORT", raising=False)
    monkeypatch.delenv("OMBISUB_PORT", raising=False)
    set_env("PORT", "2945")
    assert os.environ["OMNISUB_PORT"] == os.environ["OMBISUB_PORT"] == "2945"
    monkeypatch.delenv("OMNISUB_PORT")
    monkeypatch.delenv("OMBISUB_PORT")


def test_series_tagged_with_the_old_skip_tag_stay_skipped():
    from services.autopilot.config import _with_legacy_tags
    assert "ombisub-skip" in _with_legacy_tags(["omnisub-skip", "no-greek"])
    assert _with_legacy_tags(["mine"]) == ["mine"]


def test_the_web_app_finds_its_base_under_either_name():
    from utils.spa import inject_base
    out = inject_base("<html><head></head><body></body></html>", "/omnisub")
    assert 'window.__OMNISUB_BASE__ = "/omnisub";' in out and "window.__OMBISUB_BASE__" in out
