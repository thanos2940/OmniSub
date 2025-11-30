"""
Translation Pipeline - Sequential Multi-Agent Workflow

Combines Cartographer and Translator agents in a deterministic pipeline
for complete subtitle translation with glossary enhancement.
"""

from google.adk.agents import SequentialAgent
from .cartographer_agent import create_cartographer_agent
from .translator_agent import create_translator_agent
from typing import Dict


def create_translation_pipeline(
    project_name: str,
    target_language: str,
    glossary: Dict,
    cartographer_model: str = "gemini-flash-latest",
    translator_model: str = "gemini-flash-latest",
    skip_glossary_step: bool = False
) -> SequentialAgent:
    """
    Create sequential pipeline for full translation workflow.
    
    Pipeline Steps:
    1. CartographerAgent: Extract/enhance glossary from subtitle text (optional)
    2. TranslatorAgent: Translate using enhanced glossary
    
    Args:
        project_name: Name of the translation project
        target_language: Target language for translation
        glossary: Existing project glossary
        cartographer_model: Model for glossary extraction
        translator_model: Model for translation
        skip_glossary_step: If True, skip glossary enhancement
        
    Returns:
        SequentialAgent that runs both steps in order
    """
    sub_agents = []
    
    if not skip_glossary_step:
        sub_agents.append(create_cartographer_agent(model_name=cartographer_model))
    
    sub_agents.append(create_translator_agent(
        model_name=translator_model,
        glossary=glossary,
        target_language=target_language
    ))
    
    return SequentialAgent(
        name=f"TranslationPipeline_{project_name}",
        sub_agents=sub_agents
    )
