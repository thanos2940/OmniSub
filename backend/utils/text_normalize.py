"""
Text Normalization and Morphology-aware Matching utilities.
"""

import unicodedata
import re
from typing import List, Optional

from utils.language_codes import to_code
from utils.text_repair import salvage_leak

# Standard Modern Greek suffixes list, ordered from longest to shortest
GREEK_SUFFIXES = [
    # Length 7
    "σιματοσ", "σιματων",
    # Length 6
    "ουσατε", "ουσουν", "ουνται", "ουμουν", "ησουνε", "ομαστε", "ουνπαν",
    "ιματοσ", "ιματων", "σιματα", "μενουσ", "τερουσ",
    # Length 5
    "ησατε", "ησανε", "ησαμε", "αγανε", "αγατε", "αγαμε", "οτανε", "ατανε",
    "ουσεσ", "ησουν", "ιαδεσ", "ιαδων", "ιματα", "ουδεσ",
    "ουδων", "μενησ", "μενων", "μενοσ", "μενοι", "μενεσ", "μενου", "τερησ",
    "τερων", "τεροσ", "τεροι", "τερεσ", "τερου",
    # Length 4
    "αδεσ", "αδων", "ουσα", "εστε", "ιμου", "ηδεσ", "ηδων", "εδεσ",
    "εδων", "μενη", "μενο", "μενα", "τερη", "τερο", "τερα", "ιουσ",
    "ατοσ", "ατων",
    # Length 3
    "ιμο", "εωσ", "εων", "ιου", "ιων", "ιοσ", "ιοι", "ουσ", "εισ", "ατα",
    # Length 2
    "ια", "ιε", "ιο", "ου", "ων", "οσ", "οι", "ησ", "εσ", "ασ", "υσ",
    # Length 1
    "η", "ο", "ι", "ε", "α", "σ"
]


def strip_accents_and_normalize(s: str) -> str:
    """Unicode NFD accent/diacritic folding."""
    nfd_form = unicodedata.normalize('NFD', s)
    return "".join(c for c in nfd_form if unicodedata.category(c) != 'Mn')


def stem_greek(word: str) -> str:
    """Lightweight suffix-stripping rules for Greek."""
    word = word.lower()
    word = strip_accents_and_normalize(word)
    word = word.replace('ς', 'σ')
    
    if len(word) <= 3:
        return word
        
    for suffix in GREEK_SUFFIXES:
        if word.endswith(suffix):
            stem = word[:-len(suffix)]
            if len(stem) >= 3:
                if "μμ" in stem:
                    norm = stem.replace("μμ", "μ")
                    if len(norm) >= 3:
                        stem = norm
                return stem
                
    if "μμ" in word:
        norm = word.replace("μμ", "μ")
        if len(norm) >= 3:
            return norm
    return word


def tokenize_and_normalize(text: str, lang: str = "Greek", stem_tokens: bool = False) -> List[str]:
    """Tokenize and normalize text."""
    if not text:
        return []
    text = text.lower()
    text = strip_accents_and_normalize(text)
    if lang.lower() == "greek":
        text = text.replace('ς', 'σ')
    
    # Extract word characters only
    tokens = re.findall(r'\w+', text)
    
    if stem_tokens:
        if lang.lower() == "greek":
            tokens = [stem_greek(t) for t in tokens]
    return tokens


def is_sublist(sub: List[str], parent: List[str]) -> bool:
    if not sub:
        return True
    n = len(sub)
    for i in range(len(parent) - n + 1):
        if parent[i:i+n] == sub:
            return True
    return False


# ---------------------------------------------------------------------------
# Greek Tonismos for Japanese Names (First-Syllable Accent Rule)
# ---------------------------------------------------------------------------

_ACCENT_TO_PLAIN = {
    'ά': 'α', 'έ': 'ε', 'ή': 'η', 'ί': 'ι', 'ό': 'ο', 'ύ': 'υ', 'ώ': 'ω',
    'Ά': 'Α', 'Έ': 'Ε', 'Ή': 'Η', 'Ί': 'Ι', 'Ό': 'Ο', 'Ύ': 'Υ', 'Ώ': 'Ω',
}

_PLAIN_TO_ACCENT = {
    'α': 'ά', 'ε': 'έ', 'η': 'ή', 'ι': 'ί', 'ο': 'ό', 'υ': 'ύ', 'ω': 'ώ', 'ϊ': 'ΐ', 'ϋ': 'ΰ',
    'Α': 'Ά', 'Ε': 'Έ', 'Η': 'Ή', 'Ι': 'Ί', 'Ο': 'Ό', 'Υ': 'Ύ', 'Ω': 'Ώ',
}

# Known canonical Japanese character name forms in Modern Greek anime localization
_CANONICAL_JAPANESE_NAMES_GREEK = {
    # Demon Slayer
    "ταντζίρο": "τάντζιρο", "ταντζίρου": "τάντζιρου", "ταντζιρό": "τάντζιρο", "ταντζιρο": "τάντζιρο", "ταντζιρου": "τάντζιρου",
    "νεζούκο": "νέζουκο", "νεζουκό": "νέζουκο", "νεζουκο": "νέζουκο",
    "ζενίτσου": "ζένιτσου", "ζενιτσού": "ζένιτσου", "ζενιτσου": "ζένιτσου",
    "ινοσούκε": "ινόσουκε", "ινοσουκέ": "ινόσουκε", "ινόσουκε": "ινόσουκε", "ινοσουκε": "ινόσουκε",
    "κιμπουτσούτζι": "κιμπούτσουτζι", "κιμπούτσουτζι": "κιμπούτσουτζι", "κιμπουτσουτζι": "κιμπούτσουτζι",
    "σινομπού": "σίνομπου", "σινόμπου": "σίνομπου", "σινομπου": "σίνομπου",
    "καναό": "κάναο", "καναο": "κάναο",
    "γκιγιού": "γκίγιου", "γκιγιου": "γκίγιου",
    "τομιοκά": "τόμιοκα", "τομιοκα": "τόμιοκα",
    "ρενγκοκού": "ρένγκοκου", "ρενγκοκου": "ρένγκοκου",
    "ακαζά": "άκαζα", "ακάζα": "άκαζα", "ακαζα": "άκαζα",
    "μουζάν": "μούζαν", "μουζαν": "μούζαν",
    "καμάντο": "κάμαντο", "καμαντο": "κάμαντο",
    # Naruto
    "ναρούτο": "νάρουτο", "ναρουτό": "νάρουτο", "ναρουτο": "νάρουτο",
    "σασούκε": "σάσουκε", "σασκέ": "σάσκε", "σασουκε": "σάσουκε", "σασκε": "σάσκε",
    "σακούρα": "σάκουρα", "σακουρά": "σάκουρα", "σακουρα": "σάκουρα",
    "κακάσι": "κάκασι", "κακασί": "κάκασι", "κακασι": "κάκασι",
    "ιτατσί": "ίτατσι", "ιτάτσι": "ίτατσι", "ιτατσι": "ίτατσι",
    "μαδάρα": "μάδαρα", "μαντάρα": "μάνταρα", "μαδαρα": "μάδαρα", "μανταρα": "μάνταρα",
    "χινάτα": "χίνατα", "χινατα": "χίνατα",
    "μπορούτο": "μπόρουτο", "μπορουτο": "μπόρουτο",
    # (not "σαράντα": that is the Greek word for "forty")
    "σαράδα": "σάραδα", "σαραδα": "σάραδα",
    "μιτσούκι": "μίτσουκι", "μιτσουκι": "μίτσουκι",
    "κανκουρό": "κάνκουρο", "κανκουρο": "κάνκουρο",
    "γκααρά": "γκάαρα", "γκααρα": "γκάαρα",
    "τσουναντέ": "τσούναντε", "τσουνάντε": "τσούναντε", "τσουναντε": "τσούναντε",
    "τζιραϊγιά": "τζίραγια", "τζιράγια": "τζίραγια", "τζιραγια": "τζίραγια",
    "οροτσιμαρού": "οροτσίμαρου", "οροτσιμάρου": "οροτσίμαρου", "οροτσιμαρου": "οροτσίμαρου",
    # One Piece
    "ζορό": "ζόρο", "ζορο": "ζόρο",
    "λουφί": "λούφι", "λουφι": "λούφι",
    "ναμί": "νάμι", "ναμι": "νάμι",
    "σαντζί": "σάντζι", "σαντζι": "σάντζι",
    "τσοπέρ": "τσόπερ", "τσοπερ": "τσόπερ",
    "ουσοπ": "ούσοπ", "ούσοπ": "ούσοπ",
    # My Hero Academia
    "ντεκού": "ντέκου", "ντεκου": "ντέκου",
    "μπακουγκό": "μπάκουγκο", "μπακουγκο": "μπάκουγκο",
    "τοντοροκί": "τοντόροκι", "τοντόροκι": "τοντόροκι", "τοντοροκι": "τοντόροκι", "τομοντόκι": "τοντόροκι",
    # Attack on Titan
    "μικασά": "μίκασα", "μικάσα": "μίκασα", "μικασα": "μίκασα",
    "αρμίν": "άρμιν", "αρμιν": "άρμιν",
    "έρεν": "έρεν", "ερεν": "έρεν", "ερέν": "έρεν",
    # Bleach / Dragon Ball / Jujutsu
    "ιτσιγκό": "ίτσιγκο", "ιτσιγκο": "ίτσιγκο",
    "ρεντζί": "ρέντζι", "ρεντζι": "ρέντζι",
    "γκοκού": "γκόκου", "γκοκου": "γκόκου",
    "βεγκετά": "βέγκετα", "βεγκέτα": "βέγκετα", "βεγκετα": "βέγκετα",
    "γκοντζό": "γκότζο", "γκοτζό": "γκότζο", "γκοτζο": "γκότζο",
    "σουνγκούρου": "σούγκουρου", "σουγκούρου": "σούγκουρου", "σουγκουρου": "σούγκουρου",
}

# The values follow shift_greek_accent_to_first_syllable (first syllable; third from the end for four or more
# syllables) and are recomputed with it below, so the table cannot drift from the rule. Older forms ("ίνοσουκε")
# are found through their unaccented key.


_DIGRAPH_PAIRS = ("ου", "αι", "ει", "οι", "αυ", "ευ", "υι")
_GREEK_VOWELS = "αεηιουωϊϋ"


def greek_syllable_nuclei(word: str) -> List[int]:
    """Per syllable of a Greek word, the index of the letter that carries its accent: the second letter of a
    digraph (ου, αι, ει, οι, αυ, ευ, υι), the vowel after a glide ι (consonant + ι + vowel is one syllable:
    "Κιο-το", "Ριουκ"), otherwise the vowel. ϊ and ϋ always start a syllable of their own."""
    w = "".join(_ACCENT_TO_PLAIN.get(c, c) for c in word).lower().replace("ΐ", "ϊ").replace("ΰ", "ϋ")
    out: List[int] = []
    i, n = 0, len(w)
    while i < n:
        if w[i] not in _GREEK_VOWELS:
            i += 1
            continue
        if (w[i] == "ι" and i > 0 and w[i - 1] not in _GREEK_VOWELS and i + 1 < n and w[i + 1] in "αεοω"):
            i += 1   # glide: the syllable's vowel is the next one
        if i + 1 < n and w[i:i + 2] in _DIGRAPH_PAIRS:
            out.append(i + 1)
            i += 2
        else:
            out.append(i)
            i += 1
    return out


def shift_greek_accent_to_first_syllable(word: str) -> str:
    """Put the accent of a transliterated Japanese name on its first syllable, the way Omnisub writes Japanese
    names in Greek (Tanjiro = Τάντζιρο, Nezuko = Νέζουκο, Kyojuro = Κιότζουρο). Greek never accents a syllable
    before the third from the end, so a name of four or more syllables takes it on the third from the end
    (Inosuke = Ινόσουκε, Kibutsuji = Κιμπούτσουτζι). A word of one syllable is left as it is ("ζει", "μου").

    Respects Greek digraphs (ου, αι, ει, οι, αυ, ευ) and the glide ι ("Κιό-", "Ριού-").
    """
    if not word or len(word) < 2:
        return word

    clean_word = "".join(_ACCENT_TO_PLAIN.get(ch, ch) for ch in word)
    nuclei = greek_syllable_nuclei(clean_word)
    if len(nuclei) <= 1:
        return word
    target_idx = nuclei[0] if len(nuclei) < 4 else nuclei[-3]

    ch_to_accent = clean_word[target_idx]
    accented_ch = _PLAIN_TO_ACCENT.get(ch_to_accent, ch_to_accent)

    result = clean_word[:target_idx] + accented_ch + clean_word[target_idx + 1:]
    return result


# Common Greek words (stopwords, particles, pronouns, conjunctions, common verbs and nouns)
# that must NEVER be treated as Japanese names or have accents shifted.
_COMMON_GREEK_WORDS = {
    # Conjunctions, particles, prepositions, articles
    "και", "κι", "να", "για", "το", "τη", "την", "της", "του", "τον", "τα", "των", "τους", "τις",
    "σε", "στο", "στη", "στην", "στης", "στου", "στον", "στα", "στων", "στους", "στις",
    "με", "απο", "γιατι", "πως", "που", "οτι", "αν", "σαν", "αλλα", "ομως",
    "λοιπον", "ακομα", "τωρα", "ποτε", "παντα", "μαζι", "ξανα", "μετα", "πριν",
    "μεσα", "εξω", "εδω", "εκει", "πανω", "κατω", "πολυ", "λιγο",
    "ολος", "ολη", "ολο", "ολοι", "ολες", "ολα",
    "αλλος", "αλλη", "αλλο", "αλλοι", "αλλες", "αλλα",
    "αυτος", "αυτη", "αυτο", "αυτοι", "αυτες", "αυτα",
    "μου", "σου", "μας", "σας", "εγω", "εσυ", "εμεις", "εσεις",
    "τι", "ποιος", "ποια", "ποιο", "ποιοι", "ποιες",
    "καποιος", "καποια", "καποιο", "καποιοι", "καποιες",
    "κανενας", "κανεις", "καμια", "κανενα", "τιποτα",
    "καλα", "κακο", "καλος", "καλη",
    # Common verbs
    "ειμαι", "εισαι", "ειναι", "ειμαστε", "ειστε", "ηταν", "ησουν", "ημουν",
    "εχω", "εχεις", "εχει", "εχουμε", "εχετε", "εχουν", "ειχα", "ειχε",
    "κανω", "κανεις", "κανει", "κανουμε", "κανετε", "κανουν", "εκανα", "εκανε", "κανε",
    "λεω", "λες", "λεει", "λεμε", "λετε", "λενε", "ειπα", "ειπε", "πες",
    "παω", "πας", "παει", "παμε", "πατε", "πανε", "πηγα", "πηγε", "πηγαινε",
    "ξερω", "ξερεις", "ξερει", "ξερουμε", "ξερετε", "ξερουν",
    "θελω", "θελεις", "θες", "θελει", "θελουμε", "θελετε", "θελουν",
    "μπορω", "μπορεις", "μπορει", "μπορουμε", "μπορειτε", "μπορουν",
    "πρεπει", "επρεπε", "γεια", "ναι", "οχι", "ενταξει", "νερο",
    "παιδι", "παιδια", "ανθρωπος", "ανθρωποι", "σπιτι", "μερα", "νυχτα", "χρονος", "χρονια",
}


_CANONICAL_JAPANESE_NAMES_GREEK = {k: shift_greek_accent_to_first_syllable(v)
                                  for k, v in _CANONICAL_JAPANESE_NAMES_GREEK.items()}


def fix_japanese_name_greek_tonos(name: str, guess: bool = False) -> str:
    """Ensure Japanese names in Greek place the accent (τόνος) on the first syllable.

    Translates misaccented and unaccented forms like:
        Tanjiro: Ταντζίρο / Ταντζιρο -> Τάντζιρο
        Nezuko:  Νεζούκο / Νεζουκο   -> Νέζουκο
        Zenitsu: Ζενίτσου / Ζενιτσου -> Ζένιτσου
        Inosuke: Ινοσούκε / Ίνοσουκε -> Ινόσουκε (four syllables)
        Naruto:  Ναρούτο / Ναρουτο   -> Νάρουτο
        Sasuke:  Σασούκε / Σασκέ     -> Σάσουκε / Σάσκε

    By default only the known names (``_CANONICAL_JAPANESE_NAMES_GREEK``) change, so a whole subtitle
    line is safe. ``guess=True`` also moves the accent of other words that look like a transliterated
    Japanese name; it rewrites ordinary Greek words and Western names just as readily ("Περίμενε",
    "Μαρία", "Λονδίνο"), so it is only for the Greek rendering of a single glossary term whose English
    source is romaji, in a Japanese work (``normalize_japanese_term``).
    """
    if not name or not isinstance(name, str):
        return name

    tokens = re.split(r'(\s+|[.,;!?"\'-])', name)
    out_tokens = []
    is_standalone_term = len([t for t in tokens if t and any(c.isalpha() for c in t)]) <= 2

    for token in tokens:
        if not token or not any(c.isalpha() for c in token):
            out_tokens.append(token)
            continue

        lower_token = token.lower()
        clean_lower = "".join(_ACCENT_TO_PLAIN.get(c, c) for c in lower_token)

        corrected_lower = None
        # 1. Direct canonical lookup (covers accented and unaccented variants)
        if lower_token in _CANONICAL_JAPANESE_NAMES_GREEK:
            corrected_lower = _CANONICAL_JAPANESE_NAMES_GREEK[lower_token]
        elif clean_lower in _CANONICAL_JAPANESE_NAMES_GREEK:
            corrected_lower = _CANONICAL_JAPANESE_NAMES_GREEK[clean_lower]
        elif clean_lower in _COMMON_GREEK_WORDS:
            # Never mutate regular Greek words (e.g. "και", "νερό", "καλά", "πρέπει")
            corrected_lower = None
        elif guess and (token[0].isupper() or is_standalone_term) and len(clean_lower) >= 3 and any(clean_lower.endswith(sfx) for sfx in ("ο", "ου", "ε", "ι", "α", "ν", "ρ")):
            # Heuristic for Japanese proper nouns (capitalized in sentences, or standalone term)
            has_jp_clusters = any(cl in clean_lower for cl in (
                "τζ", "τσ", "κα", "κι", "κου", "κε", "κο",
                "ρο", "ρου", "ρα", "ρι", "σο", "σου", "σα",
                "το", "τσι", "νο", "νε", "να", "ζο", "ζε",
                "μπο", "μπου", "μπα", "μπι", "μπε", "γκ"
            ))
            if has_jp_clusters:
                corrected_lower = shift_greek_accent_to_first_syllable(clean_lower)

        if corrected_lower:
            # Modern Greek orthography: All-caps words do NOT take tonos accents
            if token.isupper():
                clean_upper = "".join(_ACCENT_TO_PLAIN.get(c, c) for c in token)
                out_tokens.append(clean_upper)
            elif token and token[0].isupper():
                out_tokens.append(corrected_lower.capitalize())
            else:
                out_tokens.append(corrected_lower)
        else:
            out_tokens.append(token)

    return "".join(out_tokens)


# A romanised Japanese word (Hepburn or Kunrei) is a run of these syllables: an optional consonant
# (cluster) and a vowel, a syllabic n, or the first half of a doubled consonant ("Hattori", "Ippo").
_ROMAJI_WORD = re.compile(
    r"^(?:(?:ky|gy|sh|ch|ny|hy|by|py|my|ry|ts|dz|ty|zy|dy|sy|jy|[kgsztdnhbpmrwyfvj])?[aeiou]"
    r"|n(?![aeiouy])|([kgsztdbpmhjfr])(?=\1)|t(?=ch))+$")
_LONG_VOWELS = str.maketrans("āīūēōâîûêôàáèéìíòóùú", "aiueoaiueoaaeeiioouu")


def looks_like_romaji(text: str) -> bool:
    """True when every word of ``text`` reads as romanised Japanese ("Tanjiro", "Kamado Nezuko",
    "Ken'ichi", "Kyōjurō"); "London", "Daniel", "Demon Slayer" do not."""
    words = re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", (text or "").lower())
    if not words:
        return False
    for word in words:
        plain = re.sub(r"['’-]", "", word).translate(_LONG_VOWELS)
        if len(plain) < 2 or not plain.isascii() or not _ROMAJI_WORD.match(plain):
            return False
    return True


# Greek spelling -> a rough Latin one, to tell whether a Greek word transliterates a romaji word.
_GREEK_TO_LATIN = (("ντ", "d"), ("μπ", "b"), ("γκ", "g"), ("γγ", "g"), ("τσ", "ts"), ("τζ", "j"), ("ου", "u"),
                   ("αι", "e"), ("ει", "i"), ("οι", "i"), ("θ", "th"), ("χ", "h"), ("ψ", "ps"), ("ξ", "x"),
                   ("φ", "f"), ("β", "v"), ("γ", "g"), ("δ", "d"), ("ζ", "z"), ("κ", "k"), ("λ", "l"), ("μ", "m"),
                   ("ν", "n"), ("π", "p"), ("ρ", "r"), ("σ", "s"), ("ς", "s"), ("τ", "t"), ("α", "a"), ("ε", "e"),
                   ("η", "i"), ("ι", "i"), ("ο", "o"), ("υ", "i"), ("ω", "o"), ("ϊ", "i"), ("ΐ", "i"), ("ϋ", "i"))


def _rough_latin(word: str) -> str:
    out = "".join(_ACCENT_TO_PLAIN.get(c, c) for c in word.lower())
    for greek, latin in _GREEK_TO_LATIN:
        out = out.replace(greek, latin)
    return out


def _rough_romaji(word: str) -> str:
    w = re.sub(r"['’-]", "", word.lower()).translate(_LONG_VOWELS)
    return w.replace("ch", "ts").replace("sh", "s").replace("y", "i").replace("ou", "o").replace("uu", "u")


def _transliterates(greek_word: str, romaji: List[str]) -> bool:
    """Whether a Greek word spells one of the romaji words. A word with a Greek inflection (-ς, -ων) is a Greek
    word, even when it shares its root with the English one ("Demon" = "Δαίμονας", "Titan" = "Τιτάνας")."""
    from difflib import SequenceMatcher
    plain = "".join(_ACCENT_TO_PLAIN.get(c, c) for c in greek_word.lower())
    if plain.endswith(("ς", "ων")):
        return False
    latin = _rough_latin(plain).replace("ou", "o")
    return len(latin) >= 3 and any(SequenceMatcher(None, latin, r).ratio() >= 0.75 for r in romaji)


def normalize_japanese_term(source: str, translation: str, japanese_work: bool) -> str:
    """The Greek rendering of a glossary term, with the accent on the first syllable for Japanese names: the
    known names always; in a Japanese work (utils/work_origin.py) also every word of the rendering that
    transliterates a romaji word of the English source ("Nichirin Blade" = "Σπαθί Νίτσιριν": "Σπαθί" stays).
    Everything else is returned as it was."""
    if not translation:
        return translation
    fixed = fix_japanese_name_greek_tonos(translation) or translation
    if not japanese_work:
        return fixed
    romaji = [_rough_romaji(w) for w in re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", source or "")
              if len(w) >= 3 and looks_like_romaji(w)]
    if not romaji:
        return fixed
    out = []
    for token in re.split(r"(\s+|[.,;!?\"'-])", fixed):
        if token and any(c.isalpha() for c in token) and _GREEK_LETTER.search(token) and _transliterates(token, romaji):
            shifted = shift_greek_accent_to_first_syllable("".join(_ACCENT_TO_PLAIN.get(c, c) for c in token.lower()))
            if token.isupper():
                token = "".join(_ACCENT_TO_PLAIN.get(c, c) for c in token)
            elif token[0].isupper():
                token = shifted.capitalize()
            else:
                token = shifted
        out.append(token)
    return "".join(out)


def contains_term(
    haystack: str,
    term: str,
    lang: str = "Greek",
    normalization: bool = True,
    stemming: bool = True
) -> bool:
    """Check if haystack contains term, considering morphology."""
    if not haystack or not term:
        return False
        
    if not normalization:
        return term.lower() in haystack.lower()
        
    haystack_tokens = tokenize_and_normalize(haystack, lang, stem_tokens=False)
    term_tokens = tokenize_and_normalize(term, lang, stem_tokens=False)
    
    if not term_tokens:
        return False
        
    if is_sublist(term_tokens, haystack_tokens):
        return True
        
    if stemming and lang.lower() == "greek":
        haystack_stems = [stem_greek(t) for t in haystack_tokens]
        term_stems = [stem_greek(t) for t in term_tokens]
        if is_sublist(term_stems, haystack_stems):
            return True
            
    return False
 
 
def sanitize_translation_text(
    text: str,
    source_text: Optional[str] = None,
    target_lang: Optional[str] = None,
    target_lang_code: Optional[str] = None,
) -> str:
    """Detect and strip any dual source/target formatting from translation output.
    
    Protects against LLM prompt leaks where translations are emitted as:
    - 'How are you -> Πως είσαι'
    - 'source => target' or 'source → target'
    - 'EN: How are you EL: Πως είσαι' or 'EN: ... \\n EL: ...'
    - 'English: How are you | Greek: Πως είσαι'
    - 'Source: How are you - Target: Πως είσαι'
    - 'EL: Πως είσαι' or 'Target: Πως είσαι'
    - '<i>An E-rank hunter...</i> -> <i>Ένας κυνηγός...</i>'
    - '{\\an8}Source -> {\\an8}Target'

    Also strips prompt text that leaked in place of a translation (``utils.text_repair.salvage_leak``):
    a work-file prefix ('12| Γεια'), an echoed '[prev] ...' marker, a 'EN: ... CORRECTED: ...' reviewer
    block, an episode summary. What is salvageable is kept; a leak with nothing of a translation in it
    gives "", which every caller treats as an untranslated line (the model path retries it, the export
    leaves it blank, the agent audit lists it as a gap) rather than storing another line's text.
    """
    if not text or not isinstance(text, str):
        return text or ""

    val = text.strip()
    if not val:
        return ""

    # Guard: Do not modify valid subtitle timecodes
    if re.match(r'^\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}', val):
        return val

    # Leaked prompt text is no translation. Without a source line the checks that need it are skipped.
    leak_code = (target_lang_code or "").strip() or (to_code(target_lang) if (target_lang or "").strip() else "")
    leak = salvage_leak(val, source_text, leak_code)
    if leak:
        val = leak[1].strip()
        if not val:
            return ""

    # Build language labels pattern
    lang_labels = ["EL", "Greek", "Target", "Translation", "TGT"]
    if target_lang:
        lang_labels.append(re.escape(target_lang.strip()))
    if target_lang_code:
        lang_labels.append(re.escape(target_lang_code.strip()))
    lang_labels_pattern = "|".join(dict.fromkeys(lang_labels))

    # 1. Block-level labeled pairs (e.g. EN: ... \n EL: ...)
    labeled_block_pattern = re.compile(
        rf'(?:^|[\r\n])\s*(?:\[(?:EN|English|Source|Original|SRC|ORIG)\]|(?:EN|English|Source|Original|SRC|ORIG)\s*[:=\-])\s*(.*?)\s*(?:[\r\n]|\s+|\|)\s*(?:\[(?:{lang_labels_pattern})\]|(?:{lang_labels_pattern})\s*[:=\-])\s*(.*)$',
        re.IGNORECASE | re.DOTALL
    )
    m = labeled_block_pattern.search(val)
    if m:
        target_cand = m.group(2).strip()
        if target_cand:
            val = target_cand

    def _sanitize_line(line: str, s_line: Optional[str] = None) -> str:
        s = line.strip()
        if not s:
            return ""
        if re.match(r'^\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}', s):
            return s

        # A. Strip outer quotes wrapping the whole line first
        # Only if the opening quote is not closed before an arrow
        for q_open, q_close in (('"', '"'), ("'", "'"), ('“', '”')):
            if s.startswith(q_open) and s.endswith(q_close) and len(s) >= 2:
                inner = s[1:-1]
                arrow_indices = [inner.find(a) for a in ("->", "=>", "→") if inner.find(a) != -1]
                if arrow_indices:
                    first_arrow = min(arrow_indices)
                    if inner.find(q_close, 0, first_arrow) != -1:
                        break
                s = inner.strip()
                break

        # B. Outer HTML tag wrapping
        tag_prefix = ""
        tag_suffix = ""
        html_wrap = re.match(r'^<([a-zA-Z0-9]+)(?:\s+[^>]*)?>(.*)</\1>$', s, re.DOTALL)
        if html_wrap:
            tag_name = html_wrap.group(1)
            # Only treat as outer wrap if not closed inside (e.g. not <i>A</i> -> <i>B</i>)
            if f"</{tag_name}>" not in html_wrap.group(2):
                tag_prefix = s[:html_wrap.start(2)]
                tag_suffix = s[html_wrap.end(2):]
                s = html_wrap.group(2).strip()

        # C. ASS position tag prefix
        ass_prefix = ""
        pos_m = re.match(r'^(\{\\[^}]+\})', s)
        if pos_m:
            ass_prefix = pos_m.group(1)

        # D. In-line labeled pair: EN: ... EL: ...
        line_labeled = re.compile(
            rf'(?:^|\s+|\|)\s*(?:\[(?:EN|English|Source|Original|SRC|ORIG)\]|(?:EN|English|Source|Original|SRC|ORIG)\s*[:=\-])\s*(.*?)\s*(?:\||\s+)\s*(?:\[(?:{lang_labels_pattern})\]|(?:{lang_labels_pattern})\s*[:=\-])\s*(.*)$',
            re.IGNORECASE
        )
        lm = line_labeled.search(s)
        if lm:
            cand = lm.group(2).strip()
            if cand:
                s = cand

        # E. Arrow delimiters: '->', '=>', '→'
        s_line_clean = s_line.strip() if s_line else ""
        s_has_arrow = any(arrow in s_line_clean for arrow in ("->", "=>", "→")) if s_line_clean else False

        arrow_pattern = re.compile(r'(?<!\d{2}:\d{2}:\d{2})\s*(?:->|=>|→)\s*(?!\d{2}:\d{2}:\d{2})')
        if arrow_pattern.search(s):
            parts = arrow_pattern.split(s)
            if len(parts) >= 2:
                should_split = True
                if s_has_arrow:
                    should_split = (parts[0].strip().lower() == s_line_clean.lower())
                if should_split:
                    s = parts[-1].strip()

        # F. If s_line_clean is provided and matches the start of s
        if s_line_clean and len(s_line_clean) >= 3 and not s_has_arrow:
            s_norm = s_line_clean.strip('"\'').strip()
            if s.lower().startswith(s_norm.lower()):
                rem = s[len(s_norm):].strip()
                sep_m = re.match(r'^(?:->|=>|→|[:=\/\|]|-)\s*(.*)$', rem)
                if sep_m and sep_m.group(1).strip():
                    s = sep_m.group(1).strip()

        # G. Strip leading single target labels (must have explicit delimiter: [EL], EL:, Target:, Greek:)
        # Notice: [EL] does not consume trailing '-' so dialogue dashes are preserved!
        s = re.sub(
            rf'^(?:\[(?:{lang_labels_pattern})\]\s*[:=]?\s*|(?:{lang_labels_pattern})\s*[:=\-]\s*)',
            '',
            s,
            flags=re.IGNORECASE
        ).strip()

        # H. Strip stray leading colons or equals (PRESERVING leading '-' for dialogue dashes!)
        s = re.sub(r'^[:=]+\s*', '', s).strip()

        # I. Clean up quotes around target candidate
        for q_open, q_close in (('"', '"'), ("'", "'"), ('“', '”')):
            if s.startswith(q_open) and s.endswith(q_close) and len(s) >= 2:
                s = s[1:-1].strip()
            if s.endswith(q_close) and not s.startswith(q_open):
                s = s[:-1].strip()
            if s.startswith(q_open) and not s.endswith(q_close):
                s = s[1:].strip()

        # J. Re-attach outer HTML or ASS tag if needed
        if tag_prefix and tag_suffix:
            if not s.startswith(tag_prefix) and not s.endswith(tag_suffix):
                s = f"{tag_prefix}{s}{tag_suffix}"
        if ass_prefix:
            if not s.startswith("{\\"):
                s = f"{ass_prefix}{s}"

        return s

    # 2. Multi-line support: sanitize each line independently
    if "\n" in val:
        lines = val.split("\n")
        src_lines = source_text.split("\n") if source_text and "\n" in source_text else [source_text] * len(lines) if source_text else [None] * len(lines)
        cleaned_lines = []
        for i, l in enumerate(lines):
            sl = src_lines[i] if i < len(src_lines) else None
            cleaned_lines.append(_sanitize_line(l, s_line=sl))
        out = "\n".join(cleaned_lines)
    else:
        out = _sanitize_line(val, s_line=source_text)
    if leak_code == "el":
        out = greek_question_marks(out)
    return out


_GREEK_LETTER = re.compile(r"[\u0370-\u03FF\u1F00-\u1FFF]")
_TAG_OR_OVERRIDE = re.compile(r"(\{[^}]*\}|<[^>]+>)")


def greek_question_marks(text: str) -> str:
    """Greek writes the question mark as ';'. A Latin '?' in a line that is mostly Greek becomes ';' (ASS
    override blocks and HTML tags are left alone); a line that is mostly Latin letters is not touched."""
    if not text or "?" not in text:
        return text
    parts = _TAG_OR_OVERRIDE.split(text)
    plain = "".join(p for p in parts if p[:1] not in ("{", "<"))
    greek = len(_GREEK_LETTER.findall(plain))
    if not greek or greek <= len(re.findall(r"[A-Za-z]", plain)):
        return text
    return "".join(part if part[:1] in ("{", "<") else part.replace("?", ";") for part in parts)
