"""
SRT Subtitle File Parser and Reconstructor

Provides utilities for parsing, extracting, and reconstructing SRT subtitle files.
Converts between raw SRT format and structured data for AI processing.
"""

from typing import List, Dict


def parse_srt(content: str) -> List[Dict]:
    """
    Parse SRT subtitle content into structured entries.
    
    Args:
        content: Raw SRT file content (supports Windows/Unix/Mac line endings)
    
    Returns:
        List of dictionaries with id, timecode, original, translated, and flags
    """
    if not content:
        return []
    
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    blocks = content.strip().split('\n\n')
    
    parsed_data = []
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            parsed_data.append({
                "id": lines[0].strip(),
                "timecode": lines[1].strip(),
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
    return [entry["original"] for entry in parsed_data]


def reconstruct_srt(parsed_data: List[Dict]) -> str:
    """
    Reconstruct SRT content from parsed data.
    
    Prioritizes translated text over original when available.
    
    Args:
        parsed_data: List of subtitle entries
    
    Returns:
        Complete SRT file content
    """
    output = []
    for entry in parsed_data:
        text = entry["translated"] if entry["translated"] else entry["original"]
        output.append(f"{entry['id']}\n{entry['timecode']}\n{text}")
    return "\n\n".join(output)
