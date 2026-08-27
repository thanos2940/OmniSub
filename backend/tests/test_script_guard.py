"""Script guard — wrong-alphabet detection and the deterministic twin repair.

Every fixture here is a real line taken from the project library, so the split
between "fix for free" and "send to the repair pass" is anchored to what models
(and imported OCR'd targets) actually produce.
"""

import pytest

from utils.script_guard import (
    flag_rows,
    foreign_issues,
    normalize_confusables,
    scrub_rows,
)


# --------------------------------------------------------------------------
# Deterministic repair: visual twins only
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad, good", [
    ("Δεv ξέρω.", "Δεν ξέρω."),                       # Latin v for ν (OCR-style)
    ("Είμαι σε έvα μέρος.", "Είμαι σε ένα μέρος."),
    ("Tόνι, Τόνι...", "Τόνι, Τόνι..."),               # Latin T for Τ
    ("Σοk!", "Σοκ!"),                                  # Latin k for κ
    ("Mπορώ;", "Μπορώ;"),                              # Latin M for Μ
    ("Aλεξάντερ", "Αλεξάντερ"),                        # Latin A for Α
    ("μπορούσáς", "μπορούσάς"),                        # Latin á for ά
    ("παραγκouπόλεις", "παραγκουπόλεις"),              # Latin ou for ου
])
def test_visual_twins_are_repaired_deterministically(bad, good):
    fixed, n = normalize_confusables(bad, "el")
    assert fixed == good
    assert n > 0


@pytest.mark.parametrize("text", [
    "ακούsheτε",        # ακούσετε — 'sh' is a transliteration fragment, not a twin
    "Αλχημistή",        # Αλχημιστή — 'st' likewise
    "ικanοί",           # ικανοί — 'n' is ambiguous (ν or η)
    "μονόκlina",        # 'l' is ambiguous (λ or ι)
    "Χομούνκulus",
    "συντριptιικής",    # 'p' is ambiguous (π or ρ)
    "ήθεlα",
])
def test_ambiguous_contamination_is_left_for_the_repair_pass(text):
    """One unmapped character must escalate the whole token, never half-fix it."""
    fixed, n = normalize_confusables(text, "el")
    assert fixed == text
    assert n == 0
    assert foreign_issues("some source line", text, "el")


@pytest.mark.parametrize("text", [
    "Πάμε στο YouTube και μετά σπίτι.",   # Latin word, no Greek letters in it
    "Δες το Wi-Fi και το DVD.",
    "Ένα καφέ στο Café Müller.",          # Latin-1 accents in a proper noun
    "Ο κύριος Rick Tiegler ήρθε.",
])
def test_latin_words_in_greek_text_are_untouched(text):
    assert normalize_confusables(text, "el") == (text, 0)
    assert foreign_issues("source", text, "el") == []


def test_only_greek_target_has_a_twin_table():
    """Targets without a curated table must never be rewritten by guesswork."""
    text = "Дeн ξέρω"
    assert normalize_confusables(text, "ru") == (text, 0)


# --------------------------------------------------------------------------
# Detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "Ταγματάρχה!",                                  # Hebrew he
    "Ήταν ακριبتώς αυτή τη στιγμή,",                # Arabic
    "Εσύ, Τжон...",                                  # Cyrillic
    "Εσύ έχεις κάποια手がρίο;",                      # CJK + kana
    "Θα μπορούσłeś να πας;",                         # exotic Latin (Polish)
    "μερικές lầnες σε καταλαβαίνουν.",               # exotic Latin (Vietnamese)
])
def test_wrong_script_is_detected(bad):
    issues = foreign_issues("An English source line", bad, "el")
    assert issues, bad
    assert "writing system" in issues[0] or "mixes alphabets" in issues[0]


def test_characters_present_in_the_source_pass_through():
    """Signs, karaoke and quoted foreign text are legitimate, not contamination."""
    src = "巡り巡ってもまたここで逢いたい"
    assert foreign_issues(src, f"{src} (τραγούδι)", "el") == []


def test_clean_greek_is_not_flagged():
    assert foreign_issues("How are you today", "Πώς είσαι σήμερα;", "el") == []


def test_latin_target_language_is_not_second_guessed():
    """No script assertion for a target we have no script model for."""
    assert foreign_issues("Hello there", "Hallo daar vriend", "nl") == []


# --------------------------------------------------------------------------
# Episode-level helpers
# --------------------------------------------------------------------------

def _row(orig, trans):
    return {"original": orig, "translated": trans, "translations": {"el": trans}}


def test_scrub_rows_updates_both_fields_and_is_idempotent():
    rows = [_row("I don't know.", "Δεv ξέρω."), _row("Fine.", "Εντάξει.")]
    assert scrub_rows(rows, "el", "el") == 1
    assert rows[0]["translated"] == "Δεν ξέρω."
    assert rows[0]["translations"]["el"] == "Δεν ξέρω."
    assert scrub_rows(rows, "el", "el") == 0          # nothing left to fix
    assert rows[1]["translated"] == "Εντάξει."        # clean row untouched


def test_scrub_rows_leaves_translated_alone_for_secondary_language():
    """A secondary-language pass must not overwrite the primary 'translated' field."""
    rows = [{"original": "I don't know.", "translated": "Δεν ξέρω.",
             "translations": {"el": "Δεν ξέρω.", "es": "No lo sé."}}]
    scrub_rows(rows, "es", "el")
    assert rows[0]["translated"] == "Δεν ξέρω."


def test_flag_rows_marks_review_and_does_not_duplicate_notes():
    rows = [_row("Major!", "Ταγματάρχה!"), _row("Yes.", "Ναι.")]
    assert flag_rows(rows, "el") == 1
    assert rows[0]["needs_review"] is True
    assert "Wrong-script" in rows[0]["review_issues"]
    assert "needs_review" not in rows[1]

    flag_rows(rows, "el")
    assert rows[0]["review_issues"].count("Wrong-script") == 1


def test_flag_rows_preserves_an_existing_review_reason():
    rows = [_row("Major!", "Ταγματάρχה!")]
    rows[0]["review_issues"] = "Translation missing — batch API gap"
    flag_rows(rows, "el")
    assert "batch API gap" in rows[0]["review_issues"]
    assert "Wrong-script" in rows[0]["review_issues"]
