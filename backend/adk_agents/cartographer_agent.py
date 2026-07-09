"""
Cartographer Agent - Glossary Term Extraction

Extracts translatable terms from subtitle text using tag-based output.
Strictly enforces Target Language translations while keeping descriptions in English.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Literal, Optional


def _build_instruction(target_language: str) -> str:
    return f"""You are a glossary builder for subtitle translation. Your job is to produce a COMPREHENSIVE glossary of terms for a show/movie, translated into {target_language}.

You have TWO sources of terms:
1. **SOURCE TEXT** — Extract every named entity and special term from the provided subtitle text.
2. **YOUR KNOWLEDGE** — Use your knowledge of the show/movie to add ALL important terms that don't appear in the text sample. This includes characters, locations, organizations, items, techniques, and concepts from the ENTIRE series/movie, not just what's in the sample.

Categories to cover thoroughly:
- ALL character names (main, recurring, and notable minor characters)
- ALL significant locations (cities, buildings, regions, worlds)
- Organizations, factions, houses, teams, groups
- Special items, weapons, artifacts, technologies
- Unique concepts, techniques, powers, abilities, titles
- Episode/arc-specific terminology, catchphrases, invented words

Skip: common words, generic verbs/adjectives, terms with no show-specific meaning.
Deduplication: skip any term already in the provided existing-terms list (case-insensitive).

AIM FOR 40-80+ TERMS. Be thorough — a translator needs every proper noun and special term to maintain consistency.

Output ONLY valid XML — no extra text:

<glossary>
  <term>
    <source>Winterfell</source>
    <translation>Γουίντερφελ</translation>
    <description>Stark ancestral castle</description>
    <context_quote>There must always be a Stark in Winterfell.</context_quote>
    <type>location</type>
    <gender>neuter</gender>
    <case_sensitive>true</case_sensitive>
    <keep_original>false</keep_original>
  </term>
</glossary>

Field rules: source=original text, translation=in {target_language}, description=brief English context, context_quote=the exact sentence where the term was found (if applicable), type=person/location/organization/object/technique/other, gender=masculine/feminine/neuter/n/a, case_sensitive=true/false, keep_original=true if should NOT be translated."""


def create_cartographer_agent(
    model_name: str = "gemini-flash-lite-latest",
    target_language: str = "English",
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
) -> Agent:
    """
    Create Cartographer Agent for glossary term extraction.
    
    Args:
        model_name: Gemini model identifier
        target_language: Target language for translations
        
    Returns:
        ADK Agent for tag-based extraction
    """
    is_local = model_name.startswith("local/")
    is_gemma = "gemma" in model_name.lower()
    
    instruction = _build_instruction(target_language)
    if is_local and is_gemma:
        # User requested thinking trigger for Gemma
        instruction = "<|think|> " + instruction
    
    return Agent(
        name="CartographerAgent",
        model=create_model(
            model_name, 
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
            response_schema=None
        ),
        instruction=instruction,
        tools=[],
        output_schema=None,
        output_key="glossary_result"
    )