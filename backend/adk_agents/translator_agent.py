"""
Translator Agent - Context-Aware Subtitle Translation

Translates subtitle text while maintaining glossary consistency,
cultural adaptation, and natural dialogue flow.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Optional


# Compact type/gender abbreviations to save tokens
_TYPE_ABBREV = {"person": "P", "location": "L", "organization": "O", "object": "obj", "technique": "T", "other": ""}
_GENDER_ABBREV = {"masculine": "m", "feminine": "f", "neuter": "n", "n/a": ""}


def _build_glossary_context(glossary: Dict) -> str:
    """Format glossary as a compact pipe-delimited table to minimize tokens.

    Format: source|translation|type|gender|flags
    Flags: cs=case-sensitive, ci=case-insensitive, ko=keep-original
    """
    if not glossary or not glossary.get("terms"):
        return "(none)"

    rows = []
    for term in glossary["terms"]:
        src = term.get("term", "")
        if not src:
            continue

        flags = ["cs" if term.get("case_sensitive", True) else "ci"]
        if term.get("keep_original", False):
            tgt = src
            flags.append("ko")
        else:
            tgt = term.get("translation", src)

        t = _TYPE_ABBREV.get(term.get("type", ""), "")
        g = _GENDER_ABBREV.get(term.get("gender", "n/a"), "")

        # Only emit non-empty fields
        meta = "|".join(x for x in [t, g, *flags] if x)
        desc = term.get("description", "")
        row = f"{src}|{tgt}|{meta}" + (f"|{desc}" if desc else "")
        rows.append(row)

    header = "# source|translation|type|gender|case\n"
    return header + "\n".join(rows)


def _build_instruction(
    target_language: str,
    glossary_context: str,
    context_guide: str = "",
) -> str:
    """Build slim instruction for the translator agent."""

    context_section = f"\n{context_guide}\n" if context_guide else ""

    return f"""Translate subtitles to {target_language}. Output ONLY numbered translations — no extra text:

1| translated line one
2| translated line two

Rules:
- Every input line number must have a matching N| line in output.
- Preserve <br> line-break markers within a line.
- Match exact glossary translations. Apply correct grammatical gender for articles/adjectives.
- Flags: cs=match case exactly, ci=adapt casing naturally, ko=KEEP ORIGINAL (do not translate!).
- Translate only what appears in source: "Rin" ≠ "Rin Tohsaka" unless the full name is present.
{context_section}
Glossary (source|translation|type|gender|case):
{glossary_context}"""


def _build_gemma_instruction(
    target_language: str,
    glossary_context: str,
    context_guide: str = "",
) -> str:
    """Gemma-specific instruction with native-speaker fluency hint."""
    base = _build_instruction(target_language, glossary_context, context_guide)
    return (
        f"<|think|> You are natively fluent in {target_language}. "
        f"Use correct articles, natural word order, and idiomatic expressions.\n\n" + base
    )


def build_translation_prompt(
    lines: List[str],
    target_language: str,
    context_line: str = "",
    few_shot_context: str = "",
    priority_glossary: str = "",
    character_context: str = "",
    episode_context: str = "",
) -> str:
    """Build a compact translation prompt for a batch of subtitle lines.

    Prompt section ordering (most → least stable) for Gemini cache efficiency:
        1. episode_context  — same for every scene in the episode (semi-stable)
        2. priority_glossary — filtered per scene but often similar
        3. character_context — changes only when different characters appear
        4. few_shot_context  — varies per scene (TM examples)
        5. context_line      — changes every scene (previous scene hint)
        6. numbered lines    — always unique

    Keeping stable sections at the front maximises Gemini implicit cache hits
    across scenes within the same episode.

    Args:
        lines: Original subtitle text lines (no timecodes).
        target_language: Target language name.
        context_line: Optional one-line context from the end of the previous scene.
        few_shot_context: Optional TM-retrieved example translations block.
        priority_glossary: Optional filtered glossary terms for this scene.
        character_context: Optional character profile block for this scene.
        episode_context: Optional "Previously in this show:" block from past episodes.
    """
    numbered = "\n".join(
        f"{i + 1}| {line.replace(chr(10), '<br>')}" for i, line in enumerate(lines)
    )
    ctx = f"[prev] {context_line}\n" if context_line else ""
    fs = f"{few_shot_context}\n\n" if few_shot_context else ""
    pg = f"{priority_glossary}\n\n" if priority_glossary else ""
    ch = f"{character_context}\n\n" if character_context else ""
    ep = f"{episode_context}\n\n" if episode_context else ""
    return f"Translate to {target_language}:\n\n{ep}{pg}{ch}{fs}{ctx}{numbered}"


def create_translator_agent(
    model_name: str = "gemini-flash-latest",
    glossary: Dict = None,
    target_language: str = "English",
    context_guide: str = "",
    temperature: Optional[float] = None,
    top_k: Optional[int] = None,
    top_p: Optional[float] = None,
) -> Agent:
    """Create Translator Agent for context-aware subtitle translation."""
    glossary_context = _build_glossary_context(glossary)

    is_local = model_name.startswith("local/")
    is_gemma = "gemma" in model_name.lower()

    if is_local and is_gemma:
        instruction = _build_gemma_instruction(target_language, glossary_context, context_guide)
    else:
        instruction = _build_instruction(target_language, glossary_context, context_guide)

    final_temp = temperature if temperature is not None else (0.5 if is_local and is_gemma else 0.3)

    return Agent(
        name="TranslatorAgent",
        model=create_model(
            model_name,
            temperature=final_temp,
            top_k=top_k,
            top_p=top_p,
            response_schema=None,
        ),
        instruction=instruction,
        tools=[],
        output_schema=None,
        output_key="translation_result",
    )
