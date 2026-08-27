import pytest
from fastapi.testclient import TestClient

from main import app
from utils import storage


@pytest.fixture
def temp_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)
    return projects_dir


@pytest.fixture
def client(temp_projects_dir):
    return TestClient(app)


@pytest.fixture
def episode(temp_projects_dir):
    storage.create_project("TP")
    storage.save_episode("TP", "E01", [{"index": 0, "original": "Hi", "translated": "Hi"}])


def test_first_get_returns_etag(client, episode):
    resp = client.get("/projects/TP/episodes/E01")
    assert resp.status_code == 200
    assert resp.headers.get("etag")


def test_second_get_with_matching_etag_returns_304(client, episode):
    first = client.get("/projects/TP/episodes/E01")
    etag = first.headers["etag"]

    second = client.get("/projects/TP/episodes/E01", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert not second.content


def test_stale_etag_returns_fresh_data(client, episode):
    resp = client.get("/projects/TP/episodes/E01", headers={"If-None-Match": 'W/"stale-0"'})
    assert resp.status_code == 200
    assert resp.json()["data"][0]["original"] == "Hi"


def test_etag_changes_after_save(client, episode):
    first = client.get("/projects/TP/episodes/E01")
    etag = first.headers["etag"]

    storage.save_episode("TP", "E01", [{"index": 0, "original": "Hi", "translated": "Hello"}])

    resp = client.get("/projects/TP/episodes/E01", headers={"If-None-Match": etag})
    assert resp.status_code == 200
    assert resp.json()["data"][0]["translated"] == "Hello"
    assert resp.headers["etag"] != etag


def test_missing_episode_still_404s(client, temp_projects_dir):
    storage.create_project("TP")
    resp = client.get("/projects/TP/episodes/DoesNotExist")
    assert resp.status_code == 404
