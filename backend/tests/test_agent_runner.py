"""
Direct agent runner
===================
The runner translates queued episodes through an agent CLI with one prompt per episode (or
part), all context inline, and submits through the normal agent path. These tests drive it
with ``fake_agent_cli.py`` (a stand-in for Antigravity's ``agy``: no network, no quota) and
pin the behaviour that makes it safe to leave running: replies that lose their place are put
back in order, quota stops switch to the fallback model, safety refusals go to another model
family, and only one Omnisub process runs the queue at a time.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from utils import event_broadcaster, glossary_judgment, storage
from utils.translation_queue import TranslationQueue, KIND_EXTRACTION, KIND_TRANSLATION, PRIORITY_MANUAL
from services.agent_task_service import AgentTaskService
from services import agent_cli, agent_prompts as ap, agent_runner, agent_workspace as aw
from services.agent_runner import AgentRunner, clean_config

FAKE = [sys.executable, str(Path(__file__).with_name("fake_agent_cli.py"))]

LINES = [
    "Where were you last night?", "I was at the station, waiting for Johnny.", "He never came.",
    "That's not like him at all.", "I know. I called him three times.", "Did he answer?",
    "No. His phone was off.", "We should go to his place.", "It's late. Let's wait until morning.",
    "Fine, but I'm not sleeping.", "Neither am I.", "Look! There's someone at the door.",
]


@pytest.fixture(autouse=True)
def empty_queue():
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    yield
    wipe()


@pytest.fixture(autouse=True)
def no_bootstrap(monkeypatch):
    """The show-context request before a first episode is its own test class; elsewhere it would add a prompt
    to every count (``bootstrap=True`` turns it on for a run)."""
    monkeypatch.setitem(agent_runner.DEFAULT_CONFIG, "bootstrap", False)


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    for var in ("FAKE_AGY_SKIP_AT", "FAKE_AGY_QUOTA_MODELS", "FAKE_AGY_BLOCK_MODELS", "FAKE_AGY_BLOCK_WORD",
                "FAKE_AGY_CUT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FAKE_AGY_LOG", str(tmp_path / "calls"))
    storage.save_global_config({"tm_enabled": False})  # no embedding model in these tests


def _api_answers(prompt):
    """What the Gemini API would answer: like the fake CLI, ``N| Ελ: English`` per line asked."""
    out, inside = [], False
    for line in prompt.splitlines():
        if line.startswith("## "):
            inside = (line.startswith("## Lines to translate") or line.startswith("## Lines to fix")
                      or line.startswith("## Line to translate"))
            continue
        m = re.match(r"^(\d+)\| (.*)$", line) if inside else None
        if m:
            out.append(f"{m.group(1)}| Ελ: {m.group(2)}")
    return "\n".join(out)


@pytest.fixture(autouse=True)
def gemini_api(monkeypatch):
    """The refused-lines rescue never reaches the real Gemini API: by default it answers like an API
    that cannot be reached; ``gemini_api["answer"] = True`` makes it translate. Calls are recorded."""
    state = {"answer": False, "calls": []}

    def fake(self, model, prompt, project, episode):
        state["calls"].append({"model": model, "prompt": prompt})
        if not state["answer"]:
            return agent_cli.Reply(failure="error", message="no Gemini API in tests")
        return agent_cli.Reply(text=_api_answers(prompt), seconds=0.01)

    monkeypatch.setattr(AgentRunner, "_api_call", fake)
    return state


def _cues(lines, start=1000):
    cues, t = [], start
    for i, text in enumerate(lines):
        a, b = t, t + 1800
        cues.append({"index": i + 1, "start": f"00:{a // 60000:02d}:{a // 1000 % 60:02d},{a % 1000:03d}",
                     "end": f"00:{b // 60000:02d}:{b // 1000 % 60:02d},{b % 1000:03d}", "original": text})
        t = b + 300
    return cues


def _show(name="Runner Show", episodes=("S01E01",), lines=LINES):
    storage.create_project(name, {"show_name": name, "target_language": "Greek"})
    ids = []
    for ep in episodes:
        storage.save_episode(name, ep, _cues(lines))
        ids.append(TranslationQueue().enqueue(project_name=name, episode_name=ep, priority=PRIORITY_MANUAL,
                                              kind=KIND_TRANSLATION))
    return ids


def _run(tmp_path, **config):
    service = AgentTaskService(tasks_dir=tmp_path / "agent_tasks")
    runner = AgentRunner(service=service, exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")
    runner.start({"lanes": 1, **config})
    for t in list(runner.threads):
        t.join(timeout=120)
    assert not runner.running(), "the runner did not finish"
    return runner


def _greek(project="Runner Show", episode="S01E01"):
    return [(c["original"], (c.get("translations") or {}).get("el")) for c in storage.load_episode_data(project, episode)]


def _log(runner):
    return "\n".join(e["text"] for e in runner.info()["log"])


def _prompts(tmp_path):
    return [p.read_text(encoding="utf-8") for p in sorted((tmp_path / "calls").glob("call_*.txt"))]


def _runner(tmp_path):
    return AgentRunner(service=AgentTaskService(tasks_dir=tmp_path / "agent_tasks"),
                       exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")


def _join(runner, timeout=60):
    for t in list(runner.threads):
        t.join(timeout=timeout)
    assert not runner.running(), "the runner did not finish"


def _wait(condition, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.05)
    return False


def _record_events(monkeypatch):
    """Everything the runner and the service broadcast, as (type, data)."""
    events = []
    monkeypatch.setattr(event_broadcaster, "broadcast_event", lambda kind, data=None: events.append((kind, dict(data or {}))))
    return events


def _status_events(events):
    return [(d["status"], d["message"]) for kind, d in events if kind == "agent_runner"]


# ===========================================================================
# 1. Prompts and replies
# ===========================================================================

class TestReplies:

    def test_parse_reply_reads_answers_summary_guide_and_terms(self):
        reply = "\n".join([
            "Here you go:", "```", "1| Πού ήσουν;", "2| Στον σταθμό.", "7| stray", "```",
            "S| Ο Τζόνι χάνεται.", "S| Τον ψάχνουν.", "G| Δράμα.",
            "+ Johnny | Τζόνι | character | masculine", "+ Load | Φόρτωση | other | feminine | the menu command",
        ])
        parsed = ap.parse_reply(reply, allowed={1, 2})
        assert parsed["answers"] == {1: "Πού ήσουν;", 2: "Στον σταθμό."}
        assert parsed["extra"] == 1
        assert parsed["summary"] == "Ο Τζόνι χάνεται. Τον ψάχνουν."
        assert parsed["guide"] == "Δράμα."
        assert parsed["terms"][0] == {"term": "Johnny", "translation": "Τζόνι", "type": "character", "gender": "masculine"}
        assert parsed["terms"][1]["sense"] == "the menu command"

    def test_two_line_cue_answered_on_two_lines_keeps_its_second_line(self):
        reply = "3| - Έρχεσαι;\n- Όχι.\n4| Καλά.\nThanks for reading!"
        parsed = ap.parse_reply(reply, allowed={3, 4}, two_line={3})
        assert parsed["answers"] == {3: "- Έρχεσαι;\n- Όχι.", 4: "Καλά."}

    def test_escaped_line_breaks_round_trip(self):
        assert ap.unescape(ap.escape("- Ready?\n- Yes.")) == "- Ready?\n- Yes."
        assert ap.parse_reply("1| Έτοιμος;\\nΝαι.", allowed={1})["answers"][1] == "Έτοιμος;\nΝαι."

    def test_write_answers_replaces_entries_and_can_clear(self):
        content = "# head\n1| Hello\nthere\n2| Bye\n"
        out = aw.write_answers(content, {1: "Γεια\nσου"})
        assert aw.parse_work_file(out)[0] == {1: "Γεια\nσου", 2: "Bye"}
        cleared = aw.write_answers(out, {2: ""}, clear=True)
        assert aw.parse_work_file(cleared)[0] == {1: "Γεια\nσου", 2: ""}

    def test_long_episodes_split_at_scene_changes(self):
        units = []
        t = 0
        for i in range(1, 101):
            gap = 8000 if i in (35, 70) else 500
            t += gap
            units.append(aw.Unit(id=i, kind="dialogue", text=f"line {i}", start_ms=t, end_ms=t + 1000))
            t += 1000
        units.append(aw.Unit(id=101, kind="sign", text="CHAPTER ONE"))
        chunks = AgentRunner._chunks(units, 40)
        assert [len(c) for c in chunks] == [34, 35, 32]
        assert chunks[-1][-1].kind == "sign"
        assert [u.id for c in chunks for u in c] == list(range(1, 102))

    def test_clean_config_bounds_and_types(self):
        cfg = clean_config({"lanes": "20", "fallback_models": "a, b,", "watch": "true", "nonsense": 1, "fix_rounds": -3})
        assert cfg == {"lanes": 8, "fallback_models": ["a", "b"], "watch": True, "fix_rounds": 0}
        with pytest.raises(ValueError):
            clean_config({"engine": "notepad"})

    def test_rescue_settings_are_cleaned(self):
        assert clean_config({"api_rescue": "false", "rescue_model": " gemini-x "}) == {"api_rescue": False,
                                                                                      "rescue_model": "gemini-x"}

    def test_every_prompt_says_what_the_job_is(self):
        static = ap.static_part("Greek", "", "")
        assert ap.JOB_CONTEXT in static
        assert static.index(ap.JOB_CONTEXT) < static.index("## Answer format")

    def test_set_aside_lines_get_the_translated_lines_around_them(self):
        units = [aw.Unit(id=i, kind="dialogue", text=f"line {i}", start_ms=i * 1000, end_ms=i * 1000 + 500)
                 for i in range(1, 7)]
        cur = {1: "γραμμή 1", 2: "γραμμή 2", 3: "", 4: "γραμμή 4", 5: "", 6: "γραμμή 6"}

        def needs(u, c):
            return not c.get(u.id)

        around = AgentRunner._around_done(units, [units[2], units[4]], cur, needs)
        assert around[3] == ([("line 1", "γραμμή 1"), ("line 2", "γραμμή 2")], [("line 4", "γραμμή 4")])
        assert around[5] == ([("line 2", "γραμμή 2"), ("line 4", "γραμμή 4")], [("line 6", "γραμμή 6")])

    def test_failures_are_classified(self):
        assert agent_cli.classify_failure('AGY_ERROR: {"status": "RESOURCE_EXHAUSTED", "error_code": 429, '
                                          '"message": "quota exhausted"}') == "quota"
        assert agent_cli.classify_failure("blocked by the content safety filter") == "blocked"
        assert agent_cli.classify_failure("Please run /login") == "auth"
        assert agent_cli.classify_failure("HTTP 503 overloaded") == "rate"
        assert agent_cli.parse_wait("Your quota will reset after 1h2m3s.") == 3723


# ===========================================================================
# 2. Running the queue
# ===========================================================================

class TestRunner:

    def test_translates_submits_and_keeps_summary_guide_and_terms(self, tmp_path):
        (qid,) = _show()
        runner = _run(tmp_path)
        assert runner.status == "done"
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        assert TranslationQueue().get_item(qid)["status"] == "completed"
        from utils.episode_summaries import EpisodeSummaryManager
        assert "Τζόνι" in EpisodeSummaryManager("Runner Show").get_summary("S01E01")
        meta = storage.load_project_metadata("Runner Show")
        assert [t["term"] for t in meta["glossary"]["terms"]] == ["Johnny"]
        assert meta["context_guide"].startswith("Μια ήσυχη")
        assert runner.session["episodes"] == 1 and runner.session["lines"] == len(LINES)
        assert len(_prompts(tmp_path)) == 1  # one prompt for the whole episode

    def test_reply_that_skipped_a_line_is_put_back_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_SKIP_AT", "4")
        _show()
        runner = _run(tmp_path)
        assert all(t == f"Ελ: {s}" for s, t in _greek())  # every answer on its own line
        assert "lost its place at line 4" in _log(runner)
        prompts = _prompts(tmp_path)
        assert len(prompts) == 2 and "## Lines to fix" in prompts[1]

    def test_used_up_quota_pauses_the_group_and_uses_the_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_QUOTA_MODELS", "gemini")
        _show()
        runner = _run(tmp_path)
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        assert "gemini-3.8-flash-low" in runner.pauses
        assert all(p.startswith("model: claude-sonnet-5-5-low") for p in _prompts(tmp_path))
        saved = json.loads((tmp_path / "state" / "agent_runner.json").read_text(encoding="utf-8"))
        assert "gemini-3.8-flash-low" in saved["pauses"]  # pauses survive a restart

    def test_quota_error_without_a_report_still_switches_models(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_QUOTA_MODELS", "gemini")
        monkeypatch.setattr(agent_cli, "antigravity_quota", lambda exe: None)  # no quota report available
        _show()
        runner = _run(tmp_path)
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        assert runner.session["quota_hits"] == 1
        assert "reset time from the error" in _log(runner)

    def test_safety_refusal_goes_to_another_model_family(self, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "phone")
        _show()
        runner = _run(tmp_path)
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        log = _log(runner)
        assert len(gemini_api["calls"]) == 1 and "the Gemini API did not translate" in log  # tried first
        assert "trying claude-sonnet-5-5-low" in log

    def test_refused_part_is_set_aside_and_sent_to_the_gemini_api_at_the_end(self, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "phone")
        gemini_api["answer"] = True
        (qid,) = _show()
        runner = _run(tmp_path, rescue_model="gemini-test-model")
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        assert TranslationQueue().get_item(qid)["status"] == "completed"
        log = _log(runner)
        assert "set aside" in log and f"sending the {len(LINES)} refused line(s)" in log
        assert "retrying once" not in log and "splitting" not in log  # no crawl through the refusal
        assert len(_prompts(tmp_path)) == 1  # the CLI saw the episode once; the API took the refused part
        (call,) = gemini_api["calls"]
        assert call["model"] == "gemini-test-model"
        assert f"## Lines to translate ({len(LINES)})" in call["prompt"] and "## About this job" in call["prompt"]

    def test_lines_that_came_through_before_the_filter_cut_in_are_kept(self, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "phone")
        monkeypatch.setenv("FAKE_AGY_CUT", "1")
        gemini_api["answer"] = True
        _show()
        runner = _run(tmp_path)
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        assert "cut the reply off after 6 line(s); keeping the first 5" in _log(runner)
        (call,) = gemini_api["calls"]
        assert "## Lines to translate (7)" in call["prompt"]  # line 6 (perhaps cut mid-line) to 12

    def test_without_a_gemini_api_key_refused_lines_go_to_the_other_family_at_the_end(self, tmp_path, monkeypatch,
                                                                                      gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "phone")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        _show()
        runner = _run(tmp_path)
        assert all(t == f"Ελ: {s}" for s, t in _greek())
        assert gemini_api["calls"] == []
        assert f"sending the {len(LINES)} refused line(s) of Runner Show — S01E01 to claude-sonnet-5-5-low" in _log(runner)
        assert [p.splitlines()[0] for p in _prompts(tmp_path)] == ["model: gemini-3.8-flash-low",
                                                                   "model: claude-sonnet-5-5-low"]

    def test_rescue_switched_off_narrows_the_refusal_down_on_the_spot(self, tmp_path, monkeypatch, gemini_api):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "phone")
        _show()
        runner = _run(tmp_path, api_rescue=False, fallback_models=[])
        assert gemini_api["calls"] == [] and "set aside" not in _log(runner)
        greek = dict(_greek())
        assert greek["No. His phone was off."] is None
        assert all(t == f"Ελ: {s}" for s, t in greek.items() if "phone" not in s)

    def test_refused_line_without_a_fallback_stays_a_gap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_BLOCK_MODELS", "gemini")
        monkeypatch.setenv("FAKE_AGY_BLOCK_WORD", "phone")
        (qid,) = _show()
        runner = _run(tmp_path, fallback_models=[])
        greek = dict(_greek())
        assert greek["No. His phone was off."] is None  # never saved as English
        assert all(t == f"Ελ: {s}" for s, t in greek.items() if "phone" not in s)
        assert TranslationQueue().get_item(qid)["status"] == "completed"
        meta = storage.load_episode_metadata("Runner Show", "S01E01")
        assert meta["translation_gaps"]["dialogue"] == 1  # the audit will find it
        assert "stays untranslated" in _log(runner)

    def test_stops_after_the_requested_number_of_episodes(self, tmp_path):
        first, second = _show(episodes=("S01E01", "S01E02"))
        runner = _run(tmp_path, max_episodes=1)
        assert runner.status == "stopped" and "requested 1" in runner.message
        assert TranslationQueue().get_item(first)["status"] == "completed"
        assert TranslationQueue().get_item(second)["status"] == "pending"

    def test_episode_continues_with_story_so_far_and_gap_context(self, tmp_path):
        _show(episodes=("S01E01",))
        _run(tmp_path)
        # S01E02 is already translated except two lines: they get the translated lines around them
        cues = _cues(LINES)
        for i, c in enumerate(cues):
            if i not in (3, 8):
                c["translations"] = {"el": f"Ελ: {c['original']}"}
                c["translated"] = c["translations"]["el"]
        storage.save_episode("Runner Show", "S01E02", cues)
        TranslationQueue().enqueue(project_name="Runner Show", episode_name="S01E02", priority=PRIORITY_MANUAL,
                                   kind=KIND_TRANSLATION)
        _run(tmp_path)
        prompt = _prompts(tmp_path)[-1]
        assert "## Previous episodes" in prompt and "S01E01" in prompt
        assert "Around 1:" in prompt and "He never came. → Ελ: He never came." in prompt
        assert "## Lines to translate (2)" in prompt
        assert "S| " not in prompt.split("## After the lines")[-1]  # no summary from two lines
        assert all(t == f"Ελ: {s}" for s, t in _greek(episode="S01E02"))

    def test_resumes_its_own_half_done_episode_first(self, tmp_path):
        _show()
        service = AgentTaskService(tasks_dir=tmp_path / "agent_tasks")
        claimed = service.prepare_task_workspace(None)
        folder = Path(claimed["workspace_dir"])
        manifest = service._read_manifest(folder)
        manifest["runner"] = {"engine": "antigravity"}
        service._write_manifest(folder, manifest)
        work = folder / aw.WORK_FILE
        work.write_text(aw.write_answers(work.read_text(encoding="utf-8"), {1: "Πού ήσουν χθες βράδυ;"}),
                        encoding="utf-8")
        runner = AgentRunner(service=service, exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")
        runner.start({"lanes": 1})
        for t in list(runner.threads):
            t.join(timeout=120)
        greek = _greek()
        assert greek[0][1] == "Πού ήσουν χθες βράδυ;"  # kept, not asked again
        assert "## Lines to translate (11)" in _prompts(tmp_path)[0]
        assert "(resumed)" in _log(runner)

    def test_only_one_process_runs_the_queue(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        (state / "agent_runner.pid").write_text(json.dumps({"pid": os.getppid()}), encoding="utf-8")
        runner = AgentRunner(service=AgentTaskService(tasks_dir=tmp_path / "agent_tasks"),
                             exe_override={"antigravity": FAKE}, state_dir=state)
        with pytest.raises(RuntimeError, match="another Omnisub process"):
            runner.start({"lanes": 1})
        assert not runner.running()

    def test_empty_queue_finishes_and_disables(self, tmp_path):
        runner = _run(tmp_path)
        assert runner.status == "done" and "queue is empty" in runner.message
        assert runner.enabled is False
        assert not (tmp_path / "state" / "agent_runner.pid").exists()

    def test_api_reports_and_validates(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app
        from services import agent_runner
        runner = AgentRunner(service=AgentTaskService(tasks_dir=tmp_path / "agent_tasks"),
                             exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")
        monkeypatch.setattr(agent_runner, "_runner", runner)
        client = TestClient(app)
        info = client.get("/api/agent/runner").json()
        assert info["status"] == "stopped" and info["config"]["engine"] == "antigravity"
        assert client.post("/api/agent/runner/start", json={"engine": "notepad"}).status_code == 400
        res = client.post("/api/agent/runner/start", json={"lanes": 1}).json()
        for t in list(runner.threads):
            t.join(timeout=60)
        assert res["config"]["lanes"] == 1
        assert client.get("/api/agent/runner").json()["status"] == "done"


# ===========================================================================
# 3. Claiming, progress numbers and status events
# ===========================================================================

class TestClaiming:

    def test_pending_items_come_in_claim_order_without_reading_episodes(self, tmp_path, monkeypatch):
        ids = _show("Order Show", episodes=("E1", "E2", "E3", "E4", "E5", "E6"))
        order = [  # (priority, created_at): the lowest priority number first, then the oldest
            (3, "2026-01-01T00:00:01+00:00"), (1, "2026-01-01T00:00:05+00:00"), (0, "2026-01-01T00:00:09+00:00"),
            (1, "2026-01-01T00:00:03+00:00"), (0, "2026-01-01T00:00:02+00:00"), (2, "2026-01-01T00:00:00+00:00"),
        ]
        q = TranslationQueue()
        with q._conn() as conn:
            for item_id, (priority, created) in zip(ids, order):
                conn.execute("UPDATE translation_queue SET priority = ?, created_at = ? WHERE id = ?",
                             (priority, created, item_id))
            conn.execute("UPDATE translation_queue SET status = 'running' WHERE id = ?", (ids[5],))  # E6 is claimed
            conn.commit()
        q.enqueue("Order Show", "E7", priority=0, kind=KIND_EXTRACTION)  # other kinds of work are not translations
        service = AgentTaskService(tasks_dir=tmp_path / "agent_tasks")
        runner = AgentRunner(service=service, exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")
        heavy = [(i["id"], i["project_name"], i["episode_name"]) for i in service.list_pending_tasks()]
        monkeypatch.setattr(storage, "load_episode_metadata", lambda *a, **k: pytest.fail("episode metadata was read"))
        light = [(i["id"], i["project_name"], i["episode_name"]) for i in runner._pending_items()]
        assert light == heavy
        assert [episode for _, _, episode in light] == ["E5", "E3", "E4", "E2", "E1"]

    def test_run_now_item_is_claimed_before_everything_else(self, tmp_path):
        """Continue / Run Now leaves an item 'queued' at priority 10; the runner takes it first."""
        first, _, third = _show(episodes=("S01E01", "S01E02", "S01E03"))
        q = TranslationQueue()
        assert q.continue_item(third)
        assert q.get_item(third)["status"] == "queued"
        _run(tmp_path, max_episodes=1)
        assert q.get_item(third)["status"] == "completed"
        assert q.get_item(first)["status"] == "pending"

    def test_ignored_show_keeps_only_explicit_requests(self, tmp_path):
        manual, automatic = _show("Ignored Show", episodes=("E1", "E2"))
        q = TranslationQueue()
        with q._conn() as conn:  # a webhook item that was queued before the show was ignored
            conn.execute("UPDATE translation_queue SET priority = 0 WHERE id = ?", (automatic,))
            conn.commit()
        meta = storage.load_project_metadata("Ignored Show")
        meta["translation_priority"] = "ignored"
        storage.save_project_metadata("Ignored Show", meta)
        service = AgentTaskService(tasks_dir=tmp_path / "agent_tasks")
        assert [i["id"] for i in service.list_pending_tasks()] == [manual]
        assert service._claim_from_queue()["id"] == manual
        assert q.get_item(automatic)["status"] == "cancelled"

    def test_claiming_does_not_list_every_pending_episode(self, tmp_path, monkeypatch):
        def boom(self):
            raise AssertionError("list_pending_tasks loads the metadata of every pending episode")
        monkeypatch.setattr(AgentTaskService, "list_pending_tasks", boom)
        first, second = _show(episodes=("S01E01", "S01E02"))
        runner = _run(tmp_path, per_show=1)
        assert runner.status == "done"
        assert TranslationQueue().get_item(first)["status"] == "completed"
        assert TranslationQueue().get_item(second)["status"] == "completed"

    def test_abandoned_claims_are_released_at_most_once_a_minute(self, tmp_path, monkeypatch):
        runner = _runner(tmp_path)
        calls = []
        monkeypatch.setattr(runner.service, "housekeeping", lambda: calls.append(1) or [])
        for _ in range(3):
            assert runner._next_task() is None  # nothing is queued
        assert len(calls) == 1
        runner._swept -= agent_runner.HOUSEKEEPING_SECONDS + 1
        assert runner._next_task() is None
        assert len(calls) == 2

    def test_a_shows_context_is_reused_until_its_glossary_or_guide_changes(self, tmp_path, monkeypatch):
        built = []

        class Counting(glossary_judgment.Matcher):
            def __init__(self, *args, **kwargs):
                built.append(1)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(glossary_judgment, "Matcher", Counting)
        _show(episodes=("S01E01", "S01E02"))
        runner = _runner(tmp_path)

        def context(episode="S01E01", language="Greek"):
            return runner._context("Runner Show", episode, language, [], True, "el", True)

        first, second = context("S01E01"), context("S01E02")
        assert first["matcher"] is second["matcher"] and first["static"] == second["static"]
        assert len(built) == 1
        assert first["story"] == "" and second["whole"] is True  # the per-episode parts are still per episode

        meta = storage.load_project_metadata("Runner Show")  # a new glossary term
        meta["glossary"] = {"terms": [{"term": "Johnny", "translation": "Τζόνι", "type": "character"}]}
        storage.save_project_metadata("Runner Show", meta)
        with_term = context()
        assert with_term["matcher"] is not first["matcher"] and len(built) == 2
        assert [t["term"] for t in with_term["matcher"].find("Where is Johnny?")] == ["Johnny"]
        assert context()["matcher"] is with_term["matcher"] and len(built) == 2

        meta["context_guide"] = "A quiet drama about two friends."  # a context guide
        storage.save_project_metadata("Runner Show", meta)
        with_guide = context()
        assert with_guide["has_guide"] and "A quiet drama about two friends." in with_guide["static"]
        assert not with_term["has_guide"] and "A quiet drama" not in with_term["static"]
        assert len(built) == 3

        french = context(language="French")  # another target language is not served the Greek prompt
        assert "into French" in french["static"] and "into Greek" in with_guide["static"]
        assert len(built) == 4

        runner._shared[("Runner Show", "Greek")]["at"] -= agent_runner.CONTEXT_CACHE_SECONDS + 1
        assert context()["matcher"] is not with_guide["matcher"]  # and it is rebuilt after a few minutes
        assert len(built) == 5


class TestProgress:

    def test_info_reports_queue_length_throughput_and_eta_after_a_run(self, tmp_path):
        _show(episodes=("S01E01", "S01E02", "S01E03"))
        runner = _run(tmp_path, max_episodes=1)
        info = runner.info()
        assert info["pending"] == 2  # the other two are still queued
        assert info["session"]["episodes"] == 1 and info["session"]["lines"] == len(LINES)
        rates = info["throughput"]
        assert rates["active_minutes"] > 0 and rates["lines_per_min"] > 0 and rates["episodes_per_hour"] > 0
        assert rates["lines_per_min"] == pytest.approx(len(LINES) / rates["active_minutes"], rel=0.25)
        assert rates["episodes_per_hour"] == pytest.approx(60 / rates["active_minutes"], rel=0.25)
        assert info["eta_minutes"] == pytest.approx(2 * rates["active_minutes"], abs=0.1)
        for lane in info["lanes"]:  # nothing in hand any more
            assert (lane["progress"], lane["units_done"], lane["units_total"]) == (None, 0, 0)

    def test_api_exposes_the_progress_numbers(self, tmp_path, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app
        _show(episodes=("S01E01", "S01E02"))
        runner = _runner(tmp_path)
        monkeypatch.setattr(agent_runner, "_runner", runner)
        client = TestClient(app)
        assert client.post("/api/agent/runner/start", json={"lanes": 1, "max_episodes": 1}).status_code == 200
        _join(runner, 120)
        body = client.get("/api/agent/runner").json()
        assert body["pending"] == 1
        assert set(body["throughput"]) == {"lines_per_min", "episodes_per_hour", "active_minutes"}
        assert body["throughput"]["lines_per_min"] > 0 and body["eta_minutes"] is not None
        assert body["lanes"] and all({"progress", "units_done", "units_total"} <= set(lane) for lane in body["lanes"])
        assert {"session", "started", "log_seq", "quota", "pauses"} <= set(body)  # what was there stays there

    def test_no_rates_before_the_first_episode_is_done(self, tmp_path):
        _show()
        info = _runner(tmp_path).info()
        assert info["pending"] == 1
        assert info["throughput"] == {"lines_per_min": None, "episodes_per_hour": None, "active_minutes": 0.0}
        assert info["eta_minutes"] is None

    def test_pending_count_is_read_every_few_seconds_and_after_the_runner_changes_the_queue(self, tmp_path):
        _show(episodes=("S01E01",))
        runner = _runner(tmp_path)
        assert runner.info()["pending"] == 1
        storage.save_episode("Runner Show", "S01E02", _cues(LINES))
        TranslationQueue().enqueue(project_name="Runner Show", episode_name="S01E02", priority=PRIORITY_MANUAL,
                                   kind=KIND_TRANSLATION)
        assert runner.info()["pending"] == 1  # cached
        runner._queue_changed()
        assert runner.info()["pending"] == 2

    def test_throughput_and_eta_are_per_active_minute(self, tmp_path):
        runner = _runner(tmp_path)
        runner.session.update(episodes=4, lines=1000)
        runner._active = 600.0  # ten minutes with a lane at work
        rates, eta = runner._rates(time.time(), pending=8)
        assert rates == {"lines_per_min": 100.0, "episodes_per_hour": 24.0, "active_minutes": 10.0}
        assert eta == 20.0  # 8 episodes of 250 lines at 100 lines a minute
        assert runner._rates(time.time(), pending=0)[1] == 0.0

    def test_only_time_with_a_lane_at_work_counts(self, tmp_path, monkeypatch):
        runner = _runner(tmp_path)
        clock = [1000.0]
        monkeypatch.setattr(agent_runner, "time", SimpleNamespace(time=lambda: clock[0]))
        runner.session.update(episodes=2, lines=400)
        runner._work_begin()  # lane 1 starts an episode at t=0
        clock[0] += 10
        runner._work_begin()  # lane 2 joins at t=10: the overlap counts once
        clock[0] += 20
        runner._work_end()    # lane 1 is done at t=30
        clock[0] += 10
        runner._work_end()    # lane 2 is done at t=40
        clock[0] += 3600      # an hour waiting for quota is not work
        rates, _ = runner._rates(clock[0], pending=0)
        assert rates == {"lines_per_min": 600.0, "episodes_per_hour": 180.0, "active_minutes": 0.667}
        runner._work_begin()  # an episode in hand counts as time spent so far
        clock[0] += 20
        assert runner._rates(clock[0], pending=0)[0]["active_minutes"] == 1.0

    def test_a_lane_reports_units_done_and_total_while_it_works(self, tmp_path, monkeypatch):
        _show()
        seen = {}
        real_chunk, real_submit = AgentRunner._chunk, AgentRunner._submit

        def chunk(self, *args, **kwargs):
            seen.setdefault("first_chunk", self.info()["lanes"][0])
            return real_chunk(self, *args, **kwargs)

        def submit(self, *args, **kwargs):
            seen["submit"] = self.info()["lanes"][0]
            return real_submit(self, *args, **kwargs)

        monkeypatch.setattr(AgentRunner, "_chunk", chunk)
        monkeypatch.setattr(AgentRunner, "_submit", submit)
        _run(tmp_path)
        n = len(LINES)
        start, end = seen["first_chunk"], seen["submit"]
        assert (start["units_done"], start["units_total"], start["progress"]) == (0, n, f"0/{n}")
        assert (end["units_done"], end["units_total"], end["progress"]) == (n, n, f"{n}/{n}")
        assert end["episode"] == "S01E01" and end["project"] == "Runner Show"

    def test_progress_advances_part_by_part(self, tmp_path, monkeypatch):
        _show(lines=[f"Line number {i} of one long scene." for i in range(1, 121)])
        seen = []
        real_chunk = AgentRunner._chunk

        def chunk(self, *args, **kwargs):
            lane = self.info()["lanes"][0]
            seen.append((lane["units_done"], lane["units_total"], lane["progress"]))
            return real_chunk(self, *args, **kwargs)

        monkeypatch.setattr(AgentRunner, "_chunk", chunk)
        _run(tmp_path, chunk_lines=40)  # parts of 50, 50 and 20 lines
        assert seen == [(0, 120, "0/120"), (50, 120, "50/120"), (100, 120, "100/120")]

    def test_a_resumed_episode_starts_with_its_finished_lines_counted(self, tmp_path, monkeypatch):
        _show()
        service = AgentTaskService(tasks_dir=tmp_path / "agent_tasks")
        claimed = service.prepare_task_workspace(None)
        folder = Path(claimed["workspace_dir"])
        manifest = service._read_manifest(folder)
        manifest["runner"] = {"engine": "antigravity"}
        service._write_manifest(folder, manifest)
        work = folder / aw.WORK_FILE
        work.write_text(aw.write_answers(work.read_text(encoding="utf-8"),
                                         {1: "Πού ήσουν χθες βράδυ;", 2: "Στον σταθμό."}), encoding="utf-8")
        seen = []
        real_chunk = AgentRunner._chunk

        def chunk(self, *args, **kwargs):
            seen.append(self.info()["lanes"][0])
            return real_chunk(self, *args, **kwargs)

        monkeypatch.setattr(AgentRunner, "_chunk", chunk)
        runner = AgentRunner(service=service, exe_override={"antigravity": FAKE}, state_dir=tmp_path / "state")
        runner.start({"lanes": 1})
        _join(runner, 120)
        assert (seen[0]["units_done"], seen[0]["units_total"], seen[0]["progress"]) == (2, len(LINES), f"2/{len(LINES)}")


class TestStatusEvents:

    def test_a_finished_run_announces_running_then_done_once_each(self, tmp_path, monkeypatch):
        events = _record_events(monkeypatch)
        _show()
        _run(tmp_path)
        assert _status_events(events) == [("running", ""), ("done", "The queue is empty. Runner stopped.")]
        submitted = [d for kind, d in events if kind == "agent_runner_submitted"]
        assert [d["episode_name"] for d in submitted] == ["S01E01"]  # a submit is not a status change

    def test_reaching_the_episode_limit_announces_stopped(self, tmp_path, monkeypatch):
        events = _record_events(monkeypatch)
        _show(episodes=("S01E01", "S01E02"))
        _run(tmp_path, max_episodes=1)
        assert _status_events(events) == [("running", ""), ("stopped", "Finished the requested 1 episode(s).")]

    def test_waiting_for_quota_and_a_graceful_stop_are_announced(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FAKE_AGY_QUOTA_MODELS", "gemini")
        events = _record_events(monkeypatch)
        _show()
        runner = _runner(tmp_path)
        runner.start({"lanes": 2, "fallback_models": []})
        assert _wait(lambda: runner.status == "waiting"), runner.message
        assert "All models are paused" in runner.message
        runner.stop()
        _join(runner)
        statuses = _status_events(events)
        assert [s for s, _ in statuses] == ["running", "waiting", "stopping", "stopped"]
        assert "All models are paused" in statuses[1][1]
        assert statuses[2][1] == "Finishing the episodes in hand, then stopping."
        assert statuses[3][1] == "Stopped after finishing the episodes in hand."

    def test_a_watching_runner_announces_idle_and_running_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_runner, "WATCH_SECONDS", 0.2)
        events = _record_events(monkeypatch)
        runner = _runner(tmp_path)
        runner.start({"lanes": 1, "watch": True})
        assert _wait(lambda: runner.status == "idle")
        _show()  # an episode arrives while it watches
        assert _wait(lambda: runner.session["episodes"] == 1 and runner.status == "idle")
        runner.stop(now=True)
        _join(runner)
        statuses = _status_events(events)
        assert [s for s, _ in statuses] == ["running", "idle", "running", "idle", "stopped"]
        assert statuses[1][1] == "The queue is empty — watching for new episodes."

    def test_starting_again_during_a_graceful_stop_announces_running(self, tmp_path, monkeypatch):
        # an idle lane that does not leave by itself while draining, so "start" meets a live runner
        monkeypatch.setattr(AgentRunner, "_sleep", lambda self, seconds: self.stop_event.wait(seconds))
        events = _record_events(monkeypatch)
        runner = _runner(tmp_path)
        runner.start({"lanes": 1, "watch": True})
        assert _wait(lambda: runner.status == "idle")
        runner.stop()
        assert runner.status == "stopping"
        runner.start()  # changed my mind: keep going
        assert runner.status == "running" and runner.enabled
        runner.stop(now=True)
        _join(runner)
        assert [s for s, _ in _status_events(events)] == ["running", "idle", "stopping", "running", "stopped"]

    def test_an_error_is_announced_once_even_when_two_lanes_fail(self, tmp_path, monkeypatch):
        events = _record_events(monkeypatch)
        monkeypatch.setattr(agent_cli, "call_model",
                            lambda *args, **kwargs: agent_cli.Reply(failure="auth", message="Please run /login"))
        _show(episodes=("S01E01", "S01E02"))
        runner = _run(tmp_path, lanes=2, per_show=2)
        assert runner.status == "error"
        statuses = _status_events(events)
        assert [s for s, _ in statuses] == ["running", "error"]
        assert "not signed in" in statuses[1][1]

    def test_stopping_an_idle_runner_still_answers_with_one_event(self, tmp_path, monkeypatch):
        events = _record_events(monkeypatch)
        runner = _runner(tmp_path)
        runner.stop()
        assert _status_events(events) == [("stopped", "Stopped.")]

    def test_lane_status_updates_are_sent_only_when_they_change_and_never_over_a_final_state(self, tmp_path, monkeypatch):
        events = _record_events(monkeypatch)
        runner = _runner(tmp_path)
        for message in ("back at 10:05", "back at 10:05", "back at 10:20"):
            runner._set_status("waiting", message)
        assert _status_events(events) == [("waiting", "back at 10:05"), ("waiting", "back at 10:20")]
        runner._finish("error", "broken", "error")
        runner._set_status("running", "")
        assert runner.status == "error"
        assert _status_events(events)[2:] == [("error", "broken")]


class TestNudge:
    """A watching runner sleeps a minute on an empty queue; a newly queued translation wakes it."""

    def test_nudge_ends_the_idle_wait(self, tmp_path):
        import threading
        runner = _runner(tmp_path)
        threading.Timer(0.2, runner.nudge).start()
        t0 = time.time()
        runner._idle_sleep(30)
        assert time.time() - t0 < 5

    def test_nudge_does_not_cut_a_quota_pause(self, tmp_path):
        import threading
        runner = _runner(tmp_path)
        threading.Timer(0.1, runner.nudge).start()
        t0 = time.time()
        runner._sleep(1.5)
        assert time.time() - t0 >= 1.4

    def test_enqueue_translation_nudges_a_running_runner(self, monkeypatch):
        from services import queue_service
        calls = []
        monkeypatch.setattr(agent_runner, "_runner", SimpleNamespace(enabled=True, nudge=lambda: calls.append(1)))
        queue_service.enqueue_translation("Nudge Show", "S01E01", PRIORITY_MANUAL, {})
        assert calls == [1]
        monkeypatch.setattr(agent_runner, "_runner", SimpleNamespace(enabled=False, nudge=lambda: calls.append(1)))
        queue_service.enqueue_translation("Nudge Show", "S01E02", PRIORITY_MANUAL, {})
        assert calls == [1]  # a stopped runner is left alone

    def test_enqueue_without_a_runner_does_not_create_one(self, monkeypatch):
        from services import queue_service
        monkeypatch.setattr(agent_runner, "_runner", None)
        queue_service.enqueue_translation("Nudge Show", "S01E03", PRIORITY_MANUAL, {})
        assert agent_runner._runner is None


def test_stored_retired_fallback_model_is_upgraded(tmp_path):
    """Antigravity dropped claude-sonnet-4-6; a runner saved with it uses Sonnet 5.5 instead."""
    state = tmp_path / "state"
    state.mkdir()
    (state / "agent_runner.json").write_text(json.dumps(
        {"config": {"engine": "antigravity", "fallback_models": ["claude-sonnet-4-6", "gemini-3.8-flash-high"]}}),
        encoding="utf-8")
    runner = AgentRunner(service=AgentTaskService(tasks_dir=tmp_path / "t"), exe_override={"antigravity": FAKE},
                         state_dir=state)
    assert runner.config["fallback_models"] == ["claude-sonnet-5-5-low", "gemini-3.8-flash-high"]


def test_quota_is_not_asked_again_when_agy_has_no_quota_command(tmp_path, monkeypatch):
    """A CLI that runs /quota as a prompt spends a request each time: ask once, then stop."""
    calls = []
    monkeypatch.setattr(agent_cli, "antigravity_quota", lambda exe: calls.append(exe) or [])
    runner = AgentRunner(service=AgentTaskService(tasks_dir=tmp_path / "t"), exe_override={"antigravity": FAKE},
                         state_dir=tmp_path / "state")
    assert runner.quota_status(force=True) is None
    assert runner.quota_status(force=True) is None
    assert len(calls) == 1 and runner.quota_unsupported


def test_antigravity_quota_recognises_a_prompt_reply(monkeypatch):
    reply = '{"conversation_id":"x","status":"SUCCESS","response":"","usage":{"total_tokens":29728}}'
    monkeypatch.setattr(agent_cli.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=reply + "\n"))
    assert agent_cli.antigravity_quota(["agy"]) == []
