"""
Translator Agent - Context-Aware Subtitle Translation

Translates subtitle text while maintaining glossary consistency,
cultural adaptation, and natural dialogue flow.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Optional


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
3. Adapt idioms to target culture (avoid literal translations)
4. Multi-line subtitles use `<br>` as a line break marker. Preserve these markers.

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
**Strict Coverage Rule:**
You MUST provide a translation for EVERY input line. Do not skip any.

**Output Format (STRICT TAGS):**
Provide your output exactly in this format. No other text.

<translations>
<line index="1">Translation here</line>
<line index="2">Translation here</line>
</translations>

**CRITICAL:** Every input line number MUST have a matching `<line index="N">` tag inside the `<translations>` block.
"""


def _build_local_instruction(
    target_language: str,
    glossary_context: str,
    context_guide: str = "",
) -> str:
    return f"""Translate subtitles into {target_language}. Use the Tag-Based format.

Rules:
1. Provide finalized translations in <translations> tags.
2. Every input line MUST have a <line index="N"> tag.

{context_section}{glossary_section}

Example Output:

<translations>
<line index="5">Καλή επιτυχία!</line>
<line index="6">Μην με κοροϊδεύεις.</line>
</translations>

Translate these lines now:"""


def create_translator_agent(
    model_name: str = "gemini-flash-latest",
    glossary: Dict = None,
    target_language: str = "English",
    context_guide: str = "",
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
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
    
    is_local = model_name.startswith("local/")
    is_gemma = "gemma" in model_name.lower()

    # Always use the comprehensive instructions for modern models.
    # We append grammatical steering for Gemma models specifically if needed.
    instruction = _build_instruction(target_language, glossary_context, context_guide)
    
    if is_local and is_gemma:
        # User defined trigger for Gemma thinking mode
        
        if target_language:
            lang_hint = (
                f"You are natively fluent in {target_language}. "
                f"Your {target_language} output must sound like a native speaker: "
                f"use correct articles, natural word order, and idiomatic expressions. "
                f"Never leave foreign words untranslated unless marked keep_original.\n\n"
            )
            instruction = "<|think|> " + lang_hint + instruction
    
    # Use passed temperature or fallback to defaults
    final_temp = temperature if temperature is not None else (0.5 if is_local and is_gemma else 0.3)
    
    return Agent(
        name="TranslatorAgent",
        model=create_model(
            model_name, 
            temperature=final_temp,
            top_k=top_k,
            top_p=top_p,
            response_schema=None # We use raw Tag parsing now for maximum robustness
        ),
        instruction=instruction,
        tools=[],
        output_schema=None,
        output_key="translation_result"
    )
