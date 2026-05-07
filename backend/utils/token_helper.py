from typing import Dict, List, Any
import json

def estimate_tokens(text: str) -> int:
    """
    Heuristic token estimator (characters / 4).
    Adds a 15% buffer for safety and XML tag overhead common in OmbiSub.
    """
    if not text:
        return 0
    
    # Simple heuristic + 15% buffer
    base_tokens = len(text) / 3.8
    return int(base_tokens * 1.15)

def estimate_glossary_tokens(glossary: Dict[str, Any]) -> int:
    """Estimates tokens for the glossary as it would be formatted in a prompt."""
    if not glossary or 'terms' not in glossary:
        return 0
    
    # The prompt usually includes terms in a list format
    # Example: "Term (Gender): Translation. Description."
    glossary_str = json.dumps(glossary.get("terms", []), ensure_ascii=False)
    return estimate_tokens(glossary_str)

def get_base_instruction_tokens() -> int:
    """Estimated tokens for the static TranslatorAgent instructions."""
    return 1200 # Fixed estimate for the system instructions + few-shot examples
