"""
Glossary Enforcer — Non-Destructive Two-Pass Consistency Check & Diagnostic

After LLM translation, runs a fast morphology-aware check to detect and repair
glossary violations. Non-destructively avoids injecting raw nominative lemmas into
inflected Greek clauses (e.g. after oblique articles like 'τον', 'του', 'της'),
leaving inflected corrections to the zero-token QC validator and batched repair pass.

Usage:
    from utils.glossary_enforcer import enforce_glossary, detect_glossary_violations

    corrected_lines, count = enforce_glossary(translated_lines, glossary, source_lines)
"""

import re
from typing import Dict, List, Tuple


_OBLIQUE_GREEK_MARKERS = {
    # Masculine oblique
    "τον", "του", "στον", "στου", "εναν", "ενος",
    # Feminine genitive
    "τησ", "στησ", "μιασ",
    # Plural oblique
    "των", "τουσ", "τισ", "στων", "στουσ", "στισ",
}

_GOVERNING_PREPOSITIONS = {
    "απο", "για", "με", "σε", "προσ", "χωρισ", "διπλα", "κατα", "μετα", "αντι", "περι"
}


def _is_declinable_greek_term(lemma: str) -> bool:
    """Check if a Greek lemma inflects across grammatical cases."""
    if not lemma:
        return False
    from utils.text_normalize import strip_accents_and_normalize
    norm = strip_accents_and_normalize(lemma.strip()).lower().replace('ς', 'σ')
    declinable_endings = ("οσ", "ησ", "ασ", "υσ", "α", "η", "μα", "σιμο", "ιμο")
    return any(norm.endswith(end) for end in declinable_endings)


def _is_destructive_in_greek(trans_line: str, match_start: int, lemma: str) -> bool:
    """Detect if substituting the raw nominative lemma into trans_line would break Greek syntax.
    
    If the match is in an oblique case context (e.g. after 'τον', 'του', 'της', 'στον', etc.)
    and the lemma is declinable, injecting the uninflected nominative is destructive.
    """
    if not _is_declinable_greek_term(lemma):
        return False

    prefix = trans_line[:match_start].strip()
    if not prefix:
        return False

    from utils.text_normalize import strip_accents_and_normalize
    norm_prefix = strip_accents_and_normalize(prefix).lower().replace('ς', 'σ')
    words_before = re.findall(r'\b\w+\b', norm_prefix)
    if not words_before:
        return False

    last_word = words_before[-1]
    if last_word in _OBLIQUE_GREEK_MARKERS:
        return True

    if len(words_before) >= 2:
        second_last = words_before[-2]
        if second_last in _GOVERNING_PREPOSITIONS and last_word in ("τον", "του", "τη", "την", "τησ", "το", "τα", "τουσ", "τισ", "των"):
            if last_word in _OBLIQUE_GREEK_MARKERS:
                return True

    return False


def enforce_glossary(
    translated_lines: List[str],
    glossary: Dict,
    source_lines: List[str],
    target_lang: str = "Greek",
) -> Tuple[List[str], int]:
    """Apply glossary consistency corrections to translated subtitle lines non-destructively.

    For each active glossary term (where keep_original=False), checks if:
    1. The source line contains the original term
    2. The translated line does NOT contain the expected translation (even inflected)

    If both conditions are true:
    - If the raw term leaked into translated_lines, checks whether replacement is safe.
    - If the leaked term is in an inflected oblique context in Greek (e.g. 'τον Peter'),
      avoids destructive raw nominative replacement ('τον Πέτρος') and leaves the line
      to be repaired with correct case declension in the QC repair pass.
    - If safe (nominative / indeclinable), replaces the leak with exact word boundaries.

    Args:
        translated_lines: List of translated subtitle strings.
        glossary: Project glossary dict with {"terms": [...]} structure.
        source_lines: Original (pre-translation) subtitle strings for reference.
        target_lang: Target language for morphology checks.

    Returns:
        Tuple of (corrected_lines, correction_count)
    """
    terms = glossary.get("terms", []) if glossary else []
    if not terms:
        return translated_lines, 0

    from utils import storage
    from utils.text_normalize import contains_term

    config = storage.load_global_config()
    morphology_normalization = config.get("morphology_normalization", True)
    morphology_stemming = config.get("morphology_stemming", True)

    lines = list(translated_lines)
    correction_count = 0

    for term_def in terms:
        term = term_def.get("term", "")
        translation = term_def.get("translation", "")
        keep_original = term_def.get("keep_original", False)
        case_sensitive = term_def.get("case_sensitive", True)

        # Skip terms that shouldn't be translated or have no translation
        if keep_original or not term or not translation:
            continue

        # Build word-boundary regex for safe replacement
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", flags=flags)

        for i, (src_line, trans_line) in enumerate(zip(source_lines, lines)):
            if not src_line or not trans_line:
                continue

            # Check: does the source line contain this term?
            if not contains_term(src_line, term, lang="English", normalization=morphology_normalization, stemming=False):
                continue

            # Check: does the translated line already contain the expected translation (including inflections)?
            if contains_term(trans_line, translation, lang=target_lang, normalization=morphology_normalization, stemming=morphology_stemming):
                continue

            # Violation detected — check if safe non-destructive correction is possible
            match = pattern.search(trans_line)
            if not match:
                continue

            # In Greek, avoid destructive raw nominative replacement in oblique inflected clauses
            if target_lang.lower() == "greek" and _is_destructive_in_greek(trans_line, match.start(), translation):
                continue

            corrected, n = pattern.subn(translation, trans_line, count=1)
            if n > 0:
                lines[i] = corrected
                correction_count += 1

    return lines, correction_count


def detect_glossary_violations(
    source_lines: List[str],
    translated_lines: List[str],
    glossary: Dict,
    target_lang: str = "Greek",
) -> List[Dict]:
    """Non-destructively inspect and return structured glossary violations.

    Returns a list of dicts:
    [{"index": line_idx, "term": src_term, "expected": tgt_term, "source_line": src, "translated_line": tgt}]
    """
    violations = []
    terms = glossary.get("terms", []) if glossary else []
    if not terms:
        return violations

    from utils import storage
    from utils.text_normalize import contains_term

    config = storage.load_global_config()
    morphology_normalization = config.get("morphology_normalization", True)
    morphology_stemming = config.get("morphology_stemming", True)

    for term_def in terms:
        src_term = term_def.get("term", "")
        tgt_term = term_def.get("translation", "")
        keep_original = term_def.get("keep_original", False)

        if keep_original or not src_term or not tgt_term:
            continue

        for i, (src_line, trans_line) in enumerate(zip(source_lines, translated_lines)):
            if not src_line or not trans_line:
                continue

            if not contains_term(src_line, src_term, lang="English", normalization=morphology_normalization, stemming=False):
                continue

            if contains_term(trans_line, tgt_term, lang=target_lang, normalization=morphology_normalization, stemming=morphology_stemming):
                continue

            violations.append({
                "index": i,
                "term": src_term,
                "expected": tgt_term,
                "source_line": src_line,
                "translated_line": trans_line,
            })

    return violations
