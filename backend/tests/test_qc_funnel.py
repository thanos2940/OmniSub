"""QC funnel (v2 D6) — validator and batched-repair behavior."""

import pytest
from unittest.mock import AsyncMock, patch

from utils.qc_funnel import validate_lines, build_repair_prompt, run_repair_pass


def _line(orig, trans):
    return {"original": orig, "translated": trans, "translations": {"el": trans}}


def test_validator_flags_untranslated_latin_for_greek_target():
    rows = [
        _line("How are you today", "Πώς είσαι σήμερα"),       # fine
        _line("This line was not translated", "This line was not translated"),  # untranslated
    ]
    tickets = validate_lines(rows, "el")
    assert len(tickets) == 1
    assert tickets[0]["index"] == 1
    assert "untranslated" in tickets[0]["issues"][0]


def test_validator_flags_dropped_inline_tags():
    rows = [
        _line("<i>Whispering</i> in the dark", "Ψιθυρίζοντας στο σκοτάδι"),  # tags dropped
        _line("<i>Quiet</i> now", "<i>Ησυχία</i> τώρα"),                      # tags kept
    ]
    tickets = validate_lines(rows, "el")
    assert len(tickets) == 1
    assert tickets[0]["index"] == 0
    assert "tags" in tickets[0]["issues"][0]


def test_validator_skips_latin_targets():
    rows = [_line("Hello there friend", "Hallo daar vriend")]  # Dutch target — Latin is fine
    assert validate_lines(rows, "nl") == []


def test_validator_flags_half_translated_long_line():
    # The screenshot case: a long, complete EN cue split by the whole-episode
    # model so only the first clause survives (ends mid-clause on a comma).
    rows = [
        _line(
            "Even if my Archer-class ability to sustain my life reaches its limits, "
            "given the vast difference in our fighting abilities, it won't even prove "
            "to be a handicap.",
            "Ακόμη κι αν η ικανότητα του Τοξότη να με συντηρεί φτάσει στα όριά της,",
        ),
    ]
    tickets = validate_lines(rows, "el")
    assert len(tickets) == 1
    assert "truncated" in tickets[0]["issues"][0]


def test_validator_does_not_flag_complete_long_translation():
    # A full Greek rendering of the same source must NOT be flagged.
    rows = [
        _line(
            "Even if my Archer-class ability to sustain my life reaches its limits, "
            "given the vast difference in our fighting abilities, it won't even prove "
            "to be a handicap.",
            "Ακόμη κι αν η ικανότητα του Τοξότη να με συντηρεί φτάσει στα όριά της, "
            "δεδομένης της τεράστιας διαφοράς στις ικανότητές μας στη μάχη, δεν θα "
            "αποδειχθεί καν μειονέκτημα.",
        ),
    ]
    assert validate_lines(rows, "el") == []


def test_validator_does_not_flag_short_or_midsentence_source():
    rows = [
        _line("And, Emiya Shirou…", "Και, Έμιγια Σίρου…"),          # short source
        # Long source that itself ends mid-sentence (continues next cue) — a short
        # target here is legitimate, not a truncation.
        _line(
            "Even if my Archer-class ability to sustain my life reaches its limits,",
            "Ακόμη κι αν η ικανότητα του Τοξότη να με συντηρεί,",
        ),
    ]
    assert validate_lines(rows, "el") == []


@pytest.mark.asyncio
async def test_repair_pass_is_one_batched_call_and_applies_fixes():
    rows = [
        _line("This was not translated", "This was not translated"),
        _line("<i>Hi</i> there", "Γεια σου"),
    ]
    tickets = validate_lines(rows, "el")
    assert len(tickets) == 2

    calls = {"n": 0}

    async def fake_generate(model_name, prompt, **kwargs):
        calls["n"] += 1
        assert kwargs.get("role") == "repair"
        # Item numbers from the repair prompt, not global indices.
        return "1| Αυτό δεν μεταφράστηκε\n2| <i>Γεια</i> σου"

    class _NoLimiter:
        async def acquire(self):
            return

    with patch("adk_agents.llm_factory.generate", fake_generate):
        fixed = await run_repair_pass(rows, tickets, "el", "Greek", "el",
                                      "gemini-2.5-flash", _NoLimiter())

    assert calls["n"] == 1          # ONE batched call, never per-line
    assert fixed == 2
    assert rows[0]["translations"]["el"] == "Αυτό δεν μεταφράστηκε"
    assert rows[1]["translations"]["el"] == "<i>Γεια</i> σου"


def test_repair_prompt_contains_sources_and_issues():
    rows = [_line("Stay close", "Stay close")]
    tickets = validate_lines(rows, "el")
    prompt = build_repair_prompt(rows, tickets, "el", "Greek")
    assert "EN: Stay close" in prompt
    assert "ISSUES:" in prompt
    assert "1|" in prompt
