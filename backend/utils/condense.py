"""
Subtitle Condensation Utility (Plan 24).
"""

import re
import json
import logging
from typing import List, Dict, Optional
from services.cached_translator import generate_cached
from utils import storage

logger = logging.getLogger(__name__)


async def condense_lines(
    project_name: str,
    lines: List[Dict],
    max_cps: float,
    max_chars: int,
    glossary: Dict,
) -> Dict[int, str]:
    """Condense over-limit lines via GenAI.

    Args:
        project_name: Project name
        lines: List of line dicts, each with keys: "index" (global index), "original", "translated", "_duration"
        max_cps: Maximum characters per second limit
        max_chars: Maximum characters per visual line limit
        glossary: Glossary dictionary for preserving key terms

    Returns:
        A dict mapping global line index -> condensed translation.
    """
    if not lines:
        return {}

    # Build priority glossary list for prompts
    glossary_terms = [t.get("term", "") for t in glossary.get("terms", []) if t.get("term")]

    # Build prompt
    prompt_lines = []
    for i, item in enumerate(lines):
        orig = item.get("original", "").replace("\n", " ")
        trans = item.get("translated", "").replace("\n", " ")
        dur = item.get("_duration", 2.0)
        # Calculate target char count based on max_cps and duration
        target_len = min(max_chars * 2, int(max_cps * dur))
        prompt_lines.append(
            f"i: {i+1} | orig: \"{orig}\" | trans: \"{trans}\" | duration: {dur}s | max_len: {target_len} chars"
        )

    prompt = f"""You are a professional subtitler. Rewrite each subtitle line to be shorter so that it can be read comfortably within the character limit and CPS (characters per second) limit, while preserving its meaning, tone, and register. Keep it natural spoken dialogue.

Rules:
- Do not omit character names.
- Do not drop these glossary terms: {', '.join(glossary_terms)}
- Keep line breaks if necessary, but keep total length under the max_len.
- Output valid JSON conforming to the requested schema.

Subtitles:
{chr(10).join(prompt_lines)}

Output JSON format:
{{
  "lines": [
    {{ "i": 1, "t": "condensed translation" }},
    ...
  ]
}}
"""
    try:
        from pydantic import BaseModel, Field

        class CondensedLine(BaseModel):
            i: int = Field(description="1-based index matching input subtitle line number")
            t: str = Field(description="Condensed translation text")

        class SceneCondensationResponse(BaseModel):
            lines: List[CondensedLine]

        # Use same model as translation
        from utils.model_resolver import resolve_model
        metadata = storage.load_project_metadata(project_name) or {}
        model_name = resolve_model("condense", metadata)
        
        from adk_agents.llm_factory import generate
        response_text = await generate(
            model_name=model_name,
            prompt=prompt,
            system_instruction="You are a professional subtitle condensation assistant.",
            temperature=0.3,
            response_schema=SceneCondensationResponse,
            role="condense",
        )
        
        # Parse JSON
        from utils.structured_output import strip_reasoning_blocks
        clean_text = strip_reasoning_blocks(response_text)
        
        # Clean candidates
        clean_candidate = clean_text.strip()
        if clean_candidate.startswith("```json"):
            clean_candidate = clean_candidate[7:]
        elif clean_candidate.startswith("```"):
            clean_candidate = clean_candidate[3:]
        if clean_candidate.endswith("```"):
            clean_candidate = clean_candidate[:-3]
        clean_candidate = clean_candidate.strip()
        
        # Find the outermost braces/brackets
        match = re.search(r'(\{.*\})|(\[.*\])', clean_candidate, re.DOTALL)
        if match:
            clean_candidate = match.group(0)

        parsed = json.loads(clean_candidate)
        results = {}
        for item in parsed.get("lines", []):
            idx = int(item["i"]) - 1
            if 0 <= idx < len(lines):
                results[lines[idx]["index"]] = item["t"]
        return results
    except Exception as e:
        logger.error(f"Failed to condense lines: {e}")
        return {}
