"""
Line alignment — did each translation land on its own subtitle line?
====================================================================
A model translating a long episode in one go can lose its place: it skips a line (or splits
one in two) but keeps counting, and every answer after that point sits one or more lines off.
Each such line still looks like perfectly good Greek, so the usual checks (still English? empty?)
never notice.

Every pairing of a translation with a source line gets a score from cheap signals — length
ratio, end punctuation, questions and exclamations, numbers, capitalised names, tags, italics
and dashes. The weights (a small naive-Bayes table) were learned from this library's own
translations: each line with its own source versus the same line with its neighbours' sources.
A run of translations that fits lines a fixed distance away clearly better than their own is a
shifted run.

Subtitles split sentences across cues, and a good translator may move words between the cues
of one sentence (Greek word order differs). So a ±1 shift only counts where both lines are
complete sentences.

Used by the direct runner (a reply that came back short or long is put back in order), by
``agent_workspace.validate`` (misplaced lines are reported) and by the project audit.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_FILE = Path(__file__).with_name("line_alignment_model.json")
RUN_SCORE = 30.0          # evidence (log-odds) a run of shifted lines must reach
RUN_LINES = 3             # ... with at least this many lines that clearly fit the other source
GAIN_CAP = 6.0            # no single line adds more than this to a run
WEAK = -3.0               # a translation that fits its own line this badly is worth a closer look
MAX_OFFSET = 60           # how far translations may have run ahead or fallen behind
REALIGN_EVIDENCE = 20.0   # a reply that came back short (or long) is re-sorted on this much evidence
DROP_EVIDENCE = 8.0       # ... and with less (but this much), the lines that seem to have slipped are asked again

# Markup that carries no words: ASS override blocks, HTML-ish tags, ASS line breaks / hard spaces.
_MARKUP = re.compile(r"\{[^}]*\}|<[^>]+>|\\[Nn]|\\h")
_OVERRIDE = re.compile(r"\{[^}]*\}")
_TRAIL = re.compile(r"[\s\"'*)\]»”’>]+$")
_SPACE = re.compile(r"\s+")
_NOTES = re.compile(r"\[[^\[\]]*\]|\|")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_TOKEN = re.compile(r"([^\W\d_]+(?:['’\-][^\W\d_]+)*)|([.!?…:;]+)")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")
_STOP_CAPS = {"i", "i'm", "i'll", "i've", "i'd", "o", "ok"}
_COMPLETE = re.compile(r"[.?!♪\"'”»)\]]\s*$")
_ELLIPSIS_END = re.compile(r"(\.\.\.|…)\s*$")


def _fold(text: str) -> str:
    import unicodedata
    nfd = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").replace("ς", "σ")


def plain(text: Optional[str]) -> str:
    return _SPACE.sub(" ", _MARKUP.sub(" ", text or "")).strip()


def complete_sentence(text: Optional[str]) -> bool:
    """Does this subtitle line end a sentence? (Not when it trails off into the next cue.)"""
    p = plain(text)
    return bool(p) and bool(_COMPLETE.search(p)) and not _ELLIPSIS_END.search(p)


@dataclass
class Sig:
    """What the checks need from one line, computed once."""
    ln: float                  # log of the visible length
    end: str                   # final punctuation class
    digits: tuple
    caps: int                  # capitalised words that do not start a sentence (names), up to 3
    sents: int                 # sentence breaks inside the line
    q: int                     # question marks, up to 2
    ex: int                    # exclamation marks, up to 2
    star: int                  # dialogue dashes / italics: "- A - B" two-speaker cues
    tags: int                  # ASS override blocks
    stems: frozenset           # content-word stems (target-language lines only)


def signature(text: Optional[str], greek_q: bool = False, stems: bool = False) -> Optional[Sig]:
    """``greek_q``: ';' is the question mark (Greek). ``stems``: collect word stems (target language)."""
    if text is None:
        return None
    raw = text.strip()
    tags = len(_OVERRIDE.findall(raw))
    flat = _SPACE.sub(" ", _NOTES.sub(" ", _MARKUP.sub(" ", raw))).strip()
    tail = _TRAIL.sub("", flat)
    if tail.endswith(("...", "…")):
        end = "…"
    elif tail[-1:] == "?" or (greek_q and tail[-1:] == ";"):
        end = "?"
    elif tail[-1:] in ("!", ".", ":", ","):
        end = tail[-1]
    elif tail[-1:] in ("-", "—", "–"):
        end = "-"
    else:
        end = "w"
    q = flat.count("?") + (flat.count(";") if greek_q else 0)
    stops = ".!?" + (";" if greek_q else "")
    caps = sents = 0
    start, brk, seen = True, False, False
    for word, punct in _TOKEN.findall(flat):
        if punct:
            if "…" in punct or ".." in punct:
                start = True  # a pause, not a new sentence (but a capital after it is no name)
                continue
            if seen and any(c in stops for c in punct):
                start = brk = True
            elif punct.startswith(":"):
                start = True
            continue
        if brk:
            sents += 1
        elif not start and word[0].isupper() and word.lower() not in _STOP_CAPS:
            caps += 1
        start = brk = False
        seen = True
    dashes = len(re.findall(r"(?:^|\n|\\N)\s*[-–—]\s", raw)) + (1 if re.match(r"^\s*[-–—]\s", flat) else 0)
    st = frozenset()
    if stems:
        st = frozenset(w[:max(4, len(w) - 2)] for w in _WORD.findall(_fold(flat)) if len(w) >= 4)
    return Sig(math.log(len(flat) + 8), end, tuple(sorted(_NUM.findall(flat))), min(caps, 3), sents, min(q, 2),
               min(flat.count("!"), 2), min(dashes, 2), tags, st)


def _len_bin(t: Sig, s: Sig, c: float) -> int:
    return max(-12, min(15, round((t.ln - s.ln - c) / 0.15)))


def features(t: Sig, s: Sig, c: float = 0.0) -> tuple:
    """The categorical features of pairing translation ``t`` with source ``s``; ``c`` is the usual
    length ratio of the language pair (log), so lengths compare fairly."""
    dig = "none" if not t.digits and not s.digits else ("same" if t.digits == s.digits else "diff")
    tag = "none" if not t.tags and not s.tags else ("same" if t.tags == s.tags else "diff")
    return (("len", _len_bin(t, s, c)), ("end", s.end + t.end), ("dig", dig), ("caps", s.caps * 10 + t.caps),
            ("sent", max(-2, min(2, t.sents - s.sents))), ("q", s.q * 10 + t.q), ("ex", s.ex * 10 + t.ex),
            ("star", s.star * 10 + t.star), ("tag", tag))


def center(pairs: Iterable[Tuple[Optional[Sig], Optional[Sig]]]) -> Optional[float]:
    """The batch's typical length ratio (log) from (translation, source) pairs, if there are enough."""
    xs = [t.ln - s.ln for t, s in pairs if t is not None and s is not None]
    return statistics.median(xs) if len(xs) >= 5 else None


class Model:
    """Log-odds per feature value: aligned pairs versus neighbouring (misaligned) pairs."""

    FEATURES = ("len", "end", "dig", "caps", "sent", "q", "ex", "star", "tag")

    def __init__(self, table: Dict, trained_on: int = 0, center: float = 0.0):
        self.table = {f: {self._key(k): v for k, v in vals.items()} for f, vals in table.items()}
        self.trained_on = trained_on
        self.center = center
        self._t = tuple(self.table.get(f, {}) for f in self.FEATURES)

    @staticmethod
    def _key(k):
        try:
            return int(k)
        except (TypeError, ValueError):
            return k

    def score(self, t: Sig, s: Sig, c: float = 0.0) -> float:
        """Log-odds that ``t`` is the translation of ``s`` rather than of a neighbouring line."""
        ln, end, dig, caps, sent, q, ex, star, tag = self._t
        v = ln.get(_len_bin(t, s, c), 0.0) + end.get(s.end + t.end, 0.0)
        v += dig.get(("same" if t.digits == s.digits else "diff") if t.digits or s.digits else "none", 0.0)
        v += tag.get(("same" if t.tags == s.tags else "diff") if t.tags or s.tags else "none", 0.0)
        return v + (caps.get(s.caps * 10 + t.caps, 0.0) + sent.get(max(-2, min(2, t.sents - s.sents)), 0.0)
                    + q.get(s.q * 10 + t.q, 0.0) + ex.get(s.ex * 10 + t.ex, 0.0) + star.get(s.star * 10 + t.star, 0.0))

    def to_json(self) -> Dict:
        return {"table": {f: {str(k): round(v, 3) for k, v in vals.items()} for f, vals in self.table.items()},
                "trained_on": self.trained_on, "center": round(self.center, 4)}

    @classmethod
    def from_json(cls, data: Dict) -> "Model":
        return cls(data["table"], data.get("trained_on", 0), data.get("center", 0.0))


def train(batches: Iterable[Sequence[Tuple[Sig, Optional[Sig]]]]) -> Model:
    """``batches``: per episode, (source Sig, translation Sig or None) in time order."""
    pos: Dict = {}
    neg: Dict = {}
    n_pos = n_neg = 0
    centers: List[float] = []
    for rows in batches:
        c = center((t, s) for s, t in rows)
        if c is None:
            c = statistics.median(centers) if centers else 0.0
        else:
            centers.append(c)
        for i, (s, t) in enumerate(rows):
            if t is None:
                continue
            n_pos += 1
            for f, v in features(t, s, c):
                pos.setdefault(f, {}).setdefault(v, 0)
                pos[f][v] += 1
            for j in (i - 2, i - 1, i + 1, i + 2):
                if 0 <= j < len(rows) and rows[j][0] != s:
                    n_neg += 1
                    for f, v in features(t, rows[j][0], c):
                        neg.setdefault(f, {}).setdefault(v, 0)
                        neg[f][v] += 1
    table = {}
    for f in set(pos) | set(neg):
        vals = set(pos.get(f, {})) | set(neg.get(f, {}))
        k = len(vals) + 1
        table[f] = {v: math.log((pos.get(f, {}).get(v, 0) + 1) / (n_pos + k))
                    - math.log((neg.get(f, {}).get(v, 0) + 1) / (n_neg + k)) for v in vals}
    return Model(table, n_pos, statistics.median(centers) if centers else 0.0)


_default: Optional[Model] = None


def default_model() -> Model:
    global _default
    if _default is None:
        _default = Model.from_json(json.loads(DEFAULT_FILE.read_text(encoding="utf-8")))
    return _default


# ------------------------------------------------------------------ detection

def _segments(gains: List[Optional[float]], min_score: float) -> List[Tuple[int, int, float]]:
    """Disjoint stretches whose gains add up to at least ``min_score`` (greedy, best first)."""
    out = []
    g = [x if x is not None else 0.0 for x in gains]
    blocked = [False] * len(g)
    while True:
        best, cur, start, span = 0.0, 0.0, 0, None
        for i, x in enumerate(g):
            if blocked[i]:
                cur, start = 0.0, i + 1
                continue
            if cur <= 0:
                cur, start = 0.0, i
            cur += x
            if cur > best:
                best, span = cur, (start, i)
        if span is None or best < min_score:
            return out
        out.append((span[0], span[1], best))
        for i in range(span[0], span[1] + 1):
            blocked[i] = True


def find_shifts(src: List[Sig], tgt: List[Optional[Sig]], model: Model, complete: Optional[List[bool]] = None,
                max_offset: int = MAX_OFFSET, run_score: float = RUN_SCORE) -> Dict[int, Tuple[int, float]]:
    """Positions whose translation belongs to another line: {position: (offset, evidence)}.

    Lists are in time order; ``tgt[i]`` is None for lines without a translation (they neither
    count for nor against a run). A run needs several lines that each fit the other source better
    — one striking line cannot carry it. ``complete``: whether each source line ends a sentence;
    a ±1 offset only counts between two complete lines."""
    size = len(src)
    c = center(zip(tgt, src))
    c = model.center if c is None else c
    own = [model.score(t, src[i], c) if t is not None else None for i, t in enumerate(tgt)]
    weak = [i for i, o in enumerate(own) if o is not None and o < WEAK]
    if not weak:
        return {}
    zones: List[List[int]] = []
    for i in weak:
        if zones and i - zones[-1][1] <= 8:
            zones[-1][1] = i
        else:
            zones.append([i, i])
    found: Dict[int, Tuple[int, float]] = {}
    reach = min(max_offset, size - 1)
    for lo, hi in zones:
        lo, hi = max(0, lo - 4), min(size - 1, hi + 4)
        for d in range(-reach, reach + 1):
            if d == 0:
                continue
            gains: List[Optional[float]] = []
            for i in range(lo, hi + 1):
                j = i + d
                if tgt[i] is None or not 0 <= j < size:
                    gains.append(None)
                    continue
                if abs(d) == 1 and complete is not None and not (complete[i] and complete[j]):
                    gains.append(None)  # words may move between the cues of one sentence
                    continue
                gains.append(model.score(tgt[i], src[j], c) - own[i])
            capped = [None if g is None else min(g, GAIN_CAP) for g in gains]
            for a, b, total in _segments(capped, run_score):
                hits = [lo + k for k in range(a, b + 1) if gains[k] is not None and gains[k] > 0]
                strong = [i for i in hits if gains[i - lo] >= 2]
                if total < run_score or len(strong) < RUN_LINES:
                    continue
                for i in hits:
                    if i not in found or found[i][1] < total:
                        found[i] = (d, total)
    return found


def _norm_words(text: str) -> set:
    return set(_WORD.findall(_fold(_NOTES.sub(" ", plain(text)))))


def find_duplicates(sources: List[str], src: List[Sig], tgt: List[Optional[Sig]], model: Model,
                    window: int = 3) -> Dict[int, int]:
    """Lines that repeat a nearby line's translation although the originals differ — one line's
    translation was written twice, once on the wrong line: {position: position it belongs to}."""
    c = center(zip(tgt, src))
    c = model.center if c is None else c
    out: Dict[int, int] = {}
    for i, t in enumerate(tgt):
        if t is None or len(t.stems) < 2:
            continue
        for j in range(max(0, i - window), min(len(tgt), i + window + 1)):
            if j == i or tgt[j] is None:
                continue
            a, b = t.stems, tgt[j].stems
            if 2 * len(a & b) / (len(a) + len(b)) < 0.85:
                continue
            ws, wo = _norm_words(sources[i]), _norm_words(sources[j])
            if ws and wo and len(ws & wo) / len(ws | wo) >= 0.5:
                continue  # similar originals: similar translations are expected
            here, there = model.score(t, src[i], c), model.score(t, src[j], c)
            if there - here >= 4 and here < 0:
                out[i] = j
    return out


def realign(order: List[int], answers: Dict[int, Sig], src: Dict[int, Sig], gap: int, model: Model,
            penalty: float = 6.0) -> Dict:
    """A reply came back with ``gap`` lines missing at the end (gap > 0: the answers ran ahead of
    the numbering) or with ``-gap`` numbers too many (gap < 0: they fell behind). Find where the
    answers slipped: {"slipped": {n: line it belongs to}, "unsure": [n...], "evidence": float}."""
    size = len(order)
    pos = [p for p, n in enumerate(order) if n in answers]
    offs = list(range(0, gap + 1)) if gap > 0 else list(range(gap, 1))
    if not pos or len(offs) == 1:
        return {"slipped": {}, "unsure": [], "evidence": 0.0}
    c = center((answers[order[p]], src[order[p]]) for p in pos)
    c = model.center if c is None else c
    neg_inf = float("-inf")

    def emit(p: int, o: int) -> float:
        q = p + o
        if not 0 <= q < size:
            return neg_inf
        return model.score(answers[order[p]], src[order[q]], c)

    # offsets only grow: skipped lines push answers further ahead, split lines further behind
    rank = sorted(offs, key=abs)
    V = {o: (0.0 if o == 0 else -penalty) + emit(pos[0], o) for o in rank}
    back = []
    for p in pos[1:]:
        nv, bp, run_best, run_arg = {}, {}, neg_inf, None
        for o in rank:
            if run_arg is not None and run_best - penalty > V[o]:
                nv[o], bp[o] = run_best - penalty, run_arg
            else:
                nv[o], bp[o] = V[o], o
            if V[o] > run_best:
                run_best, run_arg = V[o], o
            nv[o] += emit(p, o)
        back.append(bp)
        V = nv
    o = max(V, key=V.get)
    path = [o]
    for bp in reversed(back):
        o = bp[o]
        path.append(o)
    path.reverse()
    evidence = sum(emit(p, o) - emit(p, 0) for p, o in zip(pos, path) if o != 0)
    slipped = {order[p]: order[p + o] for p, o in zip(pos, path) if o != 0}
    unsure = []
    for k in range(1, len(path)):
        if path[k] != path[k - 1]:
            unsure += [order[pos[kk]] for kk in range(max(0, k - 2), k)
                       if path[kk] == path[k - 1] and emit(pos[kk], path[kk]) < 2.0]
    return {"slipped": slipped, "unsure": unsure, "evidence": round(evidence, 1)}


# ------------------------------------------------------------------ what callers use

def _greek(target_code: str) -> bool:
    return (target_code or "").lower() == "el"


def misplaced(sources: List[str], translations: List[Optional[str]], target_code: str = "el",
              run_score: float = RUN_SCORE, model: Optional[Model] = None) -> Dict[int, Dict]:
    """Lines (positions, time order) whose translation belongs to another line, and duplicated
    answers: {position: {"belongs_to": position, "evidence": float}}."""
    if len(sources) < 4:
        return {}
    model = model or default_model()
    gq = _greek(target_code)
    src = [signature(s) for s in sources]
    tgt = [signature(t, gq, True) if t and t.strip() and t.strip() != (s or "").strip() else None
           for s, t in zip(sources, translations)]
    complete = [complete_sentence(s) for s in sources]
    out: Dict[int, Dict] = {}
    for i, (d, ev) in find_shifts(src, tgt, model, complete, run_score=run_score).items():
        out[i] = {"belongs_to": i + d, "evidence": round(ev, 1)}
    for i, j in find_duplicates(sources, src, tgt, model).items():
        out.setdefault(i, {"belongs_to": j, "evidence": 0.0, "duplicate": True})
    return out


def repair_reply(order: List[int], updates: Dict[int, str], sources: Dict[int, str], extra: int,
                 target_code: str = "el", model: Optional[Model] = None) -> Tuple[Dict[int, str], Optional[Dict]]:
    """A reply whose answers ran ahead (lines missing at the end) or fell behind (numbers left
    over): put the answers back on their own lines. ``order``: the numbers asked for, in the
    order they were shown. Returns the corrected answers and a summary, or the answers
    unchanged and None when nothing slipped."""
    pos = {n: p for p, n in enumerate(order)}
    answered = [n for n in order if (updates.get(n) or "").strip()]
    if len(answered) < 3:
        return updates, None
    last = max(pos[n] for n in updates if n in pos)
    gap = len(order) - 1 - last
    if gap == 0 and extra:
        gap = -min(extra, 10)
    if gap == 0:
        return updates, None
    gap = max(-MAX_OFFSET, min(MAX_OFFSET, gap))
    model = model or default_model()
    gq = _greek(target_code)
    src = {n: signature(sources[n]) for n in order}
    answers = {n: signature(updates[n], gq, True) for n in answered}
    res = realign(order, answers, src, gap, model)
    if not res["slipped"] or res["evidence"] < DROP_EVIDENCE:
        return updates, None
    out = {n: u for n, u in updates.items() if n not in res["slipped"] and n not in res["unsure"]}
    first = min(res["slipped"], key=lambda n: pos[n])
    summary = {"from": first, "gap": gap, "moved": 0, "dropped": len(res["slipped"]) + len(res["unsure"]),
               "evidence": res["evidence"]}
    if res["evidence"] < REALIGN_EVIDENCE:  # probably slipped, too little to go on: ask for those lines again
        return out, summary
    moved: Dict[int, str] = {}
    clash = set()
    for n, real in res["slipped"].items():
        if real in moved or real in out:
            clash.add(real)
        else:
            moved[real] = updates[n]
    for real in clash:
        moved.pop(real, None)
        out.pop(real, None)
    out.update(moved)
    return out, dict(summary, moved=len(moved), dropped=len(res["unsure"]) + len(clash))


def retrain_from_library(projects_dir: Path, target_code: str = "el", limit: int = 900) -> Model:
    """Learn the weights from the library's translated episodes (used to build the shipped default)."""
    import glob
    import random
    gq = _greek(target_code)
    files = glob.glob(str(Path(projects_dir) / "*" / "episodes" / "*" / "data.json"))
    random.Random(3).shuffle(files)
    batches = []
    for f in files:
        if len(batches) >= limit:
            break
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        cues = data.get("data") if isinstance(data, dict) else data
        rows = []
        for cue in cues or []:
            if not isinstance(cue, dict):
                continue
            meta = cue.get("_ass") if isinstance(cue.get("_ass"), dict) else {}
            if meta.get("kind") == "sign" or meta.get("translatable") is False:
                continue
            s = (cue.get("original") or "").strip()
            t = ((cue.get("translations") or {}).get(target_code) or cue.get("translated") or "").strip()
            if s and any(ch.isalpha() for ch in s):
                rows.append((signature(s), signature(t, gq, True) if t and t != s else None))
        if sum(1 for _, t in rows if t) >= 20:
            batches.append(rows)
    return train(batches)
