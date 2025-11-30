"""
Research Agent - Handles web research using Google Search

This agent is specialized for researching show/anime information
to support glossary creation.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.adk.tools import google_search
from google.genai import types

retry_config = types.HttpRetryOptions(attempts=5)

def create_research_agent(model_name: str = "gemini-flash-latest") -> Agent:
    """
    Create a Research Agent specialized in web research.
    
    This agent uses Google Search to find:
    - Official character names and romanizations
    - Location information
    - Cultural context and terminology
    
    Returns:
        ADK Agent configured with google_search tool
    """
    
    instruction = """You are a Research Agent specializing in anime/show information gathering.

Your task:
- Use Google Search to find official information about characters, locations, and terms
- Prioritize official sources (wikis, official websites, fan communities)
- Provide concise summaries of your findings

Output your research findings as clear text that will be used by another agent."""

    agent = Agent(
        name="ResearchAgent",
        model=Gemini(model=model_name, retry_options=retry_config),
        instruction=instruction,
        tools=[google_search],
        output_key="research_findings"
    )
    
    return agent
