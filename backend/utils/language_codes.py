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

# Human-readable name -> ISO-639-1 code. One English name per code: reverse lookups (code -> name) use it,
# and it is the name the settings store and the prompts say ("translating from English into Greek").
LANG_CODES: Dict[str, str] = {
    "Greek": "el", "English": "en", "Spanish": "es", "French": "fr",
    "German": "de", "Italian": "it", "Portuguese": "pt", "Russian": "ru",
    "Japanese": "ja", "Korean": "ko", "Chinese": "zh", "Dutch": "nl",
    "Polish": "pl", "Swedish": "sv", "Danish": "da", "Finnish": "fi",
    "Norwegian": "no", "Czech": "cs", "Hungarian": "hu", "Romanian": "ro",
    "Turkish": "tr", "Arabic": "ar", "Hebrew": "he", "Ukrainian": "uk",
    "Bulgarian": "bg", "Croatian": "hr", "Serbian": "sr", "Slovak": "sk", "Slovenian": "sl",
    "Lithuanian": "lt", "Latvian": "lv", "Estonian": "et", "Icelandic": "is", "Albanian": "sq",
    "Macedonian": "mk", "Bosnian": "bs", "Catalan": "ca", "Basque": "eu", "Galician": "gl", "Irish": "ga",
    "Welsh": "cy", "Maltese": "mt", "Belarusian": "be", "Georgian": "ka", "Armenian": "hy",
    "Azerbaijani": "az", "Kazakh": "kk", "Mongolian": "mn", "Hindi": "hi", "Bengali": "bn", "Tamil": "ta",
    "Telugu": "te", "Malayalam": "ml", "Marathi": "mr", "Urdu": "ur", "Persian": "fa", "Thai": "th",
    "Vietnamese": "vi", "Indonesian": "id", "Malay": "ms", "Filipino": "tl", "Khmer": "km", "Lao": "lo",
    "Burmese": "my", "Nepali": "ne", "Sinhala": "si", "Swahili": "sw", "Afrikaans": "af", "Amharic": "am",
}

# Other ways people write a language (native names, variants) -> code. Matched case-insensitively.
_ALIASES: Dict[str, str] = {
    "ελληνικά": "el", "ελληνικα": "el", "modern greek": "el",
    "español": "es", "espanol": "es", "castellano": "es", "latin american spanish": "es",
    "français": "fr", "francais": "fr", "deutsch": "de", "italiano": "it",
    "português": "pt", "portugues": "pt", "brazilian portuguese": "pt", "português do brasil": "pt",
    "русский": "ru", "日本語": "ja", "한국어": "ko", "中文": "zh", "简体中文": "zh", "繁體中文": "zh",
    "simplified chinese": "zh", "traditional chinese": "zh", "mandarin": "zh", "cantonese": "zh",
    "nederlands": "nl", "flemish": "nl", "polski": "pl", "svenska": "sv", "dansk": "da", "suomi": "fi",
    "norsk": "no", "bokmål": "no", "bokmal": "no", "norwegian bokmål": "no", "čeština": "cs", "cestina": "cs",
    "magyar": "hu", "română": "ro", "romana": "ro", "türkçe": "tr", "turkce": "tr", "العربية": "ar",
    "עברית": "he", "українська": "uk", "български": "bg", "hrvatski": "hr", "српски": "sr", "srpski": "sr",
    "slovenčina": "sk", "slovenščina": "sl", "slovene": "sl", "lietuvių": "lt", "latviešu": "lv", "eesti": "et",
    "íslenska": "is", "shqip": "sq", "македонски": "mk", "bosanski": "bs", "català": "ca", "euskara": "eu",
    "galego": "gl", "gaeilge": "ga", "cymraeg": "cy", "беларуская": "be", "ქართული": "ka", "հայերեն": "hy",
    "हिन्दी": "hi", "বাংলা": "bn", "தமிழ்": "ta", "اردو": "ur", "فارسی": "fa", "farsi": "fa", "ไทย": "th",
    "tiếng việt": "vi", "bahasa indonesia": "id", "bahasa melayu": "ms", "tagalog": "tl", "kiswahili": "sw",
}

# ISO-639-1 code -> set of lowercase filename variants used for on-disk
# matching. Includes the 639-1 code, common 639-2 (B/T) codes, and the
# English name, mirroring how *arr names subtitle files.
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
    lower = " ".join(name_or_code.strip().lower().split())

    for name, code in LANG_CODES.items():
        if name.lower() == lower:
            return code

    if lower in _ALIASES:
        return _ALIASES[lower]

    if lower in _KNOWN_CODES:
        return lower

    # A locale tag ("el-GR", "pt_BR", "zh-Hant") or a three-letter code ("ell", "gre", "spa").
    m = re.match(r"^([a-z]{2,3})(?:[-_][a-z0-9]{2,8})*$", lower)
    if m:
        base = m.group(1)
        if base in _KNOWN_CODES:
            return base
        for code, variants in LANG_VARIANTS.items():
            if base in variants:
                return code

    # "Greek (Modern)", "Portuguese (Brazil)", "Ελληνικά (Greek)": the longest known name inside it.
    names = sorted([*((n.lower(), c) for n, c in LANG_CODES.items()), *_ALIASES.items()], key=lambda nc: -len(nc[0]))
    for name, code in names:
        if len(name) >= 4 and name in lower:
            return code
    for name, code in names:   # an abbreviation of a name ("Port", "Gree")
        if len(lower) >= 3 and name.startswith(lower):
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
