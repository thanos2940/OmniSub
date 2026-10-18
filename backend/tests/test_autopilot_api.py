"""Autopilot HTTP API (routers/autopilot.py) and the settings changes that belong to it."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from main import app
from routers.settings import SECRET_SENTINEL
from services.autopilot import service as service_module
from services.autopilot.service import AutopilotService
from services.autopilot.store import AutopilotStore, iso_utc, parse_iso
from utils import storage

NOW = datetime.now(timezone.utc)


class StubWatcher:
    def __init__(self):
        self.store = None
        self.cancelled = []
        self.poll = AsyncMock(return_value={"sonarr": {"ok": True, "created": 1}})
        self.reconcile = AsyncMock(return_value={"created": 2, "requeued": 0, "backlog": 0})

    async def poll_once(self):
        return await self.poll()

    async def reconcile_once(self):
        return await self.reconcile()

    def health(self):
        return {"sonarr": {"configured": False, "ok": None, "since": None, "error": None, "cursor": None},
                "radarr": {"configured": False, "ok": None, "since": None, "error": None, "cursor": None}}

    def _cancel_queue_rows(self, item):
        self.cancelled.append(item["id"])


@pytest.fixture
def api(tmp_path):
    service_module.reset_service()
    store = AutopilotStore(tmp_path / "api.db")
    watcher = StubWatcher()
    svc = AutopilotService(store=store, watcher=watcher)
    service_module._service = svc
    storage.save_global_config({"autopilot_enabled": True, "autopilot_enabled_at": iso_utc(NOW)})

    class Api:
        pass

    a = Api()
    a.client, a.store, a.watcher, a.svc, a.n = TestClient(app), store, watcher, svc, 0

    def make(state="waiting", due_in=300, **fields):
        a.n += 1
        item, _ = store.upsert_detection(
            source="sonarr", kind="episode", arr_item_id=a.n, arr_parent_id=1, arr_file_id=500 + a.n,
            media_path=f"/tv/Show.S01E{a.n:02d}.mkv", project_name="Show", episode_name=f"S01E{a.n:02d}",
            title=fields.pop("title", "Show"), season=1, episode=a.n,
            detected_at=NOW + timedelta(seconds=due_in - 300), grace_seconds=300)
        if state != "waiting":
            item = store.transition(item["id"], state, **fields)
        return item

    a.make = make
    yield a
    service_module.reset_service()
    store.close()


ITEM_KEYS = {"id", "source", "kind", "arr_item_id", "arr_parent_id", "arr_file_id", "project_name", "episode_name",
             "title", "season", "episode", "media_path", "state", "reason", "detail", "detected_at", "due_at",
             "updated_at", "attempts", "next_attempt_at", "source_kind", "routed_at", "outputs", "events"}


# --- status ----------------------------------------------------------------------------------

def test_status(api):
    storage.save_global_config({"plex_refresh_enabled": True, "plex_token": "tok-123"})
    api.make()
    r = api.client.get("/api/autopilot/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True and body["running"] is False
    assert body["counts"]["waiting"] == 1 and len(body["next_due"]) == 1
    assert body["settings"]["plex_token"] == "***" and "tok-123" not in r.text
    assert set(body) == {"enabled", "running", "settings", "loops", "arr", "engine", "plex", "counts", "next_due"}


# --- items -----------------------------------------------------------------------------------

def test_items_default_order_active_first_then_recent(api):
    late = api.make(due_in=900, title="late")
    soon = api.make(due_in=60, title="soon")
    checking = api.make(state="checking")
    extracting = api.make(state="extracting")
    translating = api.make(state="translating")
    done_old = api.make(state="done")
    failed = api.make(state="failed", detail="x")
    done_new = api.make(state="done")
    api.store._conn.execute("UPDATE items SET updated_at = ? WHERE id = ?",
                            (iso_utc(NOW - timedelta(hours=5)), done_old["id"]))
    api.store._conn.execute("UPDATE items SET updated_at = ? WHERE id = ?",
                            (iso_utc(NOW - timedelta(hours=1)), failed["id"]))
    api.store._conn.execute("UPDATE items SET updated_at = ? WHERE id = ?",
                            (iso_utc(NOW - timedelta(minutes=1)), done_new["id"]))

    body = api.client.get("/api/autopilot/items").json()
    assert body["total"] == 8
    assert [i["id"] for i in body["items"]] == [
        soon["id"], late["id"], checking["id"], extracting["id"], translating["id"],
        done_new["id"], failed["id"], done_old["id"]]
    assert set(body["items"][0]) == ITEM_KEYS

    page1 = api.client.get("/api/autopilot/items?limit=4&offset=0").json()
    page2 = api.client.get("/api/autopilot/items?limit=4&offset=4").json()
    page3 = api.client.get("/api/autopilot/items?limit=4&offset=8").json()
    assert page1["total"] == page2["total"] == 8
    assert [i["id"] for i in page1["items"] + page2["items"]] == [i["id"] for i in body["items"]]
    assert page3["items"] == []


def test_items_filters(api):
    api.make(title="Severance")
    api.make(title="Silo", state="done")
    api.make(title="Silo", state="failed", detail="x")
    only_done = api.client.get("/api/autopilot/items?state=done").json()
    assert only_done["total"] == 1 and only_done["items"][0]["state"] == "done"
    both = api.client.get("/api/autopilot/items?state=done,failed").json()
    assert both["total"] == 2
    found = api.client.get("/api/autopilot/items?q=Severance").json()
    assert found["total"] == 1 and found["items"][0]["title"] == "Severance"
    assert api.client.get("/api/autopilot/items?q=Silo").json()["total"] == 2
    assert api.client.get("/api/autopilot/items?state=bogus").status_code == 400
    assert api.client.get("/api/autopilot/items?limit=0").status_code == 422


def test_get_item(api):
    item = api.make()
    r = api.client.get(f"/api/autopilot/items/{item['id']}")
    assert r.status_code == 200 and r.json()["id"] == item["id"] and r.json()["events"][0]["state"] == "waiting"
    assert api.client.get("/api/autopilot/items/nope").status_code == 404


# --- actions ---------------------------------------------------------------------------------

@pytest.mark.parametrize("state", ["failed", "no_source", "skipped", "has_greek"])
def test_retry_puts_the_item_back_to_waiting_due_now(api, state):
    item = api.make(state=state, attempts=3, next_attempt_at=NOW + timedelta(hours=2), reason="x", detail="y")
    r = api.client.post(f"/api/autopilot/items/{item['id']}/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "waiting" and body["attempts"] == 0 and body["next_attempt_at"] is None
    assert parse_iso(body["due_at"]) <= datetime.now(timezone.utc) + timedelta(seconds=2)
    assert api.store.get(item["id"])["state"] == "waiting"
    assert [i["id"] for i in api.store.due_items(datetime.now(timezone.utc) + timedelta(seconds=1))] == [item["id"]]


@pytest.mark.parametrize("state", ["waiting", "checking", "extracting", "translating", "done", "superseded", "gone"])
def test_retry_conflicts(api, state):
    item = api.make(state=state)
    assert api.client.post(f"/api/autopilot/items/{item['id']}/retry").status_code == 409
    assert api.store.get(item["id"])["state"] == state


@pytest.mark.parametrize("state", ["waiting", "checking", "extracting", "translating"])
def test_skip_active_items(api, state):
    item = api.make(state=state)
    r = api.client.post(f"/api/autopilot/items/{item['id']}/skip")
    assert r.status_code == 200 and r.json()["state"] == "skipped" and r.json()["reason"] == "manual"
    assert (item["id"] in api.watcher.cancelled) == (state in ("extracting", "translating"))


@pytest.mark.parametrize("state", ["done", "failed", "has_greek", "skipped", "gone"])
def test_skip_conflicts(api, state):
    item = api.make(state=state)
    assert api.client.post(f"/api/autopilot/items/{item['id']}/skip").status_code == 409


def test_now_makes_a_waiting_item_due(api):
    item = api.make(due_in=3600)
    assert api.store.due_items(datetime.now(timezone.utc)) == []
    r = api.client.post(f"/api/autopilot/items/{item['id']}/now")
    assert r.status_code == 200 and r.json()["state"] == "waiting"
    assert [i["id"] for i in api.store.due_items(datetime.now(timezone.utc) + timedelta(seconds=1))] == [item["id"]]


@pytest.mark.parametrize("state", ["checking", "translating", "done", "failed"])
def test_now_conflicts(api, state):
    item = api.make(state=state)
    assert api.client.post(f"/api/autopilot/items/{item['id']}/now").status_code == 409


@pytest.mark.parametrize("action", ["retry", "skip", "now"])
def test_actions_on_unknown_items_are_404(api, action):
    assert api.client.post(f"/api/autopilot/items/missing/{action}").status_code == 404


def test_poll_and_reconcile(api):
    r = api.client.post("/api/autopilot/poll")
    assert r.status_code == 200 and r.json() == {"success": True, "summary": {"sonarr": {"ok": True, "created": 1}}}
    r = api.client.post("/api/autopilot/reconcile")
    assert r.json() == {"success": True, "summary": {"created": 2, "requeued": 0, "backlog": 0}}
    api.watcher.poll.side_effect = RuntimeError("sonarr down")
    r = api.client.post("/api/autopilot/poll")
    assert r.status_code == 200 and r.json()["success"] is False and "sonarr down" in r.json()["summary"]["error"]


def test_the_api_is_behind_the_api_key_gate(api):
    storage.save_global_config({"auth_enabled": True, "api_key": "k-123"})
    assert api.client.get("/api/autopilot/status").status_code == 401
    assert api.client.get("/api/autopilot/status", headers={"X-Api-Key": "k-123"}).status_code == 200
    assert api.client.post("/api/autopilot/poll").status_code == 401


# --- settings --------------------------------------------------------------------------------

def test_plex_token_is_masked_and_a_returned_sentinel_keeps_it(api):
    assert api.client.post("/api/settings", json={"plex_token": "tok-123", "plex_url": "http://p:32400"}).status_code == 200
    got = api.client.get("/api/settings").json()
    assert got["plex_token"] == SECRET_SENTINEL and got["secrets_set"]["plex_token"] is True
    assert "tok-123" not in str(got)
    api.client.post("/api/settings", json={"plex_token": SECRET_SENTINEL, "plex_url": "http://other:32400"})
    cfg = storage.load_global_config()
    assert cfg["plex_token"] == "tok-123" and cfg["plex_url"] == "http://other:32400"
    api.client.post("/api/settings", json={"plex_token": "new-token"})
    assert storage.load_global_config()["plex_token"] == "new-token"
    storage.save_global_config({"plex_token": ""})
    assert api.client.get("/api/settings").json()["secrets_set"]["plex_token"] is False


def test_switching_the_autopilot_on_records_when(api):
    storage.save_global_config({"autopilot_enabled": False, "autopilot_enabled_at": ""})
    api.client.post("/api/settings", json={"autopilot_grace_minutes": 7})
    assert not storage.load_global_config().get("autopilot_enabled_at")  # off: nothing recorded

    before = datetime.now(timezone.utc) - timedelta(seconds=2)
    api.client.post("/api/settings", json={"autopilot_enabled": True})
    stamp = storage.load_global_config()["autopilot_enabled_at"]
    assert parse_iso(stamp) >= before.replace(microsecond=0)

    # saving again (the UI sends the whole form) keeps the first stamp
    api.client.post("/api/settings", json={"autopilot_enabled": True, "autopilot_enabled_at": stamp,
                                           "autopilot_grace_minutes": 9})
    assert storage.load_global_config()["autopilot_enabled_at"] == stamp
    api.client.post("/api/settings", json={"autopilot_enabled": True})
    assert storage.load_global_config()["autopilot_enabled_at"] == stamp

    # an empty value in the form does not leave the autopilot without a start
    api.client.post("/api/settings", json={"autopilot_enabled": True, "autopilot_enabled_at": ""})
    assert storage.load_global_config()["autopilot_enabled_at"]
