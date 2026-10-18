"""
Glossary with judgment
======================
A glossary term is the agreed rendering of a *name or term* — not of every occurrence of the
same English letters. "Load" (the menu command) is not "a heavy load"; "Pillar" (a Hashira) is
not "the pillar collapsed"; "Ren" is not the inside of "current". So:

* terms are found as whole words (plurals and possessives too), names case-sensitively;
* a one-word term that is also an everyday English word only counts where it is used as the
  term — capitalised inside a sentence — not in "hold on" or at the start of a sentence;
* a term can carry a **sense** ("used as": "the Demon Slayer rank") — briefs and prompts then
  say "only as …", and translators translate the other meanings by context;
* such ambiguous terms are never flagged as missing by the glossary check: whether the line
  means the term is decided from the sentence, not by string matching;
* agents cannot add a single everyday word as a new term unless they say in which sense.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Set

logger = logging.getLogger("omnisub.glossary")

_TOKEN = re.compile(r"[A-Za-z0-9À-ÖØ-öø-ÿ]+(?:['’][A-Za-zÀ-ÖØ-öø-ÿ]+)*")
_TAGS = re.compile(r"\{[^}]*\}|<[^>]+>|\\[Nn]|\\h")
_LOWER_WORD = re.compile(r"\b[a-z][a-z']+\b")
_SENTENCE_STOP = ".!?:;…\"“”*-—–(|♪"

# What an ambiguous term stands for when nobody has written its sense down.
SENSE_BY_TYPE = {
    "character": "a character's name", "person": "a character's name", "location": "a place name",
    "organization": "the group's name", "object": "the named object", "technique": "the technique's name",
    "title": "a title or form of address", "other": "the term in this story",
}

# Frequent English words, so tiny projects still know an everyday word when they see one.
COMMON_WORDS = set("""
a about above after again against all almost alone along already also although always am among an and
another any anyone anything are around as ask at away back bad be because become been before behind being
believe below best better between big black blood blue body both boy break bring brother but by call can
car care carry case catch cause change child children city clear close cold come could course cut dark day
dead deal death did die different do does dog done door down dream drink drive each early earth easy eat
end enough even ever every everyone everything eye eyes face fact fall family far fast father fear feel few
fight fill find fine fire first fish follow food for force forget form free friend from front full game
get girl give go god gold good great green ground group grow guard gun hair half hand hands happen happy
hard has have he head hear heart heavy hell help her here high him his hit hold home hope horse hot house how
human hurt i if important in inside into is it its job just keep kill kind king know land last late later
leave left let life light like line listen little live load long look lord lose lost lot love low magic
make man many master may me mean meet might mind miss moment money moon more morning most mother move much
must my name need never new next nice night no none nothing now of off old on once one only open or order
other our out over own pain part people person pick piece place plan play please point power pretty pull
push put queen question quite rather reach read ready real really red remember rest return right ring rock
room run safe same save say school sea second see seem sell send shadow shall she ship short should show
side sign since sister sit sky sleep small so some someone something son soon sorry soul sound speak
spirit stand star start stay still stone stop story strong such sun sure sword take talk tell than thank
that the their them then there these they thing think this those though three through time to today
together tomorrow tonight too touch toward town true trust try turn two under understand until up us use
very voice wait walk wall want war watch water way we weapon well what when where which while white who
whole why wild will win wind with without woman wonder word work world would wrong year yes yet you young
your
""".split())


def _plain(text: str) -> str:
    return _TAGS.sub(" ", text or "")


def _singulars(word: str) -> List[str]:
    w = word.lower()
    out = []
    for poss in ("'s", "’s"):
        if w.endswith(poss):
            w = w[:-2]
            out.append(w)
    if w.endswith("ies") and len(w) > 4:
        out.append(w[:-3] + "y")
    elif w.endswith("es") and len(w) > 4:
        out += [w[:-2], w[:-1]]
    elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        out.append(w[:-1])
    return out


def _key(words: Iterable[str]) -> str:
    return " ".join(w.lower().replace("’", "'") for w in words)


# ------------------------------------------------------------------ everyday words

_VOCAB: Dict[str, Set[str]] = {}
_VOCAB_LOCK = threading.Lock()
_VOCAB_BUILD_LOCK = threading.Lock()   # lanes ask together: one reads the episodes, the others find the result
_VOCAB_CACHE_VERSION = 1               # bump when vocabulary() changes what it counts


def vocabulary(texts: Iterable[str], floor: int = 3) -> Set[str]:
    """Words that appear in lowercase (so: everyday words) at least ``floor`` times, plus the common list."""
    counts: Counter = Counter()
    for t in texts:
        counts.update(_LOWER_WORD.findall(_plain(t)))
    return {w for w, n in counts.items() if n >= floor} | COMMON_WORDS


def _vocab_cache_file(project_name: str) -> Path:
    """``<data dir>/cache/vocab/<project>.json`` (the name made safe as a file name)."""
    from utils import paths
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", project_name).strip(" .")
    if not stem or stem != project_name or len(stem) > 100:   # odd names must not collide with others
        stem = (stem[:90] or "project") + "-" + hashlib.sha1(project_name.encode("utf-8")).hexdigest()[:8]
    return Path(paths.DATA_DIR) / "cache" / "vocab" / f"{stem}.json"


def _data_stamp(project_name: str, episode: str) -> str:
    """Episode name, mtime and size of its data file ("-" without one): changes whenever the file does."""
    from utils import storage
    try:
        st = (storage.episode_dir(project_name, episode) / "data.json").stat()
        return f"{episode}\x1f{st.st_mtime_ns}\x1f{st.st_size}"
    except Exception:   # no data file, or no usable path
        return f"{episode}\x1f-"


def _fingerprint(stamps: Iterable[str]) -> str:
    """Same fingerprint, same files, same vocabulary."""
    return hashlib.sha256("\n".join(stamps).encode("utf-8")).hexdigest()


def _read_vocab_cache(path: Path, fingerprint: str) -> Optional[Set[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["version"] == _VOCAB_CACHE_VERSION and data["fingerprint"] == fingerprint:
            return set(data["words"].split()) | COMMON_WORDS
    except Exception:
        pass   # missing, unreadable or for other files: build it again
    return None


def _write_vocab_cache(path: Path, fingerprint: str, vocab: Set[str]) -> None:
    from utils import storage
    try:   # words are letters and apostrophes, so one string keeps the file small
        storage.write_json_atomic(path, {"version": _VOCAB_CACHE_VERSION, "fingerprint": fingerprint,
                                         "words": " ".join(sorted(vocab - COMMON_WORDS))})
    except Exception as exc:   # only a speed-up: never fail a translation over it
        logger.debug(f"Could not write the vocabulary cache {path}: {exc}")


def _load_vocabulary(project_name: str, episodes: List[str]) -> Set[str]:
    """The cache file when the episodes' data files are unchanged, otherwise read every episode."""
    from utils import storage
    names, path = sorted(episodes), None
    if names:
        try:
            path = _vocab_cache_file(project_name)
            cached = _read_vocab_cache(path, _fingerprint(_data_stamp(project_name, ep) for ep in names))
            if cached is not None:
                return cached
        except Exception as exc:
            logger.debug(f"Vocabulary cache unavailable for {project_name!r}: {exc}")
            path = None
    stamps: Dict[str, str] = {}
    complete = True

    def originals() -> Iterator[str]:   # one episode in memory at a time
        nonlocal complete
        for ep in names:
            with storage.get_episode_lock(project_name, ep):   # the stamp is of the file as it was read
                try:
                    cues = storage.load_episode_data(project_name, ep) or []
                except Exception:
                    cues, complete = [], False
                stamps[ep] = _data_stamp(project_name, ep)   # (reading may rewrite an old-format file)
            for cue in cues:
                if isinstance(cue, dict) and cue.get("original"):
                    yield cue["original"]

    vocab = vocabulary(originals())
    if path and complete:   # an episode that failed to load is not cached as having no words
        _write_vocab_cache(path, _fingerprint(stamps[ep] for ep in names), vocab)
    return vocab


def project_vocabulary(project_name: str) -> Set[str]:
    """Everyday words of a project's source subtitles. Kept in memory per project and episode count, and on
    disk (``cache/vocab``) while the episodes' data files stay as they were, so a restart does not read
    every episode again."""
    from utils import storage
    try:
        episodes = storage.list_episodes(project_name) or []
    except Exception:
        episodes = []
    key = f"{project_name}\x1f{len(episodes)}"
    with _VOCAB_LOCK:
        if key in _VOCAB:
            return _VOCAB[key]
    with _VOCAB_BUILD_LOCK:
        with _VOCAB_LOCK:
            if key in _VOCAB:   # another thread finished it while this one waited
                return _VOCAB[key]
        vocab = _load_vocabulary(project_name, episodes)
        with _VOCAB_LOCK:
            for k in [k for k in _VOCAB if k.startswith(project_name + "\x1f")]:
                _VOCAB.pop(k, None)
            _VOCAB[key] = vocab
    return vocab


def is_everyday_word(term: str, vocab: Optional[Set[str]]) -> bool:
    words = _TOKEN.findall(term or "")
    return bool(vocab) and len(words) == 1 and words[0].lower() in vocab


def is_ambiguous(term: Dict, vocab: Optional[Set[str]]) -> bool:
    """A term that is also an everyday word, or whose sense is written down: whether a line uses it
    as the term is decided from the sentence, not by string matching."""
    if term.get("keep_original"):
        return False
    if (term.get("sense") or "").strip():
        return True
    return is_everyday_word(term.get("term", ""), vocab)


# ------------------------------------------------------------------ finding terms

class Matcher:
    """Finds glossary terms in English subtitle text: whole words (n-grams), plural/possessive aware,
    names case-sensitively, everyday words only where they are used as the term."""

    def __init__(self, terms: Iterable[Dict], vocab: Optional[Set[str]] = None):
        self.index: Dict[str, List[Dict]] = defaultdict(list)
        self.max_n = 1
        self.vocab = vocab or set()
        self.ambiguous: Dict[int, bool] = {}
        for t in terms:
            if not isinstance(t, dict) or not (t.get("term") or "").strip():
                continue
            toks = _TOKEN.findall(t["term"])
            if not toks:
                continue
            self.index[_key(toks)].append(t)
            self.ambiguous[id(t)] = is_ambiguous(t, self.vocab)
            self.max_n = max(self.max_n, min(len(toks), 8))

    def _as_term(self, word: str, start: int, plain: str) -> bool:
        """Is this one-word hit used as a term rather than as an everyday word?"""
        w = word.lower().replace("’", "'")
        if w not in self.vocab and not any(s in self.vocab for s in _singulars(w)):
            return True
        before = plain[:start].rstrip()
        return word[:1].isupper() and bool(before) and before[-1] not in _SENTENCE_STOP

    @staticmethod
    def _case_ok(term: Dict, span: List[str]) -> bool:
        if not term.get("case_sensitive"):
            return True
        return all(a[:1] == b[:1] for a, b in zip(_TOKEN.findall(term["term"]), span))

    def find(self, text: str) -> List[Dict]:
        """The terms used in ``text``; each hit is a copy of the term with ``ambiguous`` set."""
        plain = _plain(text)
        spans = [(m.group(0), m.start()) for m in _TOKEN.finditer(plain)]
        toks = [t for t, _ in spans]
        found: Dict[int, Dict] = {}
        i = 0
        while i < len(toks):
            matched = 0
            for n in range(min(self.max_n, len(toks) - i), 0, -1):
                span = toks[i:i + n]
                keys = [_key(span)] + [_key(span[:-1] + [s]) for s in _singulars(span[-1])]
                if n > 1:  # "Pillars of the Demon Slayer Corps": the head noun comes first
                    keys += [_key([s] + span[1:]) for s in _singulars(span[0])]
                for k in keys:
                    for term in self.index.get(k, ()):
                        if not self._case_ok(term, span):
                            continue
                        if n == 1 and self.vocab and not self._as_term(span[0], spans[i][1], plain):
                            continue
                        found[id(term)] = dict(term, ambiguous=self.ambiguous.get(id(term), False))
                        matched = n
                if matched:
                    break
            i += matched or 1
        return list(found.values())

    def find_all(self, texts: Iterable[str]) -> List[Dict]:
        seen: Dict[str, Dict] = {}
        for t in texts:
            for hit in self.find(t):
                seen.setdefault(hit["term"].lower(), hit)
        return list(seen.values())


def sense_of(term: Dict) -> str:
    return (term.get("sense") or "").strip() or SENSE_BY_TYPE.get((term.get("type") or "").lower(), "the term in this story")


def brief_lines(hits: Iterable[Dict]) -> List[str]:
    """One line per term for briefs and prompts: rendering, type/gender, notes, and the sense
    for terms that are also everyday words."""
    lines = []
    for t in sorted(hits, key=lambda h: h.get("term", "").lower()):
        src = t.get("term", "")
        tgt = src if t.get("keep_original") else (t.get("translation") or src)
        tags = ", ".join(x for x in (t.get("type"), t.get("gender")) if x and x != "n/a")
        line = f"- {src} → {tgt}" + (f"  ({tags})" if tags else "")
        note = (t.get("description") or t.get("notes") or "").strip()
        if note:
            line += f" — {note[:160]}"
        if t.get("ambiguous") or (t.get("sense") or "").strip():
            line += f" — only as {sense_of(t)}"
        lines.append(line)
    return lines


GLOSSARY_INSTRUCTION = (
    "Use these renderings where a line uses the word as this name or term, declined as the sentence "
    "needs. Where the word means something else (an everyday word, a verb, an idiom), translate it by "
    "its meaning in the sentence; a term marked \"only as …\" applies only in that sense.")


def missing_terms(source: str, translation: str, matcher: Matcher, target_language: str = "Greek") -> List[Dict]:
    """Glossary terms used in ``source`` whose rendering is missing from ``translation``.
    Ambiguous terms are left to the translator's judgment."""
    from utils.text_normalize import contains_term
    out = []
    for t in matcher.find(source):
        if t.get("ambiguous") or t.get("keep_original") or not (t.get("translation") or "").strip():
            continue
        if contains_term(translation or "", t["translation"], lang=target_language, normalization=True, stemming=True):
            continue
        out.append(t)
    return out


def accept_new_term(term: Dict, vocab: Optional[Set[str]]) -> bool:
    """An agent's new glossary term is taken unless it is a single everyday word without a sense
    (a rendering that fitted one scene: "petal" as a pet name, "Hold" for "hold on")."""
    return not (is_everyday_word(term.get("term", ""), vocab) and not (term.get("sense") or "").strip())
