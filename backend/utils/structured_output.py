"""
Structured JSON output schema and parsing logic for translator agent (Plan 18).
"""

import json
import re
import logging
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from utils.llm_utils import strip_reasoning_blocks, parse_translations_from_text

logger = logging.getLogger("omnisub.structured_output")


class TranslatedLine(BaseModel):
    i: int = Field(description="1-based source index matching the input line number")
    t: str = Field(description="Translated text for this line")


class SceneTranslationResponse(BaseModel):
    lines: List[TranslatedLine]


class NewTerm(BaseModel):
    """A term the model had to invent a rendering for that wasn't in the glossary
    (v2 plan, D7 — glossary growth piggybacked on the translation response)."""
    term: str = Field(description="The source-language term/name as it appears")
    translation: str = Field(description="The rendering used in this translation")
    type: str = Field(default="other", description="person|location|organization|object|technique|other")


class EpisodeTranslationResponse(BaseModel):
    lines: List[TranslatedLine]
    new_terms: List[NewTerm] = Field(
        default_factory=list,
        description="Recurring proper nouns/terms not in the provided glossary (max 10)",
    )


class VerifiedLine(BaseModel):
    """Translated line carrying a source echo for alignment verification (the
    source-echo guard, used on the whole-episode lane where drift is worst)."""
    i: int = Field(description="1-based source index matching the input line number")
    s: str = Field(description="First 3-4 words of the SOURCE line at this index, copied VERBATIM from the input — used only to verify alignment, never translated")
    t: str = Field(description="Translated text for this line")


class VerifiedEpisodeTranslationResponse(BaseModel):
    lines: List[VerifiedLine]
    new_terms: List[NewTerm] = Field(
        default_factory=list,
        description="Recurring proper nouns/terms not in the provided glossary (max 10)",
    )


def use_source_echo(model_name: str) -> bool:
    """Whether to request + verify a source echo for this model.

    Piggybacks on ``use_structured_output`` (so it is off for local/Gemma unless the
    experimental opt-in is set) and is gated by ``source_echo_enabled`` (default on).
    """
    if not use_structured_output(model_name):
        return False
    try:
        from utils.storage import load_global_config
        return bool(load_global_config().get("source_echo_enabled", True))
    except Exception:
        return True


def parse_new_terms(text: str) -> List[Dict[str, str]]:
    """Best-effort extraction of the optional new_terms array from a response."""
    from utils.llm_utils import loads_tolerant
    try:
        parsed = loads_tolerant(strip_reasoning_blocks(text or ""))
        if isinstance(parsed, dict):
            terms = parsed.get("new_terms") or []
            out = []
            for t in terms:
                if isinstance(t, dict) and t.get("term") and t.get("translation"):
                    out.append({
                        "term": str(t["term"]).strip(),
                        "translation": str(t["translation"]).strip(),
                        "type": str(t.get("type", "other")),
                    })
            return out[:10]
    except Exception:
        pass
    return []


def use_structured_output(model_name: str) -> bool:
    """Check if structured output is enabled and supported for the given model."""
    if not model_name:
        return False
    try:
        from utils.storage import load_global_config
        config = load_global_config()
        if not config.get("structured_output_enabled", True):
            return False
    except Exception as e:
        logger.warning(f"Failed to load global config: {e}")
        
    # Auto-off for local or Gemma models, unless experimental opt-in is enabled
    is_local = model_name.startswith("local/")
    is_gemma = "gemma" in model_name.lower()
    
    if is_local or is_gemma:
        return config.get("experimental_local_structured_output", False)
        
    return True


def _fix_line_count_mismatch(results: List[Dict[str, Any]], expected_count: int) -> List[Dict[str, Any]]:
    """Fix output when model produces more lines than expected by splitting translations.

    If the model outputs N+k lines for N input lines, it likely split some
    translations across multiple indices. We detect this by finding consecutive
    pairs where the first line ends with an incomplete clause (comma, conjunction)
    and merge them. This prevents the "shift-down" alignment bug.
    """
    if expected_count <= 0 or len(results) <= expected_count:
        return results

    # Sort by index
    results = sorted(results, key=lambda x: x["index"])
    overflow = len(results) - expected_count
    logger.warning(f"Line-count mismatch: model output {len(results)} lines, expected {expected_count}. Attempting to merge {overflow} split translation(s).")

    # Try to merge overflow lines into their predecessor
    merged = []
    skip_next = False
    merges_done = 0

    for i, item in enumerate(results):
        if skip_next:
            skip_next = False
            continue

        if merges_done < overflow and i + 1 < len(results):
            current_text = item["text"].rstrip()
            next_item = results[i + 1]

            # Heuristic: merge if the current line ends with a comma, conjunction,
            # or the next line starts lowercase (continuation)
            ends_incomplete = current_text and current_text[-1] in (',', ';', '–', '—', '-', '…')
            next_text = next_item["text"].strip()
            next_starts_lower = next_text and next_text[0].islower()

            # Also check if consecutive indices share what should be one entry
            indices_consecutive = next_item["index"] == item["index"] + 1

            if indices_consecutive and (ends_incomplete or next_starts_lower):
                separator = " " if current_text and current_text[-1] != ' ' else ""
                merged.append({
                    "index": item["index"],
                    "text": current_text + separator + next_text,
                    "src_head": item.get("src_head", ""),
                })
                skip_next = True
                merges_done += 1
                continue

        merged.append(item)

    # Only renumber when the merges fully reconcile the overflow. A *partial*
    # reconciliation means we still can't account for every surplus line, so
    # renumbering 1..N would silently shift every line after the unaccounted
    # surplus — the exact drift this whole machinery exists to prevent. In that
    # case keep the model's echoed indices (callers map by index; any source left
    # unmapped stays a safe gap) and let the alignment audit catch the residual.
    if merges_done > 0 and merges_done == overflow:
        for i, item in enumerate(merged):
            item["index"] = i + 1
    elif merges_done > 0:
        logger.warning(
            f"Line-count mismatch only partially reconciled ({merges_done}/{overflow} "
            "merges); keeping echoed indices instead of renumbering to avoid a shift."
        )

    return merged


def parse_structured(text: str, expected_count: int = 0) -> List[Dict[str, Any]]:
    """Parse JSON translation response with tolerant fallback.

    If parsing fails, falls back to parse_translations_from_text.

    Args:
        text: Raw model response text.
        expected_count: Number of input lines sent to the model. When > 0,
            triggers post-processing to fix line-count mismatches from split
            translations.
    """
    if not text or not text.strip():
        return []

    clean_text = strip_reasoning_blocks(text)
    
    # Extract JSON content
    json_candidate = clean_text.strip()
    if json_candidate.startswith("```json"):
        json_candidate = json_candidate[7:]
    elif json_candidate.startswith("```"):
        json_candidate = json_candidate[3:]
    if json_candidate.endswith("```"):
        json_candidate = json_candidate[:-3]
    json_candidate = json_candidate.strip()

    # Find the outermost braces/brackets if any
    match = re.search(r'(\{.*\})|(\[.*\])', json_candidate, re.DOTALL)
    if match:
        json_candidate = match.group(0)

    try:
        # Tolerant cleanup: strip trailing commas in arrays/objects
        json_candidate_clean = re.sub(r',\s*([\]}])', r'\1', json_candidate)
        parsed = json.loads(json_candidate_clean)
        
        lines_data = []
        if isinstance(parsed, dict):
            if "lines" in parsed:
                lines_data = parsed["lines"]
            else:
                # If model returned a list of dicts directly under a different key
                for val in parsed.values():
                    if isinstance(val, list):
                        lines_data = val
                        break
        elif isinstance(parsed, list):
            lines_data = parsed

        results = []
        for item in lines_data:
            if isinstance(item, dict):
                # Use membership, not `or`: a falsy-but-valid value (index 0, an
                # intentionally empty translation "") must not be coalesced away.
                idx = item["i"] if "i" in item else item.get("index")
                if "t" in item:
                    translated_text = item["t"]
                elif "text" in item:
                    translated_text = item["text"]
                elif "translation" in item:
                    translated_text = item["translation"]
                else:
                    translated_text = None
                src_head = item.get("s") or item.get("src") or item.get("source") or ""
                if idx is not None and translated_text is not None:
                    try:
                        results.append({
                            "index": int(idx),
                            "text": str(translated_text).replace("<br>", "\n"),
                            "src_head": str(src_head),
                        })
                    except ValueError:
                        continue
                        
        if results:
            return _fix_line_count_mismatch(results, expected_count) if expected_count > 0 else results
    except Exception as e:
        logger.warning(f"Failed to parse structured JSON, falling back to text parsing: {e}")

    # Fallback to standard text parser
    fallback = parse_translations_from_text(text)
    return _fix_line_count_mismatch(fallback, expected_count) if expected_count > 0 else fallback
