"""
Text Normalization and Morphology-aware Matching utilities.
"""

import unicodedata
import re
from typing import List

# Standard Modern Greek suffixes list, ordered from longest to shortest
GREEK_SUFFIXES = [
    # Verbs / long inflections
    "ιαδεσ", "αδεσ", "ιαδων", "αδων", "ουσα", "ουσεσ", "ουσατε", "αγατε", "αγανε",
    "αγαμε", "ησατε", "ησαμε", "ησανε", "ησουν", "ησουνε", "ησατε", "ουνται",
    "ουμουν", "ουσουν", "οτανε", "ατανε", "ουνπαν", "ομαστε", "εστε",
    # Common noun/adjective suffixes
    "ιουσ", "ιου", "ιων", "ιοσ", "ιοι", "ια", "ιε", "ιο",
    "ουσ", "ου", "ων", "οσ", "οι", "ησ", "εσ", "ασ", "υσ",
    "εισ", "η", "ο", "ι", "ε", "α", "σ"
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
                return stem
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
