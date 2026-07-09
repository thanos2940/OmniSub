"""
SRT Processing Tools for ADK Agents

Wraps SRT parser functions as ADK FunctionTools for agent use.
"""

from typing import List, Dict
from google.adk.tools import FunctionTool


def parse_srt_content(content: str) -> dict:
    """
    Parse SRT subtitle content into structured data.
    
    Args:
        content: Raw SRT file content
        
    Returns:
        Dictionary with status, data (if success), or error_message
    """
    try:
        if not content:
            return {"status": "error", "error_message": "Empty content"}
        
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
        
        if not parsed_data:
            return {"status": "error", "error_message": "No valid entries found"}
        
        return {"status": "success", "data": parsed_data, "count": len(parsed_data)}
        
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def extract_text_from_srt(subtitle_data: List[Dict]) -> dict:
    """
    Extract plain text from parsed subtitle data.
    
    Args:
        subtitle_data: List of subtitle entries from parse_srt_content
        
    Returns:
        Dictionary with status, text_lines (if success), and count
    """
    try:
        lines = [entry.get("original", "") for entry in subtitle_data]
        return {"status": "success", "text_lines": lines, "count": len(lines)}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


parse_srt_tool = FunctionTool(parse_srt_content)
extract_text_tool = FunctionTool(extract_text_from_srt)
