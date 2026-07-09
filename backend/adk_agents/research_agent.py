"""
Research Agent - Web Research for Glossary Enhancement

Performs web research using Google Search to find official information
about characters, locations, and terminology for translation accuracy.
"""

from google.adk.agents import Agent
from google.adk.tools import google_search
from .llm_factory import create_model, is_local_model

INSTRUCTION = """You are a Research Agent specializing in media research for translation.

Your task:
1. Use Google Search to find official information about characters, locations, and terms
2. Prioritize official sources (wikis, official websites, fan communities)
3. Find canonical spellings and romanizations
4. Gather cultural context and background information

**Output Format (STRICT TAGS):**
Wrap your internal analysis in <research_findings> tags. Use nested tags for categories.

Example:
<research_findings>
  <characters>
    List main and recurring characters with their full names, roles, and any official translations.
  </characters>
  <locations>
    List key locations with descriptions and significance.
  </locations>
  <key_concepts>
    List specialized terminology unique to the show/movie.
  </key_concepts>
  <cultural_notes>
    Note any cultural references or historical context.
  </cultural_notes>
  <tone_style>
    Describe the overall tone (e.g., dark fantasy, lighthearted comedy) and any register variations.
  </tone_style>
</research_findings>

Be thorough but concise. Focus on information that would help a translator maintain consistency and accuracy."""


def create_research_agent(model_name: str = "gemini-flash-lite-latest") -> Agent:
    """Create Research Agent for web-based information gathering."""
    is_local = is_local_model(model_name)
    # <|think|> is a Gemma/local thinking trigger — meaningless for Gemini
    instruction = INSTRUCTION

    return Agent(
        name="ResearchAgent",
        model=create_model(model_name),
        instruction=instruction,
        tools=[google_search] if not is_local else [],
        output_key="research_findings",
    )
