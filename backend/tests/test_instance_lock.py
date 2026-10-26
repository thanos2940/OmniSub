"""
One OmbiSub process per data folder
===================================
The instance lock is an OS lock held for the life of the process: taking it again in the same process
is fine, another process is refused while it is held and gets it once it is released, and the app's
startup refuses to run a second server on the same data folder (2026-10-27: two servers ran two
translation runners on one queue and submitted episodes twice).
"""
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils import instance_lock

BACKEND = Path(__file__).resolve().parent.parent


def _holder(data_dir: Path) -> subprocess.Popen:
    """Another process that takes the lock on ``data_dir`` and keeps it until killed."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(BACKEND)!r})
        from utils import instance_lock
        ok = instance_lock.acquire(__import__('pathlib').Path({str(data_dir)!r}), port=4321)
        print('locked' if ok else 'refused', flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "locked"
    return proc


def _try_in_other_process(data_dir: Path) -> str:
    code = (f"import sys; sys.path.insert(0, {str(BACKEND)!r}); from pathlib import Path; "
            f"from utils import instance_lock; "
            f"print('locked' if instance_lock.acquire(Path({str(data_dir)!r})) else 'refused')")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60).stdout.strip()


def test_the_lock_is_per_process(tmp_path):
    assert instance_lock.acquire(tmp_path, port=8000)
    assert instance_lock.acquire(tmp_path)                 # again in the same process: fine
    assert _try_in_other_process(tmp_path) == "refused"
    assert instance_lock.holder(tmp_path)["pid"] == os.getpid()
    assert "port 8000" in instance_lock.describe_holder(tmp_path)
    instance_lock.release(tmp_path)
    assert _try_in_other_process(tmp_path) == "locked"     # that process exited, so the lock is free again


def test_a_holder_that_dies_frees_the_lock(tmp_path):
    proc = _holder(tmp_path)
    try:
        assert not instance_lock.acquire(tmp_path)
    finally:
        proc.kill()
        proc.wait()
    deadline = time.time() + 10
    while not instance_lock.acquire(tmp_path) and time.time() < deadline:
        time.sleep(0.1)
    assert instance_lock.acquire(tmp_path)
    instance_lock.release(tmp_path)


def test_a_second_server_on_the_same_data_folder_does_not_start():
    import asyncio
    from main import app, lifespan
    from utils.paths import DATA_DIR

    async def start():
        async with lifespan(app):
            pass

    instance_lock.release(DATA_DIR)          # this test process took it in earlier app startups
    proc = _holder(DATA_DIR)
    try:
        # What uvicorn runs at startup: the lifespan refuses before it starts anything.
        with pytest.raises(SystemExit) as caught:
            asyncio.run(start())
        assert caught.value.code == 3
    finally:
        proc.kill()
        proc.wait()
    deadline = time.time() + 10
    while not instance_lock.acquire(DATA_DIR) and time.time() < deadline:
        time.sleep(0.1)
    with TestClient(app) as client:          # and with the other gone, it starts
        assert client.get("/api/health").status_code == 200


def test_pid_alive_tells_a_dead_process_from_a_live_one():
    import run_server
    from services import agent_runner
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    for alive in (run_server._pid_alive, agent_runner._pid_alive):
        assert alive(os.getpid())
        assert alive(os.getppid())
        assert not alive(proc.pid)
