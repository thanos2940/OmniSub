"""
Translator Agent - Context-Aware Subtitle Translation

Translates subtitle text while maintaining glossary consistency,
cultural adaptation, and natural dialogue flow.
"""

import re

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Optional


def _flatten_cue(text: str) -> str:
    """Collapse a multi-line subtitle cue to a single line for translation.

    A cue's display line breaks are a layout artifact, not semantics — and feeding
    them to the model (as ``<br>``) makes it split a two-line cue into two numbered
    entries, which shifts every following line (the split-and-shift bug). We send
    flat text and ask for a single line back; subtitle wrapping is reapplied
    afterwards by the conformance / SubtitleEdit pass.
    """
    if not text:
        return text
    flat = text.replace("<br>", " ").replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", flat).strip()


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


def _second_person_rule(target_language: str) -> str:
    """Guidance for resolving English 'you' (which marks neither number nor register)
    into the correct {target_language} form, using the surrounding dialogue."""
    return (
        f'- SECOND PERSON ("you"): English "you" shows neither number nor formality. '
        f"Infer from the surrounding dialogue whether ONE person or SEVERAL are being "
        f"addressed, and whether the tone is familiar or formal/respectful, then use the "
        f"matching {target_language} pronoun and verb conjugation. Default to SINGULAR "
        f"familiar; use the plural or formal form ONLY when the context clearly calls for "
        f"it (several listeners, a crowd, or a deferential/formal relationship). Keep the "
        f"chosen form consistent for the same speaker-listener pair across the scene."
    )


def _morphological_guidance_rule(target_language: str) -> str:
    """Explicit guidance on declensions, grammatical cases, and articles for glossary terms."""
    lang = (target_language or "").strip().lower()
    if lang in ("greek", "el", "ελληνικά"):
        return (
            '- GLOSSARY MORPHOLOGY & DECLENSIONS: Match glossary translations faithfully, but grammatically DECLINE '
            'nouns, proper names, and adjectives to fit sentence syntax (Nominative, Genitive, Accusative, Vocative) '
            'with appropriate definite/indefinite articles (ο/του/τον, η/της/την, το/του/το, etc.). Do NOT force the lemma/nominative '
            'form where an inflected case is grammatically required.'
        )
    return (
        f"- GLOSSARY MORPHOLOGY: Match glossary translations faithfully while inflecting grammatical cases, pluralization, "
        f"and gender agreements as required by {target_language} sentence syntax."
    )


def _script_integrity_rule(target_language: str) -> str:
    """Explicit script hygiene rule to prevent homoglyph contamination and broken characters."""
    lang = (target_language or "").strip().lower()
    if lang in ("greek", "el", "ελληνικά"):
        return "- SCRIPT HYGIENE: Write ONLY in standard Modern Greek monotonic script (Α-Ω, α-ω with standard tonos ά, έ, ή, ί, ό, ύ, ώ). NEVER mix Latin, Cyrillic, or foreign alphabet characters inside Greek words."
    return ""


def _build_instruction(
    target_language: str,
    glossary_context: str,
    context_guide: str = "",
    structured: bool = False,
) -> str:
    """Build slim instruction for the translator agent."""

    context_section = f"\n{context_guide}\n" if context_guide else ""
    second_person = _second_person_rule(target_language)
    morphology_rule = _morphological_guidance_rule(target_language)
    script_rule = _script_integrity_rule(target_language)
    script_line = f"\n{script_rule}" if script_rule else ""

    if structured:
        return f"""Translate subtitles to {target_language}. You MUST output valid JSON conforming to the requested schema.
The JSON object has a "lines" key containing an array of objects, each with:
- "i": the 1-based source index (e.g. 1, 2, 3...) matching the input line number.
- "t": the translated text for that line.

Rules:
- STRICT 1:1 MAPPING: Each input line index must appear EXACTLY ONCE in the output. Never split a translation across two JSON entries. The output array length MUST equal the input line count.
- ONE LINE PER CUE: each input line is one complete subtitle cue — return its translation as a SINGLE line of plain text with NO line breaks. Never break a cue into multiple lines or entries; subtitle wrapping is applied automatically afterwards.
- Match exact glossary translations. Apply correct grammatical gender for articles/adjectives.
- {morphology_rule}
{second_person}{script_line}
- Flags: cs=match case exactly, ci=adapt casing naturally, ko=KEEP ORIGINAL (do not translate!).
- Translate only what appears in source: "Rin" ≠ "Rin Tohsaka" unless the full name is present.
- Do NOT output internal reasoning blocks or any text outside the JSON.
{context_section}
Glossary (source|translation|type|gender|case):
{glossary_context}"""

    return f"""Translate subtitles to {target_language}. Output ONLY numbered translations — no extra text:

1| translated line one
2| translated line two

Rules:
- STRICT 1:1 MAPPING: Every input line N must produce EXACTLY ONE output line N|. Never split a translation across two numbered lines. If the translation is long, keep it ALL on a single N| line.
- The total number of output lines MUST equal the total number of input lines. No extra lines, no missing lines.
- ONE LINE PER CUE: each input line is one complete subtitle cue — return its translation as a SINGLE line of plain text with NO line breaks; subtitle wrapping is applied automatically afterwards.
- Match exact glossary translations. Apply correct grammatical gender for articles/adjectives.
- {morphology_rule}
{second_person}{script_line}
- Flags: cs=match case exactly, ci=adapt casing naturally, ko=KEEP ORIGINAL (do not translate!).
- Translate only what appears in source: "Rin" ≠ "Rin Tohsaka" unless the full name is present.
{context_section}
Glossary (source|translation|type|gender|case):
{glossary_context}"""


def _build_gemma_instruction(
    target_language: str,
    glossary_context: str,
    context_guide: str = "",
    structured: bool = False,
) -> str:
    """Gemma-specific instruction with native-speaker fluency hint."""
    base = _build_instruction(target_language, glossary_context, context_guide, structured)
    return (
        f"You are natively fluent in {target_language}. "
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
    conformance_hint: str = "",
    negative_examples: str = "",
    lookahead_line: str = "",
    forced_translations: str = "",
) -> str:
    """Build a compact translation prompt for a batch of subtitle lines.

    Prompt section ordering (most → least stable) for Gemini cache efficiency:
        1. episode_context  — same for every scene in the episode (semi-stable)
        2. priority_glossary — filtered per scene but often similar
        3. character_context — changes only when different characters appear
        4. few_shot_context  — varies per scene (TM examples)
        5. context_line      — changes every scene (previous scene hint)
        6. lookahead_line    — changes every scene (lookahead hint)
        7. numbered lines    — always unique

    Keeping stable sections at the front maximises Gemini implicit cache hits
    across scenes within the same episode.
    """
    numbered = "\n".join(
        f"{i + 1}| {_flatten_cue(line)}" for i, line in enumerate(lines)
    )
    ctx = f"[prev] {context_line}\n" if context_line else ""
    la = f"[lookahead (do NOT translate)] {lookahead_line}\n" if lookahead_line else ""
    fs = f"{few_shot_context}\n\n" if few_shot_context else ""
    pg = f"{priority_glossary}\n\n" if priority_glossary else ""
    ch = f"{character_context}\n\n" if character_context else ""
    ep = f"{episode_context}\n\n" if episode_context else ""
    cf = f"{conformance_hint}\n\n" if conformance_hint else ""
    neg = f"{negative_examples}\n\n" if negative_examples else ""
    forced = f"{forced_translations}\n\n" if forced_translations else ""
    return f"Translate to {target_language}:\n\n{cf}{ep}{pg}{ch}{fs}{neg}{forced}{ctx}{la}{numbered}"


def build_translator_instruction(target_language: str = "English", glossary: Dict = None, context_guide: str = "", structured: bool = False) -> str:
    """Public helper: the stable translator system-instruction text (for context caching, Plan 02)."""
    return _build_instruction(target_language, _build_glossary_context(glossary), context_guide, structured)


def create_translator_agent(
    model_name: str = "gemini-flash-lite-latest",
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

    from utils.structured_output import SceneTranslationResponse, use_structured_output
    structured = use_structured_output(model_name)

    if is_local and is_gemma:
        instruction = _build_gemma_instruction(target_language, glossary_context, context_guide, structured)
    else:
        instruction = _build_instruction(target_language, glossary_context, context_guide, structured)

    final_temp = temperature if temperature is not None else (0.5 if is_local and is_gemma else 0.3)

    return Agent(
        name="TranslatorAgent",
        model=create_model(
            model_name,
            temperature=final_temp,
            top_k=top_k,
            top_p=top_p,
            response_schema=SceneTranslationResponse if structured else None,
            role="translation",
        ),
        instruction=instruction,
        tools=[],
        output_schema=None,
        output_key="translation_result",
    )
