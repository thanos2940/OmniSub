"""
Sequential Translation Pipeline

Combines Cartographer → Translator in a deterministic workflow.
"""

from google.adk.agents import SequentialAgent
from .cartographer_agent import create_cartographer_agent
from .translator_agent import create_translator_agent
from typing import Dict, Optional

def create_translation_pipeline(
    project_name: str,
    target_language: str,
    glossary: Dict,
    cartographer_model: str = "gemini-flash-latest",
    translator_model: str = "gemini-flash-latest",
    skip_glossary_step: bool = False
) -> SequentialAgent:
    """
    Create a sequential pipeline for full translation workflow.
    
    Pipeline steps:
    1. CartographerAgent: Extract/enhance glossary from subtitle text
    2. TranslatorAgent: Translate using enhanced glossary
    
    Args:
        project_name: Name of the translation project
        target_language: Target language for translation
        glossary: Existing project glossary
        cartographer_model: Model for glossary extraction
        translator_model: Model for translation
        skip_glossary_step: If True, skip glossary enhancement (use existing)
        
    Returns:
        SequentialAgent that runs both steps in order
        
    Example:
        >>> pipeline = create_translation_pipeline(
        ...     project_name="Frieren",
        ...     target_language="Greek",
        ...     glossary=existing_glossary
        ... )
        >>> runner = InMemoryRunner(agent=pipeline)
        >>> result = await runner.run_debug("Translate: [subtitle text]")
    """
    
    sub_agents = []
    
    # Step 1: Glossary enhancement (optional)
    if not skip_glossary_step:
        cartographer = create_cartographer_agent(model_name=cartographer_model)
        sub_agents.append(cartographer)
    
    # Step 2: Translation (always included)
    translator = create_translator_agent(
        model_name=translator_model,
        glossary=glossary,
        target_language=target_language
    )
    sub_agents.append(translator)
    
    # Create sequential pipeline
    pipeline = SequentialAgent(
        name=f"TranslationPipeline_{project_name}",
        sub_agents=sub_agents
    )
    
    return pipeline
