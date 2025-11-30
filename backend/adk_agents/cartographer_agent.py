"""
ADK-based Cartographer Agent (Extraction Only)

This agent extracts glossary terms from text using structured output.
For research, use ResearchAgent separately.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Literal

# Retry configuration for API calls
retry_config = types.HttpRetryOptions(attempts=5)

# Define Pydantic models for structured output
class GlossaryTerm(BaseModel):
    """A single glossary term for translation."""
    term: str = Field(description="The original term in the source language")
    translation: str = Field(description="The translated term")
    context: str = Field(description="Why this translation was chosen (e.g., 'Official romanization', 'Character name')")
    category: Literal["character", "location", "term", "cultural"] = Field(description="Category of the term")
    case_sensitive: bool = Field(default=True, description="Whether the term is case-sensitive (usually true for proper nouns)")

class GlossaryOutput(BaseModel):
    """Structured output for glossary extraction."""
    terms: List[GlossaryTerm] = Field(default_factory=list, description="List of extracted glossary terms")

def create_cartographer_agent(model_name: str = "gemini-flash-latest") -> Agent:
    """
    Create the Cartographer Agent for glossary term extraction.
    
    This agent analyzes subtitle text to extract terms that need translation consistency.
    Note: This agent does NOT perform web research - use ResearchAgent for that.
    
    Args:
        model_name: Gemini model identifier
        
    Returns:
        Configured ADK Agent instance with structured output schema
    """
    
    # Instructions that define the agent's behavior
    instruction = """You are the Cartographer Agent for OmbiSub, a subtitle translation platform.

Your responsibility is to extract glossary terms from subtitle text:
- Character names (e.g., "Frieren", "Himmel")
- Location names (e.g., "Kingdom of Enser")
- Specialized terminology (e.g., "mana", "grimoire")
- Cultural references specific to the source material

Quality Guidelines:
- Include context to explain translation choices
- Mark case-sensitive terms (proper nouns) appropriately
- Avoid overly generic terms (e.g., don't add "the" or "a")
- Focus on terms that need consistency across translation

Extract ALL relevant terms from the provided text."""
    
    # Create the agent with structured output ONLY
    # No tools - pure extraction with output_schema
    agent = Agent(
        name="CartographerAgent",
        model=Gemini(
            model=model_name,
            retry_options=retry_config
        ),
        instruction=instruction,
        tools=[],  # No tools to avoid conflicts with output_schema
        output_schema=GlossaryOutput,  # Structured output using Pydantic
        output_key="glossary_result"  # Saves output to session state
    )
    
    return agent
