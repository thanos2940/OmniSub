"""Autopilot item store (services/autopilot/store.py) and settings (config.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from services.autopilot.config import AutopilotSettings
from services.autopilot.store import AutopilotStore, iso_utc, parse_iso

T0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self, now=T0):
        self.t = now

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return AutopilotStore(tmp_path / "autopilot.db", clock=clock)


def detect(store, file_id=1, item_id=10, **kw):
    args = dict(source="sonarr", kind="episode", arr_item_id=item_id, arr_parent_id=5, arr_file_id=file_id,
                media_path=f"/tv/Show/S01E01.{file_id}.mkv", project_name="Show", episode_name="S01E01",
                title="Show", season=1, episode=1, detected_at=T0, grace_seconds=300)
    args.update(kw)
    return store.upsert_detection(**args)


def test_default_path_follows_data_dir_at_construction(isolate_test_data_dir):
    s = AutopilotStore()
    assert s.db_path == isolate_test_data_dir / "autopilot.db"
    assert s.db_path.exists()


def test_new_item_waits_for_the_grace_period(store, clock):
    item, created = detect(store)
    assert created and item["state"] == "waiting"
    assert item["due_at"] == iso_utc(T0 + timedelta(seconds=300))
    assert item["outputs"] == [] and item["events"][0]["message"] == "detected"
    assert store.due_items(clock()) == []
    clock.advance(seconds=299)
    assert store.due_items(clock()) == []
    clock.advance(seconds=1)
    assert [i["id"] for i in store.due_items(clock())] == [item["id"]]


def test_detected_before_a_long_outage_is_due_at_once(store, clock):
    clock.advance(hours=3)
    item, _ = detect(store, detected_at=T0)
    assert [i["id"] for i in store.due_items(clock())] == [item["id"]]


def test_same_file_is_deduplicated(store):
    first, created = detect(store)
    again, created2 = detect(store)
    assert created and not created2 and again["id"] == first["id"]
    assert store.counts()["waiting"] == 1


def test_unknown_file_id_is_deduplicated_by_path(store):
    first, _ = detect(store, file_id=None)
    again, created = detect(store, file_id=None)
    assert not created and again["id"] == first["id"]
    other, created = detect(store, file_id=None, media_path="/tv/Show/other.mkv")
    assert created and other["id"] != first["id"]


def test_unique_per_source(store):
    sonarr, _ = detect(store, file_id=7, item_id=1)
    radarr, created = detect(store, source="radarr", kind="movie", file_id=7, item_id=1, arr_parent_id=None)
    assert created and radarr["id"] != sonarr["id"]


def test_upgrade_supersedes_the_older_item(store, clock):
    old, _ = detect(store, file_id=1)
    store.transition(old["id"], "done", outputs=["/tv/a.el.srt"])
    clock.advance(minutes=1)
    new, created = detect(store, file_id=2)
    assert created and new["state"] == "waiting"
    old = store.get(old["id"])
    assert old["state"] == "superseded" and old["reason"] == "newer_file"
    assert old["events"][-1]["state"] == "superseded"
    assert [i["id"] for i in store.find_for_media("sonarr", 10)] == [new["id"], old["id"]]
    # other episodes are untouched
    other, _ = detect(store, file_id=3, item_id=11)
    assert store.get(other["id"])["state"] == "waiting"


def test_due_items_order_and_limit(store, clock):
    a, _ = detect(store, file_id=1, item_id=1, detected_at=T0 + timedelta(minutes=2))
    b, _ = detect(store, file_id=2, item_id=2, detected_at=T0)
    c, _ = detect(store, file_id=3, item_id=3, detected_at=T0 + timedelta(minutes=1))
    clock.advance(hours=1)
    assert [i["id"] for i in store.due_items(clock())] == [b["id"], c["id"], a["id"]]
    assert [i["id"] for i in store.due_items(clock(), limit=2)] == [b["id"], c["id"]]


def test_failed_item_with_a_retry_time_is_due(store, clock):
    item, _ = detect(store)
    store.transition(item["id"], "failed", reason="media_missing", next_attempt_at=T0 + timedelta(minutes=10))
    assert store.due_items(T0 + timedelta(minutes=9)) == []
    assert [i["id"] for i in store.due_items(T0 + timedelta(minutes=10))] == [item["id"]]
    parked = store.transition(item["id"], "failed", next_attempt_at=None)
    assert store.due_items(T0 + timedelta(days=1)) == [] and parked["state"] == "failed"


def test_transition_records_events_and_fields(store, clock):
    item, _ = detect(store)
    clock.advance(seconds=5)
    out = store.transition(item["id"], "translating", message="queued", source_kind="embedded_ass",
                           routed_at=clock(), outputs=["a", "b"])
    assert out["state"] == "translating" and out["source_kind"] == "embedded_ass"
    assert out["routed_at"] == iso_utc(clock()) and out["outputs"] == ["a", "b"]
    assert out["events"][-1] == {"t": iso_utc(clock()), "state": "translating", "message": "queued"}
    assert out["updated_at"] == iso_utc(clock())
    assert [i["id"] for i in store.active_items()] == [item["id"]]


def test_reason_is_replaced_on_a_state_change_but_kept_on_a_noop_update(store):
    item, _ = detect(store)
    done = store.transition(item["id"], "skipped", reason="skip_tag", detail="x")
    assert (done["reason"], done["detail"]) == ("skip_tag", "x")
    same = store.transition(item["id"], "skipped", message="media renamed", media_path="/new.mkv")
    assert (same["reason"], same["detail"], same["media_path"]) == ("skip_tag", "x", "/new.mkv")
    moved = store.transition(item["id"], "waiting")
    assert (moved["reason"], moved["detail"]) == (None, None)


def test_transition_rejects_bad_input(store):
    item, _ = detect(store)
    with pytest.raises(ValueError):
        store.transition(item["id"], "nonsense")
    with pytest.raises(ValueError):
        store.transition(item["id"], "done", bogus=1)
    with pytest.raises(KeyError):
        store.transition("missing", "done")


def test_events_are_capped_at_fifty(store):
    item, _ = detect(store)
    for n in range(80):
        store.transition(item["id"], "waiting", message=f"m{n}")
    events = store.get(item["id"])["events"]
    assert len(events) == 50 and events[-1]["message"] == "m79" and events[0]["message"] == "m30"


def test_schedule_retry_backs_off_then_fails(store, clock):
    item, _ = detect(store)
    first = store.schedule_retry(item["id"], "boom", 300, 4)
    assert first["state"] == "waiting" and first["attempts"] == 1 and first["detail"] == "boom"
    assert first["due_at"] == iso_utc(clock() + timedelta(seconds=300))
    assert store.due_items(clock()) == []
    clock.advance(seconds=300)
    assert [i["id"] for i in store.due_items(clock())] == [item["id"]]
    store.schedule_retry(item["id"], "boom", 1800, 4)
    store.schedule_retry(item["id"], "boom", 7200, 4)
    last = store.schedule_retry(item["id"], "final", 7200, 4)
    assert last["state"] == "failed" and last["attempts"] == 4 and last["detail"] == "final"
    assert last["next_attempt_at"] is None and last["reason"] == "max_attempts"
    clock.advance(days=1)
    assert store.due_items(clock()) == []


def test_recover_returns_checking_items_to_waiting_due_now(store, clock):
    a, _ = detect(store, file_id=1, item_id=1)
    b, _ = detect(store, file_id=2, item_id=2)
    c, _ = detect(store, file_id=3, item_id=3)
    store.transition(a["id"], "checking")
    store.transition(b["id"], "translating")
    assert store.recover() == 1
    assert store.get(a["id"])["state"] == "waiting"
    assert a["id"] in [i["id"] for i in store.due_items(clock())]
    assert store.get(b["id"])["state"] == "translating"  # re-tracked from the queue, not reset
    assert store.get(c["id"])["state"] == "waiting"


def test_state_survives_a_restart(tmp_path, clock):
    db = tmp_path / "x.db"
    first = AutopilotStore(db, clock=clock)
    item, _ = detect(first)
    first.transition(item["id"], "extracting", routed_at=clock())
    first.set_cursor("sonarr", {"date": "2026-10-14T12:00:00Z", "ids": [1]})
    first.close()
    second = AutopilotStore(db, clock=clock)
    assert second.get(item["id"])["state"] == "extracting"
    assert second.get_cursor("sonarr") == {"date": "2026-10-14T12:00:00Z", "ids": [1]}


def test_list_filters_counts_and_search(store):
    a, _ = detect(store, file_id=1, item_id=1, project_name="Alpha Show", title="Alpha")
    b, _ = detect(store, file_id=2, item_id=2, project_name="Beta Show", title="Beta")
    store.transition(b["id"], "done")
    items, total = store.list()
    assert total == 2 and len(items) == 2
    items, total = store.list(state="done")
    assert total == 1 and items[0]["id"] == b["id"]
    items, total = store.list(state="done,waiting")
    assert total == 2
    items, total = store.list(q="alpha")
    assert total == 1 and items[0]["id"] == a["id"]
    items, total = store.list(limit=1, offset=1)
    assert total == 2 and len(items) == 1
    counts = store.counts()
    assert counts["done"] == 1 and counts["waiting"] == 1 and counts["failed"] == 0


def test_cursors_roundtrip_and_overwrite(store):
    assert store.get_cursor("sonarr") is None
    store.set_cursor("sonarr", {"date": "a", "ids": [1]})
    store.set_cursor("sonarr", {"date": "b", "ids": [2]})
    assert store.get_cursor("sonarr") == {"date": "b", "ids": [2]}
    assert store.get_cursor("radarr") is None


def test_prune_only_removes_old_terminal_items(store, clock):
    old_done, _ = detect(store, file_id=1, item_id=1)
    old_wait, _ = detect(store, file_id=2, item_id=2)
    store.transition(old_done["id"], "done")
    clock.advance(days=100)
    fresh_done, _ = detect(store, file_id=3, item_id=3, detected_at=clock())
    store.transition(fresh_done["id"], "done")
    assert store.prune(older_than_days=90) == 1
    assert store.get(old_done["id"]) is None
    assert store.get(old_wait["id"]) is not None  # active items are never pruned
    assert store.get(fresh_done["id"]) is not None


def test_time_helpers():
    assert iso_utc(T0) == "2026-10-14T12:00:00Z"
    assert parse_iso("2026-10-14T12:00:00.1234567Z") == T0 + timedelta(microseconds=123456)
    assert parse_iso("2026-10-14T14:00:00+02:00") == T0
    assert parse_iso("garbage") is None and parse_iso(None) is None


# --- settings ---------------------------------------------------------------

def test_settings_defaults_match_the_plan():
    s = AutopilotSettings.from_config({})
    assert (s.enabled, s.grace_minutes, s.poll_seconds, s.reconcile_hours) == (False, 5, 60, 6)
    assert s.backlog_mode == "off" and s.backlog_daily_limit == 20 and s.digest_hour == 9
    assert s.skip_tags == ["ombisub-skip", "no-greek"] and s.plex_url == "http://localhost:32400"
    assert s.count_embedded_greek and s.skip_original_greek and s.monitored_only
    assert s.protect_foreign_subs and s.repair_foreign_encoding and not s.notify_success
    assert s.grace_seconds == 300


def test_settings_coercion_and_bounds():
    s = AutopilotSettings.from_config({
        "autopilot_enabled": "true", "autopilot_grace_minutes": -3, "autopilot_poll_seconds": "5",
        "autopilot_reconcile_hours": 0.1, "autopilot_backlog_mode": "NEWEST", "autopilot_backlog_daily_limit": "7",
        "autopilot_skip_tags": " A , b,,", "autopilot_digest_hour": 99, "autopilot_enabled_at": "2026-01-01T00:00:00Z",
        "plex_url": "  ", "autopilot_monitored_only": "no",
    })
    assert s.enabled is True and s.grace_minutes == 0 and s.poll_seconds == 15 and s.reconcile_hours == 1
    assert s.backlog_mode == "newest" and s.backlog_daily_limit == 7 and s.skip_tags == ["a", "b"]
    assert s.digest_hour == 23 and s.enabled_at == "2026-01-01T00:00:00Z"
    assert s.plex_url == "http://localhost:32400" and s.monitored_only is False


def test_settings_invalid_values_fall_back():
    s = AutopilotSettings.from_config({"autopilot_backlog_mode": "sideways", "autopilot_poll_seconds": "abc",
                                        "autopilot_grace_minutes": None, "autopilot_digest_hour": -1})
    assert s.backlog_mode == "off" and s.poll_seconds == 60 and s.grace_minutes == 5 and s.digest_hour == -1


def test_settings_hide_the_plex_token():
    s = AutopilotSettings.from_config({"plex_token": "secret"})
    assert s.to_dict()["plex_token"] == "***" and s.to_dict(hide_secrets=False)["plex_token"] == "secret"
