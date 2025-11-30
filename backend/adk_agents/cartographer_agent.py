"""
Cartographer Agent - Glossary Term Extraction

Extracts translatable terms from subtitle text using structured output.
For web research, use ResearchAgent separately.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Literal

RETRY_CONFIG = types.HttpRetryOptions(attempts=5)

INSTRUCTION = """You are the Cartographer Agent for OmbiSub, a subtitle translation platform.

Extract glossary terms from subtitle text:
- Character names (e.g., "Frieren", "Himmel")
- Location names (e.g., "Kingdom of Enser")
- Specialized terminology (e.g., "mana", "grimoire")
- Cultural references specific to the source material

Quality Guidelines:
- Include context explaining translation choices
- Mark case-sensitive terms (proper nouns) appropriately
- Avoid generic terms like articles or common words
- Focus on terms requiring translation consistency

Extract ALL relevant terms from the provided text."""


class GlossaryTerm(BaseModel):
    """A single glossary term for translation consistency."""
    term: str = Field(description="Original term in source language")
    translation: str = Field(description="Translated term")
    context: str = Field(description="Reason for translation choice")
    category: Literal["character", "location", "term", "cultural"] = Field(
        description="Term category"
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


def create_cartographer_agent(model_name: str = "gemini-flash-latest") -> Agent:
    """
    Create Cartographer Agent for glossary term extraction.
    
    Uses structured output schema to ensure consistent JSON format.
    Does NOT perform web research; use ResearchAgent for that.
    
    Args:
        model_name: Gemini model identifier
        
    Returns:
        Configured ADK Agent with structured output
    """
    return Agent(
        name="CartographerAgent",
        model=Gemini(model=model_name, retry_options=RETRY_CONFIG),
        instruction=INSTRUCTION,
        tools=[],
        output_schema=GlossaryOutput,
        output_key="glossary_result"
    )
