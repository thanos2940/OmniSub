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


def test_which_existing_sources_a_batch_extraction_keeps():
    from routers.embedded import _keeps_source
    # the usual pick: any source stays, except an SRT in an anime project (its ASS track is the real one)
    assert _keeps_source(is_ass=False, prefer=None, prefer_ass=False)
    assert not _keeps_source(is_ass=False, prefer=None, prefer_ass=True)
    assert _keeps_source(is_ass=True, prefer=None, prefer_ass=True)
    # a chosen format: a source in it stays, one in the other format is replaced
    assert _keeps_source(is_ass=False, prefer="srt", prefer_ass=True)
    assert not _keeps_source(is_ass=True, prefer="srt", prefer_ass=False)
    assert _keeps_source(is_ass=True, prefer="ass", prefer_ass=False)
    assert not _keeps_source(is_ass=False, prefer="ass", prefer_ass=False)


def test_a_chosen_format_wins_when_the_video_has_both():
    from integrations.embedded_subs import SubtitleTrack, select_track
    tracks = [SubtitleTrack(index=1, codec="subrip", language="eng", title="English", frames=10),
              SubtitleTrack(index=2, codec="ass", language="eng", title="English (styled)", frames=10)]
    assert select_track(tracks, "en").index == 2                      # the usual pick: ASS
    assert select_track(tracks, "en", prefer_format="srt").index == 1
    assert select_track(tracks, "en", prefer_format="ass").index == 2


def test_a_batch_extraction_that_translates_after_keeps_a_runner_waiting_until_it_ends(monkeypatch):
    import asyncio
    from routers import embedded
    from services import agent_runner
    seen = []

    async def fake(*args):
        seen.append((args[-1], agent_runner._expected))

    monkeypatch.setattr(embedded, "_extract_batch", fake)
    asyncio.run(embedded._run_batch_extract("job", "Show", None, False, False, None, True))
    asyncio.run(embedded._run_batch_extract("job", "Show", None, False, False, None, False))
    assert seen == [(True, 1), (False, 0)]
    assert agent_runner._expected == 0


def test_the_log_says_why_a_translation_was_not_written_next_to_the_video():
    from services.agent_task_service import AgentTaskService
    name = _show("Note Show")
    why = AgentTaskService._why_not_exported
    assert why(name, "S01E01", is_primary=False).startswith("not exported: only the main language")
    assert why(name, "S01E01", is_primary=True) == "not exported: no video file is linked to this episode"
    meta = storage.load_episode_metadata(name, "S01E01") or {}
    meta.update(arr_media_path="/media/Note Show/S01E01.mkv",
                translation_warning="Not exported: 'S01E01.el.srt' already exists and was not written by Omnisub (protected).")
    storage.save_episode_metadata(name, "S01E01", meta)
    assert "a subtitle Omnisub did not write" in why(name, "S01E01", is_primary=True)
    meta.pop("translation_warning")
    storage.save_episode_metadata(name, "S01E01", meta)
    assert why(name, "S01E01", is_primary=True) == "not exported (media folder unreachable)"


def test_a_translated_episode_queued_with_overwrite_replaces_the_file_on_its_re_export(monkeypatch):
    # "Only what is missing" + "Replace the subtitle file" on an episode with nothing missing: the re-export may
    # replace a file Omnisub did not write (agent CLI); the Gemini worker passes the same option to its export.
    from services.agent_task_service import AgentTaskService
    from utils.translation_queue import TranslationQueue
    calls = []
    monkeypatch.setattr(AgentTaskService, "_export",
                        staticmethod(lambda p, e, cues, overwrite=False: calls.append(overwrite) or None))
    name = _show("Replace Show")
    service = AgentTaskService()
    for overwrite in (False, True):
        item_id = TranslationQueue().enqueue(name, "S01E01", 1, {"overwrite": True} if overwrite else {}, "")
        item = TranslationQueue().get_item(item_id)
        cues = storage.load_episode(name, "S01E01")["data"]
        service._finalize_without_work(item, name, "S01E01", cues, "el", True, {"distinct_units": 1})
    assert calls == [False, True]


def test_the_gemini_endpoints_carry_overwrite_to_the_queue_item(monkeypatch):
    from utils.translation_queue import TranslationQueue
    name = _show("Gemini Show")
    r = client.post(f"/projects/{name}/episodes/S01E01/translate", json={"overwrite": True})
    assert r.status_code == 200
    item = TranslationQueue().get_item(r.json()["item_id"])
    import json as _json
    opts = item["options"] if isinstance(item["options"], dict) else _json.loads(item["options"])
    assert opts.get("overwrite") is True
