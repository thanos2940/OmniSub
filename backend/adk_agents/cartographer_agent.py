"""
Cartographer Agent - Glossary Term Extraction

Extracts translatable terms from subtitle text using structured output.
Strictly enforces Target Language translations while keeping descriptions in English.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from pydantic import BaseModel, Field
from typing import List, Literal


def _build_instruction(target_language: str) -> str:
    return f"""You are the Cartographer Agent for OmbiSub.

**Role:** Extract terminology from the source text and prepare it for translation into **{target_language}**.

**Strict Output Rules:**
1. **`translation` Field:** MUST be in **{target_language}**.
   - If {target_language} uses a different script than the source (e.g., English -> Greek), you MUST transliterate or translate names/places into the {target_language} script.
   - Do NOT output English translations unless {target_language} is English.
   
2. **`description` Field:** MUST be in **English**.
   - This is for the human editor to understand the term's context.
   - Keep it brief (1-2 sentences max).

3. **`term` Field:** Keep in the original source language/script exactly as it appears.

4. **`keep_original` Field:** Set to `true` ONLY for terms that should NOT be translated 
   (e.g., attack names, spell names, brand names that are always said in the original language).
   When `true`, the translator will use the original term as-is.

5. **`gender` Field:** Set the grammatical gender the term should take in {target_language}.
   This controls which articles/adjectives are used. Use "n/a" for languages without grammatical gender.

**Extraction Strategy:**
- **Proper Nouns (Character names):** Transliterate to {target_language} script. Type: "person".
- **Proper Nouns (Locations):** Transliterate to {target_language} script. Type: "location".
- **Concepts/Items:** Translate the meaning to {target_language}. Type: "object" or "technique".
- **Fantasy/Sci-fi Terms:** Adapt phonetically to {target_language}. Consider setting keep_original=true if the term sounds better untranslated.
- **Organizations/Groups:** Transliterate or translate as appropriate. Type: "organization".

**What NOT to extract:**
- Common words (yes, no, hello, goodbye, please, etc.)
- Generic verbs, adjectives, or adverbs
- Terms that have no special meaning in the show's context
- Sentence fragments or dialogue lines

**Deduplication:** If you are given a list of existing terms, do NOT include any term that already appears in that list (case-insensitive match on the `term` field).

**Output ONLY valid JSON. No explanations outside the JSON.**"""


def _build_local_instruction(target_language: str) -> str:
    """Build instruction for local models that don't support structured output schemas."""
    base = _build_instruction(target_language)
    return base + f"""

**JSON Structure Example:**
{{
  "terms": [
    {{
      "term": "Winterfell",
      "translation": "Γουίντερφελ",
      "description": "Ancestral castle of House Stark",
      "type": "location",
      "gender": "neuter",
      "case_sensitive": true,
      "keep_original": false
    }}
  ]
}}"""


class GlossaryTerm(BaseModel):
    """A single glossary term for translation consistency."""
    term: str = Field(description="Original term as it appears in the source text")
    translation: str = Field(
        description="The term translated or transliterated into the TARGET language defined in instructions."
    )
    description: str = Field(description="Brief definition/context of the term in ENGLISH (1-2 sentences)")
    type: Literal["person", "location", "organization", "event", "object", "technique", "other"] = Field(
        description="Term category"
    )
    gender: Literal["masculine", "feminine", "neuter", "n/a"] = Field(
        default="neuter",
        description="Grammatical gender for the term in the TARGET language"
    )
    case_sensitive: bool = Field(
        default=True, 
        description="Whether capitalization matters (true for proper nouns)"
    )
    keep_original: bool = Field(
        default=False,
        description="If true, the translator will NOT translate this term — it will be kept in the original language"
    )


class GlossaryOutput(BaseModel):
    """Structured output containing extracted glossary terms."""
    terms: List[GlossaryTerm] = Field(
        default_factory=list, 
        description="List of extracted glossary terms"
    )


def create_cartographer_agent(model_name: str = "gemini-flash-latest", target_language: str = "English") -> Agent:
    """
    Create Cartographer Agent for glossary term extraction.
    
    Args:
        model_name: Gemini model identifier
        target_language: Target language for translations
        
    Returns:
        Configured ADK Agent with structured output
    """
    is_local = model_name.startswith("local/")
    instruction = _build_local_instruction(target_language) if is_local else _build_instruction(target_language)
    
    return Agent(
        name="CartographerAgent",
        model=create_model(model_name),
        instruction=instruction,
        tools=[],
        # Disable structured output for local models for compatibility
        output_schema=None if model_name.startswith("local/") else GlossaryOutput,
        output_key="glossary_result"
    )