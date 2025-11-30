"""
Glossary Orchestrator - Research + Extraction Pipeline

Combines ResearchAgent and CartographerAgent for comprehensive
glossary creation with optional web research enhancement.
"""

from google.adk.agents import SequentialAgent
from .research_agent import create_research_agent
from .cartographer_agent import create_cartographer_agent


def create_glossary_orchestrator(
    model_name: str = "gemini-flash-latest",
    enable_research: bool = True
) -> SequentialAgent:
    """
    Create orchestrator combining research and extraction agents.
    
    Workflow:
    1. ResearchAgent (optional): Web research for canonical information
    2. CartographerAgent: Extract glossary terms with structured output
    
    Args:
        model_name: Gemini model for both agents
        enable_research: Whether to include web research step
        
    Returns:
        SequentialAgent orchestrating the glossary creation workflow
    """
    agents = []
    
    if enable_research:
        agents.append(create_research_agent(model_name))
    
    agents.append(create_cartographer_agent(model_name))
    
    return SequentialAgent(
        name="GlossaryOrchestrator",
        sub_agents=agents,
        description="Orchestrates glossary creation through research and extraction"
    )
