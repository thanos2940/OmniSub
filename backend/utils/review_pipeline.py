"""
Review Pipeline -- Selective quality review for translated episodes.

Architecture:
  1. Heuristic pre-filter selects ~20% of lines most likely to have issues
  2. Selected lines are sent to the Reviewer Agent (LLM-as-judge)
  3. Lines scoring below threshold get their suggestion applied
  4. Remaining flagged lines get needs_review=True for user review

This keeps review cost low (only ~20% of lines hit the LLM) while
catching the most common translation errors: wrong glossary terms,
gender mismatches, untranslated words, and overly long lines.
"""

import re
import json
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class ReviewResult:
    """Result of reviewing a single translated line."""
    line_index: int           # Global index into parsed_srt
    scores: Dict[str, float] = field(default_factory=dict)
    avg_score: float = 1.0
    issues: str = ""
    suggestion: str = ""
    needs_retranslation: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


def select_lines_for_review(
    parsed_srt: List[Dict],
    glossary: Dict,
    tm_fuzzy_indices: Optional[List[int]] = None,
    max_review_pct: float = 0.25,
) -> List[int]:
    """Heuristic filter to select lines worth reviewing.

    Scoring criteria (higher = more likely to have issues):
      +3  Proper noun (person) from glossary is present
      +3  High Latin character ratio in target text (untranslated words)
      +2  Target text >50% longer than source (CPS risk)
      +2  TM fuzzy match was used (0.80-0.94 similarity)
      +1  Dialogue marker (question/exclamation)
      +1  First or last line of an episode (context boundary)

    Args:
        parsed_srt: Full parsed subtitle data with 'original' and 'translated'.
        glossary: Project glossary dict with 'terms' list.
        tm_fuzzy_indices: Optional list of global indices that used fuzzy TM matches.
        max_review_pct: Maximum percentage of lines to review (0.0-1.0).

    Returns:
        Sorted list of global indices to send to the reviewer.
    """
    if not parsed_srt:
        return []

    # Extract person names from glossary (lowercase for case-insensitive matching)
    person_terms = []
    for t in glossary.get("terms", []):
        if t.get("type") == "person":
            term = t.get("term", "").strip()
            if term:
                person_terms.append(term.lower())

    tm_fuzzy_set = set(tm_fuzzy_indices or [])
    total_lines = len(parsed_srt)

    candidates = []
    for i, line in enumerate(parsed_srt):
        original = (line.get("original") or "").strip()
        translated = (line.get("translated") or "").strip()

        if not original or not translated:
            continue

        score = 0

        # Proper noun present in source text
        original_lower = original.lower()
        if any(term in original_lower for term in person_terms):
            score += 3

        # High Latin character ratio in target (potential untranslated words)
        # Remove common Latin characters that are acceptable (numbers, punctuation)
        cleaned_trans = re.sub(r'[0-9\s\W]', '', translated)
        if cleaned_trans:
            latin_chars = sum(1 for c in cleaned_trans if 'A' <= c <= 'Z' or 'a' <= c <= 'z')
            latin_ratio = latin_chars / len(cleaned_trans)
            if latin_ratio > 0.3:
                score += 3

        # Length ratio (Greek is typically 10-20% longer, >50% is suspicious)
        if len(original) > 0 and len(translated) > len(original) * 1.5:
            score += 2

        # TM fuzzy match was used for this line
        if i in tm_fuzzy_set:
            score += 2

        # Dialogue markers (questions, exclamations — formality/tone risk)
        stripped = original.rstrip()
        if stripped.endswith("?") or stripped.endswith("!"):
            score += 1

        # First and last lines of the episode (context boundary errors)
        if i == 0 or i == total_lines - 1:
            score += 1

        if score >= 2:
            candidates.append((i, score))

    # Sort by score descending, cap at max_review_pct of total
    candidates.sort(key=lambda x: -x[1])
    max_count = max(1, int(total_lines * max_review_pct))
    selected = [idx for idx, _ in candidates[:max_count]]

    logger.info(
        f"Review heuristic: {len(candidates)} candidates from {total_lines} lines, "
        f"selected top {len(selected)} (max {max_review_pct:.0%})"
    )

    return selected


async def run_review(
    parsed_srt: List[Dict],
    line_indices: List[int],
    glossary: Dict,
    target_language: str,
    model_name: str = "gemini-flash-latest",
    retranslation_threshold: float = 0.70,
    rate_limiter=None,
) -> List[ReviewResult]:
    """Send selected lines to the Reviewer Agent and parse results.

    Args:
        parsed_srt: Full parsed subtitle data.
        line_indices: Global indices of lines to review.
        glossary: Project glossary for the reviewer's reference.
        target_language: Target language name.
        model_name: Model to use for review (typically Flash for cost).
        retranslation_threshold: Average score below this → needs_retranslation.
        rate_limiter: Optional RateLimiter instance for API call wrapping.

    Returns:
        List of ReviewResult objects for lines that have issues.
    """
    from adk_agents.reviewer_agent import create_reviewer_agent, build_review_prompt
    from adk_agents.operations import _create_session_and_runner, _collect_response_text

    if not line_indices:
        return []

    agent = create_reviewer_agent(
        model_name=model_name,
        target_language=target_language,
        glossary=glossary,
    )

    runner, session_id = await _create_session_and_runner(agent, "review")

    # Build line pairs for the prompt
    line_pairs = []
    for idx in line_indices:
        if 0 <= idx < len(parsed_srt):
            line_pairs.append({
                "index": idx + 1,  # 1-based for the LLM
                "original": parsed_srt[idx].get("original", ""),
                "translated": parsed_srt[idx].get("translated", ""),
            })

    if not line_pairs:
        return []

    prompt = build_review_prompt(line_pairs, target_language)

    # Optionally wrap with rate limiter
    async def _do_review():
        return await _collect_response_text(runner, session_id, prompt)

    if rate_limiter is not None:
        from utils.api_call_wrapper import rate_limited_call
        response = await rate_limited_call(_do_review, rate_limiter=rate_limiter)
    else:
        response = await _do_review()

    # Parse the JSON response
    results = _parse_review_response(response, retranslation_threshold)
    return results


def _parse_review_response(
    response: str,
    retranslation_threshold: float = 0.70,
) -> List[ReviewResult]:
    """Parse the reviewer's JSON output into ReviewResult objects.

    The reviewer outputs a JSON array. Each item has:
      - index (1-based)
      - scores (dict of dimension -> 0.0-1.0)
      - issues (string)
      - suggestion (string — corrected translation)

    Lines with avg score < retranslation_threshold get needs_retranslation=True.
    """
    # Strip any markdown fencing the LLM might have added
    cleaned = response.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned)

    # Find the JSON array
    json_match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if not json_match:
        logger.warning("Reviewer response contained no JSON array")
        return []

    try:
        items = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse reviewer JSON: {e}")
        return []

    if not isinstance(items, list):
        return []

    results = []
    for item in items:
        if not isinstance(item, dict):
            continue

        scores = item.get("scores", {})
        if not scores:
            continue

        # Calculate average score
        score_values = [v for v in scores.values() if isinstance(v, (int, float))]
        avg = sum(score_values) / max(len(score_values), 1)

        # Convert 1-based index to 0-based
        raw_index = item.get("index", 0)
        line_index = raw_index - 1 if raw_index > 0 else 0

        results.append(ReviewResult(
            line_index=line_index,
            scores=scores,
            avg_score=round(avg, 3),
            issues=item.get("issues", ""),
            suggestion=item.get("suggestion", ""),
            needs_retranslation=avg < retranslation_threshold,
        ))

    return results


def apply_review_results(
    parsed_srt: List[Dict],
    results: List[ReviewResult],
    flag_threshold: float = 0.85,
) -> Dict[str, int]:
    """Apply review results to the parsed subtitle data.

    - Lines below retranslation threshold: apply the reviewer's suggestion
      and set needs_review=True so the user can verify.
    - Lines between retranslation and flag thresholds: set needs_review=True
      (user should look at these).
    - Lines above flag threshold: no action.

    Args:
        parsed_srt: Mutable list — will be modified in place.
        results: ReviewResult objects from run_review().
        flag_threshold: Lines with min score below this get flagged.

    Returns:
        Stats dict with counts.
    """
    retranslated = 0
    flagged = 0

    for result in results:
        idx = result.line_index
        if idx < 0 or idx >= len(parsed_srt):
            continue

        if result.needs_retranslation and result.suggestion:
            # Apply the reviewer's corrected translation
            parsed_srt[idx]["translated"] = result.suggestion
            parsed_srt[idx]["needs_review"] = True
            parsed_srt[idx]["review_issues"] = result.issues
            parsed_srt[idx]["review_scores"] = result.scores
            retranslated += 1
        elif result.scores:
            # Check if any individual dimension is below the flag threshold
            min_score = min(result.scores.values()) if result.scores else 1.0
            if min_score < flag_threshold:
                parsed_srt[idx]["needs_review"] = True
                parsed_srt[idx]["review_issues"] = result.issues
                parsed_srt[idx]["review_scores"] = result.scores
                flagged += 1

    return {
        "retranslated": retranslated,
        "flagged": flagged,
        "total_issues": len(results),
    }
