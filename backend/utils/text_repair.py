"""
Repair of broken subtitle text
==============================
Damage that reached the library, each kind with a signature precise enough to undo it (or at
least to find it):

* **Mojibake** - Greek written in cp1253, read as cp1252/latin-1 and stored as UTF-8
  (``Ôï áíèñþðéíï`` for ``Το ανθρώπινο``). Reversible, but only where the evidence is
  overwhelming: real French/German/Spanish text re-decodes into mostly-Latin gibberish and is
  never touched.
* **Leaked prompt text** - the model's reply carried part of its prompt instead of a translation:
  a work-file prefix (``12| ...``), an echoed context marker (``[prev] ...``), a reviewer block
  (``EN: ... CURRENT: ... CORRECTED: ...``) or the episode summary from its context.
* **Mixed script** - a word with Greek letters and letters of another alphabet
  (``Συνταγματάρχה``). The rest of the line is usually right, so it is only reported; whether to
  clear it for retranslation is the caller's call (``RETRANSLATE_KINDS``).

Pure functions over strings, with no dependency on storage or services, so the same rules serve
the audit, the write path (``sanitize_translation_text``) and the subtitle readers.
"""

import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

KINDS = ("mojibake", "numbered", "context_echo", "review_echo", "summary_echo", "mixed_script")
# Kinds whose text is mostly right: reported always, cleared only when retranslation is requested.
RETRANSLATE_KINDS = frozenset({"mixed_script"})

_GREEK_CODES = frozenset({"el", "ell", "gre", "grk"})
_GREEK_RE = re.compile(r"[\u0370-\u03ff\u1f00-\u1fff]")
# Script of each target language (mirrors services.agent_workspace; this module sits below services).
_TARGET_SCRIPTS = {
    "ru": re.compile(r"[\u0400-\u04ff]"),
    "uk": re.compile(r"[\u0400-\u04ff]"),
    "bg": re.compile(r"[\u0400-\u04ff]"),
    "ja": re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]"),
    "zh": re.compile(r"[\u4e00-\u9fff]"),
    "ko": re.compile(r"[\uac00-\ud7af\u1100-\u11ff]"),
    "ar": re.compile(r"[\u0600-\u06ff]"),
    "he": re.compile(r"[\u0590-\u05ff]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
    "hi": re.compile(r"[\u0900-\u097f]"),
}
_LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
# Markup that is not text: ASS override blocks and escapes, HTML-style tags.
_MARKUP = re.compile(r"(\{[^}]*\}|</?[A-Za-z][A-Za-z0-9]*(?:\s[^<>]*)?/?>|\\[Nnh])")
_WORD = re.compile(r"[^\W\d_]+")


def is_greek_code(code: Optional[str]) -> bool:
    return (code or "").strip().lower() in _GREEK_CODES


def _target_script(code: Optional[str]):
    """Regex of the target language's own script (``None``: Latin script or unknown)."""
    c = (code or "").strip().lower()
    return _GREEK_RE if c in _GREEK_CODES else _TARGET_SCRIPTS.get(c)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _visible(text: str) -> str:
    """``text`` without markup."""
    return "".join(_MARKUP.split(text)[0::2])


def has_greek(text: str) -> bool:
    return bool(_GREEK_RE.search(text or ""))


def has_text(text: str) -> bool:
    """Whether ``text`` has a letter outside its markup."""
    return any(ch.isalpha() for ch in _visible(text))


# --------------------------------------------------------------------------- mojibake

# cp1252 puts punctuation and a few letters in 0x80-0x9F; latin-1 puts control characters there.
_CP1252_BYTE: Dict[str, int] = {}
for _byte in range(0x80, 0xA0):
    try:
        _CP1252_BYTE[bytes([_byte]).decode("cp1252")] = _byte
    except UnicodeDecodeError:
        pass
_HIGH = re.compile("[\u0080-\u00ff" + re.escape("".join(_CP1252_BYTE)) + "]")

_MIN_HIGH = 3
_MIN_GREEK = 0.7


def _redecode(text: str) -> Optional[str]:
    """``text`` as cp1253 would have read the bytes it was mis-decoded from (``None`` if it
    cannot have been: a character outside cp1252/latin-1, or a byte cp1253 does not define)."""
    out = bytearray()
    for ch in text:
        o = ord(ch)
        if o < 0x100:
            out.append(o)  # ASCII and latin-1, which is where cp1252 lacks the C1 controls
        else:
            b = _CP1252_BYTE.get(ch)
            if b is None:
                return None
            out.append(b)
    try:
        return out.decode("cp1253")
    except UnicodeDecodeError:
        return None


_ONE_LETTER_WORDS = frozenset("οηήωΟΗΉΩ")


def _mojibake_shaped(text: str) -> bool:
    """For a line of a file that is mostly mojibake: whether its accented letters look like
    mis-decoded Greek rather than Western text. Greek words are all accented bytes, so one such
    word of two letters or more (``ôï`` for ``το``) settles it, even next to English names written
    with Greek look-alike capitals (``Êathryn``); so does a line whose only accented words are
    single letters (``Ç Sierra vista.``). Western words mix plain and accented letters (``für``)."""
    accented = [w for w in _WORD.findall(text) if not w.isascii()]
    if not accented:
        return False
    if any(len(w) >= 2 and not any(c.isascii() for c in w) for w in accented):
        return True
    # Single letters only: the one-letter Greek words (ο, η, ή, ω), not "à la carte".
    return all(len(w) == 1 and _redecode(w) in _ONE_LETTER_WORDS for w in accented)


def repair_mojibake(text: str, relaxed: bool = False) -> Optional[str]:
    """Undo Greek mojibake: cp1253 text that was read as cp1252 (latin-1 where cp1252 has no
    character) and stored as UTF-8 - ``'Ôï áíèñþðéíï'`` becomes ``'Το ανθρώπινο'``.

    Strict, because wrongly "repairing" real text is worse than leaving a broken line: the text
    must have no Greek letters and at least three characters in U+0080-U+00FF (or cp1252
    punctuation), re-decode without error or U+FFFD, and the result must be at least 70% Greek
    letters. German ``für den nächsten`` re-decodes to mostly Latin and is rejected. Markup
    (``<i>``, ``{\\an8}``, ``\\N``) is left alone and does not count as text.

    ``relaxed`` is for lines already known to sit in a mojibake file or episode (see
    ``mojibake_prevails``): short lines such as ``Ôé;`` (``Τι;``) and lines with English names
    (``óôï palm desert.``). Their accented words must look like mis-decoded Greek
    (``_mojibake_shaped``) and every accented letter must come back Greek; the share of Greek
    letters in the line does not matter. Returns ``None`` when ``text`` is not mojibake.
    """
    if not text or _GREEK_RE.search(text):
        return None
    parts = _MARKUP.split(text)
    visible = "".join(parts[0::2])
    if len(_HIGH.findall(visible)) < (1 if relaxed else _MIN_HIGH):
        return None
    if relaxed and not _mojibake_shaped(visible):
        return None
    fixed = list(parts)
    for k in range(0, len(parts), 2):
        if parts[k]:
            fixed[k] = _redecode(parts[k])
            if fixed[k] is None:
                return None
    result_visible = "".join(fixed[0::2])
    if "\ufffd" in result_visible:
        return None
    letters = [c for c in result_visible if c.isalpha()]
    greek = sum(1 for c in letters if _GREEK_RE.match(c))
    if not greek:
        return None
    if relaxed:
        if any(not c.isascii() and not _GREEK_RE.match(c) for c in letters):
            return None  # an accented letter that is not Greek after all
    elif greek < _MIN_GREEK * len(letters):
        return None
    return "".join(fixed)


def mojibake_prevails(hits: int, total: int) -> bool:
    """Whether ``hits`` mojibake lines out of ``total`` lines with text are the majority - the
    evidence that justifies ``repair_mojibake(relaxed=True)`` for the rest."""
    return hits > 0 and hits * 2 > total


# --------------------------------------------------------------------------- leaked prompt text

_NUM_PREFIX = re.compile(r"^\s*\d+\s*[|\uff5c]\s?")
_CONTEXT_MARK = re.compile(r"^\s*\[\s*(?:prev(?:ious)?|look\s*-?\s*ahead|next|context)\b[^\]]*\]", re.I)
_REVIEW_START = re.compile(r"^\s*EN\s*:")
_REVIEW_LABEL = re.compile(r"\b(CURRENT|CORRECTED|ISSUES?|REQUIRED\s+TERMS)\s*:", re.I)
_SUMMARY_TAG = re.compile(r"^\s*\[S\d{1,2}E\d{1,3}\]")
_SUMMARY_PHRASE = re.compile(r"Προηγουμένως\s+στη\s+σειρά|Previously\s+on", re.I)
_RECAP_SOURCE = re.compile(r"\bprevious(?:ly)?\b|\blast\s+time\s+on\b", re.I)


def _is_translation(text: str, source: Optional[str], code: Optional[str]) -> bool:
    """Whether ``text`` can be a translation into ``code``: in its script, or - for Latin-script
    and unknown targets - at least not a copy of the source."""
    if not text.strip():
        return False
    script = _target_script(code)
    if script is not None:
        return bool(script.search(text))
    return not source or _norm(text) != _norm(source)


def _review_echo(text: str, source: Optional[str], code: Optional[str]) -> Optional[str]:
    """The Greek out of ``EN: <src> CURRENT: <cur> CORRECTED: <fix>``, ``""`` for a reviewer
    block without one, ``None`` when this is not a reviewer block (``EN: x EL: y`` is the
    dual-format output ``sanitize_translation_text`` handles itself)."""
    body = _REVIEW_START.sub("", text, count=1)
    labels = list(_REVIEW_LABEL.finditer(body))
    if labels:
        corrected = [m for m in labels if m.group(1).upper() == "CORRECTED"]
        if not corrected:
            return ""
        last = corrected[-1]
        after = next((m for m in labels if m.start() > last.start()), None)
        fix = body[last.end():after.start() if after else len(body)].strip()
        return fix if _is_translation(fix, source, code) else ""
    script = _target_script(code)
    if script is not None and not script.search(text):
        return ""  # "EN: <english>" alone
    if script is None and source and _norm(body) == _norm(source):
        return ""
    return None


def _leak_step(text: str, source: Optional[str], code: Optional[str]) -> Optional[Tuple[str, str]]:
    if _NUM_PREFIX.match(text) and not (source and _NUM_PREFIX.match(source)):
        rest = "\n".join(_NUM_PREFIX.sub("", line, count=1) for line in text.split("\n")).strip()
        script = _target_script(code)
        if script is not None:
            garbage = not script.search(rest) and bool(_LATIN_WORD.search(rest))
        else:
            garbage = bool(source) and bool(rest) and _norm(rest) == _norm(source)
        return "numbered", ("" if garbage else rest)
    if _CONTEXT_MARK.match(text) and not (source and _CONTEXT_MARK.match(source)):
        return "context_echo", ""
    if _REVIEW_START.match(text) and not (source and _REVIEW_START.match(source)):
        fix = _review_echo(text, source, code)
        if fix is not None:
            return "review_echo", fix
    if _SUMMARY_TAG.match(text) and not (source and _SUMMARY_TAG.match(source)):
        return "summary_echo", ""
    # A recap line ("Previously on ...") legitimately says it; without the source it cannot be told.
    if (source is not None and _SUMMARY_PHRASE.search(text)
            and not (_SUMMARY_PHRASE.search(source) or _RECAP_SOURCE.search(source))):
        return "summary_echo", ""
    return None


def salvage_leak(text: str, source: Optional[str] = "", target_code: Optional[str] = "el") -> Optional[Tuple[str, str]]:
    """Find prompt text that leaked into a translation: ``(kind, salvaged_text)`` or ``None``.

    * ``numbered`` - a work-file prefix (``12| ...``). The prefix is stripped; what remains is
      the salvage unless it is not in the target script (Latin words, no Greek letters): that is
      another line's English, so ``""``.
    * ``context_echo`` - ``[prev] ...`` / ``[lookahead ...] ...``: the model echoed a neighbouring
      line, not a translation of this one: ``""``.
    * ``review_echo`` - ``EN: ... CURRENT: ... CORRECTED: <fix>``: the fix when it is in the
      target script, else ``""``; ``EN: <english>`` alone is ``""`` too.
    * ``summary_echo`` - the episode summary from the model's context: an ``[S01E04]`` tag at the
      start, or ``Προηγουμένως στη σειρά`` / ``Previously on``: ``""``.

    ``""`` means there is nothing to keep. A line is only a leak when the *source* line does not
    itself have that shape; ``source=None`` means the source is unknown, which skips the checks
    that need it, and ``target_code`` of ``None`` or ``""`` means the script is unknown.
    """
    if not text or not text.strip():
        return None
    kind = None
    current = text
    for _ in range(3):  # "3| [prev] ..." is a numbered echo of an echo
        step = _leak_step(current, source, target_code)
        if step is None:
            break
        kind = kind or step[0]
        current = step[1]
        if not current.strip():
            current = ""
            break
    return (kind, current.strip()) if kind else None


# --------------------------------------------------------------------------- mixed script

# Scripts that must not share a word with Greek letters: Cyrillic, Hebrew, Arabic, Devanagari,
# Thai, kana, CJK, Hangul.
_OTHER_SCRIPT = re.compile("[\u0400-\u04ff\u0590-\u05ff\u0600-\u06ff\u0900-\u097f\u0e00-\u0e7f"
                           "\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]")


def mixed_script_words(text: str, source: str = "") -> List[str]:
    """Words of a Greek translation that have Greek letters *and* letters of another non-Latin
    script (``Συνταγματάρχה``, ``prekraτικό`` in Cyrillic) - wrong-alphabet slips inside an
    otherwise fine line. A word entirely in another script is not one, and neither is anything
    when the source line has letters of those scripts itself (lyrics, names)."""
    if (not text or not _OTHER_SCRIPT.search(text) or not _GREEK_RE.search(text)
            or _OTHER_SCRIPT.search(source or "")):
        return []
    found: List[str] = []
    word: List[str] = []
    for ch in text + " ":
        if unicodedata.category(ch)[0] in "LM":
            word.append(ch)
        elif word:
            w = "".join(word)
            word = []
            if _GREEK_RE.search(w) and _OTHER_SCRIPT.search(w):
                found.append(w)
    return found


# --------------------------------------------------------------------------- one translation

def repair_translation(text: str, source: str, target_code: str = "el") -> Optional[Dict[str, str]]:
    """What is wrong with one translated line, and what it should be instead.

    Returns ``{"kind": ..., "text": ...}`` - ``kind`` one of ``KINDS``, ``text`` the repaired
    line or ``""`` when nothing of it is worth keeping (leaks that are no translation, and
    ``mixed_script`` lines, which need retranslating) - or ``None`` when the line is fine.
    Mojibake and mixed script are checked for Greek targets only.
    """
    if not text or not text.strip():
        return None
    leak = salvage_leak(text, source, target_code)
    if leak:
        return {"kind": leak[0], "text": leak[1]}
    if is_greek_code(target_code):
        fixed = repair_mojibake(text)
        if fixed is not None:
            return {"kind": "mojibake", "text": fixed}
        if mixed_script_words(text, source):
            return {"kind": "mixed_script", "text": ""}
    return None


# --------------------------------------------------------------------------- whole files

_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")  # not str.splitlines(): U+0085 etc. are no line breaks here
_TIMING = re.compile(r"\d[\d:.,]*\s*-->\s*\d")
_ASS_FIELDS = 10
_SURROGATES = re.compile("[\udc80-\udcff]")


class _Unit:
    """The text of one cue (SRT/VTT: its text lines) or one ASS event (the text field)."""
    __slots__ = ("lines", "bodies", "endings", "prefix")

    def __init__(self, lines: List[int], bodies: List[str], endings: List[str], prefix: str = ""):
        self.lines, self.bodies, self.endings, self.prefix = lines, bodies, endings, prefix

    @property
    def text(self) -> str:
        return "\n".join(self.bodies)


def _split_ending(line: str) -> Tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _units(lines: List[str]) -> List[_Unit]:
    """The text units of a subtitle file: everything that is not an index, timing, header, style
    or comment line."""
    units: List[_Unit] = []
    cue: List[int] = []
    in_cue = False
    section = ""
    fields = _ASS_FIELDS

    def flush() -> None:
        nonlocal cue
        if cue:
            parts = [_split_ending(lines[i]) for i in cue]
            units.append(_Unit(cue, [p[0] for p in parts], [p[1] for p in parts]))
        cue = []

    for i, line in enumerate(lines):
        body, ending = _split_ending(line)
        stripped = body.strip()
        if not in_cue and stripped.startswith("[") and stripped.endswith("]"):
            section = stripped.lower()
            continue
        if section == "[events]" and body.startswith("Format:"):
            fields = max(len(body.split(":", 1)[1].split(",")), 2)
            continue
        if body.startswith("Dialogue:"):
            pieces = body.split(":", 1)[1].split(",", fields - 1)
            if len(pieces) == fields:
                text = pieces[-1]
                units.append(_Unit([i], [text], [ending], body[:len(body) - len(text)]))
            continue
        if "-->" in body and _TIMING.search(body):
            flush()
            in_cue = True
            continue
        if not stripped:
            flush()
            in_cue = False
            continue
        if in_cue:
            cue.append(i)
    flush()
    return units


def _repair_bodies(bodies: List[str], relaxed: bool) -> Optional[List[str]]:
    """The repaired lines of one unit - as a whole (a short second line borrows the evidence of
    the first), else line by line (a cue may mix languages) - or ``None``."""
    fixed = repair_mojibake("\n".join(bodies), relaxed)
    if fixed is not None:
        new = fixed.split("\n")
        return new if len(new) == len(bodies) else None
    if len(bodies) > 1:
        new = [repair_mojibake(b, relaxed) or b for b in bodies]
        if new != bodies:
            return new
    return None


def _repair_lines(content: str, known: bool = False) -> Tuple[str, int, int, int]:
    """``(new content, units fixed, units fixed strictly, units with text)``. ``known``: the file
    is known to have been mojibake (an earlier pass repaired part of it), so its Greek lines count
    as evidence too when deciding whether the short or name-heavy leftovers are mojibake."""
    lines = _LINE.findall(content)
    units = _units(lines)
    results: List[Optional[List[str]]] = [None] * len(units)
    hits = total = 0
    for k, unit in enumerate(units):
        if has_text(unit.text):
            total += 1
        results[k] = _repair_bodies(unit.bodies, relaxed=False)
        hits += results[k] is not None
    greek = sum(1 for unit in units if _GREEK_RE.search(unit.text)) if known else 0
    if mojibake_prevails(hits + greek, total):
        for k, unit in enumerate(units):
            if results[k] is None:
                results[k] = _repair_bodies(unit.bodies, relaxed=True)
    out = list(lines)
    fixed = 0
    for unit, new in zip(units, results):
        if new is None:
            continue
        fixed += 1
        for idx, body, ending in zip(unit.lines, new, unit.endings):
            out[idx] = unit.prefix + body + ending
    return "".join(out), fixed, hits, total


def repair_subtitle_text(content: str, known: bool = False) -> Tuple[str, int]:
    """Repair mojibake in a whole SRT/ASS/VTT file: ``(new_content, lines_fixed)``.

    Cue by cue (ASS: the text field of each ``Dialogue:`` line, ``{...}`` blocks and ``\\N``
    kept). Index, timing, header, style and comment lines, markup and the file's own line
    endings stay as they are; ``lines_fixed`` counts the cues/events that changed. Where most
    of the file is mojibake, its shorter leftovers are repaired too (``repair_mojibake(relaxed)``);
    ``known`` says the file was mojibake before (its already repaired Greek lines count as evidence).
    """
    new, fixed, _, _ = _repair_lines(content, known)
    return new, fixed


def _repair_dominant(text: str) -> str:
    """``repair_subtitle_text`` when most of the text is mojibake, else ``text`` untouched."""
    if not _HIGH.search(text):
        return text
    new, _, hits, total = _repair_lines(text)
    return new if mojibake_prevails(hits, total) else text


def _reads_as_greek(decoded: str) -> bool:
    """Whether bytes decoded as cp1253 come out as Greek text: among the lines that have any
    accented byte, at least half of the letters are Greek. Accented letters of a cp1252 file
    (``é`` becomes ``ι``) are a small share of their lines; Greek text is nearly all of it."""
    lines = _LINE.findall(decoded)
    texts = [u.text for u in _units(lines)] or [l for l in lines if l.strip()]
    greek = letters = 0
    for text in texts:
        if not _HIGH.search(text):
            continue
        for ch in _visible(text):
            if ch.isalpha():
                letters += 1
                greek += bool(_GREEK_RE.match(ch))
    return greek > 0 and greek * 2 >= letters


def _decode_single_byte(raw: bytes) -> str:
    greek = raw.decode("cp1253", errors="replace")
    if _reads_as_greek(greek):
        return greek
    # cp1252, but its five undefined bytes keep their latin-1 meaning instead of failing the file.
    text = raw.decode("cp1252", errors="surrogateescape")
    return _SURROGATES.sub(lambda m: chr(ord(m.group()) - 0xDC00), text)


def decode_subtitle_bytes(raw: bytes) -> str:
    """Decode a subtitle file the way its bytes were most likely written, never failing.

    A BOM decides first (UTF-8; UTF-16 only with a BOM, since BOM-less UTF-16 can "decode" an
    even-length single-byte file into nonsense), then plain UTF-8. Anything else is a legacy
    single-byte file: cp1253 when its accented bytes read as Greek text, otherwise cp1252
    (latin-1 for the bytes cp1252 lacks). Finally, a file whose text is mostly Greek mojibake
    (UTF-8 that was written from a mis-decoded cp1253 file) is repaired line by line.
    The newlines are the file's own; callers normalize them.
    """
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = None
    if text is None and raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            text = raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if text is None:
        text = _decode_single_byte(raw)
    return _repair_dominant(text)
