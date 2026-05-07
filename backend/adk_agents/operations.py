"""
ADK Agent Operations - High-level Functions for OmbiSub

Provides ADK-based implementations of all agent operations.
Uses ADK Runner for proper session management and observability.
"""

import json
import re
from uuid import uuid4
from datetime import datetime
from typing import List, Dict, Tuple, Optional

from google.adk.runners import Runner, types as adk_types
from google.adk.agents import Agent
from .llm_factory import create_model
from .cartographer_agent import create_cartographer_agent
from .research_agent import create_research_agent
from .translator_agent import create_translator_agent
from .glossary_orchestrator import create_glossary_orchestrator
from adk_config.session_service import get_session_service
from utils.llm_utils import strip_reasoning_blocks, parse_glossary_from_text


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect_response_text(
    runner: Runner,
    session_id: str,
    prompt: str,
    user_id: str = "default_user"
) -> str:
    """Run an agent and collect the full text response.

    Consolidates the repeated async-for-event pattern used across all
    operations into a single reusable helper.
    """
    response_parts: list[str] = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=adk_types.Content(
            role="user",
            parts=[adk_types.Part(text=prompt)]
        ),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_parts.append(part.text)
    return "".join(response_parts)


def _make_session_id(prefix: str) -> str:
    """Generate a unique, timestamped session ID."""
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"


async def _create_session_and_runner(
    agent,
    prefix: str,
) -> Tuple[Runner, str]:
    """Create a session + runner pair for a one-shot agent execution."""
    session_id = _make_session_id(prefix)
    session_service = get_session_service()
    app_name = f"OmbiSub_{session_id}"
    runner = Runner(
        agent=agent,
        app_name=app_name,
        session_service=session_service,
    )
    await session_service.create_session(
        session_id=session_id,
        user_id="default_user",
        app_name=app_name,
    )
    return runner, session_id


# ---------------------------------------------------------------------------
# Glossary Generation
# ---------------------------------------------------------------------------

async def generate_glossary_adk(
    text_lines: List[str],
    show_name: str,
    target_language: str,
    existing_glossary: Optional[Dict] = None,
    model_name: str = "gemini-flash-latest",
    enable_research: bool = False
) -> Tuple[Dict, Dict]:
    """
    Generate or enhance glossary using ADK agents.

    Returns:
        Tuple of (glossary_dict, debug_info)
    """
    # Prepare text sample
    text_sample = "\n".join(text_lines[:500]) if text_lines else ""
    existing_terms = [
        t.get("term", "")
        for t in (existing_glossary or {}).get("terms", [])
    ]

    # Build prompt
    prompt = f"""Show: {show_name}
Target Language: {target_language}

{"Existing terms (DO NOT duplicate): " + ", ".join(existing_terms) if existing_terms else "No existing terms."}

Analyze this subtitle text, extract glossary terms, and translate them to {target_language}:
    
{text_sample}"""

    # Create appropriate agent
    if enable_research and show_name:
        agent = create_glossary_orchestrator(
            model_name=model_name,
            enable_research=True,
            target_language=target_language,
        )
    else:
        agent = create_cartographer_agent(
            model_name=model_name,
            target_language=target_language,
        )

    runner, session_id = await _create_session_and_runner(agent, "glossary")

    try:
        response_text = await _collect_response_text(runner, session_id, prompt)

        # Parse the structured JSON response
        glossary_dict = _parse_glossary_from_text(response_text)

        debug_info = {
            "prompt": prompt,
            "response": response_text,
            "model": model_name,
        }
        return glossary_dict, debug_info

    except Exception as e:
        print(f"DEBUG: generate_glossary_adk failed: {e}")
        return {"terms": []}, {"error": str(e), "prompt": prompt}


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------

async def research_project_adk(
    show_name: str,
    text_sample: List[str],
    target_language: str,
    model_name: str = "gemini-flash-latest"
) -> Tuple[Dict, Dict]:
    """
    Perform web research for a project using ADK ResearchAgent.

    Returns:
        Tuple of (research_data, debug_info)
    """
    text_preview = "\n".join(text_sample[:100]) if text_sample else "No text available"

    prompt = f"""Research the following show for translation purposes:

Show Name: {show_name}
Target Language: {target_language}

Use Google Search to find:
1. Official character names and their {target_language} translations (if available)
2. Location names and their significance
3. Cultural context and key terminology
4. Official romanizations or naming conventions
5. Overall tone and style of the show

Context from subtitles:
{text_preview}

Structure your findings clearly under these headings:
## Characters
## Locations
## Key Terms & Concepts
## Cultural Notes
## Tone & Style"""

    agent = create_research_agent(model_name=model_name)
    runner, session_id = await _create_session_and_runner(agent, "research")

    try:
        response_text = await _collect_response_text(runner, session_id, prompt)

        # Support XML tag extraction for research findings
        findings_match = re.search(r'<research_findings>(.*?)</research_findings>', response_text, re.DOTALL)
        findings = findings_match.group(1).strip() if findings_match else response_text

        research_data = {
            "findings": findings,
            "show_name": show_name,
            "target_language": target_language,
        }
        debug_info = {
            "prompt": prompt,
            "response": response_text,
            "model": model_name,
        }
        return research_data, debug_info

    except Exception as e:
        print(f"DEBUG: research_project_adk failed: {e}")
        return {"findings": "", "error": str(e)}, {"error": str(e), "prompt": prompt}


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------

async def translate_batch_adk(
    text_lines: List[str],
    glossary: Dict,
    target_language: str,
    context_guide: str = "",
    model_name: str = "gemini-flash-latest"
) -> Tuple[List[str], Dict]:
    """
    Translate batch of subtitle lines using ADK TranslatorAgent.

    Returns:
        Tuple of (translated_lines, debug_info)
    """
    # Flatten multi-line subtitles: replace \n with <br> so each entry stays on one line
    numbered_lines = "\n".join([f"{i+1}: {line.replace(chr(10), '<br>')}" for i, line in enumerate(text_lines)])

    prompt = f"""Translate these subtitles:

{numbered_lines}

{"Additional Context: " + context_guide if context_guide else ""}"""

    agent = create_translator_agent(
        model_name=model_name,
        glossary=glossary,
        target_language=target_language,
        context_guide=context_guide,
    )
    runner, session_id = await _create_session_and_runner(agent, "translate")

    try:
        response_text = await _collect_response_text(runner, session_id, prompt)

        translated_lines = _parse_numbered_output(response_text, len(text_lines))

        debug_info = {
            "prompt": prompt,
            "response": response_text,
            "model": model_name,
        }
        return translated_lines, debug_info

    except Exception as e:
        print(f"DEBUG: translate_batch_adk failed: {e}")
        return text_lines, {"error": str(e), "prompt": prompt}


# ---------------------------------------------------------------------------
# Context Guide Enhancement
# ---------------------------------------------------------------------------

async def enhance_context_guide_adk(
    research_data: str,
    show_name: str,
    model_name: str = "gemini-flash-latest"
) -> Tuple[str, Dict]:
    """
    Transform research findings into translation instructions.

    Returns:
        Tuple of (enhanced_guide, debug_info)
    """
    prompt = f"""Create detailed translation instructions for "{show_name}".

Research Data:
{research_data}

Generate a comprehensive guide covering:
1. Tone and formality level
2. Cultural adaptation notes
3. Terminology consistency rules
4. Special handling instructions

Do NOT provide specific examples of translations of names or terms, as there will be a glossary to handle that.

The instructions should provide a solid foundation for translators to work from, and should result in high quality, consistent translations.
Output as clear, actionable instructions for translators."""

    # Dedicated agent for context generation (not reusing ResearchAgent)
    agent = Agent(
        name="ContextGuideAgent",
        model=create_model(model_name),
        instruction="""You are a translation style guide expert. 
Your job is to create clear, actionable translation instructions 
based on research about a show, movie, or media property. 
Focus on tone, style, formality, and cultural adaptation — 
NOT specific term translations (those are handled by the glossary).""",
        tools=[],
        output_key="context_guide_result",
    )

    runner, session_id = await _create_session_and_runner(agent, "context")

    try:
        response_text = await _collect_response_text(runner, session_id, prompt)

        if not response_text:
            print(f"DEBUG: Context Agent returned empty response for '{show_name}'")

        debug_info = {
            "prompt": prompt,
            "response": response_text,
            "model": model_name,
        }
        return response_text, debug_info

    except Exception as e:
        print(f"DEBUG: enhance_context_guide_adk failed: {e}")
        return "", {"prompt": prompt, "error": str(e)}


# ---------------------------------------------------------------------------
# Parsing Helpers
# ---------------------------------------------------------------------------

def _sanitize_json(text: str) -> str:
    """Pre-clean common LLM JSON output errors before parsing.
    
    Handles:
    - Missing opening quote on keys: /key": → "key":
    - Trailing commas before } or ]
    - JS-style // comments
    - Mixed quote styles
    """
    # Fix common mistake: missing opening quote for JSON keys (e.g., `ingender":` or `/gender":`)
    # This catches cases where a model accidentally omits the leading quote.
    text = re.sub(r'(?<![\w"])([a-zA-Z0-9_]+)":', r'"\1":', text)
    
    # Still replace trailing slashes/dashes directly if they got attached (e.g. `"/gender":` -> `"gender":`)
    text = re.sub(r'"[/_\-\u2014]+(\w+)":', r'"\1":', text)
    
    # Remove JS-style single-line comments
    text = re.sub(r'//[^\n]*', '', text)
    
    # Remove trailing commas before closing braces/brackets
    text = re.sub(r',\s*([\}\]])', r'\1', text)
    
    return text

def _parse_glossary_from_text(text: str) -> Dict:
    """Parse glossary from AI response text using shared utility."""
    return parse_glossary_from_text(text)


def _parse_numbered_output(text: str, expected_count: int) -> List[str]:
    """Parse numbered translation output, supporting multi-line entries.
    
    Handles formats like:
    - "1: Translated text"
    - "1. Translated text"
    - "1: Line one\n   Line two" (continuation lines)
    """
    lines_map: Dict[int, str] = {}
    current_idx: Optional[int] = None

    # Strip reasoning blocks before parsing (handles Qwen/DeepSeek think tags)
    text = strip_reasoning_blocks(text)

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Match "N: text" or "N. text" where N is a number
        match = re.match(r'^(\d+)\s*[:.]\s*(.*)', stripped)
        if match:
            current_idx = int(match.group(1))
            content = match.group(2).strip()
            if 1 <= current_idx <= expected_count:
                lines_map[current_idx] = content
        elif current_idx is not None and current_idx in lines_map:
            # continuation line for the current index
            lines_map[current_idx] += "\n" + stripped

    # Build ordered result, restoring <br> placeholders to real newlines
    result = []
    for i in range(1, expected_count + 1):
        text = lines_map.get(i, "")
        text = text.replace("<br>", "\n")
        result.append(text)

    return result
