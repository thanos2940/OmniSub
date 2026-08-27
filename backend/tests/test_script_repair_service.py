"""Project-wide wrong-alphabet repair: batching, verification and persistence.

The load-bearing property here is request economy — every ticketed line in a whole
project rides a *shared* batched call, never one call per line or per episode.
"""

import pytest
from unittest.mock import patch

from utils import storage
from utils.jobs_manager import jobs
from services import script_repair_service as srs


@pytest.fixture
def project(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(storage, "PROJECTS_DIR", projects_dir)

    from utils import metadata_index
    monkeypatch.setattr(metadata_index, "DB_FILE", tmp_path / "omnisub_test.db")
    metadata_index._init()

    name = "Test Show"
    storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    return name


def _rows(pairs):
    return [{"original": o, "translated": t, "translations": {"el": t}} for o, t in pairs]


def _seed(project, episodes):
    for ep_name, pairs in episodes.items():
        storage.save_episode(project, ep_name, _rows(pairs), {"translated": True})


class _Recorder:
    """Stands in for the model: records prompts, echoes back clean Greek."""

    def __init__(self, reply=None):
        self.prompts = []
        self.reply = reply

    async def __call__(self, model_name, prompt, **kwargs):
        self.prompts.append(prompt)
        if self.reply is not None:
            return self.reply
        # One corrected line per numbered item in the prompt.
        n = sum(1 for line in prompt.splitlines() if line and line[0].isdigit() and "| EN:" in line)
        return "\n".join(f"{i}| Διορθωμένη γραμμή {i}" for i in range(1, n + 1))


@pytest.mark.asyncio
async def test_whole_project_is_repaired_in_one_batched_request(project, monkeypatch):
    """20 bad lines spread over 4 episodes must cost ONE call, not 4 and not 20."""
    _seed(project, {
        f"S01E0{e}": [(f"Major {i}!", "Ταγματάρχה!") for i in range(5)]
        for e in range(1, 5)
    })

    recorder = _Recorder()
    with patch("adk_agents.llm_factory.generate", recorder):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, model="fake-model", export=False)

    assert len(recorder.prompts) == 1
    result = jobs[job_id].result
    assert result["lines_repaired"] == 20
    assert result["requests"] == 1
    assert result["lines_flagged"] == 0

    # The one prompt carried every line, numbered.
    assert "20| EN:" in recorder.prompts[0]


@pytest.mark.asyncio
async def test_chunking_splits_large_projects_but_stays_batched(project):
    _seed(project, {"S01E01": [(f"Major {i}!", "Ταγματάρχה!") for i in range(25)]})

    recorder = _Recorder()
    with patch("adk_agents.llm_factory.generate", recorder):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, model="fake-model",
                                         chunk_size=10, export=False)

    assert len(recorder.prompts) == 3          # 10 + 10 + 5, not 25
    assert jobs[job_id].result["lines_repaired"] == 25


@pytest.mark.asyncio
async def test_deterministic_fixes_cost_no_request(project):
    """Look-alike characters are repaired without ever calling the model."""
    _seed(project, {"S01E01": [("I don't know.", "Δεv ξέρω."), ("Yes.", "Ναι.")]})

    recorder = _Recorder()
    with patch("adk_agents.llm_factory.generate", recorder):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, model="fake-model", export=False)

    assert recorder.prompts == []
    assert jobs[job_id].result["chars_fixed"] == 1
    assert storage.load_episode(project, "S01E01")["data"][0]["translated"] == "Δεν ξέρω."


@pytest.mark.asyncio
async def test_use_llm_false_flags_instead_of_calling(project):
    _seed(project, {"S01E01": [("Major!", "Ταγματάρχה!")]})

    recorder = _Recorder()
    with patch("adk_agents.llm_factory.generate", recorder):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, use_llm=False, export=False)

    assert recorder.prompts == []
    result = jobs[job_id].result
    assert result["lines_repaired"] == 0
    assert result["lines_flagged"] == 1
    row = storage.load_episode(project, "S01E01")["data"][0]
    assert row["needs_review"] is True
    assert "Wrong-script" in row["review_issues"]


@pytest.mark.asyncio
async def test_a_still_contaminated_reply_is_rejected_and_flagged(project):
    _seed(project, {"S01E01": [("Major!", "Ταγματάρχה!")]})

    recorder = _Recorder(reply="1| Ταγματάρχה!")     # same contamination back
    with patch("adk_agents.llm_factory.generate", recorder):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, model="fake-model", export=False)

    result = jobs[job_id].result
    assert result["lines_repaired"] == 0
    assert result["lines_flagged"] == 1
    row = storage.load_episode(project, "S01E01")["data"][0]
    assert row["needs_review"] is True


@pytest.mark.asyncio
async def test_repairs_are_written_back_to_the_right_episode_and_line(project):
    """Item numbers are project-wide, so mis-routing across episodes is the risk."""
    _seed(project, {
        "S01E01": [("Fine.", "Εντάξει."), ("Major!", "Ταγματάρχה!")],
        "S01E02": [("Hello.", "Γεια."), ("Colonel!", "Συνταγματάρχה!")],
    })

    async def fake_generate(model_name, prompt, **kwargs):
        return "1| Ταγματάρχη!\n2| Συνταγματάρχη!"

    with patch("adk_agents.llm_factory.generate", fake_generate):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, model="fake-model", export=False)

    ep1 = storage.load_episode(project, "S01E01")["data"]
    ep2 = storage.load_episode(project, "S01E02")["data"]
    assert ep1[0]["translated"] == "Εντάξει."          # clean line untouched
    assert ep1[1]["translated"] == "Ταγματάρχη!"
    assert ep2[0]["translated"] == "Γεια."
    assert ep2[1]["translated"] == "Συνταγματάρχη!"


@pytest.mark.asyncio
async def test_clean_project_completes_without_touching_anything(project):
    _seed(project, {"S01E01": [("Yes.", "Ναι."), ("No.", "Όχι.")]})

    recorder = _Recorder()
    with patch("adk_agents.llm_factory.generate", recorder):
        from utils.jobs_manager import create_job
        job_id = create_job("script_repair", project_name=project)
        await srs.repair_project_scripts(job_id, project, export=False)

    assert recorder.prompts == []
    assert jobs[job_id].status == "completed"
    assert jobs[job_id].result["episodes_changed"] == 0
