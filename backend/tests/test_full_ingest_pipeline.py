"""The one-click Full Ingest pipeline (routers/pipeline.py::_process_full_ingest).

It crashed with NameError ('ep_names') on every project missing a context guide,
and — once past that — queued subtitle-less episodes that could only fail.
"""
import pytest

from utils import storage
from utils.jobs_manager import create_job, jobs

SRT = "1\n00:00:01,000 --> 00:00:03,000\nWelcome to Lumon.\n"


@pytest.fixture
def stubbed(monkeypatch):
    import routers.pipeline as pipeline

    calls = {"research": 0, "glossary": 0, "enqueued": []}

    async def no_extraction(project_name, episode_names=None, progress_callback=None):
        return 0

    async def research(show, lines, target, model_name=None):
        calls["research"] += 1
        return {"findings": "office satire"}, {}

    async def enhance(findings, show, target_language=None, model_name=None):
        return "TONE: deadpan.", {}

    async def glossary(lines, show, target, model_name=None, enable_research=False, **_):
        calls["glossary"] += 1
        return {"terms": [{"term": "Lumon", "translation": "Lumon"}]}, {}

    class Sessions:
        async def update_context(self, *a, **k):
            pass

        async def update_glossary(self, *a, **k):
            pass

    async def no_monitor(**kwargs):
        pass

    def enqueue(project, episode, priority, options=None, **kwargs):
        calls["enqueued"].append(episode)
        return f"id-{episode}"

    monkeypatch.setattr("services.embedded_extractor.auto_extract_embedded_subs_for_episodes", no_extraction)
    monkeypatch.setattr(pipeline, "research_project_adk", research)
    monkeypatch.setattr(pipeline, "enhance_context_guide_adk", enhance)
    monkeypatch.setattr(pipeline, "generate_glossary_adk", glossary)
    monkeypatch.setattr(pipeline, "adk_session_manager", Sessions())
    monkeypatch.setattr(pipeline, "_monitor_stage3_queue_progress", no_monitor)
    monkeypatch.setattr("services.queue_service.enqueue_translation", enqueue)
    return calls


@pytest.mark.asyncio
async def test_full_ingest_builds_context_and_queues_only_translatable_episodes(stubbed):
    from routers.pipeline import _process_full_ingest
    from routers.schemas import AutoPipelineRequest
    from utils.source_clean import import_and_clean_srt

    storage.create_project("Severance", {"show_name": "Severance", "target_language": "Greek"})
    import_and_clean_srt("Severance", "S01E01", SRT, filename="S01E01.en.srt")
    # A placeholder the sync made for a file with no subtitle at all.
    storage.episode_dir("Severance", "S01E02").mkdir(parents=True, exist_ok=True)
    storage.save_episode("Severance", "S01E02", [], {"has_source_sub": False, "line_count": 0})

    job_id = create_job("full_ingest_pipeline", project_name="Severance")
    await _process_full_ingest(job_id, "Severance", AutoPipelineRequest())

    assert jobs[job_id].status != "failed", jobs[job_id].message
    meta = storage.load_project_metadata("Severance")
    assert meta["context_guide"] == "TONE: deadpan."
    assert meta["glossary"]["terms"]
    assert stubbed["enqueued"] == ["S01E01"]
