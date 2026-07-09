"""
Translation Pipeline — agent factory for subtitle translation.

When glossary enhancement is skipped (the common case), returns a bare
TranslatorAgent directly — no SequentialAgent wrapper overhead.
When glossary enhancement is requested, wraps both agents in a
SequentialAgent so the Cartographer runs first and its output flows into
the Translator.
"""

from google.adk.agents import Agent, SequentialAgent
from .cartographer_agent import create_cartographer_agent
from .translator_agent import create_translator_agent
from typing import Dict, Optional, Union


def create_translation_pipeline(
    project_name: str,
    target_language: str,
    glossary: Dict,
    context_guide: str = "",
    cartographer_model: str = "gemini-flash-lite-latest",
    translator_model: str = "gemini-flash-lite-latest",
    skip_glossary_step: bool = False,
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
) -> Union[Agent, SequentialAgent]:
    """Return a translation agent (or pipeline) for the given configuration.

    Returns a bare TranslatorAgent when skip_glossary_step=True (default for
    batch jobs) to avoid SequentialAgent dispatch overhead.  Returns a
    SequentialAgent(Cartographer → Translator) only when live glossary
    enhancement is requested.
    """
    shared_kwargs = dict(temperature=temperature, top_k=top_k, top_p=top_p)

    translator = create_translator_agent(
        model_name=translator_model,
        glossary=glossary,
        target_language=target_language,
        context_guide=context_guide,
        **shared_kwargs,
    )

    if skip_glossary_step:
        # Fast path — no wrapper needed
        return translator

    cartographer = create_cartographer_agent(
        model_name=cartographer_model,
        target_language=target_language,
        **shared_kwargs,
    )

    sanitized_name = "".join(c if c.isalnum() or c == "_" else "_" for c in project_name)
    return SequentialAgent(
        name=f"TranslationPipeline_{sanitized_name}",
        sub_agents=[cartographer, translator],
    )
