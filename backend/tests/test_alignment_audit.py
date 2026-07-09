"""Alignment audit — deterministic, zero-token detection of translation/source drift."""

from utils.alignment_audit import audit_alignment, alignment_tickets, flag_drift
from utils.structured_output import _fix_line_count_mismatch


def _line(orig, trans):
    return {"original": orig, "translated": trans, "translations": {"el": trans}}


# --- no false positives -------------------------------------------------------

def test_aligned_digits_produce_no_findings():
    rows = [
        _line("It happened in 1985", "Συνέβη το 1985"),
        _line("He scored 3 goals", "Σκόραρε 3 γκολ"),
        _line("Room 207 is locked", "Το δωμάτιο 207 είναι κλειδωμένο"),
    ]
    assert audit_alignment(rows, "el") == []
    assert alignment_tickets(rows, "el") == []


def test_anchorless_episode_produces_no_findings():
    rows = [
        _line("How are you", "Πώς είσαι"),
        _line("I am fine", "Είμαι καλά"),
        _line("See you soon", "Τα λέμε σύντομα"),
    ]
    assert audit_alignment(rows, "el") == []


def test_latin_target_with_aligned_numbers_is_clean():
    rows = [
        _line("Chapter 12 begins", "Hoofdstuk 12 begint"),
        _line("It cost 50 euros", "Het kostte 50 euro"),
    ]
    assert audit_alignment(rows, "nl") == []


# --- shift detection ----------------------------------------------------------

def test_detects_shift_up_as_negative_delta():
    # "Everything shifted up": the translation assigned to a line actually belongs
    # to the *next* source line, so each correct translation sits one slot EARLIER.
    rows = [
        _line("Year 1985 was cold", "Χρονιά 1990 ζεστή"),   # leaked 1990 (src 1)
        _line("Year 1990 was warm", "Χρονιά 2001 νέα"),     # leaked 2001 (src 2)
        _line("Year 2001 was new", "Χρονιά 2010 άλλη"),     # leaked from beyond
    ]
    findings = audit_alignment(rows, "el")
    assert len(findings) == 1
    f = findings[0]
    assert f["delta"] == -1
    assert set(f["lines"]) >= {1, 2}


def test_detects_shift_down_as_positive_delta():
    # Correct translation sits one slot LATER.
    rows = [
        _line("1985 here", "Κάτι άσχετο"),       # src 0's 1985 leaked forward
        _line("1990 here", "Ήταν το 1985"),      # holds src 0's translation
        _line("2001 here", "Ήταν το 1990"),      # holds src 1's translation
    ]
    findings = audit_alignment(rows, "el")
    assert len(findings) == 1
    assert findings[0]["delta"] == 1
    assert set(findings[0]["lines"]) >= {0, 1}


def test_single_strong_anchor_year_is_enough():
    # One line, no neighbouring votes, but a 4-digit year matched exclusively to a
    # neighbour is conclusive on its own.
    rows = [
        _line("Nothing here", "Τίποτα εδώ"),
        _line("It was 1985 exactly", "Κάτι άλλο χωρίς αριθμό"),  # 1985 missing here
        _line("Nothing there", "Ήταν ακριβώς το 1985"),         # 1985 leaked here
    ]
    findings = audit_alignment(rows, "el")
    assert len(findings) == 1
    assert findings[0]["delta"] == 1
    assert findings[0]["lines"] == [1]


def test_single_weak_anchor_is_not_enough():
    # A lone single-digit coincidence must NOT trip a finding.
    rows = [
        _line("I want 2 of them", "Τα θέλω όλα"),   # "2" dropped (rephrased)
        _line("Give me more", "Δώσε μου 2 ακόμη"),  # coincidental "2"
        _line("Thanks a lot", "Ευχαριστώ πολύ"),
    ]
    assert audit_alignment(rows, "el") == []


def test_aligned_line_breaks_the_shift_run():
    # A correctly-aligned anchored line in the middle splits one span into two
    # separate (here sub-threshold) candidates, so neither survives.
    rows = [
        _line("Year 1985", "Έτος 1990"),   # would vote -1 (1990 at idx1)
        _line("Year 1990", "Έτος 1990"),   # self-aligned (1990 present) -> breaks run
        _line("Year 2001", "Έτος 2010"),   # would vote -1 but isolated
    ]
    findings = audit_alignment(rows, "el")
    # idx0's 1990 is at idx1 (delta -1, single line, weak) and idx2's 2001 absent.
    assert findings == []


# --- glossary verbatim anchors ------------------------------------------------

def test_verbatim_glossary_term_used_as_anchor():
    rows = [
        _line("Sephiroth appears now", "Εμφανίζεται τώρα"),         # name missing
        _line("Then he vanishes", "Ο Sephiroth εξαφανίζεται"),      # name leaked here
    ]
    # Without the glossary anchor there is nothing to key on.
    assert audit_alignment(rows, "el") == []
    findings = audit_alignment(rows, "el", verbatim_terms=["Sephiroth"])
    assert len(findings) == 1
    assert findings[0]["delta"] == 1
    assert findings[0]["lines"] == [0]


# --- ticket / flag surfaces ---------------------------------------------------

def test_tickets_cover_every_line_in_the_span():
    # A clean one-slot "shift up" (each line holds the next line's translation),
    # with an anchorless source line sitting between two same-delta voters. The
    # span — and its ticket coverage — must include that anchorless middle line.
    rows = [
        _line("Hello there friend", "Η χρονιά 1985 ήταν κρύα"),  # holds line 1's translation
        _line("Year 1985 was cold", "Μια ήσυχη στιγμή πέρασε"),  # holds line 2's (anchorless)
        _line("A quiet moment passed", "Η χρονιά 1990 ήταν ζεστή"),  # holds line 3's
        _line("Year 1990 was warm", "Η χρονιά 2001 ήταν νέα"),   # junk leaked from beyond
    ]
    tickets = alignment_tickets(rows, "el")
    covered = {t["index"] for t in tickets}
    # Voters at 1 and 3 (delta -1); anchorless line 2 between them is in the span.
    assert {1, 2, 3} <= covered
    assert all("mis-aligned" in t["issues"][0] for t in tickets)


def test_flag_drift_marks_needs_review_without_repair():
    rows = [
        _line("Year 1985 was cold", "Χρονιά 1990 ζεστή"),
        _line("Year 1990 was warm", "Χρονιά 2001 νέα"),
        _line("Year 2001 was new", "Χρονιά 2010 άλλη"),
    ]
    n = flag_drift(rows, "el")
    assert n >= 2
    assert rows[1].get("needs_review") is True
    assert "alignment shift" in rows[1]["review_issues"]


# --- renumber-guard regression (the other silent-shift source) ----------------

def test_full_overflow_reconciliation_renumbers():
    # Model split one line into two; the merge fully accounts for the surplus, so
    # renumbering to a clean 1..N is safe.
    results = [
        {"index": 1, "text": "Hello,"},
        {"index": 2, "text": "world"},
        {"index": 3, "text": "Goodbye."},
    ]
    merged = _fix_line_count_mismatch(results, expected_count=2)
    assert [m["index"] for m in merged] == [1, 2]
    assert merged[0]["text"] == "Hello, world"


def test_partial_overflow_does_not_renumber():
    # Two surplus lines but only one confidently merges → must NOT renumber (that
    # would shift the tail). Echoed indices are preserved so callers gap-map safely.
    results = [
        {"index": 1, "text": "Wait,"},
        {"index": 2, "text": "please"},   # merges into 1 (incomplete + lowercase)
        {"index": 3, "text": "Stop."},
        {"index": 4, "text": "Now."},     # cannot merge — both complete/capitalised
    ]
    merged = _fix_line_count_mismatch(results, expected_count=2)
    # "Stop." keeps its echoed index 3, NOT a renumbered 2.
    assert {m["index"] for m in merged} == {1, 3, 4}
    assert merged[0]["text"] == "Wait, please"
