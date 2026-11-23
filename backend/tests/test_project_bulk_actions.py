"""What the project page's bulk actions rely on: overwrite on request, extraction in the chosen format, download in
any target language, glossary terms merged with judgment."""
import asyncio

from fastapi.testclient import TestClient

from main import app
from utils import storage

client = TestClient(app)


def _show(name="Bulk Show"):
    storage.create_project(name, {"show_name": name, "target_language": "Greek", "target_languages": ["Greek", "Spanish"],
                                  "glossary": {"terms": [{"term": "Johnny", "translation": "Τζόνι", "type": "character"}]}})
    storage.save_episode(name, "S01E01", [{"id": "1", "timecode": "00:00:01,000 --> 00:00:02,000", "original": "Hello",
                                           "translated": "Γεια", "translations": {"el": "Γεια", "es": "Hola"}}])
    return name


def test_overwrite_reaches_the_queue_item():
    from utils.translation_queue import TranslationQueue
    from services.agent_task_service import _options
    name = _show()
    r = client.post("/api/agent/enqueue", json={"project_name": name, "episode_names": ["S01E01"], "full": True,
                                                "overwrite": True})
    assert r.status_code == 200 and r.json()["enqueued_count"] == 1
    item = TranslationQueue().get_item(r.json()["queue_ids"][0])
    assert _options(item)["overwrite"] is True and _options(item)["mode"] == "full"


def test_the_submit_export_overwrites_only_when_asked(monkeypatch):
    from services.agent_task_service import AgentTaskService
    calls = []
    monkeypatch.setattr("services.translation.export_formatter.auto_export_translated_subtitle",
                        lambda p, e, cues, meta, force=False: calls.append(force) or None)
    AgentTaskService._export("P", "E", [])
    AgentTaskService._export("P", "E", [], overwrite=True)
    assert calls == [False, True]


def test_download_in_another_target_language():
    name = _show("Lang Show")
    r = client.get(f"/projects/{name}/episodes/S01E01/download", params={"lang": "Spanish"})
    assert r.status_code == 200 and "Hola" in r.text and ".es." in r.headers["content-disposition"]
    r = client.get(f"/projects/{name}/episodes/S01E01/download")
    assert "Γεια" in r.text and ".el." in r.headers["content-disposition"]


def test_terms_from_chosen_episodes_are_merged_with_judgment():
    name = _show("Terms Show")
    r = client.post(f"/projects/{name}/glossary/merge-terms", json={"terms": [
        {"term": "Station Road", "translation": "Οδός Σταθμού", "type": "location", "description": "a street"},
        {"term": "Johnny", "translation": "Τζώνι", "type": "character"},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 1 and body["conflicts"] and body["conflicts"][0]["kept"] == "Τζόνι"
    terms = {t["term"]: t for t in storage.load_project_metadata(name)["glossary"]["terms"]}
    assert terms["Station Road"]["translation"] == "Οδός Σταθμού" and terms["Johnny"]["translation"] == "Τζόνι"


def test_batch_extract_takes_a_format_and_can_translate_after(monkeypatch):
    seen = {}

    async def fake_run(job_id, project, episodes, force, migrate_srt, prefer=None, translate_after=False):
        seen.update(project=project, episodes=episodes, force=force, prefer=prefer, translate_after=translate_after)

    monkeypatch.setattr("routers.embedded._run_batch_extract", fake_run)
    name = _show("Extract Show")
    r = client.post(f"/projects/{name}/embedded/extract-all",
                    json={"episode_names": ["S01E01"], "prefer": "srt", "force": True, "translate_after": True})
    assert r.status_code == 200
    assert seen == {"project": name, "episodes": ["S01E01"], "force": True, "prefer": "srt", "translate_after": True}
