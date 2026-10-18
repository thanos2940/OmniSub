"""
Wrong-line guard
================
A model that skips one line but keeps counting puts every later translation on the line before
it. ``utils.line_alignment`` scores how well a translation fits a source line (length, end
punctuation, questions, numbers, names, sentence breaks — learned from the library) and finds
such runs; ``repair_reply`` puts a short reply back in order. These tests pin that behaviour on a
small dialogue: aligned text is left alone, a shifted run is found, a duplicated answer is found,
and a reply that skipped a line is repaired.
"""

from utils import line_alignment as la

LINES = [
    "Where were you last night?", "I was at the station, waiting for Johnny.", "He never came.",
    "That's not like him at all!", "I know. I called him three times.", "Did he answer?", "No.",
    "We should go to his place at 9.", "It's late. Let's wait until morning, okay?", "Fine, but I'm not sleeping.",
    "Neither am I!", "Look! There's someone at the door.", "Who is it?", "Police. Open up!",
    "What do you want from us at 3 in the morning?", "Your friend Johnny Walker is in the hospital.",
    "Is he alive?", "Yes. He was hit by a car on Route 66, but he'll live.", "Oh, thank God!", "Can we see him?",
    "Tomorrow. Visiting hours start at 10.", "Thank you, officer.", "Get some sleep.", "Good night.",
]
GOOD = [f"Ελ: {s}" for s in LINES]   # stands in for a translation that keeps each line's shape


def test_aligned_translation_is_left_alone():
    assert la.misplaced(LINES, GOOD, "el") == {}


def test_shifted_run_is_found():
    shifted = list(GOOD)
    for i in range(8, 18):              # lines 9-18 carry the translation of the next line
        shifted[i] = GOOD[i + 1]
    found = la.misplaced(LINES, shifted, "el")
    assert sorted(found) == list(range(8, 18))
    assert all(v["belongs_to"] == i + 1 for i, v in found.items())


def test_untranslated_lines_are_not_evidence():
    partial = list(GOOD)
    for i in (3, 9, 15):
        partial[i] = None               # gaps: nothing to compare
    assert la.misplaced(LINES, partial, "el") == {}


def test_answer_written_twice_is_found():
    dup = list(GOOD)
    dup[15] = GOOD[17]
    assert la.misplaced(LINES, dup, "el") == {15: {"belongs_to": 17, "evidence": 0.0, "duplicate": True}}


def test_reply_that_skipped_a_line_is_repaired():
    order = list(range(1, len(LINES) + 1))
    reply = {n: (GOOD[n - 1] if n < 6 else GOOD[n]) for n in order[:-1]}   # line 6 skipped, numbering went on
    fixed, summary = la.repair_reply(order, reply, {n: LINES[n - 1] for n in order}, 0, "el")
    assert summary["from"] == 6 and summary["gap"] == 1 and summary["moved"] == 18
    assert all(fixed[n] == GOOD[n - 1] for n in fixed)
    assert set(order) - set(fixed) == {6}   # asked again


def test_complete_reply_is_untouched():
    order = list(range(1, len(LINES) + 1))
    reply = {n: GOOD[n - 1] for n in order}
    assert la.repair_reply(order, reply, {n: LINES[n - 1] for n in order}, 0, "el") == (reply, None)


def test_sentence_ends():
    assert la.complete_sentence("He never came.")
    assert not la.complete_sentence("I was going to...")
    assert not la.complete_sentence("and then")
