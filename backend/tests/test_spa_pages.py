"""A browser loading a page of the web app gets the app, even where an API alias shares the path."""

import pytest
from fastapi.testclient import TestClient

import main

pytestmark = pytest.mark.skipif(not main._STATIC_DIR.is_dir(), reason="web UI not built (backend/static)")

BROWSER = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
AXIOS = {"Accept": "application/json, text/plain, */*"}


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.mark.parametrize("path", ["/queue", "/settings", "/project/Some Show/episode/S01E01", "/health"])
def test_browser_gets_the_app(client, path):
    resp = client.get(path, headers=BROWSER)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_api_clients_still_reach_the_queue_alias(client):
    resp = client.get("/queue", headers=AXIOS)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "items" in resp.json()
