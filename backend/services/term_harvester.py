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
from google import genai

from utils import storage

logger = logging.getLogger(__name__)

HARVEST_SYSTEM_INSTRUCTION = """You are an expert subtitle terminology and localization analyst.
Your task is to analyze translated dialogue lines and extract key named entities and domain-specific terminology that should be added to the project's localization glossary.

Focus on:
1. Character names, nicknames, aliases, and titles (e.g., "Doctor Strange", "Iron Man", "Cap", "Sensei").
2. Locations, fictional realms, planets, and landmarks (e.g., "Asgard", "Wakanda", "Sanctum Sanctorum").
3. Organizations, factions, military units, and groups (e.g., "S.H.I.E.L.D.", "Hydra", "Avengers").
4. Unique objects, artifacts, spells, weapons, and technology (e.g., "Mjolnir", "Infinity Stones", "Tesseract").
5. Specialized lore jargon or recurring cultural terms.

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
            term_str = t.get("term", "").lower().strip()
            if term_str:
                existing_terms_set.add(term_str)

    # Format dialogue sample (limit to representative window if huge)
    sample_pairs = []
    max_lines = min(len(source_lines), len(target_lines), 250)
    for i in range(max_lines):
        src = source_lines[i].strip()
        tgt = target_lines[i].strip()
        if src and tgt:
            sample_pairs.append(f"Line {i+1}: [EN] {src}  -->  [{target_language[:2].upper()}] {tgt}")

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
        config = storage.load_global_config()
        chosen_model = model_name or config.get("default_glossary_model") or config.get("default_translation_model") or "gemini-2.5-flash"
        api_key = config.get("api_key") or os.environ.get("GOOGLE_API_KEY", "")
        client = genai.Client(api_key=api_key)

        # Call Gemini API
        response = client.models.generate_content(
            model=chosen_model,
            contents=prompt,
            config={
                "system_instruction": HARVEST_SYSTEM_INSTRUCTION,
                "response_mime_type": "application/json",
                "temperature": 0.2,
            },
        )

        text = response.text or ""
        # Parse JSON
        data = json.loads(text)
        candidates = data.get("discovered_terms", [])

        # Filter and sanitize
        valid_terms = []
        for cand in candidates:
            term_name = cand.get("term", "").strip()
            translation = cand.get("translation", "").strip()
            if not term_name or not translation:
                continue
            if term_name.lower() in existing_terms_set:
                continue

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

    source_lines = []
    target_lines = []

    for item in episode_data:
        orig = item.get("original", "")
        trans = item.get("translation", "")
        if orig and trans:
            source_lines.append(orig)
            target_lines.append(trans)

    if not source_lines:
        return []

    target_lang = meta.get("target_language", "Greek")
    existing_glossary = meta.get("glossary", {"terms": []})

    return await harvest_terms_from_lines(
        source_lines=source_lines,
        target_lines=target_lines,
        target_language=target_lang,
        existing_glossary=existing_glossary,
        model_name=model_name,
    )
