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
from .cartographer_agent import create_cartographer_agent, GlossaryOutput
from .research_agent import create_research_agent
from .translator_agent import create_translator_agent
from .glossary_orchestrator import create_glossary_orchestrator
from adk_config.session_service import get_session_service


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

        research_data = {
            "findings": response_text,
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
    # Fix common mistake: /key": value → "key": value
    # This catches cases where a model accidentally types /gender", _gender", or similar
    text = re.sub(r'(?<!")\s*[/_\-\u2014]\s*(\w+)":', r' "\1":', text)
    
    # Remove JS-style single-line comments
    text = re.sub(r'//[^\n]*', '', text)
    
    # Remove trailing commas before closing braces/brackets
    text = re.sub(r',\s*([\}\]])', r'\1', text)
    
    return text


def _strip_reasoning_blocks(text: str) -> str:
    """Strip internal reasoning blocks that local thinking models emit.

    Handles formats produced by Qwen, DeepSeek-R1, Gemma-thinking, etc.:
    - <think>...</think>
    - <thinking>...</thinking>
    - [REASONING]...[/REASONING]
    - "Thinking Process:\\n..." markdown header blocks
    - "reasoning_content" JSON leakage (usually separate, but guard anyway)
    """
    if not text:
        return text

    # XML-style tags (greedy across newlines)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[REASONING\].*?\[/REASONING\]', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Markdown "Thinking Process:" header block — strip until the next heading or blank line run
    text = re.sub(
        r'(?:^|\n)(?:Thinking Process|Internal Reasoning|Chain of Thought)\s*:.*?(?=\n(?:\d+[:.])|\Z)',
        '', text, flags=re.DOTALL | re.IGNORECASE
    )

    # Strip incomplete opening tags (model was cut off mid-think)
    text = re.sub(r'<think[^>]*>.*', '', text, flags=re.DOTALL | re.IGNORECASE)

    return text.strip()


def _parse_glossary_from_text(text: str) -> Dict:
    """Parse glossary from AI response text.
    
    Handles multiple formats:
    - JSON wrapped in markdown code blocks
    - Raw JSON objects
    - JSON arrays of terms
    - Slightly malformed JSON from local LLMs (auto-sanitized)
    """
    if not text or not text.strip():
        return {"terms": []}

    # Strip reasoning blocks first (Qwen, DeepSeek-R1, Gemma-thinking)
    text = _strip_reasoning_blocks(text)

    # Strip markdown code blocks
    clean = text.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    # Try 1: Direct JSON parse
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict) and "terms" in parsed:
            return parsed
        if isinstance(parsed, list):
            return {"terms": parsed}
        return {"terms": [parsed] if "term" in parsed else []}
    except json.JSONDecodeError:
        pass

    # Try 2: Sanitize and re-parse (fixes common local LLM typos)
    sanitized = _sanitize_json(clean)
    try:
        parsed = json.loads(sanitized)
        if isinstance(parsed, dict) and "terms" in parsed:
            return parsed
        if isinstance(parsed, list):
            return {"terms": parsed}
        return {"terms": [parsed] if "term" in parsed else []}
    except json.JSONDecodeError:
        pass

    # Try 3: Find a JSON object containing "terms"
    terms_match = re.search(r'\{"terms"\s*:\s*\[.*?\]\s*\}', sanitized, re.DOTALL)
    if terms_match:
        try:
            return json.loads(terms_match.group(0))
        except json.JSONDecodeError:
            pass

    # Try 4: Find any JSON array
    array_match = re.search(r'\[.*\]', sanitized, re.DOTALL)
    if array_match:
        try:
            parsed = json.loads(array_match.group(0))
            if isinstance(parsed, list):
                return {"terms": parsed}
        except json.JSONDecodeError:
            pass

    # Try 5: Parse individual term objects (extremely lenient fallback)
    # Extracts each {...} block and tries to parse them one by one
    term_objects = re.findall(r'\{[^{}]+\}', sanitized, re.DOTALL)
    valid_terms = []
    for obj_str in term_objects:
        try:
            obj = json.loads(_sanitize_json(obj_str))
            if "term" in obj and "translation" in obj:
                valid_terms.append(obj)
        except json.JSONDecodeError:
            continue
    if valid_terms:
        print(f"DEBUG: Recovered {len(valid_terms)} terms via object-by-object fallback")
        return {"terms": valid_terms}

    print(f"DEBUG: Failed to parse glossary JSON. Response preview: {text[:200]}")
    return {"terms": []}


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
    text = _strip_reasoning_blocks(text)

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
