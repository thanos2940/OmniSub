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


def reconstruct_srt(parsed_data: List[Dict]) -> str:
    """
    Reconstruct SRT content from parsed data.
    
    Prioritizes translated text over original when available.
    
    Args:
        parsed_data: List of subtitle entries
    
    Returns:
        Complete SRT file content (with trailing newline for compatibility)
    """
    output = []
    for entry in parsed_data:
        text = entry.get("translated") or entry.get("original", "")
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


def build_scene_ast(parsed_data: List[Dict], gap_threshold_ms: int = 3000, max_lines_per_scene: int = 250) -> List[Dict]:
    """
    Groups flat parsed SRT data into semantic Scene chunks.
    A new scene starts if the gap between the end of one line and the start
    of the next is greater than gap_threshold_ms, or if the current scene
    exceeds max_lines_per_scene.
    """
    if not parsed_data:
        return []

    scenes = []
    current_scene = {
        "scene_id": 1,
        "start_index": 0,
        "end_index": 0,
        "lines": []
    }
    
    for i, entry in enumerate(parsed_data):
        tc = entry.get("timecode", "")
        tc_parts = tc.split("-->")
        start_ms = 0
        end_ms = 0
        if len(tc_parts) == 2:
            start_ms = timecode_to_ms(tc_parts[0].strip())
            end_ms = timecode_to_ms(tc_parts[1].strip())
            
        entry["_start_ms"] = start_ms
        entry["_end_ms"] = end_ms

        if current_scene["lines"]:
            prev_entry = current_scene["lines"][-1]
            prev_end_ms = prev_entry.get("_end_ms", 0)
            
            gap = start_ms - prev_end_ms
            
            if gap > gap_threshold_ms or len(current_scene["lines"]) >= max_lines_per_scene:
                current_scene["end_index"] = i
                scenes.append(current_scene)
                current_scene = {
                    "scene_id": len(scenes) + 1,
                    "start_index": i,
                    "end_index": i,
                    "lines": []
                }
                
        current_scene["lines"].append(entry)

    if current_scene["lines"]:
        current_scene["end_index"] = len(parsed_data)
        scenes.append(current_scene)

    return scenes
