"""
Comprehensive M2 Test Suite:
- Morphologically-Aware Greek Suffix Stemming (Imparisyllabics, Ancient Genitives, Participles, Comparatives)
- Non-Destructive Glossary Enforcement (Elimination of raw nominative lemma overrides in oblique clauses)
- Zero-Token Morphology-Aware QC Funnel Validation & Batched Repair Integration
"""

import pytest
from unittest.mock import patch

from utils.text_normalize import stem_greek, contains_term, tokenize_and_normalize, strip_accents_and_normalize
from utils.glossary_enforcer import enforce_glossary, detect_glossary_violations
from utils.qc_funnel import validate_lines, build_repair_prompt


# ==============================================================================
# SECTION 1: Modern Greek Morphology & Suffix Stemming Unit Tests
# ==============================================================================

class TestGreekMorphologyStemmer:

    def test_parisyllabic_masculines(self):
        """Parisyllabic masculine nouns/names in -ος, -ας, -ης across all cases."""
        # -ος: Πέτρος
        assert stem_greek("Πέτρος") == "πετρ"
        assert stem_greek("Πέτρου") == "πετρ"
        assert stem_greek("Πέτρο") == "πετρ"
        assert stem_greek("Πέτρε") == "πετρ"
        assert stem_greek("Πέτροι") == "πετρ"
        assert stem_greek("Πέτρων") == "πετρ"
        assert stem_greek("Πέτρους") == "πετρ"

        # -ος: Καπετάνιος
        assert stem_greek("Καπετάνιος") == "καπεταν"
        assert stem_greek("Καπετάνιου") == "καπεταν"
        assert stem_greek("Καπετάνιο") == "καπεταν"
        assert stem_greek("Καπετάνιε") == "καπεταν"
        assert stem_greek("Καπετάνιοι") == "καπεταν"
        assert stem_greek("Καπετάνιων") == "καπεταν"
        assert stem_greek("Καπετάνιους") == "καπεταν"

        # -ας: Κώστας / φύλακας
        assert stem_greek("Κώστας") == "κωστ"
        assert stem_greek("Κώστα") == "κωστ"
        assert stem_greek("Κώστες") == "κωστ"
        assert stem_greek("φύλακας") == "φυλακ"
        assert stem_greek("φύλακα") == "φυλακ"
        assert stem_greek("φύλακες") == "φυλακ"
        assert stem_greek("φυλάκων") == "φυλακ"

        # -ης: Γιάννης / ναύτης
        assert stem_greek("Γιάννης") == "γιανν"
        assert stem_greek("Γιάννη") == "γιανν"
        assert stem_greek("ναύτης") == "ναυτ"
        assert stem_greek("ναύτη") == "ναυτ"
        assert stem_greek("ναύτες") == "ναυτ"
        assert stem_greek("ναυτών") == "ναυτ"


    def test_imparisyllabic_neuters_geminate_and_various_roots(self):
        """Additional imparisyllabic neuters with single and double consonants across all inflections."""
        # γράμμα / γράμματος / γράμματα / γραμμάτων -> γραμ
        assert stem_greek("γράμμα") == "γραμ"
        assert stem_greek("γράμματος") == "γραμ"
        assert stem_greek("γράμματα") == "γραμ"
        assert stem_greek("γραμμάτων") == "γραμ"

        # πρόβλημα / προβλήματος / προβλήματα / προβλημάτων -> προβλημ
        assert stem_greek("πρόβλημα") == "προβλημ"
        assert stem_greek("προβλήματος") == "προβλημ"
        assert stem_greek("προβλήματα") == "προβλημ"
        assert stem_greek("προβλημάτων") == "προβλημ"

        # μάθημα / μαθήματος / μαθήματα / μαθημάτων -> μαθημ
        assert stem_greek("μάθημα") == "μαθημ"
        assert stem_greek("μαθήματος") == "μαθημ"
        assert stem_greek("μαθήματα") == "μαθημ"
        assert stem_greek("μαθημάτων") == "μαθημ"

        # πράγμα / πράγματος / πράγματα / πραγμάτων -> πραγμ
        assert stem_greek("πράγμα") == "πραγμ"
        assert stem_greek("πράγματος") == "πραγμ"
        assert stem_greek("πράγματα") == "πραγμ"
        assert stem_greek("πραγμάτων") == "πραγμ"

        # σφάλμα / σφάλματος / σφάλματα / σφαλμάτων -> σφαλμ
        assert stem_greek("σφάλμα") == "σφαλμ"
        assert stem_greek("σφάλματος") == "σφαλμ"
        assert stem_greek("σφάλματα") == "σφαλμ"
        assert stem_greek("σφαλμάτων") == "σφαλμ"

        # σώμα / σώματος / σώματα / σωμάτων -> σωμ
        assert stem_greek("σώμα") == "σωμ"
        assert stem_greek("σώματος") == "σωμ"
        assert stem_greek("σώματα") == "σωμ"
        assert stem_greek("σωμάτων") == "σωμ"

    def test_imparisyllabic_neuters(self):
        """Imparisyllabic neuters in -μα/-ματος and -σιμο/-σιματος."""
        # πρόγραμμα
        assert stem_greek("πρόγραμμα") == "προγραμ"
        assert stem_greek("προγράμματος") == "προγραμ"
        assert stem_greek("προγράμματα") == "προγραμ"
        assert stem_greek("προγραμμάτων") == "προγραμ"

        # σύστημα
        assert stem_greek("σύστημα") == stem_greek("συστήματος")
        assert stem_greek("σύστημα") == stem_greek("συστήματα")
        assert stem_greek("σύστημα") == stem_greek("συστημάτων")

        # όνομα
        assert stem_greek("όνομα") == "ονομ"
        assert stem_greek("ονόματος") == "ονομ"
        assert stem_greek("ονόματα") == "ονομ"
        assert stem_greek("ονομάτων") == "ονομ"

        # τρέξιμο / τρεξίματος
        assert stem_greek("τρέξιμο") == "τρεξ"
        assert stem_greek("τρεξίματος") == "τρεξ"
        assert stem_greek("τρεξίματα") == "τρεξ"
        assert stem_greek("τρεξιμάτων") == "τρεξ"

    def test_imparisyllabic_masculines_and_feminines(self):
        """Imparisyllabic plurals in -ηδες, -ηδων, -ουδες, -ουδων, -εδες, -εδων."""
        # καφετζής / καφετζήδες / καφετζήδων
        assert stem_greek("καφετζής") == "καφετζ"
        assert stem_greek("καφετζή") == "καφετζ"
        assert stem_greek("καφετζήδες") == "καφετζ"
        assert stem_greek("καφετζήδων") == "καφετζ"

        # παππούς / παππούδες / παππούδων
        assert stem_greek("παππούς") == "παππ"
        assert stem_greek("παππού") == "παππ"
        assert stem_greek("παππούδες") == "παππ"
        assert stem_greek("παππούδων") == "παππ"

        # καφές / καφέδες / καφέδων
        assert stem_greek("καφές") == "καφ"
        assert stem_greek("καφέ") == "καφ"
        assert stem_greek("καφέδες") == "καφ"
        assert stem_greek("καφέδων") == "καφ"

        # αλεπού / αλεπούδες / αλεπούδων
        assert stem_greek("αλεπού") == "αλεπ"
        assert stem_greek("αλεπούδες") == "αλεπ"
        assert stem_greek("αλεπούδων") == "αλεπ"

    def test_learned_ancient_genitives(self):
        """Learned / ancient genitives and plurals in -εως, -εων, -εις."""
        # πόλη / πόλεως / πόλεων / πόλεις
        assert stem_greek("πόλη") == "πολ"
        assert stem_greek("πόλης") == "πολ"
        assert stem_greek("πόλεως") == "πολ"
        assert stem_greek("πόλεων") == "πολ"
        assert stem_greek("πόλεις") == "πολ"

        # κίνηση / κινήσεως / κινήσεων / κινήσεις
        assert stem_greek("κίνηση") == "κινησ"
        assert stem_greek("κίνησης") == "κινησ"
        assert stem_greek("κινήσεως") == "κινησ"
        assert stem_greek("κινήσεων") == "κινησ"
        assert stem_greek("κινήσεις") == "κινησ"

        # τάξη / τάξεως / τάξεων / τάξεις
        assert stem_greek("τάξη") == "ταξ"
        assert stem_greek("τάξης") == "ταξ"
        assert stem_greek("τάξεως") == "ταξ"
        assert stem_greek("τάξεων") == "ταξ"
        assert stem_greek("τάξεις") == "ταξ"

    def test_participles_and_comparative_adjectives(self):
        """Participles (-μενος) and comparatives (-τερος) across all genders/cases."""
        # κουρασμένος / κουρασμένη / κουρασμένο / κουρασμένων / κουρασμένους
        assert stem_greek("κουρασμένος") == "κουρασ"
        assert stem_greek("κουρασμένη") == "κουρασ"
        assert stem_greek("κουρασμένο") == "κουρασ"
        assert stem_greek("κουρασμένου") == "κουρασ"
        assert stem_greek("κουρασμένης") == "κουρασ"
        assert stem_greek("κουρασμένων") == "κουρασ"
        assert stem_greek("κουρασμένους") == "κουρασ"
        assert stem_greek("κουρασμένες") == "κουρασ"
        assert stem_greek("κουρασμένα") == "κουρασ"
        assert stem_greek("κουρασμένοι") == "κουρασ"

        # καλύτερος / καλύτερη / καλύτερο / καλύτερων / καλύτερους
        assert stem_greek("καλύτερος") == "καλυ"
        assert stem_greek("καλύτερη") == "καλυ"
        assert stem_greek("καλύτερο") == "καλυ"
        assert stem_greek("καλύτερου") == "καλυ"
        assert stem_greek("καλύτερης") == "καλυ"
        assert stem_greek("καλύτερων") == "καλυ"
        assert stem_greek("καλύτερους") == "καλυ"
        assert stem_greek("καλύτερες") == "καλυ"
        assert stem_greek("καλύτερα") == "καλυ"
        assert stem_greek("καλύτεροι") == "καλυ"

    def test_short_roots_and_particles_stem_length_protection(self):
        """Roots with <= 3 characters must not be over-stripped."""
        assert stem_greek("της") == "τησ"
        assert stem_greek("τον") == "τον"
        assert stem_greek("Ρος") == "ροσ"
        assert stem_greek("φως") == "φωσ"
        assert stem_greek("γη") == "γη"


# ==============================================================================
# SECTION 2: Non-Destructive Glossary Enforcement Tests
# ==============================================================================

class TestNonDestructiveGlossaryEnforcer:

    @pytest.fixture(autouse=True)
    def mock_global_config(self):
        with patch("utils.storage.load_global_config", return_value={
            "morphology_normalization": True,
            "morphology_stemming": True
        }):
            yield

    def test_oblique_accusative_clause_avoids_destructive_nominative_injection(self):
        """In an accusative clause ('τον Peter'), enforcer must NOT inject raw nominative 'Πέτρος'."""
        glossary = {
            "terms": [
                {"term": "Peter", "translation": "Πέτρος", "type": "person", "gender": "masculine"}
            ]
        }
        src = ["I saw Peter yesterday."]
        trans = ["Συνάντησα τον Peter χθες."]

        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        # Must not inject 'τον Πέτρος'
        assert count == 0
        assert corrected[0] == "Συνάντησα τον Peter χθες."
        assert "τον Πέτρος" not in corrected[0]

    def test_oblique_genitive_clause_avoids_destructive_nominative_injection(self):
        """In a genitive clause ('του Peter' / 'της Hannah'), enforcer must NOT inject raw nominatives."""
        glossary = {
            "terms": [
                {"term": "Peter", "translation": "Πέτρος", "type": "person"},
                {"term": "Hannah", "translation": "Χάνα", "type": "person"}
            ]
        }
        src = [
            "This is Peter's car.",
            "Hannah's house is nearby."
        ]
        trans = [
            "Αυτό είναι το αυτοκίνητο του Peter.",
            "Το σπίτι της Hannah είναι κοντά."
        ]

        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 0
        assert "του Πέτρος" not in corrected[0]
        assert "της Χάνα" not in corrected[1]

    def test_nominative_clause_safely_replaces_leak(self):
        """In a nominative clause ('Η Tohsaka'), enforcer safely replaces the leak."""
        glossary = {
            "terms": [
                {"term": "Tohsaka", "translation": "Τοχσάκα", "type": "person"}
            ]
        }
        src = ["Tohsaka is ready."]
        trans = ["Η Tohsaka είναι έτοιμη."]

        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 1
        assert corrected[0] == "Η Τοχσάκα είναι έτοιμη."

    def test_indeclinable_proper_noun_safely_replaces_leak(self):
        """Indeclinable nouns (acronyms, loanwords in consonants) safely replace leaks."""
        glossary = {
            "terms": [
                {"term": "Winterfell", "translation": "Γουίντερφελ", "type": "location"}
            ]
        }
        src = ["Welcome to Winterfell."]
        trans = ["Καλώς ήρθατε στο Winterfell."]

        corrected, count = enforce_glossary(trans, glossary, src, target_lang="Greek")
        assert count == 1
        assert corrected[0] == "Καλώς ήρθατε στο Γουίντερφελ."

    def test_detect_glossary_violations_returns_structured_diagnostics(self):
        """detect_glossary_violations produces non-destructive structured diagnostics."""
        glossary = {
            "terms": [
                {"term": "Peter", "translation": "Πέτρος", "type": "person"}
            ]
        }
        src = ["I saw Peter."]
        trans = ["Συνάντησα τον Peter."]

        violations = detect_glossary_violations(src, trans, glossary, target_lang="Greek")
        assert len(violations) == 1
        assert violations[0]["index"] == 0
        assert violations[0]["term"] == "Peter"
        assert violations[0]["expected"] == "Πέτρος"


# ==============================================================================
# SECTION 3: QC Funnel Zero-Token Morphology-Aware Validation Tests
# ==============================================================================

class TestQCFunnelMorphologyAwareValidation:

    def test_inflected_greek_glossary_terms_produce_zero_tickets(self):
        """Valid inflected Greek terms across cases (Accusative, Genitive) produce 0 tickets."""
        glossary = {
            "terms": [
                {"term": "Peter", "translation": "Πέτρος", "type": "person"},
                {"term": "program", "translation": "πρόγραμμα", "type": "object"},
                {"term": "city", "translation": "πόλη", "type": "location"}
            ]
        }
        lines = [
            {"original": "I saw Peter.", "translated": "Συνάντησα τον Πέτρο.", "translations": {"el": "Συνάντησα τον Πέτρο."}},
            {"original": "The program's execution was fast.", "translated": "Η εκτέλεση του προγράμματος ήταν γρήγορη.", "translations": {"el": "Η εκτέλεση του προγράμματος ήταν γρήγορη."}},
            {"original": "The city's council agreed.", "translated": "Το συμβούλιο της πόλεως συμφώνησε.", "translations": {"el": "Το συμβούλιο της πόλεως συμφώνησε."}},
        ]
        tickets = validate_lines(lines, target_lang_code="el", glossary=glossary)
        assert tickets == []

    def test_missing_and_synonym_glossary_terms_emit_repair_tickets(self):
        """Missing terms (leaks or non-glossary synonyms) emit structured QC tickets."""
        glossary = {
            "terms": [
                {"term": "Peter", "translation": "Πέτρος", "type": "person"},
                {"term": "Elder", "translation": "Πρεσβύτερος", "type": "title"}
            ]
        }
        lines = [
            # Line 0: Leaked English term
            {"original": "I met Peter yesterday.", "translated": "Συνάντησα τον Peter χθες.", "translations": {"el": "Συνάντησα τον Peter χθες."}},
            # Line 1: Translated into synonym (Γέροντας) instead of glossary (Πρεσβύτερος)
            {"original": "The Elder spoke first.", "translated": "Ο Γέροντας μίλησε πρώτος.", "translations": {"el": "Ο Γέροντας μίλησε πρώτος."}},
        ]
        tickets = validate_lines(lines, target_lang_code="el", glossary=glossary)
        assert len(tickets) == 2
        assert tickets[0]["index"] == 0
        assert "missing glossary term 'Peter' -> render as 'Πέτρος'" in tickets[0]["issues"][0]
        assert "Greek syntax" in tickets[0]["issues"][0]

        assert tickets[1]["index"] == 1
        assert "missing glossary term 'Elder' -> render as 'Πρεσβύτερος'" in tickets[1]["issues"][0]

    def test_repair_prompt_incorporates_glossary_issues_cleanly(self):
        """build_repair_prompt includes the exact missing glossary issue instructions."""
        glossary = {
            "terms": [
                {"term": "Peter", "translation": "Πέτρος", "type": "person"}
            ]
        }
        lines = [
            {"original": "I saw Peter.", "translated": "Συνάντησα τον Peter.", "translations": {"el": "Συνάντησα τον Peter."}}
        ]
        tickets = validate_lines(lines, target_lang_code="el", glossary=glossary)
        prompt = build_repair_prompt(lines, tickets, "el", "Greek")

        assert "EN: I saw Peter." in prompt
        assert "CURRENT: Συνάντησα τον Peter." in prompt
        assert "missing glossary term 'Peter' -> render as 'Πέτρος'" in prompt
        assert "inflect case/article naturally for Greek syntax" in prompt
