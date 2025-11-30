"""
SRT (SubRip Subtitle) File Parser and Reconstructor

This module provides utilities for parsing, extracting, and reconstructing SRT subtitle files.
It handles the conversion between raw SRT format and structured data suitable for AI processing
and translation workflows.

SRT Format Structure:
    1
    00:00:01,000 --> 00:00:04,000
    First subtitle line
    Second line if multi-line

    2
    00:00:05,000 --> 00:00:08,000
    Next subtitle

Key Features:
    - Parse SRT files into structured dictionaries
    - Extract text for AI processing
    - Reconstruct SRT files from structured data
    - Support for multi-line subtitles
    - Cross-platform newline handling
"""

from typing import List, Dict


def parse_srt(content: str) -> List[Dict]:
    """
    Parse SRT subtitle content into a structured list of subtitle entries.
    
    This function takes raw SRT file content and converts it into a list of dictionaries,
    where each dictionary represents a subtitle entry with its metadata and text content.
    
    Args:
        content: Raw SRT file content as a string. Can contain Windows (CRLF), 
                Unix (LF), or Mac (CR) line endings.
    
    Returns:
        List of dictionaries, each containing:
            - id (str): Subtitle entry number
            - timecode (str): Timestamp in format "HH:MM:SS,MMM --> HH:MM:SS,MMM"
            - original (str): Original subtitle text (may contain newlines for multi-line entries)
            - translated (str): Placeholder for translated text (empty string)
            - is_edited (bool): Flag indicating if entry was manually edited
            - needs_review (bool): Flag indicating if entry requires human review
    
    Example:
        >>> content = "1\\n00:00:01,000 --> 00:00:04,000\\nHello World\\n\\n2\\n00:00:05,000 --> 00:00:08,000\\nGoodbye"
        >>> result = parse_srt(content)
        >>> len(result)
        2
        >>> result[0]['original']
        'Hello World'
    
    Note:
        - Entries with fewer than 3 lines (id, timecode, text) are silently skipped
        - Multi-line subtitle text is preserved with newline characters
    """
    if not content:
        return []
    
    # Normalize all types of newlines to Unix-style (LF) for consistent processing
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    
    # Split by double newlines to separate individual subtitle blocks
    blocks = content.strip().split('\n\n')
    
    parsed_data = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        
        # Valid SRT entry must have at minimum: ID, timecode, and text (3 lines)
        if len(lines) >= 3:
            # First line: Entry ID (sequential number)
            entry_id = lines[0].strip()
            
            # Second line: Timecode (start --> end)
            timecode = lines[1].strip()
            
            # Remaining lines: Subtitle text (may be multi-line)
            text = "\n".join(lines[2:]).strip()
            
            parsed_data.append({
                "id": entry_id,
                "timecode": timecode,
                "original": text,
                "translated": "",  # Populated during translation
                "is_edited": False,  # Set to True if manually edited
                "needs_review": False  # Set to True if AI flags for human review
            })
    
    return parsed_data


def extract_text_only(parsed_data: List[Dict]) -> List[str]:
    """
    Extract only the original text content from parsed subtitle data.
    
    This is a utility function for preparing subtitle text for AI processing,
    stripping away metadata like IDs and timecodes.
    
    Args:
        parsed_data: List of subtitle entry dictionaries (output from parse_srt)
    
    Returns:
        List of original text strings in the same order as the input
    
    Example:
        >>> data = [{"original": "Hello"}, {"original": "World"}]
        >>> extract_text_only(data)
        ['Hello', 'World']
    """
    return [entry["original"] for entry in parsed_data]


def reconstruct_srt(parsed_data: List[Dict]) -> str:
    """
    Reconstruct SRT file content from parsed subtitle data.
    
    This function converts structured subtitle data back into standard SRT format.
    It prioritizes translated text over original text when available.
    
    Args:
        parsed_data: List of subtitle entry dictionaries (output from parse_srt)
    
    Returns:
        Complete SRT file content as a string with proper formatting
    
    Example:
        >>> data = [
        ...     {"id": "1", "timecode": "00:00:01,000 --> 00:00:04,000", 
        ...      "original": "Hello", "translated": "Hola"}
        ... ]
        >>> print(reconstruct_srt(data))
        1
        00:00:01,000 --> 00:00:04,000
        Hola
    
    Note:
        - Uses translated text if available, otherwise falls back to original
        - Entries are separated by double newlines per SRT standard
        - Preserves multi-line subtitle text
    """
    output = []
    
    for entry in parsed_data:
        # Prefer translated text if available, otherwise use original
        text_to_use = entry["translated"] if entry["translated"] else entry["original"]
        
        # Format: ID, timecode, text (SRT standard format)
        output.append(f"{entry['id']}\n{entry['timecode']}\n{text_to_use}")
    
    # Join entries with double newlines (SRT block separator)
    return "\n\n".join(output)
