"""
Translator Agent - Context-Aware Subtitle Translation

Translates subtitle text while maintaining glossary consistency,
cultural adaptation, and natural dialogue flow.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types
from typing import Dict, List

RETRY_CONFIG = types.HttpRetryOptions(attempts=5)


def _build_glossary_context(glossary: Dict) -> str:
    """Format glossary terms for inclusion in agent instruction."""
    if not glossary or not glossary.get("terms"):
        return "(No glossary terms provided)"
    
    lines = []
    for term in glossary["terms"]:
        case_note = " (case-sensitive)" if term.get("case_sensitive", True) else " (case-insensitive)"
        gender = term.get("gender", "n/a")
        gender_note = f" [{gender}]" if gender and gender != "n/a" else ""
        lines.append(f"- {term['term']} → {term['translation']}{gender_note}{case_note}")
    return "\n".join(lines)


def _build_instruction(target_language: str, glossary_context: str) -> str:
    """Build complete instruction for the translator agent."""
    return f"""You are the Translator Agent for OmbiSub, a subtitle translation platform.

**Target Language:** {target_language}

**Translation Guidelines:**
1. Translate naturally and conversationally (subtitles must feel authentic)
2. Preserve speaker intent and emotional tone
3. Adapt idioms to target culture (avoid literal translations if awkward)
4. Maintain subtitle length constraints (readable in ~3 seconds)

**CRITICAL: Glossary Consistency**
Use these exact translations for recognized terms:

{glossary_context}

**Strict Grammar & Casing Rules:**
1. **Gender Compliance:** If a glossary term has a specified gender (e.g., [neuter]), you MUST use the corresponding articles and adjectives in {target_language}.
   - Example: "mana" [neuter] -> "το μάνα" (NOT "η μάνα")
   
2. **Case Sensitivity:**
   - **(case-sensitive):** Match the glossary term EXACTLY as shown.
   - **(case-insensitive):** If the glossary term is "Mana" but the source says "her mana", output "το μάνα της" (lowercase). Retain the casing of the SOURCE text for the term.

**Input Format:**
Numbered subtitle lines:
```
1: First subtitle line
2: Second subtitle line
```

**Output Format:**
Return ONLY translated lines in same numbered format:
```
1: Translated first line
2: Translated second line
```

Do NOT include explanations or anything other than numbered translations."""


def create_translator_agent(
    model_name: str = "gemini-flash-latest",
    glossary: Dict = None,
    target_language: str = "English"
) -> Agent:
    """
    Create Translator Agent for context-aware subtitle translation.
    
    Maintains glossary consistency, cultural adaptation, and natural dialogue flow.
    Uses lower temperature for consistent output.
    
    Args:
        model_name: Gemini model identifier
        glossary: Project glossary with terms and translations
        target_language: Target language for translation
        
    Returns:
        Configured ADK Agent for translation
    """
    glossary_context = _build_glossary_context(glossary)
    instruction = _build_instruction(target_language, glossary_context)
    
    return Agent(
        name="TranslatorAgent",
        model=Gemini(
            model=model_name,
            retry_options=RETRY_CONFIG,
            generation_config={"temperature": 0.3}
        ),
        instruction=instruction,
        tools=[],
        output_key="translation_result"
    )
