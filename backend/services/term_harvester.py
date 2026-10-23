"""
Term Harvester Service — Automated Named Entity & Key Term Extraction from Subtitles.

Scans translated dialogue for high-value entities (characters, locations, organizations,
unique lore items/jargon) not yet present in the project or parent universe glossaries.
"""

import json
import logging
import os
import re
from typing import List, Dict, Optional, Tuple

from utils import storage
from utils.llm_utils import loads_tolerant, strip_reasoning_blocks
from utils.text_normalize import fix_japanese_name_greek_tonos
from utils.language_codes import to_code

logger = logging.getLogger(__name__)

HARVEST_SYSTEM_INSTRUCTION = """You are an expert subtitle terminology and localization analyst.
Your task is to analyze translated dialogue lines and extract key named entities and domain-specific terminology that should be added to the project's localization glossary.

Focus on:
1. Character names, nicknames, aliases, and titles (e.g., "Doctor Strange", "Iron Man", "Cap", "Sensei").
2. Locations, fictional realms, planets, and landmarks (e.g., "Asgard", "Wakanda", "Sanctum Sanctorum").
3. Organizations, factions, military units, and groups (e.g., "S.H.I.E.L.D.", "Hydra", "Avengers").
4. Unique objects, artifacts, spells, weapons, and technology (e.g., "Mjolnir", "Infinity Stones", "Tesseract").
5. Specialized lore jargon or recurring cultural terms.
6. For Greek target language, Japanese character names and entities MUST place the Greek accent (τόνος) on the FIRST syllable (e.g., Tanjiro -> Τάντζιρο, Zenitsu -> Ζένιτσου, Nezuko -> Νέζουκο, Inosuke -> Ίνοσουκε, Naruto -> Νάρουτο).

Exclude:
- Common, everyday conversational words (e.g., "car", "house", "friend", "morning", "police").
- Terms already provided in the existing glossary.

For each term, output:
- "term": Canonical English source term as it appears or should be indexed.
- "translation": The exact localized translation used or recommended in the target language.
- "type": One of "character", "location", "organization", "object", "technique", "other".
- "gender": Grammatical gender in the target language ("masculine", "feminine", "neuter", or "n/a").
- "description": Brief context or role description (1 short sentence).
- "context_snippet": A brief snippet showing how the term was used in the dialogue.
- "confidence": Float between 0.0 and 1.0.

You MUST return a valid JSON object matching this schema:
{
  "discovered_terms": [
    {
      "term": "...",
      "translation": "...",
      "type": "...",
      "gender": "...",
      "description": "...",
      "context_snippet": "...",
      "confidence": 0.95
    }
  ]
}
"""


async def harvest_terms_from_lines(
    source_lines: List[str],
    target_lines: List[str],
    target_language: str = "Greek",
    existing_glossary: Optional[Dict] = None,
    model_name: Optional[str] = None,
) -> List[Dict]:
    """Extract key terms from paired source and translated dialogue lines."""
    if not source_lines or not target_lines:
        return []

    # Compile existing terms to avoid duplicates
    existing_terms_set = set()
    if existing_glossary and "terms" in existing_glossary:
        for t in existing_glossary["terms"]:
            term_str = (t.get("term") or t.get("source") or "").lower().strip()
            if term_str:
                existing_terms_set.add(term_str)

    # Format dialogue sample (limit to representative window if huge)
    sample_pairs = []
    max_lines = min(len(source_lines), len(target_lines), 250)
    tgt_tag = to_code(target_language).upper()
    for i in range(max_lines):
        src = source_lines[i].strip()
        tgt = target_lines[i].strip()
        if src and tgt:
            sample_pairs.append(f"Line {i+1}: [EN] {src}  -->  [{tgt_tag}] {tgt}")

    if not sample_pairs:
        return []

    dialogue_block = "\n".join(sample_pairs)
    existing_terms_summary = ", ".join(list(existing_terms_set)[:100]) or "(none)"

    prompt = f"""Target Language: {target_language}

Existing Glossary Terms (DO NOT output these):
{existing_terms_summary}

Translated Subtitle Dialogue:
{dialogue_block}

Analyze the dialogue above and extract all new named entities and key terms. Return JSON with the "discovered_terms" array."""

    try:
        from services import agy_llm
        if agy_llm.agent_mode():
            # Agent mode: the agent CLI answers (the Gemini API only for a prompt its filter refuses).
            from services import agent_prompts as ap
            from services.project_bootstrap import clean_terms
            pairs = [(s.strip(), t.strip()) for s, t in zip(source_lines, target_lines) if s.strip() and t.strip()]
            reply = await agy_llm.acomplete(
                ap.harvest_prompt(target_language, pairs[:max_lines], sorted(existing_terms_set)), purpose="harvest_terms")
            candidates = [{**c, "description": c.get("notes", "")}
                          for c in clean_terms(ap.parse_reply(reply, allowed=set())["terms"])]
        else:
            from adk_agents.llm_factory import generate
            from utils.model_resolver import resolve_model

            dummy_meta = {"settings": {}}
            chosen_model = model_name or resolve_model("glossary", dummy_meta)

            # Call via LLM seam (handles Gemini, LiteLLM local models, etc.)
            response_text = await generate(
                model_name=chosen_model,
                prompt=prompt,
                system_instruction=HARVEST_SYSTEM_INSTRUCTION,
                role="glossary",
            )

            clean_text = strip_reasoning_blocks(response_text or "")
            data = loads_tolerant(clean_text) or {}
            candidates = data.get("discovered_terms", []) if isinstance(data, dict) else []

        is_greek = (target_language or "").strip().lower() in ("greek", "el", "ελληνικά")

        # Filter and sanitize
        valid_terms = []
        for cand in candidates:
            term_name = cand.get("term", "").strip()
            translation = cand.get("translation", "").strip()
            if not term_name or not translation:
                continue
            if term_name.lower() in existing_terms_set:
                continue

            if is_greek:
                translation = fix_japanese_name_greek_tonos(translation)

            valid_terms.append({
                "term": term_name,
                "translation": translation,
                "type": cand.get("type", "other"),
                "gender": cand.get("gender", "n/a"),
                "description": cand.get("description", ""),
                "context_snippet": cand.get("context_snippet", ""),
                "confidence": cand.get("confidence", 0.9),
                "case_sensitive": True,
                "keep_original": False,
            })

        return valid_terms

    except Exception as e:
        logger.error(f"Term harvesting failed: {e}", exc_info=True)
        return []


async def harvest_terms_for_episode(
    project_name: str,
    episode_name: str,
    model_name: Optional[str] = None,
) -> List[Dict]:
    """Harvest terms from a specific episode in a project."""
    meta = storage.load_resolved_project_metadata(project_name)
    if not meta:
        return []

    episode_data = storage.load_episode_data(project_name, episode_name)
    if not episode_data:
        return []

    target_lang = meta.get("target_language", "Greek")
    target_code = to_code(target_lang)

    source_lines = []
    target_lines = []

    for item in episode_data:
        orig = item.get("original", "")
        trans = (
            item.get("translations", {}).get(target_code)
            or item.get("translated")
            or item.get("translation")
            or ""
        )
        if orig and trans:
            source_lines.append(orig)
            target_lines.append(trans)

    if not source_lines:
        return []

    existing_glossary = meta.get("glossary", {"terms": []})

    return await harvest_terms_from_lines(
        source_lines=source_lines,
        target_lines=target_lines,
        target_language=target_lang,
        existing_glossary=existing_glossary,
        model_name=model_name,
    )
