"""
LLM Utilities - Shared helpers for LLM response processing.
"""

import re
from typing import List, Dict

def strip_reasoning_blocks(text: str) -> str:
    """
    Strip internal reasoning blocks that local thinking models emit.

    Handles formats produced by Qwen, DeepSeek-R1, Gemma-thinking, etc.:
    - <think>...</think>
    - <thinking>...</thinking>
    - [REASONING]...[/REASONING]
    - "Thinking Process:\\n..." markdown header blocks
    """
    if not text:
        return text

    # XML-style tags (greedy across newlines)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<thinking>.*?</thinking>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<internal_reasoning>.*?</internal_reasoning>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<\|channel>thought.*?<channel\|>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'\[REASONING\].*?\[/REASONING\]', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Markdown "Thinking Process:" header block — strip until the next heading or a line starting with a number (subtitle marker)
    text = re.sub(
        r'(?:^|\n)(?:Thinking Process|Internal Reasoning|Chain of Thought)\s*:.*?(?=\n(?:\d+[:.|])|\Z)',
        '', text, flags=re.DOTALL | re.IGNORECASE
    )

    # Strip incomplete opening tags (model was cut off mid-think)
    text = re.sub(r'<think[^>]*>.*', '', text, flags=re.DOTALL | re.IGNORECASE)

    return text.strip()


def parse_glossary_from_text(text: str) -> dict:
    """
    Parse glossary from AI response text.
    
    Handles multiple formats:
    - XML Tag Parsing (The new standard: <glossary><term>...</term></glossary>)
    - JSON wrapped in markdown code blocks
    - Raw JSON objects (terms array or single object)
    """
    if not text or not text.strip():
        return {"terms": []}

    # Strip reasoning blocks first
    clean_text = strip_reasoning_blocks(text)

    # 1. Try XML Tag Parsing (New standard)
    if "<glossary>" in clean_text or "<term>" in clean_text:
        terms = []
        term_blocks = re.findall(r'<term>(.*?)</term>', clean_text, re.DOTALL)
        for block in term_blocks:
            term_data = {}
            # Standard tags
            for tag in ["source", "term", "translation", "description", "type", "gender", "case_sensitive", "keep_original"]:
                val_match = re.search(f'<{tag}>(.*?)</{tag}>', block, re.DOTALL)
                if val_match:
                    val = val_match.group(1).strip()
                    if tag in ["case_sensitive", "keep_original"]:
                        term_data[tag] = val.lower() == "true"
                    else:
                        term_data[tag] = val
            
            # Normalization
            if "source" in term_data and "term" not in term_data:
                term_data["term"] = term_data.pop("source")
            
            if "term" in term_data:
                terms.append(term_data)
        
        if terms:
            return {"terms": terms}

    # 2. Try JSON Parsing
    import json
    
    # Strip markdown code blocks
    json_candidate = clean_text.strip()
    if json_candidate.startswith("```json"):
        json_candidate = json_candidate[7:]
    elif json_candidate.startswith("```"):
        json_candidate = json_candidate[3:]
    if json_candidate.endswith("```"):
        json_candidate = json_candidate[:-3]
    json_candidate = json_candidate.strip()

    # Find the first { and last }
    obj_match = re.search(r'(\{.*\})', json_candidate, re.DOTALL)
    if obj_match:
        json_candidate = obj_match.group(1)

    try:
        parsed = json.loads(json_candidate)
        if isinstance(parsed, dict):
            if "terms" in parsed:
                return parsed
            if "term" in parsed:
                return {"terms": [parsed]}
        if isinstance(parsed, list):
            return {"terms": parsed}
    except Exception:
        pass

    # 3. Last resort: Find individual term objects in malformed JSON
    term_objects = re.findall(r'\{[^{}]+\}', clean_text, re.DOTALL)
    valid_terms = []
    for obj_str in term_objects:
        try:
            obj = json.loads(obj_str)
            if "term" in obj or "source" in obj:
                if "source" in obj and "term" not in obj:
                    obj["term"] = obj.pop("source")
                valid_terms.append(obj)
        except Exception:
            continue
    
    if valid_terms:
        return {"terms": valid_terms}

    return {"terms": []}


def parse_translations_from_text(text: str, expected_count: int = 0) -> List[Dict]:
    """Parse translated lines from AI response.

    Supports (in priority order):
    1. Pipe-delimited: ``1| translated text``  (primary format)
    2. XML tags: ``<line index="1">Text</line>``  (legacy / fallback)
    3. Colon/dot numbered: ``1: text`` or ``1. text``  (last resort)

    Returns: List of {"index": int, "text": str}
    """
    if not text:
        return []

    clean_text = strip_reasoning_blocks(text)
    results = []

    # 1. Pipe-delimited format: N| text  (primary)
    pipe_matches = re.findall(r'^(\d+)\s*\|\s*(.*)', clean_text, re.MULTILINE)
    if pipe_matches:
        # Collect into dict first so duplicate indices get overwritten by the last one
        idx_map: Dict[int, str] = {}
        for num_str, content in pipe_matches:
            idx_map[int(num_str)] = content.strip().replace("<br>", "\n")
        return [{"index": k, "text": v} for k, v in sorted(idx_map.items())]

    # 2. XML tag format: <line index="N">text</line>  (legacy)
    line_matches = list(re.finditer(
        r'<line\s+index=["\']?(\d+)["\']?>\s*(.*?)\s*(?:</line>|(?=<line)|$)',
        clean_text, re.DOTALL
    ))
    if line_matches:
        for match in line_matches:
            results.append({
                "index": int(match.group(1)),
                "text": match.group(2).strip().replace("<br>", "\n"),
            })
        return results

    # 3. Colon/dot numbered list: N: text or N. text  (last resort)
    current_idx = None
    for line in clean_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        match = re.match(r'^(\d+)\s*[:.]\s*(.*)', line)
        if match:
            current_idx = int(match.group(1))
            results.append({
                "index": current_idx,
                "text": match.group(2).strip().replace("<br>", "\n"),
            })
        elif current_idx is not None and results:
            results[-1]["text"] += "\n" + line

    return results
