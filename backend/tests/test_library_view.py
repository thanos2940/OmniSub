"""
The Library page's data
=======================
One small answer for the whole page (``GET /api/library``): every project grouped as the page shows it, with
only the fields a row needs; and the per-title queue counts inside the activity snapshot (``by_project``),
which every page already polls, instead of a 100-row queue listing that miscounted long queues.
"""

import pytest
from fastapi.testclient import TestClient

from routers import activity
from utils import storage
from utils.translation_queue import (KIND_EXPORT_RETRY, KIND_EXTRACTION, KIND_TRANSLATION, PRIORITY_MANUAL,
                                     TranslationQueue)

BIG_GLOSSARY = {"terms": [{"term": f"Name{i}", "translation": f"Όνομα{i}"} for i in range(500)]}


@pytest.fixture(autouse=True)
def clean():
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    storage.invalidate_arr_library_cache()
    activity._cache.update(at=0.0, body=None)
    yield
    wipe()
    storage.invalidate_arr_library_cache()


def _project(name, **meta):
    storage.create_project(name, {"show_name": name, "target_language": "Greek", "glossary": BIG_GLOSSARY,
                                  "context_guide": "A long guide " * 200, **meta})


def _library():
    _project("Harbour Lights", arr_source="sonarr", arr_media_type="series", arr_total_episodes=10,
             arr_translated_episodes=4, arr_last_sync="2026-10-30T10:00:00")
    _project("The Long Night", arr_source="radarr", arr_media_type="movie", arr_total_episodes=1,
             arr_translated_episodes=1, arr_last_sync="2026-10-31T09:00:00")
    _project("Old Show", arr_source="sonarr", arr_media_type="series", translation_priority="ignored",
             arr_total_episodes=3, arr_translated_episodes=0)
    _project("Home Videos", arr_total_episodes=2, arr_translated_episodes=1)
    _project("Skipped Notes", translation_priority="ignored", arr_total_episodes=0, arr_translated_episodes=0)


def test_the_library_groups_every_project_with_only_what_a_row_shows():
    _library()
    lib = storage.get_library()
    names = {group: [e["name"] for e in lib[group]] for group in ("series", "movies", "other", "ignored")}
    assert names == {"series": ["Harbour Lights"], "movies": ["The Long Night"], "other": ["Home Videos"],
                     "ignored": ["Old Show", "Skipped Notes"]}
    show = lib["series"][0]
    assert (show["episode_count"], show["translated_count"], show["translation_priority"]) == (10, 4, "normal")
    assert show["arr_source"] == "sonarr" and show["show_name"] == "Harbour Lights"
    assert "glossary" not in show and "context_guide" not in show
    assert lib["ignored"][0]["arr_disabled"] is True
    assert lib["last_sync"] == "2026-10-31T09:00:00"
    assert lib["totals"] == {"series": 1, "movies": 1, "other": 1, "ignored": 2}


def test_a_changed_project_is_seen_at_once():
    _library()
    assert storage.get_library()["series"][0]["translated_count"] == 4
    meta = storage.load_project_metadata("Harbour Lights")
    meta["arr_translated_episodes"] = 7
    storage.save_project_metadata("Harbour Lights", meta)
    assert storage.get_library()["series"][0]["translated_count"] == 7


def test_the_endpoint_answers_far_less_than_the_full_metadata():
    _library()
    from main import app
    client = TestClient(app)
    lean = client.get("/api/library")
    full = client.get("/integrations/arr/library")
    assert lean.status_code == 200 and full.status_code == 200
    assert [e["name"] for e in lean.json()["series"]] == ["Harbour Lights"]
    assert len(lean.content) * 20 < len(full.content)   # the glossaries and guides stay out


def test_the_snapshot_counts_waiting_and_running_work_per_title():
    q = TranslationQueue()
    for ep in ("S01E01", "S01E02", "S01E03"):
        q.enqueue(project_name="Harbour Lights", episode_name=ep, priority=PRIORITY_MANUAL, kind=KIND_TRANSLATION)
    q.enqueue(project_name="Harbour Lights", episode_name="S01E04", priority=PRIORITY_MANUAL, kind=KIND_EXTRACTION)
    q.enqueue(project_name="Harbour Lights", episode_name="S01E05", priority=PRIORITY_MANUAL, kind=KIND_EXPORT_RETRY)
    done = q.enqueue(project_name="The Long Night", episode_name="The Long Night", priority=PRIORITY_MANUAL,
                     kind=KIND_TRANSLATION)
    q.update_status(done, status="completed")
    q.claim_next()   # S01E01 is translating
    body = activity.snapshot(force=True)
    assert body["by_project"] == {"Harbour Lights": {"queued": 3, "running": 1}}
