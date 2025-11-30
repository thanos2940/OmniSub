"""
SRT Processing Tools for ADK Agents

These tools wrap your existing SRT parser into ADK FunctionTools.
"""

from typing import List, Dict
from google.adk.tools import FunctionTool

# Import your existing parser
import sys
from pathlib import Path
# Add backend root to path to import utils
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from utils.srt_parser import parse_srt, extract_text_only

def parse_srt_file(content: str) -> dict:
    """
    Parse an SRT subtitle file into structured data.
    
    Args:
        content: Raw SRT file content as string
        
    Returns:
        Dictionary with:
        - status: "success" or "error"
        - data: List of subtitle entries (if success)
        - error_message: Error description (if error)
        
    Example:
        >>> result = parse_srt_file(srt_content)
        >>> if result["status"] == "success":
        ...     for entry in result["data"]:
        ...         print(entry["text"])
    """
    try:
        data = parse_srt(content)
        if not data:
            return {
                "status": "error",
                "error_message": "Invalid SRT format - no entries found"
            }
        return {
            "status": "success",
            "data": data
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": str(e)
        }


def extract_subtitle_text(subtitle_data: List[Dict]) -> dict:
    """
    Extract plain text from parsed subtitle data.
    
    Args:
        subtitle_data: List of subtitle entries from parse_srt_file
        
    Returns:
        Dictionary with:
        - status: "success" or "error"
        - text_lines: List of text strings (if success)
        - count: Number of lines extracted
    """
    try:
        lines = extract_text_only(subtitle_data)
        return {
            "status": "success",
            "text_lines": lines,
            "count": len(lines)
        }
    except Exception as e:
        return {
            "status": "error",
            "error_message": str(e)
        }


# Create ADK FunctionTools (these get passed to agents)
parse_srt_tool = FunctionTool(parse_srt_file)
extract_text_tool = FunctionTool(extract_subtitle_text)
