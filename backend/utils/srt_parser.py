"""
SRT Subtitle File Parser and Reconstructor

Provides utilities for parsing, extracting, and reconstructing SRT subtitle files.
Converts between raw SRT format and structured data for AI processing.
"""

import re
from typing import List, Dict

# Regex to validate SRT timecode format: HH:MM:SS,MMM --> HH:MM:SS,MMM
TIMECODE_RE = re.compile(
    r'\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}'
)


def parse_srt(content: str) -> List[Dict]:
    """
    Parse SRT subtitle content into structured entries.
    
    Args:
        content: Raw SRT file content (supports Windows/Unix/Mac line endings and BOM)
    
    Returns:
        List of dictionaries with id, timecode, original, translated, and flags
    """
    if not content:
        return []
    
    # Strip UTF-8 BOM if present
    if content.startswith('\ufeff'):
        content = content[1:]
    
    # Normalize line endings
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    blocks = content.strip().split('\n\n')
    
    parsed_data = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        # Validate: first line should be a number, second should be a timecode
        index_line = lines[0].strip()
        timecode_line = lines[1].strip()
        
        if not index_line.isdigit():
            continue
        if not TIMECODE_RE.match(timecode_line):
            continue
        
        parsed_data.append({
            "id": index_line,
            "timecode": timecode_line,
            "original": "\n".join(lines[2:]).strip(),
            "translated": "",
            "is_edited": False,
            "needs_review": False
        })
    
    return parsed_data


def extract_text_only(parsed_data: List[Dict]) -> List[str]:
    """
    Extract original text from parsed subtitle data.
    
    Args:
        parsed_data: List of subtitle entries from parse_srt
    
    Returns:
        List of original text strings
    """
    return [entry.get("original", "") for entry in parsed_data]


def reconstruct_srt(parsed_data: List[Dict], allow_source_fallback: bool = False) -> str:
    """
    Reconstruct SRT content from parsed data.
    
    Prioritizes translated text over original. Does NOT silently fall back to English
    source text for untranslated cues when exporting translated subtitles.
    
    Args:
        parsed_data: List of subtitle entries
        allow_source_fallback: If True, falls back to original text when translated is empty.
    
    Returns:
        Complete SRT file content (with trailing newline for compatibility)
    """
    has_any_translation = any(bool(entry.get("translated") or (entry.get("translations") and any(entry["translations"].values()))) for entry in parsed_data)
    has_review_flags = any(bool(entry.get("needs_review") or entry.get("review_issues")) for entry in parsed_data)
    is_pure_source = not has_any_translation and not has_review_flags and not any("translations" in entry for entry in parsed_data)

    output = []
    for entry in parsed_data:
        if allow_source_fallback:
            text = entry.get("translated") or entry.get("original", "")
        elif has_any_translation or any("translations" in e for e in parsed_data) or has_review_flags:
            # Translated subtitle dataset: never silently export English source text for untranslated cues
            text = entry.get("translated") or ""
        elif is_pure_source:
            # Pure source subtitle dataset (e.g. saving reconstructed original srt)
            text = entry.get("original", "")
        else:
            text = entry.get("translated") or ""
        output.append(f"{entry['id']}\n{entry['timecode']}\n{text}")
    
    # SRT files should end with a blank line for compatibility
    return "\n\n".join(output) + "\n"



def timecode_to_ms(timecode: str) -> int:
    """Convert SRT timecode HH:MM:SS,MMM to milliseconds."""
    parts = timecode.replace(',', ':').split(':')
    if len(parts) != 4:
        return 0
    h, m, s, ms = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    return (h * 3600 + m * 60 + s) * 1000 + ms


def build_scene_ast(
    parsed_data: List[Dict],
    gap_threshold_ms: int = 3000,
    max_lines_per_scene: int = 250,
    min_lines_per_scene: int = 10,
) -> List[Dict]:
    """Group flat SRT entries into semantic Scene chunks.

    A new scene starts when:
    - The gap to the next subtitle exceeds gap_threshold_ms, OR
    - The current scene has reached max_lines_per_scene.

    Micro-scene merging: scenes with fewer than min_lines_per_scene lines are
    merged into their neighbour (prefer merging forward, else backward) to
    avoid wasting per-request overhead on tiny chunks.
    """
    if not parsed_data:
        return []

    # --- Pass 1: split by temporal gaps and size cap ---
    raw_scenes: List[List[Dict]] = []
    current: List[Dict] = []

    for entry in parsed_data:
        tc = entry.get("timecode", "")
        tc_parts = tc.split("-->")
        start_ms = end_ms = 0
        if len(tc_parts) == 2:
            start_ms = timecode_to_ms(tc_parts[0].strip())
            end_ms = timecode_to_ms(tc_parts[1].strip())
        entry["_start_ms"] = start_ms
        entry["_end_ms"] = end_ms

        if current:
            gap = start_ms - current[-1].get("_end_ms", 0)
            if gap > gap_threshold_ms or len(current) >= max_lines_per_scene:
                raw_scenes.append(current)
                current = []

        current.append(entry)

    if current:
        raw_scenes.append(current)

    # --- Pass 2: merge micro-scenes ---
    merged: List[List[Dict]] = []
    for chunk in raw_scenes:
        if merged and len(chunk) < min_lines_per_scene:
            # Absorb into the previous scene if it won't exceed the cap
            if len(merged[-1]) + len(chunk) <= max_lines_per_scene:
                merged[-1].extend(chunk)
                continue
        merged.append(chunk)

    # --- Pass 3: renumber and compute index offsets ---
    scenes: List[Dict] = []
    offset = 0
    for scene_id, lines in enumerate(merged, start=1):
        scenes.append({
            "scene_id": scene_id,
            "start_index": offset,
            "end_index": offset + len(lines),
            "lines": lines,
        })
        offset += len(lines)

    return scenes


def get_scene_context_hint(scene_lines: List[Dict]) -> str:
    """Return the last subtitle text from a scene as a one-line context hint.

    Used to give the translator minimal context about what came just before
    the current scene, without sending full overlap lines that waste tokens.
    """
    for entry in reversed(scene_lines):
        text = entry.get("translated") or entry.get("original", "")
        if text:
            # Collapse multi-line subtitles into a single line
            return text.replace("\n", " ").strip()
    return ""


def get_scene_lookahead_hint(scene_lines: List[Dict], k: int = 2) -> str:
    """Return the first k original lines of the next scene as a lookahead hint.

    Used to help the model make grammatically correct decisions at scene boundaries.
    """
    if not scene_lines or k <= 0:
        return ""
    parts = []
    for entry in scene_lines[:k]:
        txt = entry.get("original", "").replace("\n", " ").strip()
        if txt:
            parts.append(txt)
    if not parts:
        return ""
    return " | ".join(parts)

