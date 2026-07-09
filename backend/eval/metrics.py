"""
Translation quality metrics (Plan 11) — dependency-free.

- chrF: character n-gram F-score against a reference (robust, language-agnostic).
- glossary_adherence: % of required terms rendered with the expected target form.
- format_integrity: subtitle line-count preserved between source and hypothesis.
- consistency: a glossary term rendered with its canonical target everywhere it appears.

Each item is a dict: {source, reference?, hypothesis, glossary: [{term, translation}]}
"""

import re
from collections import Counter, defaultdict
from typing import List, Dict, Optional


def _char_ngrams(s: str, n: int) -> List[str]:
    s = s.strip()
    if len(s) < n:
        return [s] if s else []
    return [s[i:i + n] for i in range(len(s) - n + 1)]


def chrf(hyp: str, ref: str, max_n: int = 6, beta: float = 2.0) -> float:
    if not hyp or not ref:
        return 0.0
    precisions, recalls = [], []
    for n in range(1, max_n + 1):
        h = Counter(_char_ngrams(hyp, n))
        r = Counter(_char_ngrams(ref, n))
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        precisions.append(overlap / max(1, sum(h.values())))
        recalls.append(overlap / max(1, sum(r.values())))
    if not precisions:
        return 0.0
    p = sum(precisions) / len(precisions)
    rc = sum(recalls) / len(recalls)
    if p + rc == 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * p * rc / (b2 * p + rc)


def corpus_chrf(items: List[Dict]) -> Optional[float]:
    scores = [chrf(it.get("hypothesis", ""), it["reference"]) for it in items if it.get("reference")]
    return round(sum(scores) / len(scores), 4) if scores else None


def glossary_adherence(items: List[Dict]) -> Optional[float]:
    total = ok = 0
    from utils.text_normalize import contains_term
    from utils import storage
    try:
        config = storage.load_global_config()
        norm = config.get("morphology_normalization", True)
        stem = config.get("morphology_stemming", True)
    except Exception:
        norm = True
        stem = True

    for it in items:
        target_lang = it.get("target_language", "Greek")
        for g in it.get("glossary", []):
            term, tgt = g.get("term"), g.get("translation")
            if not term or not tgt:
                continue
            if contains_term(it.get("source", ""), term, lang="English", normalization=norm, stemming=False):
                total += 1
                if contains_term(it.get("hypothesis", "") or "", tgt, lang=target_lang, normalization=norm, stemming=stem):
                    ok += 1
    return round(ok / total, 4) if total else None


def format_integrity(items: List[Dict]) -> Optional[float]:
    total = ok = 0
    for it in items:
        total += 1
        s = len([l for l in (it.get("source", "")).split("\n") if l.strip()])
        h = len([l for l in (it.get("hypothesis", "")).split("\n") if l.strip()])
        if s == h:
            ok += 1
    return round(ok / total, 4) if total else None


def consistency(items: List[Dict]) -> Optional[float]:
    seen = defaultdict(set)
    from utils.text_normalize import contains_term
    from utils import storage
    try:
        config = storage.load_global_config()
        norm = config.get("morphology_normalization", True)
        stem = config.get("morphology_stemming", True)
    except Exception:
        norm = True
        stem = True

    for it in items:
        target_lang = it.get("target_language", "Greek")
        for g in it.get("glossary", []):
            term, tgt = g.get("term"), g.get("translation")
            if term and tgt and contains_term(it.get("source", ""), term, lang="English", normalization=norm, stemming=False):
                present = contains_term(it.get("hypothesis", "") or "", tgt, lang=target_lang, normalization=norm, stemming=stem)
                seen[term].add("canonical" if present else "other")
    if not seen:
        return None
    consistent = sum(1 for v in seen.values() if v == {"canonical"})
    return round(consistent / len(seen), 4)


def score_all(items: List[Dict]) -> Dict:
    metrics = {
        "chrf": corpus_chrf(items),
        "glossary_adherence": glossary_adherence(items),
        "format_integrity": format_integrity(items),
        "consistency": consistency(items),
    }
    present = [v for v in metrics.values() if v is not None]
    metrics["overall"] = round(sum(present) / len(present), 4) if present else None
    metrics["count"] = len(items)
    return metrics
