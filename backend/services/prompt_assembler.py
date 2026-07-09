"""
PromptAssembler (v2 plan, D4) — one authority for translation context.

Every translation request — live scene, whole-episode (D1), or Batch API (D3) —
assembles its system instruction + user prompt + response schema here, so a batch
translation is *identical in quality* to a live one and context changes happen in
exactly one place.

The "bible" is the stable per-project context block (glossary + context guide,
rendered as the translator system instruction), hash-versioned so episodes can
record which bible they were translated under (targeted re-translation later).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from adk_agents.translator_agent import build_translator_instruction, build_translation_prompt
from utils.cache_manager import compute_fingerprint
from utils.structured_output import (
    SceneTranslationResponse,
    EpisodeTranslationResponse,
    VerifiedEpisodeTranslationResponse,
    use_structured_output,
    use_source_echo,
)


@dataclass
class Bible:
    """Compiled stable context for one project/target-language pair."""
    project_name: str
    target_language: str
    system_instruction: str
    fingerprint: str
    glossary: Dict
    structured: bool
    model_name: str


@dataclass
class TranslationRequest:
    """Everything needed to issue one translation call on any lane."""
    system_instruction: str
    prompt: str
    response_schema: Optional[type]
    expected_count: int
    structured: bool
    # Maps the request's 1-based line numbers back to global parsed_srt indices.
    index_map: List[int] = field(default_factory=list)
    # When True the response carries a per-line source echo ("s") to verify against
    # the source before mapping (source-echo guard); a mismatch drops to a gap.
    verify_echo: bool = False


def compile_bible(
    project_name: str,
    project_meta: Dict,
    model_name: str,
    target_language: Optional[str] = None,
) -> Bible:
    """Build the stable system instruction + fingerprint for a project."""
    from utils import storage
    resolved_meta = project_meta
    if project_name:
        db_meta = storage.load_resolved_project_metadata(project_name)
        if db_meta:
            resolved_meta = db_meta

    glossary = (resolved_meta or {}).get("glossary", {"terms": []})
    context_guide = (resolved_meta or {}).get("context_guide", "")
    target = target_language or (resolved_meta or {}).get("target_language", "English")
    structured = use_structured_output(model_name)
    instruction = build_translator_instruction(target, glossary, context_guide, structured=structured)
    return Bible(
        project_name=project_name,
        target_language=target,
        system_instruction=instruction,
        fingerprint=compute_fingerprint(glossary, context_guide),
        glossary=glossary,
        structured=structured,
        model_name=model_name,
    )


_NEW_TERMS_RULE = (
    "\nAdditionally, output a top-level \"new_terms\" array (max 10): recurring proper "
    "nouns or domain terms in the source that are NOT in the glossary, each as "
    "{\"term\", \"translation\", \"type\"} using the rendering you chose. Empty array if none."
)

_SOURCE_ECHO_RULE = (
    "\nEach line object MUST also include an \"s\" field: copy the first 3-4 words of "
    "the SOURCE line at that index VERBATIM from the input. It is used only to verify "
    "alignment — never translate or alter it, and make sure it matches the line you "
    "put in \"i\"."
)


def assemble_episode_request(
    bible: Bible,
    lines: List[Dict],
    indices: List[int],
    episode_context: str = "",
    conformance_hint: str = "",
    character_context: str = "",
    negative_examples: str = "",
    collect_new_terms: bool = False,
) -> TranslationRequest:
    """Whole-episode request (D1): all untranslated lines in one numbered list.

    ``lines``/``indices`` are the untranslated subset and their global indices.
    """
    source_texts = [l.get("original", "") for l in lines]
    prompt = build_translation_prompt(
        source_texts,
        bible.target_language,
        character_context=character_context,
        episode_context=episode_context,
        conformance_hint=conformance_hint,
        negative_examples=negative_examples,
    )
    system_instruction = bible.system_instruction
    schema = None
    verify_echo = False
    if bible.structured:
        echo = use_source_echo(bible.model_name)
        if echo:
            # The verified schema carries the source echo and an optional new_terms
            # array, so it covers both the collect and no-collect cases.
            schema = VerifiedEpisodeTranslationResponse
            system_instruction = system_instruction + _SOURCE_ECHO_RULE
            verify_echo = True
            if collect_new_terms:
                system_instruction = system_instruction + _NEW_TERMS_RULE
        else:
            schema = EpisodeTranslationResponse if collect_new_terms else SceneTranslationResponse
            if collect_new_terms:
                system_instruction = system_instruction + _NEW_TERMS_RULE
    return TranslationRequest(
        system_instruction=system_instruction,
        prompt=prompt,
        response_schema=schema,
        expected_count=len(source_texts),
        structured=bible.structured,
        index_map=list(indices),
        verify_echo=verify_echo,
    )


def assemble_scene_request(
    bible: Bible,
    source_texts: List[str],
    indices: List[int],
    context_hint: str = "",
    few_shot_block: str = "",
    priority_block: str = "",
    character_block: str = "",
    episode_context: str = "",
    conformance_hint: str = "",
    negative_block: str = "",
    lookahead_hint: str = "",
) -> TranslationRequest:
    """Scene-chunk request (fallback / local lane) — same context authority."""
    prompt = build_translation_prompt(
        source_texts,
        bible.target_language,
        context_hint,
        few_shot_context=few_shot_block,
        priority_glossary=priority_block,
        character_context=character_block,
        episode_context=episode_context,
        conformance_hint=conformance_hint,
        negative_examples=negative_block,
        lookahead_line=lookahead_hint,
    )
    return TranslationRequest(
        system_instruction=bible.system_instruction,
        prompt=prompt,
        response_schema=SceneTranslationResponse if bible.structured else None,
        expected_count=len(source_texts),
        structured=bible.structured,
        index_map=list(indices),
    )
