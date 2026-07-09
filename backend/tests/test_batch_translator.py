"""Batch translator v2 (D3 parity rebuild) — network-free behavior tests.

Covers scene-response mapping via the request index_map, gap detection/flagging,
the >20%-missing re-pend gate, and the typed InlinedRequest payload carrying the
system instruction + schema + thinking config.
"""

from services.batch_translator import BatchTranslator, _ItemPlan
from services.prompt_assembler import TranslationRequest


def _rows(n):
    return [{"original": f"line {i}", "translated": "", "translations": {}} for i in range(n)]


def _req(start, count, structured=False):
    return TranslationRequest(
        system_instruction="SYS", prompt="P", response_schema=None,
        expected_count=count, structured=structured,
        index_map=list(range(start, start + count)),
    )


def test_apply_scene_maps_by_index_with_offset():
    rows = _rows(6)
    plan = _ItemPlan(item={"id": "x", "project_name": "TP", "episode_name": "E01"},
                     rows=rows, target_code="el", is_primary=True, n_scenes=2)
    # Scene 2 covers rows 3..5.
    req = _req(start=3, count=3)
    text = "1| alpha\n2| beta\n3| gamma"
    BatchTranslator._apply_scene(plan, req, text)
    assert rows[3]["translated"] == "alpha"
    assert rows[5]["translations"]["el"] == "gamma"
    assert rows[0]["translated"] == ""  # outside the scene, untouched
    assert plan.scenes_applied == 1


def test_finalize_flags_gap_lines():
    rows = _rows(5)
    for i in range(4):  # translate 4/5 -> one gap (20%, still ok)
        rows[i]["translations"] = {"el": f"t{i}"}
        rows[i]["translated"] = f"t{i}"
    plan = _ItemPlan(item={"id": "x"}, rows=rows, target_code="el", is_primary=True, n_scenes=1)
    stats = BatchTranslator._finalize_item(plan)
    assert stats["gap_count"] == 1
    assert stats["review_count"] == 1
    assert stats["ok"] is True
    assert rows[4]["needs_review"] is True
    assert "batch API gap" in rows[4]["review_issues"]


def test_finalize_repends_when_mostly_missing():
    rows = _rows(10)
    rows[0]["translations"] = {"el": "t0"}
    rows[0]["translated"] = "t0"
    plan = _ItemPlan(item={"id": "x"}, rows=rows, target_code="el", is_primary=True, n_scenes=1)
    stats = BatchTranslator._finalize_item(plan)
    assert stats["ok"] is False
    assert stats["gap_count"] == 9


def test_finalize_ignores_blank_source_lines():
    rows = _rows(3)
    rows[1]["original"] = "   "  # blank cue never counts as a gap
    rows[0]["translations"] = {"el": "a"}
    rows[2]["translations"] = {"el": "c"}
    plan = _ItemPlan(item={"id": "x"}, rows=rows, target_code="el", is_primary=True, n_scenes=1)
    stats = BatchTranslator._finalize_item(plan)
    assert stats["gap_count"] == 0
    assert stats["ok"] is True


def test_inline_request_carries_system_instruction_and_thinking():
    from utils.structured_output import SceneTranslationResponse
    req = TranslationRequest(
        system_instruction="SYS RULES", prompt="translate me",
        response_schema=SceneTranslationResponse, expected_count=1,
        structured=True, index_map=[0],
    )
    inline = BatchTranslator._to_inline_request(req, "gemini-2.5-flash")
    assert inline.config.system_instruction == "SYS RULES"
    # The SDK normalizes dict contents into typed Content objects.
    assert inline.contents[0].parts[0].text == "translate me"
    assert inline.config.response_mime_type == "application/json"
    # D2: translation defaults to thinking off on flash models.
    assert inline.config.thinking_config is not None
    assert inline.config.thinking_config.thinking_budget == 0
