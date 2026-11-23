"""
Undo what the old Japanese-name accent guess did to Greek subtitles.

Until 2026-11-23 ``fix_japanese_name_greek_tonos`` ran on every Greek line the Gemini engine translated (and
on glossary terms), and moved the accent of capitalised words to their first syllable: "Περίμενε" became
"Πέριμενε", "Σοβαρά" "Σόβαρα", "Μακάρι" "Μάκαρι", "Κυρία" "Κύρια", "Λονδίνο" "Λόνδινο"; the list of known names
also turned "σαράντα" (forty) into "σάραντα".

The repair takes its evidence from the project's own text: the guess only touched capitalised words (or
every word of a line of one or two words), so the same word written in lower case inside a longer line shows
its real accent. A suspect word is rewritten only when that evidence is clear (seen at least twice, and in at
least 80% of the cases in one form). Names are always capitalised, so there is no evidence for them: they are
listed for a person to check instead of changed.
"""

import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

_ACCENTED = "άέήίόύώΐΰ"
_PLAIN = str.maketrans("άέήίόύώΐΰΆΈΉΊΌΎΏ", "αεηιουωϊϋΑΕΗΙΟΥΩ")
_VOWELS = "αεηιουωϊϋ"
_DIGRAPHS = ("ου", "αι", "ει", "οι", "αυ", "ευ", "υι")
_SPLIT = re.compile(r"(\s+|[.,;!?\"'\-…«»()\[\]<>{}\\/])")
_GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")
FIXED_WORDS = {"σάραντα": "σαράντα"}   # wrong entries of the old list of known names
MIN_VOTES = 2
MIN_SHARE = 0.8


def _plain(word: str) -> str:
    return word.lower().translate(_PLAIN)


def _tokens(line: str) -> List[str]:
    return _SPLIT.split(line or "")


def _words(tokens: Iterable[str]) -> List[str]:
    return [t for t in tokens if t and any(c.isalpha() for c in t)]


def accent_on_first_syllable(word: str) -> bool:
    """The word has an accent, and it sits on its first vowel group (what the old guess produced)."""
    low = word.lower()
    first = next((i for i, c in enumerate(low) if c in _VOWELS or c in _ACCENTED), -1)
    if first < 0:
        return False
    end = first + 1
    if end < len(low) and _plain(low[first:end + 1]) in _DIGRAPHS:
        end += 1
    # "κιό" (ι glide + vowel) is one syllable as well
    if end < len(low) and _plain(low[first]) == "ι" and first > 0 and _plain(low[end]) in "αεοω":
        end += 1
    accents = [i for i, c in enumerate(low) if c in _ACCENTED]
    return bool(accents) and accents[0] < end


def collect_evidence(lines: Iterable[str]) -> Dict[str, Counter]:
    """How each word is accented where the old guess never reached: in lower case, in lines of 3+ words."""
    evidence: Dict[str, Counter] = defaultdict(Counter)
    for line in lines:
        words = _words(_tokens(line))
        if len(words) <= 2:
            continue
        for w in words:
            if w[0].islower() and _GREEK.search(w):
                evidence[_plain(w)][w] += 1
    return evidence


def _match_case(model: str, form: str) -> str:
    if model.isupper():
        return form.upper().translate(_PLAIN)   # all caps carry no accents
    return form[:1].upper() + form[1:] if model[:1].isupper() else form


def repair_line(line: str, evidence: Dict[str, Counter]) -> Tuple[str, List[Tuple[str, str]], List[str]]:
    """``(new line, [(old word, new word)], [suspect words left alone])``."""
    tokens = _tokens(line)
    words = _words(tokens)
    standalone = len(words) <= 2
    changes: List[Tuple[str, str]] = []
    unresolved: List[str] = []
    out = []
    for t in tokens:
        if not t or not any(c.isalpha() for c in t) or not _GREEK.search(t):
            out.append(t)
            continue
        low = t.lower()
        if low in FIXED_WORDS:
            new = _match_case(t, FIXED_WORDS[low])
            changes.append((t, new))
            out.append(new)
            continue
        if not ((t[0].isupper() or standalone) and accent_on_first_syllable(t)):
            out.append(t)
            continue
        forms = evidence.get(_plain(t))
        if forms:
            best, votes = forms.most_common(1)[0]
            if votes >= MIN_VOTES and votes / sum(forms.values()) >= MIN_SHARE and best.lower() != low:
                new = _match_case(t, best)
                changes.append((t, new))
                out.append(new)
                continue
        elif t[0].isupper() and not t.isupper():
            unresolved.append(t)
        out.append(t)
    return "".join(out), changes, unresolved
