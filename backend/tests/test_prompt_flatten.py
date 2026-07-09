"""Source cues are flattened to a single line before translation.

A two-line source cue fed to the model as ``<br>`` made it emit two numbered
entries (split-and-shift). We now send flat text and ask for one line back;
wrapping is reapplied later by the SubtitleEdit /splitlonglines (or conformance)
pass.
"""

from adk_agents.translator_agent import (
    _flatten_cue,
    build_translation_prompt,
    build_translator_instruction,
)


def test_flatten_cue_collapses_breaks_and_whitespace():
    assert _flatten_cue("a<br>b") == "a b"
    assert _flatten_cue("a\nb\r\nc") == "a b c"
    assert _flatten_cue("  x   y  ") == "x y"
    assert _flatten_cue("") == ""


def test_multiline_source_cue_is_flattened_in_prompt():
    prompt = build_translation_prompt(
        ["Previously, we were forced to\nretreat by the party of heroes."],
        "Greek",
    )
    assert "1| Previously, we were forced to retreat by the party of heroes." in prompt
    assert "<br>" not in prompt          # no break markers handed to the model
    assert prompt.count("\n1|") <= 1      # exactly one numbered entry for one cue


def test_instructions_drop_br_preservation_and_require_single_line():
    for structured in (True, False):
        instr = build_translator_instruction("Greek", {"terms": []}, "", structured=structured)
        assert "Preserve <br>" not in instr
        assert "ONE LINE PER CUE" in instr


def test_instructions_include_second_person_number_register_rule():
    for structured in (True, False):
        instr = build_translator_instruction("Greek", {"terms": []}, "", structured=structured)
        assert "SECOND PERSON" in instr
        # Biases toward singular-familiar and names the target language explicitly.
        assert "SINGULAR" in instr
        assert "Greek" in instr
