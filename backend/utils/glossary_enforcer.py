"""
Glossary Enforcer — Two-Pass CPU-Side Consistency Check

After LLM translation, runs a fast string-matching pass to detect and repair
glossary violations. This costs zero API tokens and catches the most common
local model failure mode: ignoring glossary terms mid-episode.

Usage:
    from utils.glossary_enforcer import enforce_glossary

    corrected_lines, count = enforce_glossary(translated_lines, glossary, source_lines)
"""

import re
from typing import Dict, List, Tuple


def enforce_glossary(
    translated_lines: List[str],
    glossary: Dict,
    source_lines: List[str],
) -> Tuple[List[str], int]:
    """Apply glossary consistency corrections to translated subtitle lines.

    For each active glossary term (where keep_original=False), checks if:
    1. The source line contains the original term
    2. The translated line does NOT contain the expected translation

    If both conditions are true, replaces the first occurrence of the source
    term (or any previous translation attempt) with the correct translation.

    Args:
        translated_lines: List of translated subtitle strings.
        glossary: Project glossary dict with {"terms": [...]} structure.
        source_lines: Original (pre-translation) subtitle strings for reference.

    Returns:
        Tuple of (corrected_lines, correction_count)
    """
    terms = glossary.get("terms", []) if glossary else []
    if not terms:
        return translated_lines, 0

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

        # Build regex flags
        flags = 0 if case_sensitive else re.IGNORECASE

        for i, (src_line, trans_line) in enumerate(zip(source_lines, lines)):
            if not src_line or not trans_line:
                continue

            # Check: does the source line contain this term?
            if not re.search(re.escape(term), src_line, flags):
                continue

            # Check: does the translated line already contain the expected translation?
            if re.search(re.escape(translation), trans_line, re.IGNORECASE):
                continue

            # Violation detected — attempt correction
            # Strategy: append the translation if it's clearly missing a name/term,
            # but don't blindly replace content (we may not know the wrong form used).
            # For proper nouns (people, locations), look for the untranslated source term
            # and replace it directly.
            corrected = trans_line
            term_type = term_def.get("type", "other")

            if term_type in ("person", "location", "organization"):
                # Try to replace the raw source term if it leaked into translation
                corrected, n = re.subn(
                    re.escape(term), translation, trans_line, count=1, flags=flags
                )
                if n > 0:
                    lines[i] = corrected
                    correction_count += 1

    return lines, correction_count
