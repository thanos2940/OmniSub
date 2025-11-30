"""
Glossary Orchestrator Agent

Combines ResearchAgent and CartographerAgent to create comprehensive
glossaries with both web research and term extraction.
"""

from google.adk.agents import SequentialAgent
from .research_agent import create_research_agent
from .cartographer_agent import create_cartographer_agent

def create_glossary_orchestrator(
    model_name: str = "gemini-flash-latest",
    enable_research: bool = True
) -> SequentialAgent:
    """
    Create an orchestrator that combines research and extraction agents.
    
    Workflow:
    1. ResearchAgent (optional): Performs web research if enabled
    2. CartographerAgent: Extracts glossary terms with structured output
    
    Args:
        model_name: Gemini model to use for both agents
        enable_research: Whether to include the research step
        
    Returns:
        SequentialAgent that orchestrates the glossary creation workflow
    """
    
    agents = []
    
    # Add research agent if enabled
    if enable_research:
        agents.append(create_research_agent(model_name))
    
    # Always add extraction agent
    agents.append(create_cartographer_agent(model_name))
    
    orchestrator = SequentialAgent(
        name="GlossaryOrchestrator",
        sub_agents=agents,
        description="Orchestrates glossary creation through research and extraction"
    )
    
    return orchestrator
