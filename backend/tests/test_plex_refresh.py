"""Plex after every export: the debounced refresher, grouped scans and the connection test.

Offline: httpx MockTransport for Plex, fake clients for the refresher.
"""

import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from main import app
from services import plex_refresh
from services.autopilot import plex as plex_module
from services.autopilot.plex import PlexClient, check_connection
from services.plex_refresh import PlexRefresher
from services.translation import export_formatter
from utils import storage

SECTIONS = {"MediaContainer": {"Directory": [
    {"key": "1", "title": "TV", "type": "show", "Location": [{"path": "D:\\Media\\TV"}]},
    {"key": "3", "title": "Movies", "type": "movie", "Location": [{"path": "D:\\Media\\Movies"}]},
]}}
IDENTITY = {"MediaContainer": {"friendlyName": "Morpiceserver", "version": "1.41.0.8992"}}


def plex_transport(requests, status=200):
    def handler(request: httpx.Request):
        requests.append(request)
        if status != 200:
            return httpx.Response(status)
        if request.url.path == "/":
            return httpx.Response(200, json=IDENTITY)
        if request.url.path == "/library/sections":
            return httpx.Response(200, json=SECTIONS)
        if request.url.path.endswith("/refresh"):
            return httpx.Response(200)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def refreshes(requests):
    return [r for r in requests if r.url.path.endswith("/refresh")]


# --- grouped scans ----------------------------------------------------------------------------

class TestRefreshPaths:
    def client(self, requests):
        return PlexClient("http://plex:32400", "T", transport=plex_transport(requests))

    def test_one_folder_is_scanned_by_itself(self):
        reqs = []
        assert self.client(reqs).refresh_paths(["D:\\Media\\TV\\Show\\S01\\Show.S01E01.el.srt"]) is True
        (scan,) = refreshes(reqs)
        assert scan.url.path == "/library/sections/1/refresh"
        assert scan.url.params["path"] == "D:\\Media\\TV\\Show\\S01"

    def test_many_folders_of_a_section_are_one_scan_of_their_common_parent(self):
        reqs = []
        assert self.client(reqs).refresh_paths([
            "D:\\Media\\TV\\Show\\S01\\a.el.srt", "D:\\Media\\TV\\Show\\S01\\b.el.srt",
            "D:\\Media\\TV\\Show\\S02\\c.el.srt"]) is True
        (scan,) = refreshes(reqs)
        assert scan.url.params["path"] == "D:\\Media\\TV\\Show"
        assert len([r for r in reqs if r.url.path == "/library/sections"]) == 1  # sections asked once

    def test_folders_of_different_shows_scan_the_whole_section(self):
        reqs = []
        assert self.client(reqs).refresh_paths([
            "D:\\Media\\TV\\A\\S01\\a.el.srt", "D:\\Media\\TV\\B\\S01\\b.el.srt"]) is True
        (scan,) = refreshes(reqs)
        assert scan.url.path == "/library/sections/1/refresh" and "path" not in scan.url.params

    def test_each_section_gets_its_own_scan(self):
        reqs = []
        assert self.client(reqs).refresh_paths([
            "D:\\Media\\TV\\A\\S01\\a.el.srt", "D:\\Media\\Movies\\Film (2020)\\Film.el.srt"]) is True
        assert sorted(r.url.path for r in refreshes(reqs)) == [
            "/library/sections/1/refresh", "/library/sections/3/refresh"]

    def test_a_folder_in_no_library_is_reported_not_raised(self):
        client = self.client([])
        assert client.refresh_paths(["C:\\Elsewhere\\a.el.srt"]) is False
        assert "No Plex library" in client.last_error

    def test_plex_errors_return_false(self):
        client = PlexClient("http://plex", "T", transport=plex_transport([], status=500))
        assert client.refresh_paths(["D:\\Media\\TV\\a.el.srt"]) is False
        assert "500" in client.last_error


# --- the debounced refresher -------------------------------------------------------------------

class FakeClient:
    def __init__(self, ok=True, raises=None):
        self.batches = []
        self.ok = ok
        self.raises = raises
        self.last_error = None if ok else "Plex answered HTTP 500"

    def refresh_paths(self, paths):
        if self.raises:
            raise self.raises
        self.batches.append(list(paths))
        return self.ok


class TestRefresher:
    def test_requests_are_collected_and_sent_as_one_batch(self):
        client = FakeClient()
        refresher = PlexRefresher(delay=3600, client_factory=lambda cfg: client)
        for name in ("a", "b", "a"):
            assert refresher.request(f"/tv/Show/{name}.el.srt") is True
        assert client.batches == []  # nothing yet: the window is open
        refresher.flush()
        assert client.batches == [["/tv/Show/a.el.srt", "/tv/Show/b.el.srt"]]
        refresher.flush()
        assert len(client.batches) == 1  # nothing pending: no second call
        assert refresher.state == {"ok": True, "error": None}

    def test_the_window_closes_on_its_own_thread(self):
        done = threading.Event()
        batches = []

        class Client(FakeClient):
            def refresh_paths(self, paths):
                batches.append(list(paths))
                done.set()
                return True

        refresher = PlexRefresher(delay=0.05, client_factory=lambda cfg: Client())
        refresher.request("/tv/a.el.srt")
        refresher.request("/tv/b.el.srt")
        assert done.wait(5)
        assert batches == [["/tv/a.el.srt", "/tv/b.el.srt"]]

    def test_requests_after_a_flush_start_a_new_window(self):
        client = FakeClient()
        refresher = PlexRefresher(delay=3600, client_factory=lambda cfg: client)
        refresher.request("/tv/a.el.srt")
        refresher.flush()
        refresher.request("/tv/b.el.srt")
        refresher.flush()
        assert client.batches == [["/tv/a.el.srt"], ["/tv/b.el.srt"]]

    def test_nothing_is_queued_while_plex_is_not_configured(self):
        refresher = PlexRefresher(delay=3600, client_factory=lambda cfg: None)
        assert refresher.request("/tv/a.el.srt") is False
        assert refresher.state == {"ok": None, "error": None}

    def test_the_default_client_needs_the_setting_and_a_token(self):
        cfg = {}
        refresher = PlexRefresher(delay=3600, config_provider=lambda: cfg)
        assert refresher.request("/tv/a.el.srt") is False
        cfg.update({"plex_refresh_enabled": True, "plex_token": "tok"})
        assert refresher.request("/tv/a.el.srt") is True

    def test_a_failing_plex_is_recorded_not_raised(self):
        refresher = PlexRefresher(delay=0, client_factory=lambda cfg: FakeClient(ok=False))
        assert refresher.request("/tv/a.el.srt") is True
        assert refresher.state == {"ok": False, "error": "Plex answered HTTP 500"}

    def test_an_exception_in_the_client_is_swallowed(self):
        refresher = PlexRefresher(delay=0, client_factory=lambda cfg: FakeClient(raises=RuntimeError("boom")))
        assert refresher.request("/tv/a.el.srt") is True
        assert refresher.state == {"ok": False, "error": "boom"}

    def test_a_broken_configuration_never_reaches_the_caller(self):
        def broken(cfg):
            raise RuntimeError("config unreadable")

        refresher = PlexRefresher(delay=0, client_factory=broken)
        assert refresher.request("/tv/a.el.srt") is False


# --- the export hook -----------------------------------------------------------------------------

def test_every_registered_export_asks_for_a_refresh(tmp_path, monkeypatch):
    client = FakeClient()
    plex_refresh.set_refresher(PlexRefresher(delay=3600, client_factory=lambda cfg: client))
    monkeypatch.setattr("services.autopilot.ownership.register_file", lambda *a, **k: None)
    export_formatter._register_written(tmp_path / "Show.S01E01.el.srt", "Show", "S01E01")
    export_formatter._register_written(tmp_path / "Show.S01E01.el.ass", "Show", "S01E01")
    plex_refresh.get_refresher().flush()
    assert [Path(p).name for p in client.batches[0]] == ["Show.S01E01.el.srt", "Show.S01E01.el.ass"]


def test_an_export_survives_a_plex_that_cannot_be_reached(tmp_path, monkeypatch):
    plex_refresh.set_refresher(PlexRefresher(delay=0, client_factory=lambda cfg: FakeClient(raises=OSError("down"))))
    monkeypatch.setattr("services.autopilot.ownership.register_file", lambda *a, **k: None)
    export_formatter._register_written(tmp_path / "Show.S01E01.el.srt", "Show", "S01E01")  # does not raise


# --- the connection test ---------------------------------------------------------------------------

@pytest.fixture
def plex_http(monkeypatch):
    """Route every PlexClient through a mock transport; the test sets ``.status``."""
    state = type("State", (), {"requests": [], "status": 200})()
    real = plex_module.PlexClient
    monkeypatch.setattr(
        plex_module, "PlexClient",
        lambda url, token, **kw: real(url, token, transport=plex_transport(state.requests, state.status)))
    return state


class TestConnectionTest:
    def test_lists_the_server_and_its_libraries(self, plex_http):
        res = check_connection("http://plex:32400", "tok")
        assert res["connected"] is True and res["error"] is None
        assert (res["name"], res["version"]) == ("Morpiceserver", "1.41.0.8992")
        assert [s["title"] for s in res["sections"]] == ["TV", "Movies"]
        assert plex_http.requests[0].headers["X-Plex-Token"] == "tok"

    def test_a_wrong_token_is_named(self, plex_http):
        plex_http.status = 401
        res = check_connection("http://plex:32400", "bad")
        assert res["connected"] is False and res["error"] == "Invalid Plex token"

    def test_an_unreachable_server_reports_the_error(self, monkeypatch):
        def boom(request):
            raise httpx.ConnectError("refused")

        real = plex_module.PlexClient
        monkeypatch.setattr(plex_module, "PlexClient",
                            lambda url, token, **kw: real(url, token, transport=httpx.MockTransport(boom)))
        res = check_connection("http://plex:32400", "tok")
        assert res["connected"] is False and "refused" in res["error"]

    def test_no_token_means_no_request(self, plex_http):
        res = check_connection("http://plex:32400", "")
        assert res["connected"] is False and "token" in res["error"] and plex_http.requests == []


class TestPlexTestEndpoint:
    def test_uses_the_values_from_the_body(self, plex_http):
        res = TestClient(app).post("/integrations/plex/test", json={"url": "http://other:32400", "token": "typed"})
        body = res.json()
        assert res.status_code == 200 and body["success"] is True and body["connected"] is True
        assert body["name"] == "Morpiceserver" and len(body["sections"]) == 2
        assert plex_http.requests[0].headers["X-Plex-Token"] == "typed"

    def test_falls_back_to_the_saved_settings(self, plex_http):
        storage.save_global_config({"plex_url": "http://saved:32400", "plex_token": "saved-token"})
        body = TestClient(app).post("/integrations/plex/test").json()
        assert body["success"] is True
        assert plex_http.requests[0].headers["X-Plex-Token"] == "saved-token"

    def test_the_masked_token_of_the_form_means_the_saved_one(self, plex_http):
        storage.save_global_config({"plex_token": "saved-token"})
        body = TestClient(app).post(
            "/integrations/plex/test", json={"url": "http://x:32400", "token": "__SECRET_UNCHANGED__"}).json()
        assert body["success"] is True
        assert plex_http.requests[0].headers["X-Plex-Token"] == "saved-token"

    def test_failure_is_reported_in_the_body(self, plex_http):
        plex_http.status = 401
        body = TestClient(app).post("/integrations/plex/test", json={"token": "bad"}).json()
        assert body["success"] is False and body["error"] == "Invalid Plex token"
