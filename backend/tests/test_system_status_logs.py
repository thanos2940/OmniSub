"""GET /api/system/status and the log viewer (/api/system/logs, /api/system/logs/list)."""

import os

import pytest
from fastapi.testclient import TestClient

from main import app
from services import system_info
from utils import storage
from utils.auth import generate_api_key


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setattr(system_info, "DATA_DIR", tmp_path)
    (tmp_path / "logs").mkdir()
    return tmp_path / "logs"


@pytest.fixture
def client():
    return TestClient(app)


# ------------------------------------------------------------------ tail_lines

def test_tail_returns_the_last_lines_in_order(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("".join(f"line {i}\n" for i in range(1, 101)), encoding="utf-8")
    assert system_info.tail_lines(f, 3) == ["line 98", "line 99", "line 100"]


def test_tail_of_a_short_file_returns_everything(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("one\ntwo", encoding="utf-8")  # no trailing newline
    assert system_info.tail_lines(f, 50) == ["one", "two"]


def test_tail_across_read_blocks_loses_and_cuts_no_line(tmp_path):
    f = tmp_path / "big.log"
    lines = [f"{i:06d} " + "x" * (i % 97) for i in range(6000)]  # ~300 KB: several 64 KB blocks
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert system_info.tail_lines(f, 4000) == lines[-4000:]
    assert system_info.tail_lines(f, 1) == lines[-1:]


def test_tail_handles_crlf_empty_file_and_bad_utf8(tmp_path):
    crlf = tmp_path / "c.log"
    crlf.write_bytes(b"a\r\nb\r\nc\r\n")
    assert system_info.tail_lines(crlf, 2) == ["b", "c"]
    empty = tmp_path / "e.log"
    empty.write_bytes(b"")
    assert system_info.tail_lines(empty, 5) == []
    bad = tmp_path / "b.log"
    bad.write_bytes(b"ok \xff\xfe broken\nnext\n")
    assert system_info.tail_lines(bad, 2) == ["ok \ufffd\ufffd broken", "next"]


def test_tail_of_a_log_without_line_breaks_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(system_info, "_MAX_TAIL_BYTES", 128 * 1024)
    f = tmp_path / "one_line.log"
    f.write_bytes(b"y" * (2 * 1024 * 1024))
    lines = system_info.tail_lines(f, 10)
    assert sum(len(x) for x in lines) <= 128 * 1024 + 64 * 1024


# ------------------------------------------------------------------ the endpoints

def test_log_endpoint_returns_name_path_lines_size_modified(client, logs):
    (logs / "ombisub.log").write_text("".join(f"row {i}\n" for i in range(10)), encoding="utf-8")
    body = client.get("/api/system/logs", params={"name": "ombisub", "lines": 3}).json()
    assert body["name"] == "ombisub"
    assert body["lines"] == ["row 7", "row 8", "row 9"]
    assert body["size"] == (logs / "ombisub.log").stat().st_size
    assert body["path"].endswith("ombisub.log") and body["modified"]


def test_log_endpoint_defaults_to_ombisub_and_500_lines(client, logs):
    (logs / "ombisub.log").write_text("".join(f"{i}\n" for i in range(800)), encoding="utf-8")
    assert len(client.get("/api/system/logs").json()["lines"]) == 500


def test_line_count_is_clamped(client, logs):
    (logs / "ombisub.log").write_text("".join(f"{i}\n" for i in range(6000)), encoding="utf-8")
    assert len(client.get("/api/system/logs", params={"lines": 100000}).json()["lines"]) == 5000
    assert len(client.get("/api/system/logs", params={"lines": 0}).json()["lines"]) == 1
    assert client.get("/api/system/logs", params={"lines": "many"}).status_code == 422


def test_missing_log_is_a_404_not_a_500(client, logs):
    resp = client.get("/api/system/logs", params={"name": "tray_app"})
    assert resp.status_code == 404
    assert "not been written" in resp.json()["detail"]


@pytest.mark.parametrize("name", [
    "../config", "..\\config", "config.json", "ombisub.log", "/etc/passwd", "C:\\Windows\\win.ini",
    "ombisub/../../config", "%2e%2e%2fconfig", "", "OMBISUB",
])
def test_only_whitelisted_names_are_readable(client, logs, tmp_path, name):
    (tmp_path / "config.json").write_text('{"api_key": "secret"}')
    (logs / "ombisub.log").write_text("fine\n")
    resp = client.get("/api/system/logs", params={"name": name})
    assert resp.status_code == 404
    assert "secret" not in resp.text


def test_list_shows_only_existing_whitelisted_logs(client, logs):
    (logs / "ombisub.log").write_text("x")
    (logs / "service_console.log").write_text("yy")
    (logs / "other.log").write_text("not listed")
    listed = client.get("/api/system/logs/list").json()["logs"]
    assert {entry["name"] for entry in listed} == {"ombisub", "service_console"}
    sizes = {entry["name"]: entry["size"] for entry in listed}
    assert sizes == {"ombisub": 1, "service_console": 2}


def test_logs_and_status_sit_behind_the_api_key_gate(client, logs, tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CONFIG_FILE", tmp_path / "config.json")
    key = generate_api_key()
    storage.save_global_config({"auth_enabled": True, "api_key": key})
    for path in ("/api/system/status", "/api/system/logs", "/api/system/logs/list"):
        assert client.get(path).status_code == 401
    ok = client.get("/api/system/logs/list", headers={"X-Api-Key": key})
    assert ok.status_code == 200


# ------------------------------------------------------------------ /api/system/status

def test_status_reports_the_runtime(client, logs, tmp_path, monkeypatch):
    monkeypatch.setenv("OMBISUB_PORT", "2945")
    monkeypatch.setenv("OMBISUB_SUPERVISOR", "tray")
    monkeypatch.setattr(storage, "load_global_config", lambda: {"url_base": "ombisub/"})
    (logs / "ombisub.log").write_text("x")

    body = client.get("/api/system/status").json()

    assert body["port"] == "2945"
    assert body["supervisor"] == "tray"
    assert body["url_base"] == "/ombisub"
    assert body["data_dir"] == str(system_info.DATA_DIR)
    assert body["python"]["version"].count(".") >= 1
    assert body["uptime_seconds"] >= 0 and body["started_at"]
    assert set(body["agent_clis"]) == {"agy", "claude"}
    assert "free_bytes" in body["disk"]
    assert [entry["name"] for entry in body["logs"]] == ["ombisub"]
    assert "agent_runner" in body and "ffmpeg" in body and "ffprobe" in body
    assert body["commit"] is not None and "branch" in body


def test_status_without_supervisor_says_none(client, monkeypatch):
    monkeypatch.delenv("OMBISUB_SUPERVISOR", raising=False)
    assert client.get("/api/system/status").json()["supervisor"] == "none"


def test_status_survives_a_broken_runner(client, monkeypatch):
    import services.agent_runner as agent_runner

    def boom():
        raise RuntimeError("runner exploded")

    monkeypatch.setattr(agent_runner, "get_runner", boom)
    body = client.get("/api/system/status").json()
    assert body["agent_runner"] == {"error": "runner exploded"}
