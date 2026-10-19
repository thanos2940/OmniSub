"""'Translate missing lines' in agent mode queues the episodes with gaps for the runner (no API calls)."""
from fastapi.testclient import TestClient

from utils import storage
from utils.jobs_manager import jobs
from utils.translation_queue import TranslationQueue


def _cue(i, text, greek=None):
    cue = {"index": i, "start": f"00:00:{i:02d},000", "end": f"00:00:{i:02d},900", "original": text}
    if greek:
        cue["translated"] = greek
        cue["translations"] = {"el": greek}
    return cue


def test_episodes_with_gaps_are_queued_for_the_runner(monkeypatch):
    import main
    from services.translation import line_recovery
    monkeypatch.setattr(line_recovery, "translate_project_missing_lines",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("the model path must not run")))
    storage.create_project("Gap Show", {"show_name": "Gap Show", "target_language": "Greek"})
    storage.save_episode("Gap Show", "S01E01", [_cue(1, "Where were you?", "Πού ήσουν;"),
                                                _cue(2, "At the station.", "Στον σταθμό.")])
    storage.save_episode("Gap Show", "S01E02", [_cue(1, "He never came.", "Δεν ήρθε ποτέ."),
                                                _cue(2, "That's not like him.")])
    storage.save_episode("Gap Show", "S01E03", [_cue(1, "Not translated at all.")])

    res = TestClient(main.app).post("/projects/Gap Show/translate-missing")
    assert res.status_code == 200
    job = jobs[res.json()["job_id"]]
    assert job.status == "completed", job.message
    assert job.result["episodes"] == ["S01E02"] and job.result["queued"] == 1

    with TranslationQueue()._conn() as conn:
        rows = conn.execute("SELECT episode_name FROM translation_queue WHERE project_name = 'Gap Show'").fetchall()
    assert [r["episode_name"] for r in rows] == ["S01E02"]
