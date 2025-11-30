"""
ADK Agent Operations - High-level Functions for OmbiSub

Provides ADK-based implementations of all agent operations, replacing legacy code.
Uses ADK Runner for proper session management and observability.
"""

import json
import asyncio
from uuid import uuid4
from datetime import datetime
from typing import List, Dict, Tuple, Optional

from google.adk.runners import Runner, types as adk_types
from .cartographer_agent import create_cartographer_agent, GlossaryOutput
from .research_agent import create_research_agent
from .translator_agent import create_translator_agent
from .glossary_orchestrator import create_glossary_orchestrator
from adk_config.session_service import get_session_service


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

    Args:
        text_lines: Subtitle text lines to analyze
        show_name: Name of the show/project
        target_language: Target translation language
        existing_glossary: Existing glossary to avoid duplicates
        model_name: Gemini model to use
        enable_research: Whether to use web research

    Returns:
        Tuple of (glossary_dict, debug_info)
    """
    # Prepare text sample
    text_sample = "\n".join(text_lines[:500]) if text_lines else ""
    existing_terms = [t.get("term", "") for t in existing_glossary.get("terms", [])] if existing_glossary else []

    # Build prompt
    prompt = f"""Show: {show_name}
Target Language: {target_language}

{"Existing terms (avoid duplicates): " + ", ".join(existing_terms) if existing_terms else "No existing terms."}

Analyze this subtitle text, extract glossary terms, and translate them to {target_language}:
    
{text_sample}"""

    # Create appropriate agent (with or without research)
    if enable_research and show_name:
        agent = create_glossary_orchestrator(model_name=model_name, enable_research=True, target_language=target_language)
    else:
        agent = create_cartographer_agent(model_name=model_name, target_language=target_language)

    # Run agent with unique session ID
    session_id = f"glossary_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    session_service = get_session_service()
    runner = Runner(agent=agent, app_name=f"OmbiSub_{session_id}", session_service=session_service)

    try:
        # Create session explicitly
        await session_service.create_session(session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}")
        
        response_text = ""
        async for event in runner.run_async(
            user_id="default_user",
            session_id=session_id,
            new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        class MockResponse:
            def __init__(self, text):
                self.text = text
        response = MockResponse(response_text)

        # Extract structured output
        if hasattr(response, 'glossary_result'):
            glossary_output: GlossaryOutput = response.glossary_result
            glossary_dict = {
                "terms": [
                    {
                        "term": term.term,
                        "translation": term.translation,
                        "description": term.context,
                        "category": term.category,
                        "case_sensitive": term.case_sensitive,
                        "gender": "neuter"  # Default, can be enhanced
                    }
                    for term in glossary_output.terms
                ]
            }
        else:
            # Fallback: parse text response
            glossary_dict = _parse_glossary_from_text(response.text)

        # Debug info
        debug_info = {
            "prompt": prompt,
            "response": response.text if hasattr(response, 'text') else str(response),
            "model": model_name
        }

        return glossary_dict, debug_info

    except Exception as e:
        # Return empty glossary on error
        print(f"DEBUG: generate_glossary_adk failed: {str(e)}")
        return {"terms": []}, {"error": str(e), "prompt": prompt}


async def research_project_adk(
    show_name: str,
    text_sample: List[str],
    target_language: str,
    model_name: str = "gemini-flash-latest"
) -> Tuple[Dict, Dict]:
    """
    Perform web research for a project using ADK ResearchAgent.

    Args:
        show_name: Name of the show to research
        text_sample: Sample subtitle lines for context
        target_language: Target language
        model_name: Gemini model to use

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
5. Overall tone and style of the show.

Context from subtitles:
{text_preview}

Provide findings in a clear, structured format."""

    agent = create_research_agent(model_name=model_name)
    session_id = f"research_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    session_service = get_session_service()
    runner = Runner(agent=agent, app_name=f"OmbiSub_{session_id}", session_service=session_service)

    try:
        # Create session explicitly
        await session_service.create_session(session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}")

        response_text = ""
        async for event in runner.run_async(
            user_id="default_user",
            session_id=session_id,
            new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        class MockResponse:
            def __init__(self, text):
                self.text = text
        response = MockResponse(response_text)

        research_text = response_text

        # Parse research findings into structured format
        research_data = {
            "findings": research_text,
            "show_name": show_name,
            "target_language": target_language
        }

        debug_info = {
            "prompt": prompt,
            "response": research_text,
            "model": model_name
        }

        return research_data, debug_info

    except Exception as e:
        print(f"DEBUG: research_project_adk failed: {str(e)}")
        return {"findings": "", "error": str(e)}, {"error": str(e), "prompt": prompt}


async def translate_batch_adk(
    text_lines: List[str],
    glossary: Dict,
    target_language: str,
    context_guide: str = "",
    model_name: str = "gemini-flash-latest"
) -> Tuple[List[str], Dict]:
    """
    Translate batch of subtitle lines using ADK TranslatorAgent.

    Args:
        text_lines: Lines to translate
        glossary: Project glossary
        target_language: Target language
        context_guide: Additional context/tone guidelines
        model_name: Gemini model to use

    Returns:
        Tuple of (translated_lines, debug_info)
    """
    # Format input as numbered lines
    numbered_lines = "\n".join([f"{i+1}: {line}" for i, line in enumerate(text_lines)])

    prompt = f"""Translate these subtitles:

{numbered_lines}

{f"Additional Context: {context_guide}" if context_guide else ""}"""

    agent = create_translator_agent(
        model_name=model_name,
        glossary=glossary,
        target_language=target_language
    )

    session_id = f"translate_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    session_service = get_session_service()
    runner = Runner(agent=agent, app_name=f"OmbiSub_{session_id}", session_service=session_service)

    try:
        # Create session explicitly
        await session_service.create_session(session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}")

        response_text = ""
        async for event in runner.run_async(
            user_id="default_user",
            session_id=session_id,
            new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        class MockResponse:
            def __init__(self, text):
                self.text = text
        response = MockResponse(response_text)

        # Parse numbered output
        translated_lines = _parse_numbered_output(response_text, len(text_lines))

        debug_info = {
            "prompt": prompt,
            "response": response_text,
            "model": model_name
        }

        return translated_lines, debug_info

    except Exception as e:
        # Return original lines on error
        print(f"DEBUG: translate_batch_adk failed: {str(e)}")
        return text_lines, {"error": str(e), "prompt": prompt}


def _parse_glossary_from_text(text: str) -> Dict:
    """Parse glossary from unstructured text response (fallback)."""
    try:
        # Try to find JSON block
        if "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            json_str = text[start:end]
            return json.loads(json_str)
    except:
        pass

    # Return empty glossary if parsing fails
    return {"terms": []}


async def enhance_context_guide_adk(
    research_data: str,
    show_name: str,
    model_name: str = "gemini-flash-latest"
) -> Tuple[str, Dict]:
    """
    Transform research findings into translation instructions.

    Args:
        research_data: Research findings or existing context guide
        show_name: Name of the show
        model_name: Gemini model to use

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

    # Use a simple agent for text generation
    agent = create_research_agent(model_name=model_name)
    session_id = f"context_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    session_service = get_session_service()
    runner = Runner(agent=agent, app_name=f"OmbiSub_{session_id}", session_service=session_service)

    try:
        # Create session explicitly
        await session_service.create_session(session_id=session_id, user_id="default_user", app_name=f"OmbiSub_{session_id}")

        response_text = ""
        async for event in runner.run_async(
            user_id="default_user",
            session_id=session_id,
            new_message=adk_types.Content(role="user", parts=[adk_types.Part(text=prompt)])
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_text += part.text

        class MockResponse:
            def __init__(self, text):
                self.text = text
        response = MockResponse(response_text)
        
        print(f"DEBUG: Context Agent Response Length: {len(response_text)}")
        if not response_text:
            print("DEBUG: Context Agent returned empty response!")
            print(f"DEBUG: Prompt was: {prompt[:200]}...")

        debug_info = {
            "prompt": prompt,
            "response": response_text,
            "model": model_name
        }

        return response_text, debug_info

    except Exception as e:
        print(f"DEBUG: enhance_context_guide_adk failed: {str(e)}")
        return "", {"prompt": prompt, "error": str(e)}


def _parse_numbered_output(text: str, expected_count: int) -> List[str]:
    """Parse numbered translation output."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Match "N: text" format
        if ":" in line and line[0].isdigit():
            parts = line.split(":", 1)
            if len(parts) == 2:
                lines.append(parts[1].strip())

    # Ensure we have the right count
    while len(lines) < expected_count:
        lines.append("")

    return lines[:expected_count]
