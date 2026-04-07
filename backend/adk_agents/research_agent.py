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
- Use Google Search to find official information about characters, locations, and terms
- Prioritize official sources (wikis, official websites, fan communities)
- Find canonical spellings and romanizations
- Gather cultural context and background information

**Structure your output under these headings:**

## Characters
List main and recurring characters with their full names, roles, and any official translations.

## Locations
List key locations with descriptions and significance.

## Key Terms & Concepts
List specialized terminology unique to the show/movie (e.g., magic systems, factions, special abilities).

## Cultural Notes
Note any cultural references, real-world parallels, or historical context relevant to translation.

## Tone & Style
Describe the overall tone (e.g., dark fantasy, lighthearted comedy, formal drama) and any speech patterns or register variations between characters.

Be thorough but concise. Focus on information that would help a translator maintain consistency and accuracy."""


def create_research_agent(model_name: str = "gemini-flash-latest") -> Agent:
    """
    Create Research Agent for web-based information gathering.
    
    Uses Google Search tool to find:
    - Official character names and romanizations
    - Location information and significance
    - Cultural context and terminology
    
    Args:
        model_name: Gemini model identifier
        
    Returns:
        ADK Agent configured with google_search tool
    """
    # Research Agent with Google Search tool is only compatible with Gemini models
    # Local models don't support the tool yet.
    is_local = is_local_model(model_name)
    
    return Agent(
        name="ResearchAgent",
        model=create_model(model_name),
        instruction=INSTRUCTION,
        tools=[google_search] if not is_local else [],
        output_key="research_findings"
    )
