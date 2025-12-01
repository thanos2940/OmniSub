"""
Cartographer Agent - Glossary Term Extraction

Extracts translatable terms from subtitle text using structured output.
Refined to strictly enforce Target Language translations while keeping descriptions in English.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Literal

RETRY_CONFIG = types.HttpRetryOptions(attempts=5)


def _build_instruction(target_language: str) -> str:
    return f"""You are the Cartographer Agent for OmbiSub.

**Role:** Extract terminology from the source text and prepare it for translation into **{target_language}**.

**Strict Output Rules:**
1. **`translation` Field:** MUST be in **{target_language}**.
   - If {target_language} uses a different script than the source (e.g., English -> Greek), you MUST transliterate or translate names/places into the {target_language} script.
   - Do NOT output English translations unless {target_language} is English.
   
2. **`description` Field:** MUST be in **English**.
   - This is for the human editor to understand the term's context.

3. **`term` Field:** Keep in the original source language/script.

**Extraction Strategy:**
- **Proper Nouns:** Transliterate to {target_language}.
- **Concepts/Items:** Translate the meaning to {target_language}.
- **Fantasy Terms:** Adapt phonetically to {target_language}.

**Categories:**
- Character names -> type: "person"
- Location names -> type: "location"
- Specialized terminology -> type: "object" or "technique"
- Cultural references -> type: "other"
"""


class GlossaryTerm(BaseModel):
    """A single glossary term for translation consistency."""
    term: str = Field(description="Original term as it appears in the source text")
    translation: str = Field(
        description="The term translated or transliterated into the TARGET language defined in instructions."
    )
    description: str = Field(description="Brief definition/context of the term in ENGLISH")
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
    instruction = _build_instruction(target_language)
    
    return Agent(
        name="CartographerAgent",
        model=Gemini(model=model_name, retry_options=RETRY_CONFIG),
        instruction=instruction,
        tools=[],
        output_schema=GlossaryOutput,
        output_key="glossary_result"
    )