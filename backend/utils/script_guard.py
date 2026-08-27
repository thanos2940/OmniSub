"""Script guard — detect (and where provably safe, fix) characters from the wrong
writing system leaking into a translation.

Models occasionally emit a character from another script inside an otherwise
correct target-language word. Two distinct failure modes show up in practice, and
they need different treatment:

1. **Visual twins.** ``Δεv`` for ``Δεν``, ``Tόνι`` for ``Τόνι``, ``Σοk`` for
   ``Σοκ``, ``μπορούσáς`` for ``μπορούσάς`` — a Latin letter standing in for the
   Greek letter it looks identical to (the same artifact OCR'd subtitles carry, so
   imported targets have it too). The intended character is unambiguous, so these
   are repaired here deterministically, for zero tokens.

2. **Foreign-script fragments.** ``Ταγματάρχה`` (Hebrew he for eta),
   ``ακριبتώς``, ``ακούsheτε``, ``επαگردίσουμε`` — a stray Hebrew/Arabic/CJK
   character, or a transliteration fragment from another language, spliced into a
   word. Character-level mapping would guess wrong (``ακούsheτε`` is *ακούσετε*,
   not *ακούσηετε*), so these are only *detected* here and handed to the QC
   funnel's batched repair call, which has the source line to work from.

The rule that keeps (1) from turning into a silent corruption: a token is only
rewritten when it is majority target-script AND **every** foreign character in it
has an entry in the curated twin table. One unmapped character escalates the whole
token to (2).

Characters that also appear in the *source* line are always left alone — signs,
karaoke, and quoted foreign text legitimately pass through.
"""

import logging
import re
import unicodedata
from functools import lru_cache
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Letters only (no digits/underscore), Unicode-aware.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Latin beyond this point (Extended-A and up: ł ś ę ầ ậ ƒ …) is not plausible in a
# non-Latin target unless the source has it. Basic Latin + Latin-1 Supplement stay
# allowed so ordinary proper nouns (Café, Müller, José) never trip the guard.
_PLAIN_LATIN_MAX = 0x0100

# Which scripts may appear in a given target language, beyond Latin (always allowed
# for proper nouns) and script-neutral characters. Keyed by ISO-639-1.
_TARGET_SCRIPTS: Dict[str, Set[str]] = {
    "el": {"GREEK"},
    "ru": {"CYRILLIC"}, "uk": {"CYRILLIC"}, "bg": {"CYRILLIC"}, "sr": {"CYRILLIC"},
    "he": {"HEBREW"},
    "ar": {"ARABIC"}, "fa": {"ARABIC"},
    "hi": {"DEVANAGARI"}, "mr": {"DEVANAGARI"},
    "th": {"THAI"},
    "ja": {"HIRAGANA", "KATAKANA", "CJK"},
    "zh": {"CJK", "BOPOMOFO"},
    "ko": {"HANGUL", "CJK"},
    "hy": {"ARMENIAN"}, "ka": {"GEORGIAN"}, "am": {"ETHIOPIC"},
}

# Visually identical Latin & Cyrillic → Greek pairs. Deliberately conservative: only glyphs
# that are *the same shape*, so the substitution cannot change the reading.
_LATIN_TO_GREEK: Dict[str, str] = {
    # Basic Latin visual twins
    "A": "Α", "B": "Β", "E": "Ε", "Z": "Ζ", "H": "Η", "I": "Ι", "K": "Κ",
    "M": "Μ", "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "X": "Χ", "Y": "Υ",
    "a": "α", "e": "ε", "i": "ι", "k": "κ", "o": "ο", "t": "τ", "u": "υ",
    "v": "ν", "x": "χ",
    # Accented Latin letters that models substitute for Greek vowels with tonos
    "á": "ά", "é": "έ", "í": "ί", "ó": "ό", "ú": "ύ", "ý": "ύ",
    "à": "ά", "è": "έ", "ì": "ί", "ò": "ό", "ù": "ύ",
    "Á": "Ά", "É": "Έ", "Í": "Ί", "Ó": "Ό", "Ú": "Ύ",
    # Cyrillic small homoglyphs (common tokenizer spillover in multilingual LLMs)
    "\u0430": "α",  # Cyrillic small a
    "\u0435": "ε",  # Cyrillic small e
    "\u043e": "ο",  # Cyrillic small o
    "\u0440": "ρ",  # Cyrillic small er
    "\u0441": "σ",  # Cyrillic small es
    "\u0443": "υ",  # Cyrillic small u
    "\u0445": "χ",  # Cyrillic small ha
    "\u0456": "ι",  # Cyrillic small i
    "\u0458": "ι",  # Cyrillic small je
    "\u0455": "ς",  # Cyrillic small dze
    # Cyrillic capital homoglyphs
    "\u0410": "Α",  # Cyrillic capital A
    "\u0412": "Β",  # Cyrillic capital Ve
    "\u0415": "Ε",  # Cyrillic capital Ie
    "\u041a": "Κ",  # Cyrillic capital Ka
    "\u041c": "Μ",  # Cyrillic capital Em
    "\u041d": "Ν",  # Cyrillic capital En
    "\u041e": "Ο",  # Cyrillic capital O
    "\u0420": "Ρ",  # Cyrillic capital Er
    "\u0421": "Σ",  # Cyrillic capital Es
    "\u0422": "Τ",  # Cyrillic capital Te
    "\u0425": "Χ",  # Cyrillic capital Ha
    "\u0406": "Ι",  # Cyrillic capital I
    "\u0408": "Ι",  # Cyrillic capital Je
}

# Per-target twin tables. Only Greek is populated; other targets get detection
# (which is language-agnostic) but no deterministic rewrite until their table is
# curated with the same "same shape or nothing" bar.
_CONFUSABLES: Dict[str, Dict[str, str]] = {"el": _LATIN_TO_GREEK}


@lru_cache(maxsize=4096)
def script_of(ch: str) -> str:
    """Unicode script family of a character ("GREEK", "LATIN", "CJK", …).

    Empty string for anything script-neutral (punctuation, digits, whitespace,
    symbols) — those are never a contamination signal.
    """
    if not ch.isalpha():
        return ""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "UNKNOWN"
    head = name.split()[0]
    if head == "CJK":
        return "CJK"
    return head


def allowed_scripts(target_lang_code: str) -> Set[str]:
    """Scripts a translation into ``target_lang_code`` may legitimately contain."""
    return _TARGET_SCRIPTS.get((target_lang_code or "").lower(), set()) | {"LATIN"}


def _is_foreign(ch: str, allowed: Set[str]) -> bool:
    """True if ``ch`` belongs to no allowed script, or is exotic Latin."""
    script = script_of(ch)
    if not script:
        return False
    if script not in allowed:
        return True
    return script == "LATIN" and ord(ch) >= _PLAIN_LATIN_MAX


def normalize_confusables(text: str, target_lang_code: str) -> Tuple[str, int]:
    """Rewrite visual-twin characters back into the target script.

    Returns ``(text, n_chars_fixed)``. A token is only touched when it is majority
    target-script and every non-target letter in it has a curated twin — otherwise
    it is left exactly as-is for the repair pass to handle.
    """
    if not text:
        return text, 0

    # 1. Normalize Unicode to standard precomposed NFC (merges decomposed combining accents)
    clean_text = unicodedata.normalize("NFC", text)
    # Strip invisible zero-width and directional control characters
    clean_text = re.sub(r'[\u200B-\u200F\uFEFF\u202A-\u202E]', '', clean_text)

    table = _CONFUSABLES.get((target_lang_code or "").lower())
    if not table:
        return clean_text, 0

    target_scripts = _TARGET_SCRIPTS.get(target_lang_code.lower(), set())
    fixed = 0

    def _fix_token(m: re.Match) -> str:
        nonlocal fixed
        token = m.group(0)
        if len(token) < 2:
            return token
        native = [c for c in token if script_of(c) in target_scripts]
        alien = [c for c in token if c not in native and script_of(c)]
        if not native or not alien:
            return token
        if len(native) <= len(alien):
            return token          # ties and Latin-majority tokens are never rewritten
        if any(c not in table for c in alien):
            return token          # one unmapped character → escalate the whole token
        fixed += len(alien)
        return "".join(table.get(c, c) for c in token)

    return _WORD_RE.sub(_fix_token, clean_text), fixed


def foreign_issues(original: str, translated: str, target_lang_code: str) -> List[str]:
    """Describe wrong-script contamination in ``translated``, for repair tickets.

    Characters that also occur in ``original`` are ignored (legitimate pass-through
    of signs, karaoke and quoted foreign text). Returns an empty list when clean.
    """
    if not translated:
        return []
    allowed = allowed_scripts(target_lang_code)
    target_scripts = _TARGET_SCRIPTS.get((target_lang_code or "").lower(), set())
    if not target_scripts:
        return []          # unknown/Latin-script target: nothing reliable to assert

    src = original or ""
    issues: List[str] = []

    stray = {c for c in translated if _is_foreign(c, allowed) and c not in src}
    if stray:
        scripts = sorted({script_of(c) for c in stray})
        sample = "".join(sorted(stray))[:12]
        issues.append(
            f"contains characters from the wrong writing system "
            f"({', '.join(s.title() for s in scripts)}: {sample}); "
            f"rewrite the line using only the target language's alphabet"
        )

    # Latin spliced into a target-script word (ακούsheτε) — only where the word is
    # mostly target-script, so real Latin names and acronyms are not flagged.
    for token in _WORD_RE.findall(translated):
        if len(token) < 2 or token in src:
            continue
        native = [c for c in token if script_of(c) in target_scripts]
        latin = [c for c in token if script_of(c) == "LATIN"]
        if latin and len(native) > len(latin):
            issues.append(
                f"the word '{token}' mixes alphabets (Latin letters inside a "
                f"target-language word); write it entirely in the target alphabet"
            )
            break

    return issues


def scrub_rows(
    rows: List[Dict],
    target_lang_code: str,
    primary_code: Optional[str] = None,
) -> int:
    """Apply :func:`normalize_confusables` across an episode in place.

    Returns the number of characters fixed. Rows whose translation is untouched are
    left alone, so this is safe to run repeatedly.
    """
    total = 0
    for row in rows:
        current = (row.get("translations", {}) or {}).get(target_lang_code)
        if current is None and target_lang_code == primary_code:
            current = row.get("translated")
        if not current:
            continue
        fixed_text, n = normalize_confusables(current, target_lang_code)
        if n:
            row.setdefault("translations", {})[target_lang_code] = fixed_text
            if primary_code and target_lang_code == primary_code:
                row["translated"] = fixed_text
            total += n
    return total


def flag_rows(rows: List[Dict], target_lang_code: str) -> int:
    """Flag rows still carrying wrong-script text as ``needs_review``.

    For lanes with no repair pass (the Batch API path), so the contamination at
    least surfaces in the review queue instead of shipping silently.
    """
    flagged = 0
    for row in rows:
        translated = ((row.get("translations", {}) or {}).get(target_lang_code)
                      or row.get("translated") or "")
        if not translated:
            continue
        issues = foreign_issues(row.get("original") or "", translated, target_lang_code)
        if not issues:
            continue
        row["needs_review"] = True
        existing = row.get("review_issues") or ""
        note = "Wrong-script characters in translation"
        if note not in existing:            # keep re-runs idempotent
            row["review_issues"] = f"{existing}; {note}" if existing else note
        flagged += 1
    return flagged
