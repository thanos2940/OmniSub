import pytest
from utils.text_normalize import strip_accents_and_normalize, stem_greek, contains_term, tokenize_and_normalize
from utils.translation_memory import TranslationMemory
from utils.glossary_enforcer import enforce_glossary
from utils.consistency import build_report
from utils import storage
import tempfile
import shutil
from pathlib import Path


def test_strip_accents_and_normalize():
    assert strip_accents_and_normalize("Καπετάνιος") == "Καπετανιος"
    assert strip_accents_and_normalize("Ήμουν κουρασμένη") == "Ημουν κουρασμενη"
    assert strip_accents_and_normalize("Φρίρεν") == "Φριρεν"


def test_stem_greek():
    # Test inflected forms mapping to the same stem
    stem_base = stem_greek("καπετανιοσ")
    assert stem_base == "καπεταν"
    assert stem_greek("καπετανιοι") == "καπεταν"
    assert stem_greek("καπετανιου") == "καπεταν"
    assert stem_greek("καπετανιων") == "καπεταν"
    assert stem_greek("καπετανια") == "καπεταν"
    assert stem_greek("καπετανιο") == "καπεταν"
    assert stem_greek("καπετανιε") == "καπεταν"
    assert stem_greek("καπετανι") == "καπεταν"
    assert stem_greek("καπετανιουσ") == "καπεταν"

    # Short words should not be stemmed, but are normalized (ς -> σ)
    assert stem_greek("της") == "τησ"
    assert stem_greek("τον") == "τον"


def test_contains_term():
    # Exact match
    assert contains_term("Γεια σου, καπετανιος.", "καπετανιος", lang="Greek")
    # Stemmed match (inflected form)
    assert contains_term("Γεια σου, καπετανιε.", "καπετάνιος", lang="Greek")
    assert contains_term("καπετανιοι στο πλοιο.", "Καπετάνιος", lang="Greek")
    
    # Substring matches should not trigger unless word matches
    assert not contains_term("καπετανιοπουλο", "καπετάνιος", lang="Greek")


def test_exact_matches_gates():
    # Create a temporary translation memory for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        # Override TM_DIR in translation_memory
        import utils.translation_memory
        original_tm_dir = utils.translation_memory.TM_DIR
        utils.translation_memory.TM_DIR = Path(tmpdir)
        
        try:
            tm = TranslationMemory("test_project")
            
            # Store some entries
            tm.add_translations(
                source_lines=["Hello", "What is your name?", "Right."],
                target_lines=["Γεια", "Ποιο είναι το όνομά σου;", "Σωστά."],
                episode_name="Ep01",
                character="Frieren"
            )
            
            # Test word count limit gate
            # "Hello" is 1 word, "What is your name?" is 4 words.
            # If max_words=3, "What is your name?" should be blocked.
            exact = tm.get_exact_matches(["Hello", "What is your name?"], max_words=3)
            assert 0 in exact  # "Hello" passes (1 <= 3)
            assert 1 not in exact  # "What is your name?" blocked (4 > 3)
            
            # Test speaker gate
            # If require_same_speaker=True and speaker matches:
            exact2 = tm.get_exact_matches(["Hello"], require_same_speaker=True, speakers=["Frieren"])
            assert 0 in exact2
            assert exact2[0]["target"] == "Γεια"
            assert exact2[0]["episode"] == "Ep01"
            
            # If require_same_speaker=True and speaker mismatches:
            exact3 = tm.get_exact_matches(["Hello"], require_same_speaker=True, speakers=["Fern"])
            assert 0 not in exact3
            
        finally:
            utils.translation_memory.TM_DIR = original_tm_dir


def test_glossary_enforcer_morphology():
    glossary = {
        "terms": [
            {"term": "Captain", "translation": "Καπετάνιος", "type": "person"}
        ]
    }
    
    # Case 1: Already has inflected target form -> no correction needed
    corrected, count = enforce_glossary(
        translated_lines=["Γεια σου, καπετάνιε."],
        glossary=glossary,
        source_lines=["Hello, Captain."],
        target_lang="Greek"
    )
    assert count == 0
    assert corrected[0] == "Γεια σου, καπετάνιε."
    
    # Case 2: Untranslated source term leaked -> should be replaced by canonical target
    corrected2, count2 = enforce_glossary(
        translated_lines=["Γεια σου, Captain."],
        glossary=glossary,
        source_lines=["Hello, Captain."],
        target_lang="Greek"
    )
    assert count2 == 1
    assert "Καπετάνιος" in corrected2[0]
