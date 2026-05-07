"""
Cartographer Agent - Glossary Term Extraction

Extracts translatable terms from subtitle text using tag-based output.
Strictly enforces Target Language translations while keeping descriptions in English.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Literal, Optional


def _build_instruction(target_language: str) -> str:
    return f"""Extract named entities and special terms from subtitle text and translate them to {target_language}.

Extract: character names (person), places (location), groups (organization), special items/concepts (object/technique).
Skip: common words, generic verbs/adjectives, terms with no show-specific meaning.
Deduplication: skip any term already in the provided existing-terms list (case-insensitive).

Output ONLY valid XML — no extra text:

<glossary>
  <term>
    <source>Winterfell</source>
    <translation>Γουίντερφελ</translation>
    <description>Stark ancestral castle</description>
    <type>location</type>
    <gender>neuter</gender>
    <case_sensitive>true</case_sensitive>
    <keep_original>false</keep_original>
  </term>
</glossary>

Field rules: source=original text, translation=in {target_language}, description=brief English context, type=person/location/organization/object/technique/other, gender=masculine/feminine/neuter/n/a, case_sensitive=true/false, keep_original=true if should NOT be translated."""


def create_cartographer_agent(
    model_name: str = "gemini-flash-latest",
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