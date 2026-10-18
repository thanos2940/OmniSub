"""
Agent-assisted translation
==========================
External AI agents stand in for the translation model: Omnisub claims a queue item,
builds a workspace (brief + work file of units), the agent replaces the English, and
submission validates, ingests and exports. These tests pin that contract: unit
building (karaoke/drawing skipped, repeated signs merged), gap mode, the quality
gate, claim persistence/expiry, and completion that respects explicit requests.
"""

import json
import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from utils import storage
from utils.translation_queue import TranslationQueue, KIND_TRANSLATION, PRIORITY_MANUAL
from services.agent_task_service import AgentTaskService, agent_task_service
from services import agent_workspace as aw


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def service(tmp_path):
    return AgentTaskService(tasks_dir=tmp_path / "agent_tasks")


@pytest.fixture(autouse=True)
def empty_queue():
    """The queue DB is session-wide and ``next`` claims whatever is pending, so every
    test here starts and ends with an empty queue."""
    def wipe():
        with TranslationQueue()._conn() as conn:
            conn.execute("DELETE FROM translation_queue")
            conn.commit()
    wipe()
    yield
    wipe()


def _project(name, cues, target="Greek", **meta):
    storage.create_project(name, {"show_name": name, "target_language": target, **meta})
    storage.save_episode(name, "S01E01", cues)


def _enqueue(project, episode="S01E01", **opts):
    return TranslationQueue().enqueue(project_name=project, episode_name=episode, priority=PRIORITY_MANUAL,
                                      kind=KIND_TRANSLATION, options=opts or None)


def _row(queue_id):
    return TranslationQueue().get_item(queue_id)


def _fill(ws: Path, mapping: dict):
    """Replace the text of the given unit ids in the work file, like an agent would."""
    out, skip = [], False
    for line in (ws / aw.WORK_FILE).read_text(encoding="utf-8").splitlines():
        m = aw._NUMBERED.match(line)
        if m:
            n = int(m.group(1))
            skip = n in mapping
            out.append(f"{n}| {mapping[n]}" if skip else line)
        elif skip and line.strip() and not line.startswith("#"):
            continue  # drop the English continuation of a replaced entry
        else:
            skip = False if not line.strip() else skip
            out.append(line)
    (ws / aw.WORK_FILE).write_text("\n".join(out) + "\n", encoding="utf-8")


SRT_CUES = [
    {"index": 1, "start": "00:00:01,000", "end": "00:00:03,000", "original": "What are you doing here?"},
    {"index": 2, "start": "00:00:03,500", "end": "00:00:05,000", "original": "I am waiting for you."},
]

ASS_HEADER = """[Script Info]
Title: Test ASS
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1
Style: Signs,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1
Style: OP-ROM,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,8,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# ===========================================================================
# 1. Units, work file, validation (pure logic)
# ===========================================================================

class TestWorkspaceUnits:

    def test_signs_merge_across_frames_dialogue_keeps_repeats(self):
        cues = [
            {"original": "Are you ready?", "_ass": {"kind": "dialogue", "translatable": True, "start_ms": 0, "end_ms": 900, "style": "Default"}},
            {"original": "Currently Disclosed Information", "_ass": {"kind": "sign", "translatable": True, "start_ms": 1000, "end_ms": 1040, "style": "Signs"}},
            {"original": "Currently Disclosed Information", "_ass": {"kind": "sign", "translatable": True, "start_ms": 1040, "end_ms": 1080, "style": "Signs"}},
            {"original": "m 0 0 l 10 0 10 10", "_ass": {"kind": "drawing", "translatable": False, "start_ms": 0, "end_ms": 5, "style": "Signs"}},
            {"original": "♪", "_ass": {"kind": "dialogue", "translatable": True, "start_ms": 2000, "end_ms": 2100, "style": "Default"}},
            {"original": "Are you ready?", "_ass": {"kind": "dialogue", "translatable": True, "start_ms": 9000, "end_ms": 9900, "style": "Default"}},
        ]
        units, stats = aw.build_units(cues, "el")
        assert [(u.kind, u.text, u.cues) for u in units] == [
            ("dialogue", "Are you ready?", [0]),
            ("dialogue", "Are you ready?", [5]),
            ("sign", "Currently Disclosed Information", [1, 2]),
        ]
        assert [u.id for u in units] == [1, 2, 3]
        assert stats["passthrough_cues"] == 2

    def test_gap_mode_keeps_only_missing_or_copied_lines(self):
        cues = [
            {"original": "Hello.", "translated": "Γεια."},
            {"original": "Damn.", "translated": "Damn."},            # copied English
            {"original": "Right!", "translated": ""},                # missing
            {"original": "Eren!", "translated": "Έρεν!"},
        ]
        units, stats = aw.build_units(cues, "el")
        assert [u.text for u in units] == ["Damn.", "Right!"]
        assert stats["already_translated_units"] == 2
        full, _ = aw.build_units(cues, "el", full=True)
        assert len(full) == 4

    def test_fragmented_lyric_style_is_passthrough(self):
        words = ["Like", "the", "scar", "of", "night", "you", "can", "hide", "fear", "truth", "lie", "dear", "star"]
        cues = [{"original": w, "_ass": {"kind": "sign", "translatable": True, "start_ms": i * 10, "end_ms": i * 10 + 5,
                                         "style": "OP-ENG"}} for i, w in enumerate(words * 2)]
        cues.append({"original": "Currently Disclosed Information",
                     "_ass": {"kind": "sign", "translatable": True, "start_ms": 5000, "end_ms": 5040, "style": "Signs"}})
        units, _ = aw.build_units(cues, "el")
        assert [u.text for u in units] == ["Currently Disclosed Information"]

    def test_repeated_letter_filler_is_not_a_unit(self):
        cues = [{"original": "bbbbbbbbbb", "_ass": {"kind": "sign", "translatable": True, "start_ms": 0, "end_ms": 10, "style": "Signs"}},
                {"original": "Wall Maria", "_ass": {"kind": "sign", "translatable": True, "start_ms": 20, "end_ms": 30, "style": "Signs"}}]
        units, _ = aw.build_units(cues, "el")
        assert [u.text for u in units] == ["Wall Maria"]

    def test_work_file_round_trip_with_multiline(self):
        units = [aw.Unit(id=1, kind="dialogue", text="We need to hurry\nto Shiganshina.", cues=[0]),
                 aw.Unit(id=2, kind="sign", text="Syringe", cues=[1])]
        content = aw.render_work_file(units, "T", "Greek")
        parsed, issues = aw.parse_work_file(content)
        assert parsed == {1: "We need to hurry\nto Shiganshina.", 2: "Syringe"}
        assert issues["duplicates"] == []

    def test_looks_untranslated(self):
        assert aw.looks_untranslated("Damn.", "Damn.", "el")
        assert aw.looks_untranslated("Right!", "Right!", "el")
        assert aw.looks_untranslated("Wings of Freedom", "Wings of Freedom ki ki ri ri", "el")
        assert not aw.looks_untranslated("Eren!", "Έρεν!", "el")
        assert not aw.looks_untranslated("...", "...", "el")
        assert aw.looks_untranslated("Hello", "", "el")
        assert not aw.looks_untranslated("Hello", "Bonjour", "fr")
        assert aw.looks_untranslated("Hello", "Hello", "fr")

    def test_validate_blocks_english_and_missing(self):
        units = [aw.Unit(id=i, kind="dialogue", text=f"Line {i} here", cues=[i]) for i in range(1, 5)]
        rep = aw.validate(units, {1: "Γραμμή 1", 2: "Line 2 here", 3: "", 4: "Γραμμή 4"}, "el")
        assert rep["missing"] == [3] and rep["untranslated"] == [2] and not rep["ok"]
        rep = aw.validate(units, {i: f"Γραμμή {i}" for i in range(1, 5)}, "el")
        assert rep["ok"] and rep["translated"] == 4


# ===========================================================================
# 2. Workspace lifecycle
# ===========================================================================

class TestAgentWorkspaceLifecycle:

    def test_prepare_workspace_empty_queue(self, service):
        res = service.prepare_task_workspace(queue_item_id="non_existent_queue_id")
        assert res["success"] is False
        assert res["status"] == "empty"

    def test_full_workspace_and_submit_cycle(self, service):
        _project("AgentTestProject", [dict(c) for c in SRT_CUES])
        queue_id = _enqueue("AgentTestProject")

        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        assert prep["success"] is True and prep["status"] == "claimed"
        ws = Path(prep["workspace_dir"])
        for name in (aw.BRIEF_FILE, aw.WORK_FILE, aw.PLAN_FILE, "manifest.json", "glossary.json", aw.TERMS_FILE):
            assert (ws / name).is_file(), name
        brief = (ws / aw.BRIEF_FILE).read_text(encoding="utf-8")
        assert "AgentTestProject" in brief and "Greek" in brief and "PRO-DROP" in brief
        assert "What are you doing here?" in (ws / aw.WORK_FILE).read_text(encoding="utf-8")

        row = _row(queue_id)
        assert row["status"] == "running"
        assert json.loads(row["options"])["agent_task_id"] == prep["task_id"]

        _fill(ws, {1: "Τι κάνεις εδώ;", 2: "Σε περιμένω."})
        check = service.check_task(prep["task_id"])
        assert check["status"] == "ready"

        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is True and res["status"] == "completed"
        assert res["cues_translated"] == 2
        row = _row(queue_id)
        assert row["status"] == "completed"
        assert "agent_task_id" not in json.loads(row["options"] or "{}")

        saved = storage.load_episode_data("AgentTestProject", "S01E01")
        assert saved[0]["translated"] == "Τι κάνεις εδώ;"
        assert saved[1]["translations"]["el"] == "Σε περιμένω."
        manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed" and manifest["translated_cues"] == 2

        again = service.submit_task_result(prep["task_id"])
        assert again["status"] == "already_completed"

    def test_legacy_output_file_still_accepted(self, service):
        _project("AgentLegacyOut", [dict(c) for c in SRT_CUES])
        prep = service.prepare_task_workspace(queue_item_id=_enqueue("AgentLegacyOut"))
        ws = Path(prep["workspace_dir"])
        (ws / "output_translated.srt").write_text(
            "1\n00:00:01,000 --> 00:00:03,000\nΤι κάνεις εδώ;\n\n2\n00:00:03,500 --> 00:00:05,000\nΣε περιμένω.\n",
            encoding="utf-8")
        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is True and res["cues_translated"] == 2

    def test_submit_rejects_english_lines_and_writes_nothing(self, service):
        _project("AgentGate", [dict(c) for c in SRT_CUES])
        queue_id = _enqueue("AgentGate")
        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        ws = Path(prep["workspace_dir"])
        _fill(ws, {1: "Τι κάνεις εδώ;"})  # line 2 left in English

        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is False and res["status"] == "validation_failed"
        assert res["report"]["untranslated"] == [2]
        assert "I am waiting for you." in res["message"]
        assert not storage.load_episode_data("AgentGate", "S01E01")[0].get("translated")
        assert _row(queue_id)["status"] == "running"

        forced = service.submit_task_result(prep["task_id"], force=True)
        assert forced["success"] is True

    def test_ass_workspace_dedups_signs_skips_karaoke_and_exports_styled(self, service, tmp_path):
        project = "AgentASSTestProject"
        storage.create_project(project, {"show_name": project, "target_language": "Greek"})
        raw_ass = ASS_HEADER + "\n".join([
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\pos(960,820)}Who goes there?",
            "Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,It is only me.",
            "Dialogue: 0,0:00:07.00,0:00:07.04,Signs,,0,0,0,,{\\pos(100,100)}Wall Maria",
            "Dialogue: 0,0:00:07.04,0:00:07.08,Signs,,0,0,0,,{\\pos(101,100)}Wall Maria",
            "Dialogue: 0,0:00:07.08,0:00:07.12,Signs,,0,0,0,,{\\pos(102,100)}Wall Maria",
            "Dialogue: 0,0:00:08.00,0:00:09.00,OP-ROM,,0,0,0,,{\\pos(10,10)}sasageyo",
            "Dialogue: 0,0:00:08.00,0:00:09.00,Signs,,0,0,0,,{\\p1}m 0 0 l 100 0 100 100{\\p0}",
        ]) + "\n"
        storage.save_original_subtitle(project, "S01E01", raw_ass, filename="original.ass")
        media = tmp_path / "Anime" / "Episode1.mkv"
        media.parent.mkdir(parents=True)
        media.touch()
        storage.save_episode_metadata(project, "S01E01", {"arr_media_path": str(media)})

        prep = service.prepare_task_workspace(queue_item_id=_enqueue(project))
        assert prep["success"] is True
        # A positioned line counts as on-screen text; dialogue is listed first.
        assert (prep["dialogue_units"], prep["sign_units"]) == (1, 2)
        ws = Path(prep["workspace_dir"])
        work = (ws / aw.WORK_FILE).read_text(encoding="utf-8")
        assert "1| It is only me." in work and "2| Who goes there?" in work and "3| Wall Maria" in work
        assert "\\pos" not in work and "sasageyo" not in work and "m 0 0" not in work

        _fill(ws, {1: "Εγώ είμαι.", 2: "Ποιος είναι εκεί;", 3: "Τείχος Μαρία"})
        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is True
        assert res["cues_translated"] == 5  # the sign fans out to its three frames

        exported = (media.parent / "Episode1.el.ass").read_text(encoding="utf-8-sig")
        assert "[V4+ Styles]" in exported and "{\\pos(960,820)}Ποιος είναι εκεί;" in exported
        assert exported.count("Τείχος Μαρία") == 3
        assert "sasageyo" in exported and "m 0 0 l 100 0 100 100" in exported

    def test_untranslated_sign_is_kept_not_blanked_on_export(self, service, tmp_path):
        project = "AgentBlankSign"
        storage.create_project(project, {"show_name": project, "target_language": "Greek"})
        raw_ass = ASS_HEADER + "\n".join([
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello there.",
            "Dialogue: 0,0:00:04.00,0:00:06.00,Signs,,0,0,0,,{\\pos(100,100)}Currently Disclosed Information",
        ]) + "\n"
        storage.save_original_subtitle(project, "S01E01", raw_ass, filename="original.ass")
        media = tmp_path / "Show" / "Ep.mkv"
        media.parent.mkdir(parents=True)
        media.touch()
        storage.save_episode_metadata(project, "S01E01", {"arr_media_path": str(media)})
        prep = service.prepare_task_workspace(queue_item_id=_enqueue(project))
        ws = Path(prep["workspace_dir"])
        _fill(ws, {1: "Γεια σου.", 2: "Currently Disclosed Information"})
        assert service.submit_task_result(prep["task_id"], force=True)["success"]
        # force keeps the English sign rather than a blank event
        assert "{\\pos(100,100)}Currently Disclosed Information" in (media.parent / "Ep.el.ass").read_text(encoding="utf-8-sig")

    def test_nothing_to_translate_is_finalized_and_next_moves_on(self, service):
        _project("AgentDoneProj", [{"original": "Hello.", "translated": "Γεια."}])
        done_id = _enqueue("AgentDoneProj")
        res = service.prepare_task_workspace(None)
        assert res["status"] == "empty"
        assert any(f["project_name"] == "AgentDoneProj" for f in res["finalized_without_work"])
        assert _row(done_id)["status"] == "completed"

    def test_episode_changed_since_claim_is_refused(self, service):
        _project("AgentChanged", [dict(c) for c in SRT_CUES])
        prep = service.prepare_task_workspace(queue_item_id=_enqueue("AgentChanged"))
        _fill(Path(prep["workspace_dir"]), {1: "Τι κάνεις εδώ;", 2: "Σε περιμένω."})
        storage.save_episode("AgentChanged", "S01E01", [{"original": "Something else entirely."}, {"original": "And more."}])
        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is False and res["status"] == "episode_changed"

    def test_prepare_target_returns_existing_claim(self, service):
        _project("AgentTargetProj", [dict(c) for c in SRT_CUES])
        first = service.prepare_target_workspace("AgentTargetProj", "S01E01")
        assert first["status"] == "claimed"
        second = service.prepare_target_workspace("agenttargetproj", "1x01")
        assert second["status"] == "already_claimed" and second["task_id"] == first["task_id"]


# ===========================================================================
# 3. Queue routing, claim persistence and expiry
# ===========================================================================

class TestAgentQueueContract:

    def test_worker_never_claims_agent_items(self, service):
        _project("AgentRouteProj", [dict(c) for c in SRT_CUES])
        agent_id = _enqueue("AgentRouteProj", engine="agent", mode="gaps")
        q = TranslationQueue()
        assert q.claim_next(exclude_agent=True) is None or q.get_item(agent_id)["status"] == "pending"
        assert q.claim_next_for_agent()["id"] == agent_id

    def test_claim_survives_restart_reset(self, service):
        _project("AgentRestartProj", [dict(c) for c in SRT_CUES])
        queue_id = _enqueue("AgentRestartProj")
        service.prepare_task_workspace(queue_item_id=queue_id)
        TranslationQueue().reset_orphaned_running()
        assert _row(queue_id)["status"] == "running"

    def test_idle_claim_is_released_after_ttl(self, service, monkeypatch):
        _project("AgentStaleProj", [dict(c) for c in SRT_CUES])
        queue_id = _enqueue("AgentStaleProj")
        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        ws = Path(prep["workspace_dir"])
        old = time.time() - 3 * 3600
        for f in ws.iterdir():
            os.utime(f, (old, old))
        monkeypatch.setattr(AgentTaskService, "_claim_ttl_hours", staticmethod(lambda: 2.0))
        released = service.release_stale_claims()
        assert [r["task_id"] for r in released] == [prep["task_id"]]
        assert _row(queue_id)["status"] == "pending"
        assert json.loads((ws / "manifest.json").read_text(encoding="utf-8"))["status"] == "expired"
        assert service.submit_task_result(prep["task_id"])["status"] == "expired"

    def test_bulk_completion_leaves_explicit_and_claimed_items_alone(self, service):
        _project("AgentIntentProj", [dict(c) for c in SRT_CUES])
        storage.save_episode("AgentIntentProj", "S01E02", [dict(c) for c in SRT_CUES])
        plain = _enqueue("AgentIntentProj", "S01E02")
        explicit = _enqueue("AgentIntentProj", "S01E01", engine="agent", mode="gaps")
        assert service.mark_task_completed("AgentIntentProj", "S01E01") == 0
        assert _row(explicit)["status"] == "pending"
        assert service.mark_task_completed("AgentIntentProj", "S01E02") == 1
        assert _row(plain)["status"] == "completed"

    def test_release_puts_episode_back(self, service):
        _project("AgentReleaseProj", [dict(c) for c in SRT_CUES])
        queue_id = _enqueue("AgentReleaseProj")
        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        assert service.release_agent_task(prep["task_id"]) is True
        assert _row(queue_id)["status"] == "pending"

    def test_audit_finds_copied_english_and_requeues(self, service):
        _project("AgentAuditProj", [{"original": "Hello.", "translated": "Γεια."}, {"original": "Damn.", "translated": "Damn."}])
        storage.save_episode_metadata("AgentAuditProj", "S01E01", {"translated": True})
        res = service.audit_project("AgentAuditProj", requeue=True)
        assert res["incomplete"] == ["S01E01"]
        assert res["enqueue"]["enqueued_count"] == 1
        opts = json.loads(TranslationQueue().find_item("AgentAuditProj", "S01E01")["options"])
        assert opts == {"engine": "agent", "mode": "gaps"}
        assert storage.load_episode_metadata("AgentAuditProj", "S01E01")["translation_gaps"]["dialogue"] == 1


# ===========================================================================
# 4. Context, glossary and continuity ingestion
# ===========================================================================

class TestAgentContextAndContinuityIngestion:

    def test_context_guide_extraction_and_persistence(self, service):
        _project("AgentContextProj", [{"index": 1, "original": "Where are we going?"}])
        prep = service.prepare_task_workspace(queue_item_id=_enqueue("AgentContextProj"))
        ws = Path(prep["workspace_dir"])
        guide = "Kimetsu no Yaiba is set in Taisho-era Japan. Tanjiro is a kind boy fighting demons."
        (ws / "context_guide.md").write_text(f"# Context Guide: AgentContextProj\n\n{guide}\n", encoding="utf-8")
        _fill(ws, {1: "Πού πάμε;"})
        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is True and res["context_guide_updated"] is True
        assert guide in storage.load_project_metadata("AgentContextProj")["context_guide"]

    def test_term_harvesting_with_tonos_dedup_and_update_flag(self, service):
        _project("AgentTermsProj", [{"index": 1, "original": "Tanjiro, watch out!"}],
                 glossary={"terms": [{"term": "Nezuko", "translation": "Νέζουκο", "type": "character"},
                                     {"term": "Hashira", "translation": "Χασίρα", "type": "other"}]})
        prep = service.prepare_task_workspace(queue_item_id=_enqueue("AgentTermsProj"))
        ws = Path(prep["workspace_dir"])
        (ws / "new_terms.json").write_text(json.dumps([
            {"term": "Tanjiro", "translation": "Ταντζίρο", "type": "character"},
            {"term": "Zenitsu", "translation": "Ζενίτσου", "type": "character"},
            {"term": "Nezuko", "translation": "Νετζούκο", "type": "character"},
            {"term": "Hashira", "translation": "Πυλώνας", "type": "other", "update": True},
        ], ensure_ascii=False), encoding="utf-8")
        _fill(ws, {1: "Τάντζιρο, πρόσεχε!"})
        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is True
        assert res["harvested_terms_count"] == 2
        assert res["glossary_updated_terms"] == 1
        assert [c["term"] for c in res["glossary_conflicts"]] == ["Nezuko"]
        saved = {t["term"]: t["translation"] for t in storage.load_project_metadata("AgentTermsProj")["glossary"]["terms"]}
        assert saved["Nezuko"] == "Νέζουκο"
        assert saved["Tanjiro"] == "Τάντζιρο"
        assert saved["Zenitsu"] == "Ζένιτσου"
        assert saved["Hashira"] == "Πυλώνας"

    def test_multi_episode_sequential_continuity_loop(self, service):
        storage.create_project("AgentContinuityProj", {"show_name": "AgentContinuityProj", "target_language": "Greek"})
        storage.save_episode("AgentContinuityProj", "S01E01", [{"index": 1, "original": "Goodbye family."}])
        storage.save_episode("AgentContinuityProj", "S01E02", [{"index": 1, "original": "Sabito, we must go to the mountain."}])
        q1 = _enqueue("AgentContinuityProj", "S01E01")
        q2 = _enqueue("AgentContinuityProj", "S01E02")

        prep_1 = service.prepare_task_workspace(queue_item_id=q1)
        ws_1 = Path(prep_1["workspace_dir"])
        _fill(ws_1, {1: "Αντίο, οικογένεια."})
        (ws_1 / "episode_summary.txt").write_text("Tanjiro leaves home after demons attack his family.", encoding="utf-8")
        (ws_1 / "new_terms.json").write_text(json.dumps([{"term": "Sabito", "translation": "Σάμπιτο", "type": "character"}]),
                                             encoding="utf-8")
        sub_1 = service.submit_task_result(prep_1["task_id"])
        assert sub_1["episode_summary_saved"] is True and sub_1["harvested_terms_count"] == 1

        prep_2 = service.prepare_task_workspace(queue_item_id=q2)
        ws_2 = Path(prep_2["workspace_dir"])
        brief = (ws_2 / aw.BRIEF_FILE).read_text(encoding="utf-8")
        assert "Story so far" in brief and "Tanjiro leaves home after demons attack his family." in brief
        assert "Sabito → Σάμπιτο" in brief
        assert "Sabito" in [t["term"] for t in json.loads((ws_2 / "glossary.json").read_text(encoding="utf-8"))["terms"]]

    def test_context_guide_extraction_edge_cases(self):
        service = AgentTaskService()
        project_name = "Demon Slayer [2026] (Re-mastered)"

        # 1. Empty or whitespace
        assert service._extract_context_guide("", project_name) is None
        assert service._extract_context_guide("   \n\t  ", project_name) is None

        # 2. Boilerplate template with only comments and placeholders -> must return None
        boilerplate = f"""# Context Guide: {project_name}
**Show**: {project_name}
**Target Language**: Greek
**Series Type**: anime

<!-- Context Guide is currently empty for this project. -->
<!-- As an AI Agent, please research/generate a 2-3 paragraph context guide. -->
"""
        assert service._extract_context_guide(boilerplate, project_name) is None

        # 3. Very short substance (< 15 chars) -> must return None
        short_text = f"""# Context Guide: {project_name}
Anime series.
"""
        assert service._extract_context_guide(short_text, project_name) is None

        # 4. Legitimate substantive lore with regex-special project name -> cleanly extracted
        good_guide = f"""# Context Guide: {project_name}
**Show**: {project_name}
**Target Language**: Greek
**Series Type**: anime

Set in Taisho-era Japan, this series follows Tanjiro Kamado as he battles deadly demons and protects his sister Nezuko.
"""
        extracted = service._extract_context_guide(good_guide, project_name)
        assert extracted is not None
        assert "Set in Taisho-era Japan" in extracted
        assert "Context Guide:" not in extracted
        assert "**Show**:" not in extracted

    def test_term_harvesting_normalization_formats(self):
        service = AgentTaskService()

        # Format 1: Wrapper with "discovered_terms"
        wrapper_1 = {
            "discovered_terms": [
                {"term": "Inosuke Hashibira", "translation": "Ινοσούκε Χασιμπίρα", "type": "character"}
            ]
        }
        res_1 = service._normalize_harvested_terms(wrapper_1, target_code="el")
        assert len(res_1) == 1
        assert res_1[0]["term"] == "Inosuke Hashibira"

        # Format 2: Wrapper with "terms"
        wrapper_2 = {
            "terms": [
                {"term": "Giyu Tomioka", "translation": "Γκιγιού Τομιόκα", "type": "character"}
            ]
        }
        res_2 = service._normalize_harvested_terms(wrapper_2, target_code="el")
        assert len(res_2) == 1
        assert res_2[0]["term"] == "Giyu Tomioka"

        # Format 3: Direct key-value dict mapping
        wrapper_3 = {
            "Total Concentration Breathing": "Αναπνοή Απόλυτης Συγκέντρωσης",
            "Nichirin Blade": "Σπαθί Νιτσιρίν"
        }
        res_3 = service._normalize_harvested_terms(wrapper_3, target_code="el")
        assert len(res_3) == 2
        terms_dict = {t["term"]: t["translation"] for t in res_3}
        assert terms_dict["Total Concentration Breathing"] == "Αναπνοή Απόλυτης Συγκέντρωσης"
        # Japanese tonos correction automatically standardizes "Νιτσιρίν" -> "Νίτσιριν"
        assert terms_dict["Nichirin Blade"] == "Σπαθί Νίτσιριν"

        # Format 4: Raw JSON string input
        json_str = json.dumps([
            {"term": "Wisteria Flower", "translation": "Λουλούδι Γλυκίνιας", "type": "plant"}
        ])
        res_4 = service._normalize_harvested_terms(json_str, target_code="el")
        assert len(res_4) == 1
        assert res_4[0]["term"] == "Wisteria Flower"

        # Format 5: Invalid or malformed inputs
        assert service._normalize_harvested_terms("not json at all", target_code="el") == []
        assert service._normalize_harvested_terms(12345, target_code="el") == []
        assert service._normalize_harvested_terms([{"term": "", "translation": ""}], target_code="el") == []
        assert service._normalize_harvested_terms(["invalid_item", None], target_code="el") == []


# ===========================================================================
# 7. Legacy output parsing (parse_agent_translation)
# ===========================================================================

class TestAgentTranslationParsing:
    """Test parsing across JSON, Numbered Lines, and standard SRT."""

    def test_parse_numbered_lines(self):
        service = AgentTaskService()
        raw = """
        1| Γεια σου, πώς είσαι;
        2| Όλα καλά, εσύ;
        3| Μια χαρά, ευχαριστώ.
        """
        parsed = service.parse_agent_translation(raw, expected_count=3)
        assert len(parsed) == 3
        assert parsed[0] == "Γεια σου, πώς είσαι;"
        assert parsed[1] == "Όλα καλά, εσύ;"
        assert parsed[2] == "Μια χαρά, ευχαριστώ."

    def test_parse_json_array(self):
        service = AgentTaskService()
        raw = json.dumps(["Γεια σου", "Κόσμε", "Τέλος"])
        parsed = service.parse_agent_translation(raw, expected_count=3)
        assert len(parsed) == 3
        assert parsed == ["Γεια σου", "Κόσμε", "Τέλος"]

    def test_parse_json_dict_translations(self):
        service = AgentTaskService()
        raw = json.dumps({
            "translations": [
                {"index": 1, "t": "Γραμμή 1"},
                {"index": 2, "t": "Γραμμή 2"}
            ]
        })
        parsed = service.parse_agent_translation(raw, expected_count=2)
        assert len(parsed) == 2
        assert parsed[0] == "Γραμμή 1"
        assert parsed[1] == "Γραμμή 2"

    def test_parse_srt_timestamps(self):
        service = AgentTaskService()
        raw = """1
00:00:01,000 --> 00:00:04,000
Γεια σου φίλε μου.

2
00:00:04,500 --> 00:00:08,000
Τα λέμε σύντομα!
"""
        parsed = service.parse_agent_translation(raw, expected_count=2)
        assert len(parsed) == 2
        assert parsed[0] == "Γεια σου φίλε μου."
        assert parsed[1] == "Τα λέμε σύντομα!"

    def test_parse_plain_lines_fallback(self):
        service = AgentTaskService()
        raw = "Πρώτη γραμμή\nΔεύτερη γραμμή\nΤρίτη γραμμή"
        parsed = service.parse_agent_translation(raw, expected_count=3)
        assert len(parsed) == 3
        assert parsed[0] == "Πρώτη γραμμή"
        assert parsed[2] == "Τρίτη γραμμή"

    def test_parse_empty_or_whitespace(self):
        service = AgentTaskService()
        assert service.parse_agent_translation("", expected_count=3) == []
        assert service.parse_agent_translation("   \n\t  ", expected_count=3) == []

    def test_parse_invalid_json_fallback_rejected(self):
        service = AgentTaskService()
        # Unrecognized JSON or error dump must NOT fall through to plain lines
        err_json = json.dumps({"error": "Internal server error", "status": 500})
        parsed = service.parse_agent_translation(err_json, expected_count=2)
        assert parsed == []

    def test_parse_srt_count_mismatch_rejected(self):
        service = AgentTaskService()
        # SRT with 2 cues when expected count is 20 must return empty list rather than treating timestamps as text
        srt_text = """1
00:00:01,000 --> 00:00:03,000
Γεια σου.

2
00:00:03,500 --> 00:00:05,000
Αντίο.
"""
        parsed = service.parse_agent_translation(srt_text, expected_count=20)
        assert parsed == []

    def test_parse_json_dict_with_lines_key(self):
        service = AgentTaskService()
        raw = json.dumps({"lines": ["Γραμμή 1", "Γραμμή 2"]})
        parsed = service.parse_agent_translation(raw, expected_count=2)
        assert parsed == ["Γραμμή 1", "Γραμμή 2"]

    def test_parse_ass_format(self):
        service = AgentTaskService()
        raw = """[Script Info]
Title: Test
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\pos(960,820)}Γεια σου κόσμε
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Αντίο φίλε
"""
        parsed = service.parse_agent_translation(raw, expected_count=2)
        assert len(parsed) == 2
        assert parsed[0] == "Γεια σου κόσμε"
        assert parsed[1] == "Αντίο φίλε"


# ===========================================================================
# 2. Workspace Preparation & Submission Tests
# ===========================================================================

    def test_parse_markdown_fenced_srt(self):
        service = AgentTaskService()
        raw = """```srt
1
00:00:01,000 --> 00:00:03,000
Γεια σου κόσμε.

2
00:00:04,000 --> 00:00:06,000
Αντίο φίλε.
```"""
        parsed = service.parse_agent_translation(raw, expected_count=2)
        assert len(parsed) == 2
        assert parsed[0] == "Γεια σου κόσμε."
        assert parsed[1] == "Αντίο φίλε."

    def test_parse_markdown_fenced_json(self):
        service = AgentTaskService()
        raw = """```json
[
  "Πρώτη γραμμή",
  "Δεύτερη γραμμή"
]
```"""
        parsed = service.parse_agent_translation(raw, expected_count=2)
        assert len(parsed) == 2
        assert parsed == ["Πρώτη γραμμή", "Δεύτερη γραμμή"]

    def test_parse_numbered_lines_with_multiline_dialogue(self):
        service = AgentTaskService()
        raw = """
        1| Πρώτη γραμμή διαλόγου
        συνέχεια πρώτης γραμμής
        2| Δεύτερη ατάκα
        3| Τρίτη ατάκα
        και ακόμη μία γραμμή εδώ
        """
        parsed = service.parse_agent_translation(raw, expected_count=3)
        assert len(parsed) == 3
        assert parsed[0] == "Πρώτη γραμμή διαλόγου\nσυνέχεια πρώτης γραμμής"
        assert parsed[1] == "Δεύτερη ατάκα"
        assert parsed[2] == "Τρίτη ατάκα\nκαι ακόμη μία γραμμή εδώ"

    def test_parse_numbered_lines_partial_dialogue_sparse_ass(self):
        service = AgentTaskService()
        raw = """
        5| Πέμπτη ατάκα
        12| Δωδέκατη ατάκα
        """
        # Expected count is 1000 (e.g. ASS with many non-dialogue cues)
        parsed = service.parse_agent_translation(raw, expected_count=1000)
        assert len(parsed) == 1000
        assert parsed[4] == "Πέμπτη ατάκα"
        assert parsed[11] == "Δωδέκατη ατάκα"
        assert parsed[0] == ""
        assert parsed[5] == ""


# ===========================================================================
# 5. Router REST API
# ===========================================================================

class TestAgentRouterAPI:

    def test_list_pending_and_active_endpoints(self, client):
        for path in ("/api/agent/tasks/pending", "/api/agent/tasks/active"):
            resp = client.get(path)
            assert resp.status_code == 200
            assert isinstance(resp.json()["tasks"], list)

    def test_claim_next_task_when_empty(self, client):
        with patch.object(agent_task_service, "prepare_task_workspace", return_value={"success": False, "status": "empty"}):
            assert client.post("/api/agent/tasks/next").status_code == 404

    def test_prepare_task_endpoint(self, client):
        mock_res = {"success": True, "task_id": "task_mock_123", "queue_id": "q_123", "workspace_dir": "/tmp/mock_task"}
        with patch.object(agent_task_service, "prepare_task_workspace", return_value=mock_res):
            resp = client.post("/api/agent/tasks/q_123/prepare")
            assert resp.status_code == 200 and resp.json()["task_id"] == "task_mock_123"

    def test_submit_task_endpoint_passes_payload(self, client):
        mock_res = {"success": True, "status": "completed", "task_id": "task_mock_456", "cues_translated": 2,
                    "harvested_terms_count": 1, "context_guide_updated": True, "episode_summary_saved": True}
        with patch.object(agent_task_service, "submit_task_result", return_value=mock_res) as mock_submit:
            payload = {
                "content": "1| Γεια σου\n2| Κόσμε",
                "context_guide": "Updated lore and tone guide",
                "new_terms": [{"term": "Zenitsu", "translation": "Ζενίτσου", "type": "character"}],
                "episode_summary": "Zenitsu awakens his lightning powers.",
                "force": True,
            }
            resp = client.post("/api/agent/tasks/task_mock_456/submit", json=payload)
            assert resp.status_code == 200
            assert resp.json()["harvested_terms_count"] == 1
            mock_submit.assert_called_once_with(
                "task_mock_456", "1| Γεια σου\n2| Κόσμε", "Updated lore and tone guide",
                [{"term": "Zenitsu", "translation": "Ζενίτσου", "type": "character"}],
                "Zenitsu awakens his lightning powers.", True,
            )

    def test_submit_validation_failure_returns_report(self, client):
        failure = {"success": False, "status": "validation_failed", "report": {"untranslated": [2]}, "message": "2 lines"}
        with patch.object(agent_task_service, "submit_task_result", return_value=failure):
            resp = client.post("/api/agent/tasks/task_x/submit", json={})
            assert resp.status_code == 400
            assert resp.json()["report"]["untranslated"] == [2]

    def test_real_http_claim_check_submit(self, client):
        _project("AgentRealRouterProj", [{"index": 1, "original": "Hello friend"}])
        queue_id = _enqueue("AgentRealRouterProj")
        prep = client.post(f"/api/agent/tasks/{queue_id}/prepare").json()
        task_id = prep["task_id"]

        check = client.post(f"/api/agent/tasks/{task_id}/check", json={}).json()
        assert check["status"] == "incomplete" and check["report"]["untranslated"] == [1]

        resp = client.post(f"/api/agent/tasks/{task_id}/submit", json={
            "content": "1| Γεια σου, φίλε",
            "new_terms": [{"term": "Akaza", "translation": "Ακάζα", "type": "character"}],
            "episode_summary": "Akaza appears in the forest.",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True and data["cues_translated"] == 1
        assert data["harvested_terms_count"] == 1 and data["episode_summary_saved"] is True
        assert _row(queue_id)["status"] == "completed"

    def test_sync_tasks_endpoint(self, client):
        resp = client.post("/api/agent/tasks/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "finished_count" in data and "reconciled_queue_items" in data and "released_claims" in data


# ===========================================================================
# 6. Housekeeping & reconciliation
# ===========================================================================

class TestAgentHousekeeping:

    def test_housekeeping_never_ingests_output_files(self, service):
        _project("AutoDetectProj", [dict(c) for c in SRT_CUES])
        queue_id = _enqueue("AutoDetectProj")
        prep = service.prepare_task_workspace(queue_item_id=queue_id)
        ws = Path(prep["workspace_dir"])
        # A half-written output must not be swallowed by a background scan.
        (ws / "output_translated.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\nΚαλημέρα\n", encoding="utf-8")
        assert service.detect_and_finish_completed_tasks() == []
        assert _row(queue_id)["status"] == "running"
        assert not storage.load_episode_data("AutoDetectProj", "S01E01")[0].get("translated")

    def test_reconcile_completes_plain_items_only(self, service):
        _project("ReconcileStorageProj", [{"index": 1, "original": "Hello", "translated": "Γεια"}])
        storage.save_episode("ReconcileStorageProj", "S01E02", [{"index": 1, "original": "Hi"}])
        for ep in ("S01E01", "S01E02"):
            storage.save_episode_metadata("ReconcileStorageProj", ep, {
                "translated": True, "translation_status": "completed", "arr_has_target": True})
        plain = _enqueue("ReconcileStorageProj", "S01E01")
        explicit = _enqueue("ReconcileStorageProj", "S01E02", engine="agent", mode="gaps")
        assert service.reconcile_queue_with_storage() >= 1
        assert _row(plain)["status"] == "completed"
        assert _row(explicit)["status"] == "pending"

    def test_submit_task_from_alternate_candidate_dir(self, tmp_path, monkeypatch):
        primary_dir, secondary_dir = tmp_path / "primary_tasks", tmp_path / "secondary_tasks"
        _project("CandidateDirProj", [{"index": 1, "original": "Hello"}])
        prep = AgentTaskService(tasks_dir=secondary_dir).prepare_task_workspace(queue_item_id=_enqueue("CandidateDirProj"))
        _fill(Path(prep["workspace_dir"]), {1: "Γεια σου"})

        service = AgentTaskService(tasks_dir=primary_dir)
        monkeypatch.setattr(service, "_candidate_dirs", lambda: [primary_dir, secondary_dir])
        res = service.submit_task_result(prep["task_id"])
        assert res["success"] is True and res["cues_translated"] == 1

    def test_save_episode_completes_pending_plain_item(self, client):
        storage.create_project("SaveEpProj", {"show_name": "SaveEpProj", "target_language": "Greek"})
        storage.save_episode("SaveEpProj", "S01E01", [{"index": 1, "original": "Hello"}])
        queue_id = _enqueue("SaveEpProj")
        resp = client.post("/projects/SaveEpProj/episodes/S01E01/save",
                           json={"data": [{"id": "1", "original": "Hello", "translated": "Γεια σας"}]})
        assert resp.status_code == 200
        assert _row(queue_id)["status"] == "completed"


class TestInSceneSignsStayTranslatable:

    @staticmethod
    def _sign(text, start, style):
        return {"original": text, "_ass": {"kind": "sign", "translatable": True, "start_ms": start,
                                           "end_ms": start + 3000, "style": style}}

    def test_map_labels_shown_together_are_translated(self):
        names = ["Trost", "Karanes", "Utopia", "Shiganshina", "Stohess", "Yarckel", "Ehrmich",
                 "Orvud", "Mitras", "Rose", "Maria", "Sina", "Krolva", "Ragako"]
        cues = [self._sign(n, 1000, "MapLabels") for n in names]   # all appear at the same instant
        units, _ = aw.build_units(cues, "el")
        assert len(units) == len(names)

    def test_staggered_lyric_words_are_skipped(self):
        words = ["Why", "did", "the", "earth", "and", "sky", "have", "to", "split", "apart", "world", "cruel", "yet"]
        cues = [self._sign(w, i * 400, "Insert-Words") for i, w in enumerate(words)]
        units, _ = aw.build_units(cues, "el")
        assert units == []

    def test_animated_sign_with_effect_field_is_still_a_sign(self):
        cue = self._sign("Beware of the dog", 0, "Signs")
        cue["_ass"]["effect"] = "Banner;80"
        units, _ = aw.build_units([cue], "el")
        assert [u.text for u in units] == ["Beware of the dog"]


class TestSignFitting:
    """Translated signs must stay inside the frame (export copy only)."""

    HEADER = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Signs,Cardo,42,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,0,0,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

    @staticmethod
    def _export(lines, translations):
        from utils.ass_parser import parse_ass, reconstruct_ass
        raw = TestSignFitting.HEADER + "\n".join(lines) + "\n"
        cues = parse_ass(raw)
        for c, t in zip(cues, translations):
            c["translated"] = t
            c["translations"] = {"el": t}
        return reconstruct_ass(cues, raw, target_lang_code="el")

    def test_long_centered_card_title_is_wrapped(self):
        out = self._export(
            [r"Dialogue: 0,0:00:01.00,0:00:05.00,Signs,,0,0,0,,{\an2\pos(420,90)\fs42}Currently Disclosed Information"],
            ["Πληροφορίες που έχουν δημοσιοποιηθεί προς το παρόν σε όλους"])
        text = out.split(",,", 1)[1].split("\n")[0] if ",,," not in out else out
        assert r"\N" in text

    def test_short_text_is_left_alone(self):
        out = self._export(
            [r"Dialogue: 0,0:00:01.00,0:00:05.00,Signs,,0,0,0,,{\an5\pos(960,500)\fs42}Wall Maria"],
            ["Τείχος Μαρία"])
        assert r"\N" not in out and "fscx" not in out

    def test_stacked_card_lines_are_condensed_not_wrapped_into_each_other(self):
        lines = [
            r"Dialogue: 0,0:00:01.00,0:00:09.00,Signs,,0,0,0,,{\an4\pos(1500,500)\fs42}First card line goes here",
            r"Dialogue: 0,0:00:01.00,0:00:09.00,Signs,,0,0,0,,{\an4\pos(1500,550)\fs42}Second card line goes here",
        ]
        out = self._export(lines, ["Η πρώτη γραμμή της κάρτας είναι αρκετά μεγάλη για να χωρέσει εδώ",
                                   "Η δεύτερη γραμμή της κάρτας είναι κι αυτή πολύ μεγάλη"])
        assert "fscx" in out


class TestMisalignedTranslations:

    def test_whole_card_pasted_into_one_line_counts_as_untranslated(self):
        card = r"Πληροφορίες που έχουν δημοσιοποιηθεί\NΧειμερινή Εκπαίδευση\NΗ ικανότητα να κινείσαι γρήγορα"
        assert aw.looks_untranslated("Winter Training", card, "el")

    def test_normal_translations_are_not_flagged(self):
        assert not aw.looks_untranslated("Hey!", "Ε, εσύ!", "el")
        assert not aw.looks_untranslated("Gunther is...", "Ο Γκούντερ είναι...", "el")
        assert not aw.looks_untranslated("We now begin the 57th\nExterior Scouting Mission!",
                                         r"Ξεκινάμε τώρα την 57η\NΑποστολή Εξερεύνησης!", "el")
