"""
Character Profile Agent — Analyzes subtitles to generate character voice profiles.

Given a sample of subtitle text and a glossary of character names, determines
each character's gender, formality level, speech patterns, and verbal tics.

This information is critical for Greek (and other gendered languages) where
adjectives, articles, and verb forms differ by speaker gender.
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, Optional


def create_profile_agent(
    model_name: str = "gemini-flash-lite-latest",
    target_language: str = "Greek",
    temperature: Optional[float] = None,
) -> Agent:
    """Create an agent that analyzes subtitle text for character voice profiles.

    The agent receives subtitle text and a list of character names, then
    outputs a JSON array of character profiles with gender, formality,
    and speech pattern information.
    """
    return Agent(
        name="CharacterProfileAgent",
        model=create_model(
            model_name,
            temperature=temperature if temperature is not None else 0.2,
        ),
        instruction=f"""You are analyzing subtitle dialogue to build character profiles for {target_language} translation.

For each character who has dialogue in the text, determine:
- gender: "masculine", "feminine", "neuter", or "unknown"
- formality: "formal", "informal", or "mixed" (how they speak to others)
- speech_patterns: brief description of their speaking style (max 100 chars)
- verbal_tics: list of catchphrases or repeated expressions (max 5)

Output ONLY a JSON array — no commentary, no markdown fencing:
[
  {{"name": "CharacterName", "gender": "feminine", "formality": "informal", "speech_patterns": "calm, understated, dry humor", "verbal_tics": ["That takes me back"]}},
  ...
]

Rules:
- Only include characters with enough dialogue to assess (at least 3 lines).
- Determine gender from context clues: pronouns used about them, gendered adjectives in source text, character descriptions.
- For anime characters, consider Japanese naming conventions and honorific usage as gender clues.
- Skip characters who are only mentioned but never speak.
- If gender cannot be determined, use "unknown" — do NOT guess randomly.""",
        tools=[],
        output_key="profile_result",
    )


def build_profile_prompt(
    subtitle_text: str,
    character_names: list,
    show_name: str = "",
) -> str:
    """Build a prompt for the profile agent.

    Args:
        subtitle_text: Sample subtitle lines (ideally from first 1-2 episodes).
        character_names: Known character names from glossary.
        show_name: Name of the show/movie for context.
    """
    names_section = ""
    if character_names:
        names_section = f"\nKnown characters: {', '.join(character_names)}\n"

    show_section = f"\nShow: {show_name}\n" if show_name else ""

    return f"""Analyze these subtitles and generate character voice profiles.
{show_section}{names_section}
Subtitle text:

{subtitle_text}"""
