"""
Agy first: one-off requests, show context and the runner's defaults
===================================================================
Everything here is offline: the agent CLI is ``fake_agent_cli.py`` and the Gemini API is the
``gemini_api`` stand-in of test_agent_runner. They pin the contract of the "agent first" work:

* ``agy_llm.complete`` asks the runner's CLI (fallback chain, pauses) and reaches the Gemini API
  only for a prompt the CLI's safety filter refuses -- never because the CLI is missing or broken;
* retranslate-line, summaries, glossary/context buttons, term harvest and the wrong-alphabet repair
  use it in agent mode and keep their old Gemini paths in model mode;
* a show without a context guide or glossary gets both from one request before its first
  episode, and the guide grows every few episodes;
* the engine defaults to ``agent`` and "Pause translations" pauses the runner's claiming too.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from test_agent_runner import (  # noqa: F401  (fixtures are picked up by name)
    FAKE, LINES, _cues, _join, _log, _prompts, _run, _runner, _show, _wait,
    empty_queue, fake_env, gemini_api, no_bootstrap,
)
from services import agent_cli, agent_prompts as ap, agent_runner, agy_llm, project_bootstrap as pb
from services.agent_task_service import AgentTaskService
from utils import storage
from utils.character_profiles import CharacterProfileManager
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_MANUAL

LINE_PROMPT = "## Line to translate (1)\n1| Hello there\n"
BRIEF_MARK = "You are preparing the translation brief"


@pytest.fixture
def agy(tmp_path, monkeypatch):
    """The runner ``agy_llm`` talks to: the fake CLI, state in tmp_path (it is the module's singleton)."""
    runner = _runner(tmp_path)
    monkeypatch.setattr(agent_runner, "_runner", runner)
    return runner


def _briefs(tmp_path):
    return [p for p in _prompts(tmp_path) if BRIEF_MARK in p]


def _set_meta(project, **changes):
    meta = storage.load_project_metadata(project)
    meta.update(changes)
    storage.save_project_metadata(project, meta)


def _add_episode(project, episode, lines=LINES, **options):
    storage.save_episode(project, episode, _cues(lines))
    return TranslationQueue().enqueue(project_name=project, episode_name=episode, priority=PRIORITY_MANUAL,
                                      kind=KIND_TRANSLATION, options=options or None)


# ===========================================================================
# 1. agy_llm: the CLI first, the API only for a refusal
# ===========================================================================

class TestComplete:

    def test_asks_the_runners_cli(self, agy, tmp_path):
        assert agy_llm.complete(LINE_PROMPT, purpose="test") == "1| Ελ: Hello there"
        assert agy.session["calls"] == 1

    def test_a_used_up_quota_moves_on_to_the_fallback_model(self, agy, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_QUOTA_MODELS", "gemini")
        assert agy_llm.complete(LINE_PROMPT, purpose="test") == "1| Ελ: Hello there"
        assert "gemini-3.8-flash-low" in agy.pauses      # the runner knows, too
        assert _prompts(tmp_path)[-1].startswith("model: claude-sonnet-5-5-low")

    def test_models_the_runner_paused_are_not_asked(self, agy, tmp_path):
        agy._pause("gemini-3.8-flash-low", 600, "test")
        agy_llm.complete(LINE_PROMPT, purpose="test")
        assert [p.splitlines()[0] for p in _prompts(tmp_path)] == ["model: claude-sonnet-5-5-low"]

    def test_every_model_paused_says_until_when(self, agy):
        for model in agy._models():
            agy._pause(model, 3600, "test")
        with pytest.raises(agy_llm.AgyError, match="paused until") as err:
            agy_llm.complete(LINE_PROMPT, purpose="test")
        assert err.value.kind == "unavailable"

    def test_a_refusal_goes_to_the_gemini_api_and_only_then(self, agy, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "Hello")
        gemini_api["answer"] = True
        assert agy_llm.complete(LINE_PROMPT, purpose="test") == "1| Ελ: Hello there"
        assert len(gemini_api["calls"]) == 1 and len(_prompts(tmp_path)) == 1   # the CLI was asked once

    def test_a_refusal_without_a_rescue_goes_to_another_model_family(self, agy, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "Hello")
        agy.config["api_rescue"] = False
        assert agy_llm.complete(LINE_PROMPT, purpose="test") == "1| Ελ: Hello there"
        assert not gemini_api["calls"]
        assert _prompts(tmp_path)[-1].startswith("model: claude-sonnet-5-5-low")

    def test_a_refusal_nothing_can_answer_raises_and_spends_no_api(self, agy, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini,claude")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "Hello")
        agy.config["api_rescue"] = False
        with pytest.raises(agy_llm.AgyError, match="safety filter") as err:
            agy_llm.complete(LINE_PROMPT, purpose="test")
        assert err.value.kind == "blocked" and not gemini_api["calls"]

    def test_failing_models_raise_and_never_reach_the_api(self, agy, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_FAIL_MODELS", "gemini,claude")
        gemini_api["answer"] = True
        with pytest.raises(agy_llm.AgyError, match="No model answered") as err:
            agy_llm.complete(LINE_PROMPT, purpose="test")
        assert err.value.kind == "failed" and not gemini_api["calls"]

    def test_no_cli_is_a_clear_error_and_never_the_api(self, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setattr(agent_cli, "engine_command", lambda engine: None)
        monkeypatch.setattr(agent_runner, "_runner", agent_runner.AgentRunner(
            service=AgentTaskService(tasks_dir=tmp_path / "tasks"), state_dir=tmp_path / "state"))
        gemini_api["answer"] = True
        with pytest.raises(agy_llm.AgyError, match="not installed") as err:
            agy_llm.complete(LINE_PROMPT, purpose="test")
        assert err.value.kind == "unavailable" and not gemini_api["calls"]

    def test_a_slow_request_times_out(self, agy, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_SLEEP", "10")
        started = time.time()
        with pytest.raises(agy_llm.AgyError, match="timed out") as err:
            agy_llm.complete(LINE_PROMPT, purpose="test", timeout=1.0)
        assert err.value.kind == "timeout" and time.time() - started < 8

    def test_only_a_few_one_off_requests_run_at_once(self, agy, monkeypatch):
        monkeypatch.setattr(agy_llm, "_slots", threading.BoundedSemaphore(1))
        agy_llm._slots.acquire()
        with pytest.raises(agy_llm.AgyError, match="Too many") as err:
            agy_llm.complete(LINE_PROMPT, purpose="test", timeout=0.2)
        assert err.value.kind == "busy"

    def test_calls_are_counted_in_the_usage_table(self, agy, monkeypatch):
        seen = []
        monkeypatch.setattr(agy, "_usage", lambda model, lane, project, episode, reply: seen.append((lane, episode)))
        agy_llm.complete(LINE_PROMPT, purpose="my_purpose", project="P")
        assert seen == [(-1, "my_purpose")]

    @pytest.mark.asyncio
    async def test_async_wrapper(self, agy):
        assert await agy_llm.acomplete(LINE_PROMPT, purpose="test") == "1| Ελ: Hello there"


# ===========================================================================
# 2. retranslate-line
# ===========================================================================

def _line_show(name="Line Show"):
    _show(name)
    cues = storage.load_episode_data(name, "S01E01")
    for i in (0, 1, 2):
        cues[i]["translated"] = f"Παλιά {i}"
        cues[i]["translations"] = {"el": f"Παλιά {i}"}
    storage.save_episode(name, "S01E01", cues)
    _set_meta(name, context_guide="A quiet drama about two friends.",
              glossary={"terms": [{"term": "Johnny", "translation": "Τζόνι", "type": "character", "gender": "masculine"}]})
    return name


class TestRetranslateLine:

    @pytest.mark.asyncio
    async def test_the_cli_translates_the_line_with_its_surroundings(self, agy, tmp_path):
        from routers.episodes import retranslate_single_line
        name = _line_show()
        resp = await retranslate_single_line(name, "S01E01", {"line_index": 1})
        body = json.loads(resp.body.decode("utf-8"))
        assert body["status"] == "success" and body["line_index"] == 1
        assert body["translated"] == f"Ελ: {LINES[1]}" and body["previous_translated"] == "Παλιά 1"
        assert body["etag"] and resp.headers["ETag"] == body["etag"]
        saved = storage.load_episode_data(name, "S01E01")[1]
        assert saved["translations"]["el"] == body["translated"] and saved["previous_translated"] == "Παλιά 1"

        (prompt,) = _prompts(tmp_path)
        assert "## About this job" in prompt and "A quiet drama about two friends." in prompt     # job framing, guide
        assert "- Johnny → Τζόνι  (character, masculine)" in prompt                               # glossary hit of the line
        assert f"- {LINES[0]} → Παλιά 0" in prompt and f"- {LINES[2]} → Παλιά 2" in prompt       # neighbours
        assert f"- {LINES[4]}" in prompt                                                          # ... untranslated ones as source
        assert f"## Line to translate (1)\n1| {LINES[1]}" in prompt
        assert "## The current translation\nΠαλιά 1" in prompt

    @pytest.mark.asyncio
    async def test_an_explicit_text_never_asks_a_model(self, agy, tmp_path):
        from routers.episodes import retranslate_single_line
        name = _line_show()
        resp = await retranslate_single_line(name, "S01E01", {"line_index": 0, "new_text": "Χειροκίνητο"})
        assert json.loads(resp.body.decode("utf-8"))["translated"] == "Χειροκίνητο"
        assert not _prompts(tmp_path)

    @pytest.mark.asyncio
    async def test_a_refused_line_is_rescued_through_the_api(self, agy, tmp_path, monkeypatch, gemini_api):
        from routers.episodes import retranslate_single_line
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "station")
        gemini_api["answer"] = True
        name = _line_show()
        resp = await retranslate_single_line(name, "S01E01", {"line_index": 1})
        assert json.loads(resp.body.decode("utf-8"))["translated"] == f"Ελ: {LINES[1]}"
        assert len(gemini_api["calls"]) == 1

    @pytest.mark.asyncio
    async def test_without_a_cli_the_answer_is_a_readable_503(self, tmp_path, monkeypatch, gemini_api):
        from routers.episodes import retranslate_single_line
        monkeypatch.setattr(agent_cli, "engine_command", lambda engine: None)
        monkeypatch.setattr(agent_runner, "_runner", agent_runner.AgentRunner(
            service=AgentTaskService(tasks_dir=tmp_path / "tasks"), state_dir=tmp_path / "state"))
        name = _line_show()
        with pytest.raises(HTTPException) as err:
            await retranslate_single_line(name, "S01E01", {"line_index": 1})
        assert err.value.status_code == 503 and "not installed" in err.value.detail
        assert not gemini_api["calls"]
        assert storage.load_episode_data(name, "S01E01")[1]["translated"] == "Παλιά 1"   # nothing was touched

    @pytest.mark.asyncio
    async def test_a_reply_without_a_line_is_a_502(self, agy, monkeypatch):
        from routers.episodes import retranslate_single_line
        monkeypatch.setattr(agy_llm, "complete", lambda *a, **k: "Sorry, I cannot help with that.")
        name = _line_show()
        with pytest.raises(HTTPException) as err:
            await retranslate_single_line(name, "S01E01", {"line_index": 1})
        assert err.value.status_code == 502

    @pytest.mark.asyncio
    async def test_the_model_engine_keeps_the_gemini_path(self, agy, monkeypatch):
        from routers.episodes import retranslate_single_line
        storage.save_global_config({"translation_engine": "model"})

        def gemini_path(**kwargs):
            raise RuntimeError("gemini path")

        monkeypatch.setattr("adk_agents.create_translation_pipeline", gemini_path)
        name = _line_show()
        with pytest.raises(RuntimeError, match="gemini path"):
            await retranslate_single_line(name, "S01E01", {"line_index": 1})


# ===========================================================================
# 3. The other one-off operations
# ===========================================================================

class TestOneOffOperations:

    @pytest.mark.asyncio
    async def test_summary_through_the_cli(self, agy, tmp_path):
        from routers.summaries import _process_summary_generation
        from utils.jobs_manager import create_job, jobs
        from utils.episode_summaries import EpisodeSummaryManager
        name = _line_show()
        job = create_job("generate_summary", project_name=name, episode_name="S01E01")
        await _process_summary_generation(job, name, "S01E01", "gemini-ignored")
        assert jobs[job].status == "completed" and "Τζόνι" in jobs[job].result["summary"]
        assert "Τζόνι" in EpisodeSummaryManager(name).get_summary("S01E01")
        assert "## Subtitle text" in _prompts(tmp_path)[0]

    @pytest.mark.asyncio
    async def test_term_harvest_through_the_cli(self, agy, tmp_path):
        from services.term_harvester import harvest_terms_from_lines
        terms = await harvest_terms_from_lines(["Johnny is at Station Road."], ["Ο Τζόνι είναι στην Οδό Σταθμού."],
                                               "Greek", existing_glossary={"terms": [{"term": "Mira"}]})
        assert [(t["term"], t["translation"], t["type"], t["gender"]) for t in terms] == [
            ("Johnny", "Τζόνι", "character", "masculine"), ("Station Road", "Οδός Σταθμού", "location", "n/a")]
        assert "Already in the glossary (do not repeat): mira." in _prompts(tmp_path)[0]

    @pytest.mark.asyncio
    async def test_script_repair_through_the_cli(self, agy, tmp_path):
        from services.script_repair_service import _repair_chunk
        chunk = [{"source": "Hello", "current": "Γειαx", "issues": ["Latin letter"]},
                 {"source": "Bye", "current": "Αντίοx", "issues": ["Latin letter"]}]
        assert await _repair_chunk(chunk, "Greek", "ignored") == {1: "Διορθωμένο 1", 2: "Διορθωμένο 2"}
        assert "## About this job" in _prompts(tmp_path)[0]

    @pytest.mark.asyncio
    async def test_context_and_glossary_buttons_use_the_cli(self, agy, tmp_path):
        from routers import glossary_context as gc
        from utils.jobs_manager import create_job, jobs
        name = _line_show()
        _set_meta(name, context_guide="", glossary={"terms": []})

        job = create_job("create_context", project_name=name)
        await gc._process_create_context(job, name, [], "ignored")
        assert jobs[job].status == "completed", jobs[job].message
        guide = storage.load_project_metadata(name)["context_guide"]
        assert guide.startswith("A quiet drama about two friends in a harbour town.")
        assert jobs[job].result["context_guide"] == guide

        job = create_job("enhance_context", project_name=name)
        await gc._process_enhance_context(job, name, "ignored", False)
        assert jobs[job].status == "completed", jobs[job].message
        assert "## The current context guide" in _prompts(tmp_path)[-1]       # revised, not started again

        job = create_job("create_glossary", project_name=name)
        await gc._process_create_glossary(job, name, [], "ignored")
        assert jobs[job].status == "completed", jobs[job].message
        terms = {t["term"]: t for t in storage.load_project_metadata(name)["glossary"]["terms"]}
        assert {"Johnny", "Mira", "Harbor Street"} <= set(terms)
        assert terms["Mira"]["gender"] == "feminine" and terms["Mira"]["translation"] == "Μίρα"
        assert CharacterProfileManager(name).load_all()["Mira"].gender == "feminine"

        job = create_job("enhance_glossary", project_name=name)
        await gc._process_enhance_glossary(job, name, None, "ignored", False)
        assert jobs[job].status == "completed", jobs[job].message
        assert jobs[job].result["terms"] == []     # everything the CLI named is in the glossary already

    @pytest.mark.asyncio
    async def test_a_cli_that_gives_nothing_fails_the_job_with_a_reason(self, agy, monkeypatch):
        from routers import glossary_context as gc
        from utils.jobs_manager import create_job, jobs
        monkeypatch.setenv("FAKE_AGY_NO_CONTEXT", "1")
        name = _line_show()
        _set_meta(name, context_guide="")
        job = create_job("create_context", project_name=name)
        await gc._process_create_context(job, name, [], "ignored")
        assert jobs[job].status == "failed" and "no context guide" in jobs[job].message
        assert storage.load_project_metadata(name)["context_guide"] == ""

    @pytest.mark.asyncio
    async def test_model_engine_keeps_the_adk_paths(self, agy, monkeypatch):
        from routers import glossary_context as gc
        from utils.jobs_manager import create_job, jobs
        storage.save_global_config({"translation_engine": "model"})
        calls = []

        async def research(*args, **kwargs):
            calls.append("research")
            return {"findings": "f"}, {}

        async def enhance(*args, **kwargs):
            calls.append("enhance")
            return "ADK guide", {}

        monkeypatch.setattr(gc, "research_project_adk", research)
        monkeypatch.setattr(gc, "enhance_context_guide_adk", enhance)
        monkeypatch.setattr(gc.adk_session_manager, "update_context", lambda *a, **k: _noop())
        name = _line_show()
        job = create_job("create_context", project_name=name)
        await gc._process_create_context(job, name, [], "gemini-x")
        assert calls == ["research", "enhance"] and jobs[job].result["context_guide"] == "ADK guide"


async def _noop():
    return None


# ===========================================================================
# 4. The show's context before its first episode
# ===========================================================================

class TestBootstrap:

    def test_first_episode_gets_a_guide_and_glossary_before_it_is_translated(self, tmp_path):
        (qid,) = _show()
        _set_meta("Runner Show", overview="Two friends wait for a train that never comes.", genres=["Drama"], year=2019)
        runner = _run(tmp_path, bootstrap=True)
        assert TranslationQueue().get_item(qid)["status"] == "completed"

        brief, translate = _prompts(tmp_path)
        assert BRIEF_MARK in brief and "Title: Runner Show (2019)" in brief and "Genres: Drama" in brief
        assert "Overview: Two friends wait for a train that never comes." in brief
        assert "into Greek" in brief and "waiting for Johnny" in brief                  # target language, dialogue sample
        assert "### Context guide" in brief and "### Glossary" in brief
        assert "## About this job" in brief

        meta = storage.load_project_metadata("Runner Show")
        assert meta["context_guide"].startswith("A quiet drama about two friends in a harbour town.")
        terms = {t["term"]: t for t in meta["glossary"]["terms"]}
        assert {"Johnny", "Mira", "Harbor Street"} <= set(terms)
        assert terms["Johnny"]["gender"] == "masculine" and terms["Mira"]["gender"] == "feminine"   # "male", "f"
        assert terms["Mira"]["notes"] == "Johnny's sister, the station clerk"
        assert meta["bootstrapped_at"] and not meta.get("bootstrap_error")

        # ... and the episode was translated with all of it
        assert "## Show context" in translate and "A quiet drama about two friends" in translate
        assert "- Johnny → Τζόνι  (character, masculine)" in translate
        assert "Johnny (m, informal)" in translate                                       # the character profile
        assert "starting with `G| `" not in translate                                    # the guide exists now
        assert CharacterProfileManager("Runner Show").load_all()["Mira"].gender == "feminine"
        assert "built the context for Runner Show: a context guide, 3 glossary term(s)" in _log(runner)

    def test_a_show_that_has_both_is_not_asked_again(self, tmp_path):
        _show()
        _set_meta("Runner Show", context_guide="Already written.",
                  glossary={"terms": [{"term": "Johnny", "translation": "Τζόνι"}]})
        _run(tmp_path, bootstrap=True)
        assert not _briefs(tmp_path)

    def test_only_what_is_missing_is_asked_for(self, tmp_path):
        _show()
        _set_meta("Runner Show", context_guide="Already written.")
        _run(tmp_path, bootstrap=True)
        (brief,) = _briefs(tmp_path)
        assert "### Glossary" in brief and "### Context guide" not in brief
        assert storage.load_project_metadata("Runner Show")["context_guide"] == "Already written."

    def test_a_failed_bootstrap_does_not_block_the_translation_and_waits_a_day(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_NO_CONTEXT", "1")
        (qid,) = _show()
        runner = _run(tmp_path, bootstrap=True)
        assert TranslationQueue().get_item(qid)["status"] == "completed"
        assert all(t == f"Ελ: {s}" for s, t in [(c["original"], (c["translations"] or {}).get("el"))
                                                 for c in storage.load_episode_data("Runner Show", "S01E01")])
        meta = storage.load_project_metadata("Runner Show")
        assert "no context guide and no glossary terms" in meta["bootstrap_error"] and meta["bootstrap_error_at"]
        assert not meta.get("bootstrapped_at")
        assert "translating without it" in _log(runner)
        assert len(_briefs(tmp_path)) == 1

        # the next episode, while the show still lacks both: not asked again within a day ...
        _set_meta("Runner Show", context_guide="", glossary={"terms": []})
        _add_episode("Runner Show", "S01E02")
        _run(tmp_path, bootstrap=True)
        assert len(_briefs(tmp_path)) == 1
        # ... and asked again after it
        _set_meta("Runner Show", context_guide="", glossary={"terms": []},
                  bootstrap_error_at=(datetime.now(timezone.utc) - timedelta(hours=25)).isoformat())
        _add_episode("Runner Show", "S01E03")
        _run(tmp_path, bootstrap=True)
        assert len(_briefs(tmp_path)) == 2

    def test_ensure_context_is_read_from_the_queue_item(self, tmp_path):
        (plain,) = _show()
        asking = _add_episode("Runner Show", "S01E02", ensure_context=True)
        runner = _runner(tmp_path)
        assert runner._context_requested({"queue_id": asking}) is True
        assert runner._context_requested({"queue_id": plain}) is False
        assert runner._context_requested({"queue_id": "nope"}) is False

    def test_ensure_context_forces_the_check_even_right_after_a_failure(self, tmp_path):
        storage.create_project("Runner Show", {"show_name": "Runner Show", "target_language": "Greek"})
        _set_meta("Runner Show", bootstrap_error="boom", bootstrap_error_at=datetime.now(timezone.utc).isoformat())
        _add_episode("Runner Show", "S01E01", ensure_context=True)
        _run(tmp_path, bootstrap=True)
        assert len(_briefs(tmp_path)) == 1
        assert storage.load_project_metadata("Runner Show")["glossary"]["terms"]

    def test_ensure_context_runs_even_when_the_setting_is_off(self, tmp_path):
        storage.create_project("Runner Show", {"show_name": "Runner Show", "target_language": "Greek"})
        _add_episode("Runner Show", "S01E01", ensure_context=True)
        _run(tmp_path, bootstrap=False)
        assert len(_briefs(tmp_path)) == 1
        assert storage.load_project_metadata("Runner Show")["glossary"]["terms"]

    def test_the_setting_off_means_no_request(self, tmp_path):
        _show()
        _run(tmp_path, bootstrap=False)
        assert not _briefs(tmp_path)

    def test_two_lanes_bootstrap_a_show_once(self, tmp_path):
        _show(episodes=("S01E01", "S01E02"))
        runner = _run(tmp_path, bootstrap=True, lanes=2, per_show=2)
        assert runner.session["episodes"] == 2
        assert len(_briefs(tmp_path)) == 1

    def test_a_paused_cli_postpones_the_bootstrap_without_recording_a_failure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_QUOTA_MODELS", "gemini,claude")
        _show()
        runner = _runner(tmp_path)                       # every model is out of quota: the runner just waits
        runner.start({"lanes": 1, "bootstrap": True})
        assert _wait(lambda: "translating without it" in _log(runner))
        runner.stop(now=True)
        _join(runner)
        meta = storage.load_project_metadata("Runner Show")
        assert not meta.get("bootstrap_error") and not meta.get("bootstrapped_at")   # asked again once a model is back

    def test_needs_bootstrap_waits_a_day_after_a_failure(self):
        now = datetime.now(timezone.utc)
        empty = {"context_guide": "", "glossary": {"terms": []}}
        assert pb.needs_bootstrap(empty)
        assert pb.needs_bootstrap({"context_guide": "x", "glossary": {"terms": []}})
        assert pb.needs_bootstrap({"context_guide": "", "glossary": {"terms": [{"term": "a"}]}})
        assert not pb.needs_bootstrap({"context_guide": "x", "glossary": {"terms": [{"term": "a"}]}}, force=True)
        recent = {**empty, "bootstrap_error_at": (now - timedelta(hours=3)).isoformat()}
        assert not pb.needs_bootstrap(recent) and pb.needs_bootstrap(recent, force=True)
        assert pb.needs_bootstrap({**empty, "bootstrap_error_at": (now - timedelta(hours=25)).isoformat()})
        assert pb.needs_bootstrap({**empty, "bootstrap_error_at": "garbage"})

    def test_the_sample_is_spread_over_the_first_three_episodes(self):
        storage.create_project("Long Show", {"show_name": "Long Show", "target_language": "Greek"})
        for e in range(1, 6):
            storage.save_episode("Long Show", f"S01E0{e}", _cues([f"e{e} line {i}" for i in range(200)]))
        lines, used = pb.sample_dialogue("Long Show")
        assert used == 3 and 380 <= len(lines) <= 400
        assert not any(l.startswith(("e4 ", "e5 ")) for l in lines)                       # the earliest three
        for e in (1, 2, 3):
            own = [l for l in lines if l.startswith(f"e{e} ")]
            assert f"e{e} line 0" in own and f"e{e} line 199" in own                      # start to end
            idx = [int(l.rsplit(" ", 1)[1]) for l in own]
            assert idx == sorted(idx) and idx[-1] - idx[0] > 150
        assert pb.sample_dialogue("Long Show", limit=40)[0].__len__() <= 40

    def test_the_prompt_asks_for_the_formats_the_parser_reads(self):
        prompt = ap.context_prompt(show="Show", target_name="Greek", brief={"year": 2001, "movie": True},
                                   lines=["Hi."], want_guide=True, want_terms=True, known_terms=["Zed"], episodes=1)
        assert "Kind: film" in prompt and "`+ term | Greek rendering | type | gender | used as | note`" in prompt
        assert "each starting with `G| `" in prompt and "Zed" in prompt
        parsed = ap.parse_reply("G| One.\nG| Two.\n+ Mira | Μίρα | person | f | | the clerk\n+ Zed | Ζεντ", allowed=set())
        assert parsed["guide"] == "One.\nTwo."
        assert parsed["terms"][0] == {"term": "Mira", "translation": "Μίρα", "type": "character", "gender": "f",
                                      "notes": "the clerk"}
        assert parsed["terms"][1] == {"term": "Zed", "translation": "Ζεντ"}

    def test_genders_are_normalised_and_unknown_ones_dropped(self):
        terms = pb.clean_terms([{"term": "A", "gender": "male"}, {"term": "B", "gender": "F"},
                                {"term": "C", "gender": "unknown"}, {"term": "D"}])
        assert [t.get("gender") for t in terms] == ["masculine", "feminine", None, None]


# ===========================================================================
# 5. The glossary's genders reach the character profiles
# ===========================================================================

class TestProfileSeeding:

    def test_new_character_terms_with_a_gender_get_a_profile_and_existing_ones_stay(self, tmp_path):
        storage.create_project("Seed Show", {"show_name": "Seed Show", "target_language": "Greek"})
        CharacterProfileManager("Seed Show").update_profile("Anna", {"gender": "feminine", "speech_patterns": "curt"})
        service = AgentTaskService(tasks_dir=tmp_path / "tasks")
        res = service._merge_terms("Seed Show", [
            {"term": "Anna", "translation": "Άννα", "type": "character", "gender": "masculine"},
            {"term": "Boris", "translation": "Μπόρις", "type": "character", "gender": "masculine"},
            {"term": "Cleo", "translation": "Κλεο", "type": "person", "gender": "feminine"},
            {"term": "Dune City", "translation": "Πόλη", "type": "location", "gender": "feminine"},
            {"term": "Evan", "translation": "Έβαν", "type": "character"},
        ], "el")
        assert res["added"] == 5
        profiles = CharacterProfileManager("Seed Show").load_all()
        assert set(profiles) == {"Anna", "Boris", "Cleo"}
        assert profiles["Anna"].gender == "feminine" and profiles["Anna"].speech_patterns == "curt"   # untouched
        assert profiles["Boris"].gender == "masculine" and profiles["Cleo"].gender == "feminine"

    def test_glossary_characters_count_as_speakers(self):
        storage.create_project("Seed Show", {"show_name": "Seed Show", "target_language": "Greek"})
        mgr = CharacterProfileManager("Seed Show")
        mgr.update_profile("Boris", {"gender": "masculine"})
        glossary = {"terms": [{"term": "Boris", "type": "character"}]}
        assert [p.name for p in mgr.get_profiles_for_chunk(["Boris is here."], glossary=glossary)] == ["Boris"]


# ===========================================================================
# 6. The guide grows as the show goes on
# ===========================================================================

class TestGuideGrowth:

    def _show_with_guide(self, counter):
        _show()
        _set_meta("Runner Show", context_guide="A quiet drama.", guide_growth_episodes=counter,
                  glossary={"terms": [{"term": "Johnny", "translation": "Τζόνι"}]})

    def test_the_sixth_episode_asks_for_additions_and_they_are_appended_once(self, tmp_path):
        self._show_with_guide(6)
        runner = _run(tmp_path)
        (prompt,) = _prompts(tmp_path)
        assert "add at most 5 short lines, one fact each" in prompt
        meta = storage.load_project_metadata("Runner Show")
        assert meta["context_guide"] == ("A quiet drama.\n\n## Story so far\n"
                                         "- Mira, the station clerk, is Johnny's sister.\n"
                                         "- The story moves to the harbour town.")     # the repeated fact once
        assert meta["guide_growth_episodes"] == 0
        assert "2 addition(s) to the context guide" in _log(runner)
        assert not list((tmp_path / "agent_tasks").rglob("guide_additions.txt"))

    def test_earlier_episodes_only_count(self, tmp_path):
        self._show_with_guide(2)
        _run(tmp_path)
        (prompt,) = _prompts(tmp_path)
        assert "add at most" not in prompt
        meta = storage.load_project_metadata("Runner Show")
        assert meta["context_guide"] == "A quiet drama." and meta["guide_growth_episodes"] == 3

    def test_gaps_filled_into_a_translated_episode_do_not_count(self, tmp_path):
        self._show_with_guide(4)
        _run(tmp_path)                                  # the whole episode: 5
        cues = storage.load_episode_data("Runner Show", "S01E01")
        for c in cues[:2]:
            c["translations"], c["translated"] = {}, ""
        storage.save_episode("Runner Show", "S01E01", cues)
        TranslationQueue().enqueue(project_name="Runner Show", episode_name="S01E01", priority=PRIORITY_MANUAL,
                                   kind=KIND_TRANSLATION)
        _run(tmp_path)
        assert storage.load_project_metadata("Runner Show")["guide_growth_episodes"] == 5

    def test_additions_skip_duplicates_and_stop_at_the_cap(self, tmp_path):
        storage.create_project("Cap Show", {"show_name": "Cap Show", "target_language": "Greek"})
        service = AgentTaskService(tasks_dir=tmp_path / "tasks")
        _set_meta("Cap Show", context_guide="Johnny is Mira's brother.\n" + "x" * 5950)
        assert service.append_guide_additions("Cap Show", ["- Johnny is Mira's brother!", "tiny"]) == 0   # dup, too short
        assert service.append_guide_additions("Cap Show", ["Mira runs the station kiosk for years."]) == 0   # full
        _set_meta("Cap Show", context_guide="Short guide.")
        assert service.append_guide_additions("Cap Show", ["Mira runs the station kiosk.", "A" * 400]) == 2
        assert service.append_guide_additions("Cap Show", ["Boris is the mayor of town."]) == 1
        guide = storage.load_project_metadata("Cap Show")["context_guide"]
        assert guide.count("## Story so far") == 1 and guide.endswith("- Boris is the mayor of town.")
        assert len(guide) <= 6000
        assert service.append_guide_additions("Nobody", ["Some fact that is long enough."]) == 0


# ===========================================================================
# 7. Defaults and "Pause translations"
# ===========================================================================

class TestEngineDefaults:

    def test_the_engine_is_agent_unless_set(self):
        from routers.schemas import SettingsRequest
        assert SettingsRequest().translation_engine == "agent"
        assert storage.load_global_config()["translation_engine"] == "agent"
        assert agy_llm.agent_mode() is True
        storage.save_global_config({"translation_engine": "model"})
        assert agy_llm.agent_mode() is False

    def test_the_runner_starts_on_boot_with_the_default_engine(self, tmp_path, monkeypatch):
        runner = _runner(tmp_path)
        monkeypatch.setattr(agent_runner, "_runner", runner)
        started = []
        monkeypatch.setattr(runner, "start", lambda cfg=None, resumed=False: started.append((cfg, resumed)))
        assert agent_runner.autostart() is True
        assert started[0][0]["watch"] is True and started[0][1] is False

    def test_boot_autostart_can_be_turned_off(self, tmp_path, monkeypatch):
        runner = _runner(tmp_path)
        monkeypatch.setattr(agent_runner, "_runner", runner)
        storage.save_global_config({"agent_runner_autostart": False})
        monkeypatch.setattr(runner, "start", lambda *a, **k: pytest.fail("started"))
        assert agent_runner.autostart() is False

    def test_pause_translations_stops_claiming_but_finishes_the_episode_in_hand(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_runner, "PAUSE_CACHE_SECONDS", 0)
        monkeypatch.setenv("FAKE_AGY_SLEEP", "1.5")
        first, second = _show(episodes=("S01E01", "S01E02"))
        runner = _runner(tmp_path)
        runner.start({"lanes": 1, "watch": True})
        assert _wait(lambda: len(_prompts(tmp_path)) >= 1)             # the first episode is in the CLI's hands
        storage.save_global_config({"queue_worker_paused": True})
        assert _wait(lambda: TranslationQueue().get_item(first)["status"] == "completed")   # ... and finishes
        assert _wait(lambda: runner.info()["status"] == "waiting")
        info = runner.info()
        assert info["paused"] is True and "paused" in info["message"].lower()
        time.sleep(1.0)
        assert TranslationQueue().get_item(second)["status"] == "pending"   # nothing new was claimed
        assert len(_prompts(tmp_path)) == 1

        monkeypatch.setenv("FAKE_AGY_SLEEP", "0")
        storage.save_global_config({"queue_worker_paused": False})
        runner.nudge()
        assert _wait(lambda: TranslationQueue().get_item(second)["status"] == "completed", timeout=60)
        runner.stop(now=True)
        _join(runner)
