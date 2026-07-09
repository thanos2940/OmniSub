"""
Centralized language-code utilities — the single source of truth.

Converts human-readable target-language names (e.g. "Greek") to ISO-639-1
codes (e.g. "el"), builds output subtitle filenames, and matches subtitle
files on disk by language. This replaces previously-divergent copies in:

  - routers/episodes.py            (LANG_CODES / _lang_code / _output_filename)
  - services/background_worker.py  (get_lang_code)
  - services/translation_service.py(auto_export inline mapping)
  - integrations/subtitle_scanner.py (_LANG_VARIANTS / _matches_language)
  - routers/integrations.py        (_build_arr_engine lang_codes dict)

The previous `name[:2]` fallback was the cause of a serious bug: e.g. for
"Spanish" it produced "sp" (writing `<stem>.sp.srt`) while the scanner looked
for `<stem>.es.srt`, so a translated subtitle was never detected as present
and the episode was re-translated forever.
"""

import re
from typing import Dict, Set

# Human-readable name -> ISO-639-1 code.
LANG_CODES: Dict[str, str] = {
    "Greek": "el", "English": "en", "Spanish": "es", "French": "fr",
    "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
    "Japanese": "ja", "Korean": "ko", "Chinese": "zh", "Dutch": "nl",
    "Polish": "pl", "Swedish": "sv", "Danish": "da", "Finnish": "fi",
    "Norwegian": "no", "Czech": "cs", "Hungarian": "hu", "Romanian": "ro",
    "Turkish": "tr", "Arabic": "ar", "Hebrew": "he", "Ukrainian": "uk",
}

# ISO-639-1 code -> set of lowercase filename variants used for on-disk
# matching. Includes the 639-1 code, common 639-2 (B/T) codes, and the
# English name, mirroring how *arr / Bazarr name subtitle files.
LANG_VARIANTS: Dict[str, Set[str]] = {
    "en": {"en", "eng", "english"},
    "el": {"el", "ell", "gre", "greek"},
    "es": {"es", "spa", "spanish"},
    "fr": {"fr", "fra", "fre", "french"},
    "de": {"de", "deu", "ger", "german"},
    "it": {"it", "ita", "italian"},
    "pt": {"pt", "por", "portuguese"},
    "ru": {"ru", "rus", "russian"},
    "ja": {"ja", "jpn", "japanese"},
    "ko": {"ko", "kor", "korean"},
    "zh": {"zh", "zho", "chi", "chinese"},
    "nl": {"nl", "nld", "dut", "dutch"},
    "pl": {"pl", "pol", "polish"},
    "sv": {"sv", "swe", "swedish"},
    "da": {"da", "dan", "danish"},
    "fi": {"fi", "fin", "finnish"},
    "no": {"no", "nor", "norwegian"},
    "cs": {"cs", "ces", "cze", "czech"},
    "hu": {"hu", "hun", "hungarian"},
    "ro": {"ro", "ron", "rum", "romanian"},
    "tr": {"tr", "tur", "turkish"},
    "ar": {"ar", "ara", "arabic"},
    "he": {"he", "heb", "hebrew"},
    "uk": {"uk", "ukr", "ukrainian"},
}

# All known ISO-639-1 codes, for fast "is this already a code?" checks.
_KNOWN_CODES: Set[str] = set(LANG_CODES.values())


def to_code(name_or_code: str) -> str:
    """Resolve a language name ("Greek") or code ("el") to an ISO-639-1 code.

    Resolution order:
      1. Exact name match (case-insensitive) in LANG_CODES.
      2. The input is already a known 2-letter code.
      3. Substring/prefix match against known names.
      4. Last-resort fallback: first two characters lowercased.
    """
    if not name_or_code:
        return "xx"
    lower = name_or_code.strip().lower()

    for name, code in LANG_CODES.items():
        if name.lower() == lower:
            return code

    if lower in _KNOWN_CODES:
        return lower

    for name, code in LANG_CODES.items():
        if lower in name.lower() or name.lower() in lower:
            return code

    return lower[:2]


def variants_for(code: str) -> Set[str]:
    """Return the lowercase filename variants for a language code."""
    return LANG_VARIANTS.get(code, {code})


def strip_lang_tag(stem: str) -> str:
    """Remove a trailing 2-3 letter language tag from a filename stem.

    e.g. "Show.S01E01.en" -> "Show.S01E01"
    """
    return re.sub(r'\.[a-z]{2,3}$', '', stem, flags=re.IGNORECASE)


def output_filename(stem: str, code: str, ext: str = "srt") -> str:
    """Build "<stem>.<code>.<ext>", stripping any existing language tag on the stem."""
    return f"{strip_lang_tag(stem)}.{code}.{ext}"


def output_filename_from_original(original_filename: str, code: str) -> str:
    """Convert a source subtitle filename to the target-language filename.

    e.g. "Show.S01E01.en.srt" + "el" -> "Show.S01E01.el.srt"
         "Show.S01E01.srt"    + "el" -> "Show.S01E01.el.srt"
         "Show.S01E01.ass"    + "el" -> "Show.S01E01.el.ass"
    """
    lower = original_filename.lower()
    ext = "srt"
    base = original_filename
    for supported_ext in [".srt", ".ass", ".ssa"]:
        if lower.endswith(supported_ext):
            ext = supported_ext[1:]
            base = original_filename[:-len(supported_ext)]
            break
    return f"{strip_lang_tag(base)}.{code}.{ext}"


def matches_language(filename: str, stem: str, code: str) -> bool:
    """Check if a subtitle filename matches a media stem and language code.

    Matches "<stem>.<variant>.<ext>" for any known variant of ``code`` and
    supported extension (.srt, .ass, .ssa). A bare "<stem>.<ext>" (no language tag)
    is accepted only when matching English, preserving the historic source-language convention.
    """
    filename_lower = filename.lower()
    stem_lower = stem.lower()

    if not filename_lower.startswith(stem_lower):
        return False

    matched_ext = None
    for ext in [".srt", ".ass", ".ssa"]:
        if filename_lower.endswith(ext):
            matched_ext = ext
            break

    if not matched_ext:
        return False

    remaining = filename_lower[len(stem_lower):-len(matched_ext)]  # part between stem and extension
    for variant in variants_for(code):
        if remaining == f".{variant}":
            return True
    if code == "en" and remaining == "":
        return True
    return False
