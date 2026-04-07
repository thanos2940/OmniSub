"""
Translator Agent - Context-Aware Subtitle Translation

Translates subtitle text while maintaining glossary consistency,
cultural adaptation, and natural dialogue flow.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List


def _build_glossary_context(glossary: Dict) -> str:
    """Format glossary terms with all properties for the translator.
    
    Includes type, gender, case-sensitivity, keep_original flag,
    and description so the translator has full context for each term.
    """
    if not glossary or not glossary.get("terms"):
        return "(No glossary terms provided)"
    
    lines = []
    for term in glossary["terms"]:
        # Term → Translation
        if term.get("keep_original", False):
            header = f"- **{term['term']}** → {term['term']} [DO NOT TRANSLATE — keep original]"
        else:
            header = f"- **{term['term']}** → {term.get('translation', term['term'])}"
        
        # Collect property tags
        tags = []
        
        # Type (person, location, object, technique, etc.)
        term_type = term.get("type", "")
        if term_type:
            tags.append(term_type)
        
        # Gender
        gender = term.get("gender", "n/a")
        if gender and gender != "n/a":
            tags.append(gender)
        
        # Case sensitivity
        if term.get("case_sensitive", True):
            tags.append("case-sensitive")
        else:
            tags.append("case-insensitive")
        
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        
        # Description/context — helps translator understand the term
        desc = term.get("description", "")
        desc_str = f" — {desc}" if desc else ""
        
        lines.append(f"{header}{tag_str}{desc_str}")
    
    return "\n".join(lines)


def _build_instruction(
    target_language: str,
    glossary_context: str,
    context_guide: str = "",
) -> str:
    """Build complete instruction for the translator agent."""
    
    context_section = ""
    if context_guide:
        context_section = f"""
**Show/Project Context:**
{context_guide}

"""

    return f"""You are the Translator Agent for OmbiSub, a subtitle translation platform.

**Target Language:** {target_language}
{context_section}
**Translation Guidelines:**
1. Translate naturally and conversationally (subtitles must feel authentic to native speakers)
2. Preserve speaker intent and emotional tone
3. Adapt idioms to target culture (avoid literal translations if they sound awkward)
4. Multi-line subtitles use `<br>` as a line break marker. Preserve these markers in your translation. Example:
   - Input: `5: First part of the sentence,<br>second part of the sentence.`
   - Output: `5: Μετάφραση πρώτου μέρους,<br>μετάφραση δεύτερου μέρους.`

**CRITICAL: Glossary Consistency**
Use these exact translations for recognized terms:

{glossary_context}

**Strict Grammar & Casing Rules:**
1. **Gender Compliance:** If a glossary term has a specified gender (e.g., [neuter]), you MUST use the corresponding articles and adjectives in {target_language}.
   - Example for Greek: "mana" [neuter] -> "το μάνα" (NOT "η μάνα")
   
2. **Case Sensitivity:**
   - **(case-sensitive):** Match the glossary term EXACTLY as shown.
   - **(case-insensitive):** Adapt casing naturally based on sentence position.

3. **DO NOT TRANSLATE terms marked [DO NOT TRANSLATE — keep original].** Use the original term as-is, integrating it naturally into the {target_language} sentence structure.

4. **No Expansion of Partial Matches:** Use the glossary translation ONLY if the source text matches the glossary term (subject to case-sensitivity). 
   - **Example:** If the glossary contains "Rin Tohsaka" but the subtitle line only says "Rin", do NOT translate it as "Rin Tohsaka" (unless the context guide says otherwise). Translate "Rin" as its own entity.
   - Respect the source text's choice of using a nickname, given name, or full name.

**Input Format:**
Numbered subtitle lines:
```
1: First subtitle line
2: Second subtitle line
```

**Output Format:**
Return ONLY translated lines in same numbered format. Keep each entry on a SINGLE line (use `<br>` for line breaks within an entry):
```
1: Translated first line
2: Translated line with<br>a line break
```

Do NOT include explanations, notes, or anything other than numbered translations.
Do NOT skip any line numbers. Every input line must have a corresponding output line."""


def create_translator_agent(
    model_name: str = "gemini-flash-latest",
    glossary: Dict = None,
    target_language: str = "English",
    context_guide: str = "",
) -> Agent:
    """
    Create Translator Agent for context-aware subtitle translation.
    
    Maintains glossary consistency, cultural adaptation, and natural dialogue flow.
    Uses lower temperature for consistent output.
    
    Args:
        model_name: Gemini model identifier
        glossary: Project glossary with terms and translations
        target_language: Target language for translation
        context_guide: Project-specific context/tone/style guidance
        
    Returns:
        Configured ADK Agent for translation
    """
    glossary_context = _build_glossary_context(glossary)
    instruction = _build_instruction(target_language, glossary_context, context_guide)
    
    return Agent(
        name="TranslatorAgent",
        model=create_model(model_name, temperature=0.3),
        instruction=instruction,
        tools=[],
        output_key="translation_result"
    )
