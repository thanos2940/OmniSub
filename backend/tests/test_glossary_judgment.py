"""
Glossary with judgment
======================
A glossary term is the agreed rendering of a name or term, not of every occurrence of the same
letters: "Load" (the menu command) is not "a heavy load", "Ren" is not inside "current". These
tests pin how terms are found (whole words, plurals, names case-sensitively, everyday words only
where used as the term), how briefs word them ("only as …"), that the glossary check leaves
ambiguous terms to the translator, and that agents cannot add plain everyday words as terms.
The vocabulary of a show is read from its episodes once and then kept on disk until a data file
changes.
"""

import json
import threading
import time

import pytest

from utils import glossary_judgment, paths, storage
from utils.glossary_judgment import (
    Matcher, accept_new_term, brief_lines, is_ambiguous, missing_terms, project_vocabulary, vocabulary,
)

TERMS = [
    {"term": "Load", "translation": "Φόρτωση", "type": "other", "sense": "the menu command"},
    {"term": "Ren", "translation": "Ρεν", "type": "character", "case_sensitive": True},
    {"term": "Titan", "translation": "Τιτάνας", "type": "other"},
    {"term": "Survey Corps", "translation": "Σώμα Αναγνώρισης", "type": "organization"},
    {"term": "Pillar", "translation": "Πυλώνας", "type": "title"},
    {"term": "Rose", "translation": "Ρόουζ", "type": "character", "case_sensitive": True},
]
VOCAB = vocabulary(["carry the load", "a heavy load", "the load is", "the pillar fell", "a pillar of", "pillar"] * 2)


def names(hits):
    return sorted(h["term"] for h in hits)


def test_whole_words_only():
    m = Matcher(TERMS, VOCAB)
    assert names(m.find("The current is strong.")) == []
    assert names(m.find("Ren, wait for me!")) == ["Ren"]


def test_everyday_word_counts_only_where_used_as_the_term():
    m = Matcher(TERMS, VOCAB)
    assert names(m.find("You carry a heavy load.")) == []
    assert names(m.find("Load the game.")) == []             # sentence start: could be the verb
    assert names(m.find("Select Load to continue.")) == ["Load"]
    assert names(m.find("The pillar collapsed.")) == []
    assert names(m.find("He became a Pillar last year.")) == ["Pillar"]


def test_plurals_possessives_phrases_and_case():
    m = Matcher(TERMS, VOCAB)
    assert names(m.find("Titans are coming!")) == ["Titan"]
    assert names(m.find("The Titan's arm moved.")) == ["Titan"]
    assert names(m.find("The Survey Corps rides out.")) == ["Survey Corps"]
    assert names(m.find("A rose garden.")) == []              # names are case-sensitive
    assert names(m.find("Rose said no.")) == ["Rose"]


def test_brief_lines_say_in_which_sense():
    m = Matcher(TERMS, VOCAB)
    lines = brief_lines(m.find_all(["Select Load to continue.", "He became a Pillar.", "The Survey Corps rides."]))
    assert "- Load → Φόρτωση  (other) — only as the menu command" in lines
    assert any(line.startswith("- Pillar → Πυλώνας") and "only as a title or form of address" in line for line in lines)
    assert "- Survey Corps → Σώμα Αναγνώρισης  (organization)" in lines


def test_glossary_check_leaves_ambiguous_terms_to_the_translator():
    m = Matcher(TERMS, VOCAB)
    assert missing_terms("Select Load to continue.", "Πάτα Συνέχεια.", m) == []
    assert names(missing_terms("The Survey Corps rides out.", "Η ομάδα φεύγει.", m)) == ["Survey Corps"]
    assert missing_terms("The Survey Corps rides out.", "Το Σώμα Αναγνώρισης φεύγει.", m) == []


def test_ambiguity():
    assert is_ambiguous({"term": "Load"}, VOCAB)
    assert is_ambiguous({"term": "Eren", "sense": "the boy"}, VOCAB)
    assert not is_ambiguous({"term": "Eren"}, VOCAB)
    assert not is_ambiguous({"term": "Load", "keep_original": True}, VOCAB)


def test_new_terms_must_not_be_plain_everyday_words():
    assert not accept_new_term({"term": "load", "translation": "φορτίο"}, VOCAB)
    assert accept_new_term({"term": "Load", "translation": "Φόρτωση", "sense": "the menu command"}, VOCAB)
    assert accept_new_term({"term": "Johnny", "translation": "Τζόνι"}, VOCAB)
    assert accept_new_term({"term": "Survey Corps", "translation": "Σώμα Αναγνώρισης"}, VOCAB)


# ===========================================================================
# The everyday words of a show, kept on disk
# ===========================================================================

def _cues(*lines):
    return [{"index": i + 1, "start": "00:00:01,000", "end": "00:00:02,000", "original": text}
            for i, text in enumerate(lines)]


def _cache_file(name="Vocab Show"):
    return paths.DATA_DIR / "cache" / "vocab" / f"{name}.json"


@pytest.fixture
def show():
    glossary_judgment._VOCAB.clear()
    storage.create_project("Vocab Show", {"show_name": "Vocab Show", "target_language": "Greek"})
    storage.save_episode("Vocab Show", "S01E01", _cues("The ominous door creaked.", "An ominous sound, so ominous."))
    storage.save_episode("Vocab Show", "S01E02", _cues("Nothing here.", "Nothing at all."))
    yield "Vocab Show"
    glossary_judgment._VOCAB.clear()


@pytest.fixture
def loads(monkeypatch):
    """Every episode read through storage.load_episode_data, as (project, episode)."""
    calls = []
    real = storage.load_episode_data

    def counting(project, episode):
        calls.append((project, episode))
        return real(project, episode)

    monkeypatch.setattr(storage, "load_episode_data", counting)
    return calls


def test_vocabulary_comes_from_disk_while_the_episode_files_are_unchanged(show, loads):
    first = project_vocabulary(show)
    assert "ominous" in first and "creaked" not in first  # seen three times in lowercase: an everyday word
    assert sorted(loads) == [(show, "S01E01"), (show, "S01E02")]
    cache = _cache_file()
    assert cache.is_file()
    assert [p.name for p in cache.parent.iterdir()] == ["Vocab Show.json"]  # no temp file left behind
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["version"] == 1 and "ominous" in saved["words"].split()

    glossary_judgment._VOCAB.clear()  # the server starts again
    loads.clear()
    assert project_vocabulary(show) == first
    assert loads == []  # no episode was read
    assert project_vocabulary(show) is project_vocabulary(show)  # repeats come from memory


def test_a_changed_episode_file_rebuilds_the_vocabulary(show, loads):
    assert "zorblax" not in project_vocabulary(show)
    storage.save_episode(show, "S01E02", _cues("A zorblax.", "The zorblax again.", "One more zorblax."))
    glossary_judgment._VOCAB.clear()
    loads.clear()
    after = project_vocabulary(show)
    assert "zorblax" in after and len(loads) == 2  # the cache did not match: both files were read
    glossary_judgment._VOCAB.clear()
    loads.clear()
    assert project_vocabulary(show) == after and loads == []  # and the new result is the cached one


def test_a_new_episode_rebuilds_the_vocabulary_in_the_same_process(show):
    assert "quibble" not in project_vocabulary(show)
    storage.save_episode(show, "S01E03", _cues("a quibble", "the quibble", "no quibble"))
    assert "quibble" in project_vocabulary(show)


def test_an_unreadable_cache_is_ignored_and_replaced(show, loads):
    first = project_vocabulary(show)
    _cache_file().write_text("{not json", encoding="utf-8")
    glossary_judgment._VOCAB.clear()
    loads.clear()
    assert project_vocabulary(show) == first and len(loads) == 2
    assert json.loads(_cache_file().read_text(encoding="utf-8"))["version"] == 1


def test_a_cache_that_cannot_be_written_never_fails_the_translation(show):
    (paths.DATA_DIR / "cache").write_text("in the way", encoding="utf-8")  # cache/vocab cannot be created
    assert "ominous" in project_vocabulary(show)
    glossary_judgment._VOCAB.clear()
    assert "ominous" in project_vocabulary(show)


def test_the_cache_folder_is_read_from_the_data_dir_when_asked(show, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setattr(paths, "DATA_DIR", elsewhere)
    project_vocabulary(show)
    assert (elsewhere / "cache" / "vocab" / "Vocab Show.json").is_file()


def test_cache_file_names_are_safe_and_stay_apart():
    odd = glossary_judgment._vocab_cache_file('Show: "Part 2"?')
    assert odd.parent == paths.DATA_DIR / "cache" / "vocab" and odd.suffix == ".json"
    assert not set(odd.name) & set('<>:"/\\|?*')
    assert odd != glossary_judgment._vocab_cache_file('Show_ _Part 2__')  # cleaning up must not merge two names
    assert glossary_judgment._vocab_cache_file("Plain Show").name == "Plain Show.json"


def test_lanes_asking_together_read_the_episodes_once(show, monkeypatch):
    reads = []
    real = storage.load_episode_data

    def slow(project, episode):
        reads.append(episode)
        time.sleep(0.05)
        return real(project, episode)

    monkeypatch.setattr(storage, "load_episode_data", slow)
    gate, results = threading.Barrier(3), []

    def ask():
        gate.wait()
        results.append(project_vocabulary(show))

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(results) == 3 and all(r == results[0] for r in results)
    assert sorted(reads) == ["S01E01", "S01E02"]


def test_a_show_without_episodes_has_only_the_common_words():
    glossary_judgment._VOCAB.clear()
    storage.create_project("Empty Show", {"show_name": "Empty Show", "target_language": "Greek"})
    assert project_vocabulary("Empty Show") == glossary_judgment.COMMON_WORDS
    assert project_vocabulary("not/a/project") == glossary_judgment.COMMON_WORDS
    assert not _cache_file("Empty Show").exists()
    glossary_judgment._VOCAB.clear()
