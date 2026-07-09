"""
Glossary Orchestrator - Research + Extraction Pipeline

Combines ResearchAgent and CartographerAgent for comprehensive
glossary creation with optional web research enhancement.
"""

from google.adk.agents import SequentialAgent
from .research_agent import create_research_agent
from .cartographer_agent import create_cartographer_agent


def create_glossary_orchestrator(
    model_name: str = "gemini-flash-lite-latest",
    enable_research: bool = True,
    target_language: str = "English"
) -> SequentialAgent:
    """
    Create orchestrator combining research and extraction agents.
    
    Workflow:
    1. ResearchAgent (optional): Web research for canonical information
    2. CartographerAgent: Extract glossary terms with structured output
    
    Args:
        model_name: Gemini model for both agents
        enable_research: Whether to include web research step
        target_language: Target language for translations
        
    Returns:
        SequentialAgent orchestrating the glossary creation workflow
    """
    from .llm_factory import is_local_model
    agents = []
    
    # Auto-disable research for local models as they lack search tools
    if enable_research and is_local_model(model_name):
        # We could also keep it enabled but it won't have tools
        # For now, let's respect enable_research but log a warning if it has no tools
        pass

    if enable_research:
        agents.append(create_research_agent(model_name))
    
    agents.append(create_cartographer_agent(model_name, target_language=target_language))
    
    return SequentialAgent(
        name="GlossaryOrchestrator",
        sub_agents=agents,
        description="Orchestrates glossary creation through research and extraction"
    )
