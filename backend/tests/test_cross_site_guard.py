"""
A web page on another site must not drive Omnisub through the user's browser (CSRF), and CORS is not a
wildcard by default: the built UI is same-origin, the Vite dev server is the only other origin let in.
Server-to-server callers (Sonarr/Radarr webhooks, MorpiceHub, curl) send no Fetch Metadata and pass.
"""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_a_cross_site_write_is_refused():
    r = client.post("/api/queue/cancel-all", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_same_origin_and_same_site_writes_pass_the_guard():
    for site in ("same-origin", "same-site", "none"):
        r = client.post("/api/settings", json={"setup_completed": True}, headers={"Sec-Fetch-Site": site})
        assert r.status_code != 403, site


def test_a_server_to_server_write_passes_the_guard():
    r = client.post("/api/settings", json={"setup_completed": True})   # no Sec-Fetch-Site at all
    assert r.status_code != 403


def test_reads_are_not_refused():
    r = client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"})
    assert r.status_code == 200


def test_cors_is_not_a_wildcard():
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") is None
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_the_dev_server_may_still_write():
    r = client.post("/api/settings", json={"setup_completed": True},
                    headers={"Sec-Fetch-Site": "cross-site", "Origin": "http://localhost:5173"})
    assert r.status_code != 403


def test_another_site_cannot_open_the_event_feed():
    import pytest
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws", headers={"Sec-Fetch-Site": "cross-site",
                                                          "Origin": "https://evil.example"}) as ws:
            ws.send_text("ping")
            ws.receive_text()
    with client.websocket_connect("/api/ws", headers={"Sec-Fetch-Site": "same-origin"}) as ws:
        ws.send_text("ping")
        assert ws.receive_text() == "pong"
