"""
Research Agent - Web Research for Glossary Enhancement

Performs web research using Google Search to find official information
about characters, locations, and terminology for translation accuracy.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import google_search
from google.genai import types

RETRY_CONFIG = types.HttpRetryOptions(attempts=5)

INSTRUCTION = """You are a Research Agent specializing in media research for translation.

Your task:
- Use Google Search to find official information about characters, locations, and terms
- Prioritize official sources (wikis, official websites, fan communities)
- Find canonical spellings and romanizations
- Gather cultural context and background information

Output your findings as clear text for the extraction agent to process."""


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
    return Agent(
        name="ResearchAgent",
        model=Gemini(model=model_name, retry_options=RETRY_CONFIG),
        instruction=INSTRUCTION,
        tools=[google_search],
        output_key="research_findings"
    )
