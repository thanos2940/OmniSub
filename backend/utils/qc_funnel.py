"""
QC funnel (v2 plan, D6) — quality control ordered by cost.

1. Deterministic validators (zero tokens) scan the translated episode and produce
   repair tickets: inline formatting tags dropped/added, suspicious Latin ratio in a
   non-Latin target (untranslated lines), empty translations for non-empty sources.
2. One **batched repair call** per episode (role="repair", thinking off) fixes all
   ticketed lines together — never one call per line.
3. Anything the repair pass can't fix stays flagged ``needs_review`` for the human.

The deterministic glossary enforcer (utils.glossary_enforcer) runs separately before
this, and conformance/condensation keep their existing passes; this module covers the
validators that previously existed only as ideas.
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Inline tags worth preserving verbatim: HTML-ish (<i>, <b>, <font …>) and ASS ({\an8…}).
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>|\{\\[^}]*\}")

# Targets written in non-Latin scripts, where a high Latin ratio signals an
# untranslated line. Keyed by ISO-639-1 code.
_NON_LATIN_TARGETS = {"el", "ru", "ja", "ko", "zh", "ar", "he", "uk"}

# Targets where a logogram/no-spacing script makes character count a poor length
# proxy (a complete translation is legitimately far shorter than its source), so the
# length-ratio truncation check below would false-positive. Skip them for that check.
_COMPACT_TARGETS = {"ja", "zh", "ko", "th"}

# Sentence-final punctuation across the scripts we target. ';' is the Greek question
# mark; the CJK marks let a complete CJK line read as "terminated" even though those
# targets are excluded from the length-ratio half of the check.
_TERMINAL_CHARS = ".!?…;。！？"
_TRAILING_TRIM = "\"'»”’)]）」』 \t\r\n"


def _latin_ratio(text: str) -> float:
    cleaned = re.sub(r"[0-9\s\W]", "", text, flags=re.UNICODE)
    if not cleaned:
        return 0.0
    latin = sum(1 for c in cleaned if "A" <= c <= "Z" or "a" <= c <= "z")
    return latin / len(cleaned)


def _ends_terminal(text: str) -> bool:
    """True if the text ends in sentence-final punctuation (ignoring trailing
    quotes/brackets/whitespace) — i.e. it reads as a complete utterance."""
    t = text.rstrip(_TRAILING_TRIM)
    return bool(t) and t[-1] in _TERMINAL_CHARS


def _script_issues(original: str, translated: str, target_lang_code: str) -> List[str]:
    """Wrong-writing-system issues for this line (never raises)."""
    try:
        from utils.script_guard import foreign_issues
        return foreign_issues(original, translated, target_lang_code)
    except Exception as e:
        logger.warning(f"Script guard failed (non-critical): {e}")
        return []


def _looks_truncated(original: str, translated: str, target_lang_code: str) -> bool:
    """Detect the 'half-translated long line' symptom.

    When the whole-episode model splits a long cue across two JSON entries and
    renumbers the rest, the original cue keeps only its first clause: a long,
    self-contained source ends up as a target that is far shorter AND stops
    mid-clause. We require all three signals so it stays conservative:

    * the source is long enough that truncation is meaningful (>=60 chars),
    * the source is a complete utterance (ends in terminal punctuation) but the
      target is not (a complete translation almost always mirrors the source's
      terminal punctuation), and
    * the target is far shorter than the source warrants.

    Skipped for compact scripts (``_COMPACT_TARGETS``) where char count is not a
    reliable length proxy.
    """
    if target_lang_code in _COMPACT_TARGETS:
        return False
    if len(original) < 60:
        return False
    if not _ends_terminal(original) or _ends_terminal(translated):
        return False
    return len(translated) < 0.6 * len(original)


def validate_lines(
    parsed_srt: List[Dict],
    target_lang_code: str,
    max_tickets: int = 40,
    glossary: Optional[Dict] = None,
) -> List[Dict]:
    """Scan translated lines and return repair tickets.

    Each ticket: ``{"index": global_idx, "issues": [str, ...]}``. Zero LLM cost.

    Covers per-line issues (tags, untranslated-Latin, truncation) and, across the
    whole episode, *alignment drift* — lines whose translation belongs to a
    neighbouring source line. A drift ticket re-translates the line from its own
    source via the repair pass, which corrects the shift regardless of the wrong
    text currently there. ``glossary`` (optional) lets the drift audit use
    verbatim proper nouns as extra anchors alongside digits.
    """
    issues_by_index: Dict[int, List[str]] = {}
    check_latin = target_lang_code in _NON_LATIN_TARGETS

    for i, line in enumerate(parsed_srt):
        original = (line.get("original") or "").strip()
        if not original:
            continue
        translated = (line.get("translations", {}).get(target_lang_code)
                      or line.get("translated") or "").strip()
        if not translated:
            continue  # gaps are handled by the gap-fill/flag logic, not repair

        issues = []

        # Inline tag preservation: same multiset of tags in source and target.
        src_tags = sorted(_TAG_RE.findall(original))
        tgt_tags = sorted(_TAG_RE.findall(translated))
        if src_tags != tgt_tags:
            issues.append("inline formatting tags were changed; copy the source tags verbatim around the translated text")

        # Untranslated detection for non-Latin targets.
        if check_latin and len(original.split()) >= 2 and _latin_ratio(translated) > 0.6:
            issues.append("line appears untranslated (mostly Latin characters)")

        # Wrong writing system: a stray Hebrew/Arabic/CJK character or a
        # transliteration fragment spliced into a target-language word. The
        # unambiguous visual-twin cases were already repaired for free by the
        # script-guard scrub, so whatever reaches here genuinely needs the source.
        issues.extend(_script_issues(original, translated, target_lang_code))

        # Half-translated long line: a complete source rendered as a short
        # fragment that stops mid-clause (whole-episode split/shift bug).
        if _looks_truncated(original, translated, target_lang_code):
            issues.append(
                "translation appears truncated — it is much shorter than the source and "
                "stops mid-sentence; translate the entire source line in full"
            )

        if issues:
            issues_by_index.setdefault(i, []).extend(issues)

    # Alignment drift (zero-token, whole-episode): lines whose translation belongs
    # to a neighbour. Merged into the same tickets so they ride the one repair call.
    try:
        from utils.alignment_audit import alignment_tickets, _verbatim_terms
        for t in alignment_tickets(parsed_srt, target_lang_code, _verbatim_terms(glossary)):
            issues_by_index.setdefault(t["index"], []).extend(t["issues"])
    except Exception as e:  # detection must never break the pipeline
        logger.warning(f"Alignment audit failed (non-critical): {e}")

    tickets = [{"index": i, "issues": issues_by_index[i]} for i in sorted(issues_by_index)]
    return tickets[:max_tickets]


def build_repair_prompt_items(items: List[Dict], target_lang_name: str) -> str:
    """One prompt fixing every item in ``items``.

    Each item is ``{"source": str, "current": str, "issues": [str, ...]}``. Item
    numbering starts at 1 and is what the response is mapped back by, so the items
    need no shared origin — the caller may gather them from one episode or from a
    whole project, and either way they cost a single request.
    """
    blocks = []
    for n, item in enumerate(items, start=1):
        blocks.append(
            f"{n}| EN: {item.get('source', '')}\n"
            f"   CURRENT: {item.get('current', '')}\n"
            f"   ISSUES: {'; '.join(item.get('issues') or [])}"
        )
    listing = "\n".join(blocks)
    return (
        f"Fix the following {target_lang_name} subtitle lines. For each numbered item, "
        f"produce a corrected translation that resolves the listed issues while staying "
        f"faithful to the EN source. Preserve any inline tags exactly as in the source.\n\n"
        f"{listing}\n\n"
        f"Output ONLY numbered corrected lines, one per item:\n1| corrected line one\n2| corrected line two"
    )


def build_repair_prompt(
    parsed_srt: List[Dict],
    tickets: List[Dict],
    target_lang_code: str,
    target_lang_name: str,
) -> str:
    """One prompt fixing every ticketed line of an episode."""
    items = []
    for t in tickets:
        line = parsed_srt[t["index"]]
        items.append({
            "source": line.get("original", ""),
            "current": (line.get("translations", {}).get(target_lang_code)
                        or line.get("translated") or ""),
            "issues": t["issues"],
        })
    return build_repair_prompt_items(items, target_lang_name)


async def run_repair_pass(
    parsed_srt: List[Dict],
    tickets: List[Dict],
    target_lang_code: str,
    target_lang_name: str,
    primary_code: str,
    model_name: str,
    rate_limiter,
) -> int:
    """Apply one batched LLM repair call for all tickets. Returns lines fixed.

    Lines the model doesn't return stay flagged for human review.
    """
    if not tickets:
        return 0

    from adk_agents.llm_factory import generate
    from utils.api_call_wrapper import rate_limited_call
    from utils.llm_utils import parse_translations_from_text, strip_reasoning_blocks

    prompt = build_repair_prompt(parsed_srt, tickets, target_lang_code, target_lang_name)

    async def _call():
        return await generate(
            model_name=model_name,
            prompt=prompt,
            system_instruction=f"You are a meticulous {target_lang_name} subtitle corrector.",
            temperature=0.2,
            role="repair",
        )

    response = await rate_limited_call(_call, rate_limiter=rate_limiter)
    parsed = parse_translations_from_text(strip_reasoning_blocks(response or ""))

    by_item = {p["index"]: p["text"] for p in parsed}
    fixed = 0
    for n, t in enumerate(tickets, start=1):
        new_text = (by_item.get(n) or "").strip()
        idx = t["index"]
        if new_text:
            parsed_srt[idx].setdefault("translations", {})[target_lang_code] = new_text
            if target_lang_code == primary_code:
                parsed_srt[idx]["translated"] = new_text
            # A returned line still carrying wrong-script characters is not a fix —
            # keep the text (it is usually closer) but leave it flagged for a human
            # rather than reporting a repair that didn't happen.
            residual = _script_issues(parsed_srt[idx].get("original") or "",
                                      new_text, target_lang_code)
            if residual:
                parsed_srt[idx]["needs_review"] = True
                parsed_srt[idx]["review_issues"] = "; ".join(residual)
                continue
            parsed_srt[idx].pop("needs_review", None)
            parsed_srt[idx].pop("review_issues", None)
            fixed += 1
        else:
            parsed_srt[idx]["needs_review"] = True
            parsed_srt[idx]["review_issues"] = "; ".join(t["issues"])
    return fixed
