"""
Cartographer Agent - Glossary Term Extraction

Extracts translatable terms from subtitle text using tag-based output.
Strictly enforces Target Language translations while keeping descriptions in English.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Literal, Optional


def _build_instruction(target_language: str) -> str:
    return f"""You are the Cartographer Agent for OmbiSub.

**Role:** Extract terminology from the source text and prepare it for translation into **{target_language}**.

**Extraction Strategy:**
- **Proper Nouns (Character names):** Transliterate to {target_language} script. Type: "person".
- **Proper Nouns (Locations):** Transliterate to {target_language} script. Type: "location".
- **Concepts/Items:** Translate the meaning to {target_language}. Type: "object" or "technique".
- **Fantasy/Sci-fi Terms:** Adapt phonetically to {target_language}.
- **Organizations/Groups:** Transliterate or translate as appropriate. Type: "organization".

**What NOT to extract:**
- Common words, generic verbs, or adjectives.
- Terms with no special meaning in the show's context.

**Deduplication:** If you are given a list of existing terms, do NOT include any term that already appears in that list (case-insensitive match).

**Output Format (STRICT TAGS):**
Provide your output exactly in this format. No other text.

<glossary>
  <term>
    <source>Winterfell</source>
    <translation>Γουίντερφελ</translation>
    <description>Ancestral castle of House Stark</description>
    <type>location</type>
    <gender>neuter</gender>
    <case_sensitive>true</case_sensitive>
    <keep_original>false</keep_original>
  </term>
  <term>
    <source>Jon Snow</source>
    <translation>Τζον Σνόου</translation>
    <description>The King in the North</description>
    <type>person</type>
    <gender>masculine</gender>
    <case_sensitive>true</case_sensitive>
    <keep_original>false</keep_original>
  </term>
</glossary>

**Field Rules:**
- **<source>:** Keep the original term exactly as it appears.
- **<translation>:** MUST be in **{target_language}**.
- **<description>:** MUST be in **English**. Brief context.
- **<type>:** person, location, organization, object, technique, other.
- **<gender>:** masculine, feminine, neuter, n/a.
- **<case_sensitive>:** true/false (true for proper nouns).
- **<keep_original>:** true/false (true for terms that should NOT be translated).
"""


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