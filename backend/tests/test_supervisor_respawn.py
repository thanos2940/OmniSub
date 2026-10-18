"""The headless supervisor (install.ps1 -Mode Service) keeps the server up: it starts it again at once after
a restart request (exit 42) and with growing waits after a crash. No real processes: Popen, the clock and
the wait are injected."""

import pytest

import tray_app
from tray_app import EXIT_RESTART, RestartPolicy, ServerSupervisor


def test_restart_request_is_answered_at_once_and_clears_the_crash_count():
    policy = RestartPolicy()
    policy.next_delay(1, uptime=2)
    policy.next_delay(1, uptime=2)
    assert policy.crashes == 2
    assert policy.next_delay(EXIT_RESTART, uptime=5) == 0.0
    assert policy.crashes == 0


def test_crash_backoff_grows_and_is_capped():
    policy = RestartPolicy()
    assert [policy.next_delay(1, uptime=3) for _ in range(5)] == [5.0, 15.0, 60.0, 60.0, 60.0]


def test_backoff_starts_over_after_a_stable_minute():
    policy = RestartPolicy(stable_after=60)
    policy.next_delay(1, uptime=3)
    policy.next_delay(1, uptime=3)
    assert policy.next_delay(1, uptime=61) == 5.0  # it ran for a minute: this is a new first crash
    assert policy.next_delay(1, uptime=3) == 15.0


def test_clean_exit_that_nobody_asked_for_is_treated_as_a_crash():
    assert RestartPolicy().next_delay(0, uptime=10) == 5.0


class FakeProc:
    _next_pid = 100

    def __init__(self, cmd, **kwargs):
        self.cmd, self.kwargs = cmd, kwargs
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self.code = None

    def poll(self):
        return self.code


class FakeEvent:
    """threading.Event stand-in: waiting does not sleep, it advances the rig's clock."""

    def __init__(self, rig):
        self.rig, self.flag = rig, False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, timeout=None):
        self.rig.waits.append(timeout)
        self.rig.now += timeout or 0
        return self.flag


class Rig:
    """A supervisor over fake processes and a fake clock."""

    def __init__(self, tmp_path):
        self.now, self.procs, self.waits = 1000.0, [], []
        self.sup = ServerSupervisor(port=2945, data_dir=tmp_path, python_exe="py", popen=self._popen,
                                    clock=lambda: self.now)
        self.sup._stop_event = FakeEvent(self)

    def _popen(self, cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        self.procs.append(proc)
        return proc

    def exit_server(self, code, after):
        self.now += after
        self.procs[-1].code = code


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_starts_the_server_under_the_supervisor_env(rig):
    rig.sup.start_server()
    proc = rig.procs[0]
    assert "run_server.py" in " ".join(proc.cmd)
    assert proc.kwargs["env"]["OMBISUB_SUPERVISOR"] == "tray"
    assert "2945" in proc.cmd


def test_nothing_happens_while_the_server_runs(rig):
    rig.sup.start_server()
    assert rig.sup.supervise_once() is False
    assert len(rig.procs) == 1 and rig.waits == []


def test_exit_42_respawns_without_waiting(rig):
    rig.sup.start_server()
    rig.exit_server(EXIT_RESTART, after=500)
    assert rig.sup.supervise_once() is True
    assert len(rig.procs) == 2
    assert rig.waits == []


def test_crash_respawns_after_backoff_and_logs_the_exit_code(rig, caplog):
    rig.sup.start_server()
    with caplog.at_level("WARNING", logger="ombisub.tray"):
        for expected in (5.0, 15.0, 60.0):
            rig.exit_server(3, after=2)
            rig.sup.supervise_once()
            assert rig.waits[-1] == expected
    assert len(rig.procs) == 4
    assert "exited with code 3" in caplog.text


def test_a_stable_server_that_later_crashes_gets_the_short_wait_again(rig):
    rig.sup.start_server()
    rig.exit_server(1, after=2)
    rig.sup.supervise_once()           # 5 s
    rig.exit_server(1, after=2)
    rig.sup.supervise_once()           # 15 s
    rig.exit_server(1, after=3600)     # then it ran for an hour
    rig.sup.supervise_once()
    assert rig.waits[-1] == 5.0


def test_no_respawn_once_stopping(rig):
    rig.sup.start_server()
    rig.exit_server(1, after=2)
    rig.sup.stopping = True
    assert rig.sup.supervise_once() is False
    assert len(rig.procs) == 1


def test_stop_during_the_backoff_wait_does_not_start_a_new_server(rig):
    rig.sup.start_server()
    rig.exit_server(1, after=2)
    plain_wait = rig.sup._stop_event.wait

    def stop_while_waiting(timeout=None):
        plain_wait(timeout)
        rig.sup._stop_event.set()
        return True
    rig.sup._stop_event.wait = stop_while_waiting

    rig.sup.supervise_once()
    assert len(rig.procs) == 1


def test_restart_server_does_not_deadlock(tmp_path, monkeypatch):
    """restart_server holds the lock and calls start_server, which takes it again."""
    monkeypatch.setattr(tray_app.time, "sleep", lambda s: None)
    sup = ServerSupervisor(port=1, data_dir=tmp_path, python_exe="py", popen=FakeProc)
    sup.start_server()
    sup.proc.code = 0
    sup.restart_server()
    assert sup.proc.code is None
