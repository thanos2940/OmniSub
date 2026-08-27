"""Capture raw LLM translation responses for diagnosing alignment / split-shift.

When the model returns a different number of entries than we sent — the split-shift
symptom (a long cue split into two entries, or a line dropped/duplicated) — we dump
the prompt, the raw response, and a per-entry breakdown to the episode's ``debug/``
directory so the exact model output can be inspected.

Each dumped entry shows, side by side:
  * ``echoed_index``    — the line number the model stamped
  * ``src_echo``        — the source words the model claims it translated (source-echo
                          guard); empty if the model didn't echo / echo is off
  * ``expected_source`` — the source line that index SHOULD map to
  * ``translated``      — what the model actually returned
so a leak (translated text belonging to a neighbouring source) is visible at a glance.

Set config ``debug_save_llm_responses`` to capture EVERY whole-episode response, not
just anomalous ones.
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def detect_split(parsed: List[Dict], expected_count: int) -> Dict:
    """Cheap structural diagnosis of a parsed response.

    ``overflow > 0`` is the smoking gun for a split (model emitted extra entries);
    duplicate indices mean it reused a number; missing indices mean a dropped line.
    """
    indices = [p.get("index") for p in parsed if isinstance(p.get("index"), int)]
    seen, dupes = set(), set()
    for i in indices:
        (dupes if i in seen else seen).add(i)
    missing = sorted(set(range(1, expected_count + 1)) - set(indices)) if expected_count else []
    return {
        "parsed_count": len(parsed),
        "expected_count": expected_count,
        "overflow": len(parsed) - expected_count,
        "duplicate_indices": sorted(dupes),
        "missing_indices": missing[:50],
        "max_index": max(indices) if indices else None,
    }


def save_response(
    project: str,
    episode: str,
    kind: str,
    *,
    prompt: str,
    raw_response: str,
    parsed: List[Dict],
    expected_count: int,
    source_texts: Optional[List[str]] = None,
    extra: Optional[Dict] = None,
) -> Optional[str]:
    """Write a debug dump and return its path (or None on failure)."""
    try:
        from utils import storage
        dbg = storage.episode_dir(project, episode) / "debug"
        dbg.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = dbg / f"{kind}_{ts}.json"

        rows = []
        for p in parsed:
            i = p.get("index")
            expected_src = ""
            if source_texts and isinstance(i, int) and 1 <= i <= len(source_texts):
                expected_src = (source_texts[i - 1] or "").replace("\n", " ")[:80]
            rows.append({
                "echoed_index": i,
                "src_echo": (p.get("src_head") or "")[:60],
                "expected_source": expected_src,
                "translated": (p.get("text") or "").replace("\n", " ")[:160],
            })

        payload = {
            "kind": kind,
            "timestamp": ts,
            "expected_count": expected_count,
            "parsed_count": len(parsed),
            "count_delta": len(parsed) - expected_count,
            "parsed_entries": rows,
            "extra": extra or {},
            "prompt": prompt,
            "raw_response": raw_response,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except Exception as e:
        logger.warning(f"Failed to save LLM debug response: {e}")
        return None
