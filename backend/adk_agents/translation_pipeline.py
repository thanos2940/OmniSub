"""
Translation Pipeline - Sequential Multi-Agent Workflow

Combines Cartographer and Translator agents in a deterministic pipeline
for complete subtitle translation with glossary enhancement.
"""

from google.adk.agents import SequentialAgent
from .cartographer_agent import create_cartographer_agent
from .translator_agent import create_translator_agent
from typing import Dict, Optional


def create_translation_pipeline(
    project_name: str,
    target_language: str,
    glossary: Dict,
    context_guide: str = "",
    cartographer_model: str = "gemini-flash-latest",
    translator_model: str = "gemini-flash-latest",
    skip_glossary_step: bool = False,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
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
        context_guide: Project-specific tone/style guidance for the translator
        cartographer_model: Model for glossary extraction
        translator_model: Model for translation
        skip_glossary_step: If True, skip glossary enhancement
        
    Returns:
        SequentialAgent that runs both steps in order
    """
    sub_agents = []
    
    if not skip_glossary_step:
        sub_agents.append(create_cartographer_agent(
            model_name=cartographer_model,
            target_language=target_language,
            temperature=temperature,
            top_k=top_k,
            top_p=top_p,
        ))
    
    sub_agents.append(create_translator_agent(
        model_name=translator_model,
        glossary=glossary,
        target_language=target_language,
        context_guide=context_guide,
        temperature=temperature,
        top_k=top_k,
        top_p=top_p,
    ))
    
    # Sanitize project name to be a valid ADK agent name (no spaces)
    sanitized_name = "".join(c if c.isalnum() or c == "_" else "_" for c in project_name)
    
    return SequentialAgent(
        name=f"TranslationPipeline_{sanitized_name}",
        sub_agents=sub_agents
    )
