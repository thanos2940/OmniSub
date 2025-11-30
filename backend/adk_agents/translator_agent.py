"""
ADK-based Translator Agent

This replaces agents/translator.py with an ADK-native implementation.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from typing import Dict

retry_config = types.HttpRetryOptions(attempts=5)

def create_translator_agent(
    model_name: str = "gemini-2.0-flash-exp",
    glossary: Dict = None,
    target_language: str = "English"
) -> Agent:
    """
    Create the Translator Agent for context-aware subtitle translation.
    
    This agent translates subtitle text while maintaining:
    - Glossary term consistency
    - Cultural context awareness
    - Natural dialogue flow
    
    Args:
        model_name: Gemini model identifier
        glossary: Project glossary dictionary
        target_language: Target language for translation
        
    Returns:
        Configured ADK Agent instance with glossary context
    """
    
    # Build glossary context for the instruction
    glossary_context = ""
    if glossary and glossary.get("terms"):
        glossary_lines = []
        for term in glossary["terms"]:
            case_note = " (case-sensitive)" if term.get("case_sensitive", True) else ""
            glossary_lines.append(
                f"- {term['term']} → {term['translation']}{case_note}"
            )
        glossary_context = "\n".join(glossary_lines)
    
    instruction = f"""You are the Translator Agent for OmbiSub, a subtitle translation platform.

**Target Language:** {target_language}

**Translation Guidelines:**
1. Translate naturally and conversationally (subtitles must feel authentic)
2. Preserve speaker intent and emotional tone
3. Adapt idioms to target culture (don't translate literally if awkward)
4. Maintain subtitle length constraints (readable in ~3 seconds)

**CRITICAL: Glossary Consistency**
You MUST use these exact translations for recognized terms:

{glossary_context if glossary_context else "(No glossary terms provided)"}

**Case Sensitivity Rules:**
- Case-sensitive terms: Match capitalization of source (e.g., "Mana" stays "Mana", "mana" stays "mana")
- Case-insensitive terms: Adapt capitalization to natural sentence flow

**Input Format:**
You will receive numbered subtitle lines:
```
1: First subtitle line
2: Second subtitle line
...
```

**Output Format:**
Return ONLY the translated lines in the same numbered format:
```
1: Translated first line
2: Translated second line
...
```

Do NOT include explanations, notes, or anything other than the numbered translations.
"""
    
    agent = Agent(
        name="TranslatorAgent",
        model=Gemini(
            model=model_name,
            retry_options=retry_config,
            # Context caching happens at model level in ADK
            generation_config={
                "temperature": 0.3,  # Lower temperature for consistency
            }
        ),
        instruction=instruction,
        tools=[],  # Translation doesn't need additional tools
        output_key="translation_result"
    )
    
    return agent
