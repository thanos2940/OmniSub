"""
Glossary Research Tools for ADK Agents

These tools perform web research to enhance glossary quality.
"""

from google.adk.tools import google_search, FunctionTool
from typing import List, Dict, Optional

def research_show_context(
    show_name: str, 
    target_language: str = "English"
) -> dict:
    """
    Research background information about a show for glossary creation.
    
    Uses Google Search to find:
    - Character names and relationships
    - Location names and significance
    - Terminology and world-building elements
    
    Args:
        show_name: Name of the show/movie to research
        target_language: Target translation language
        
    Returns:
        Dictionary with research findings and search results
        
    Example:
        >>> result = research_show_context("Frieren: Beyond Journey's End")
        >>> print(result["findings"])
    """
    # This will be called by the agent with google_search tool
    # The agent decides what to search for based on show_name
    return {
        "status": "delegated_to_agent",
        "message": f"Agent should use google_search to research '{show_name}'"
    }


def validate_glossary_structure(glossary: dict) -> dict:
    """
    Validate that a glossary has the correct structure and required fields.
    
    Args:
        glossary: Glossary dictionary to validate (e.g. {"terms": [...]})
        
    Returns:
        Dictionary with:
        - valid: True if structure is correct
        - errors: List of validation errors (if any)
        - warnings: List of warnings (if any)
    """
    errors = []
    warnings = []
    
    # Check top-level structure
    if "terms" not in glossary:
        errors.append("Missing 'terms' key")
    elif not isinstance(glossary["terms"], list):
        errors.append("'terms' must be a list")
    
    # Validate each term
    if "terms" in glossary and isinstance(glossary["terms"], list):
        for i, term in enumerate(glossary["terms"]):
            if not isinstance(term, dict):
                errors.append(f"Term {i} is not a dictionary")
                continue
                
            # Check required fields
            if "term" not in term:
                errors.append(f"Term {i} missing 'term' field")
            if "translation" not in term:
                errors.append(f"Term {i} missing 'translation' field")
                
            # Check optional but recommended fields
            if "context" not in term:
                warnings.append(f"Term {i} ('{term.get('term', 'unknown')}') missing context")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "term_count": len(glossary.get("terms", []))
    }


# Export tools
validate_glossary_tool = FunctionTool(validate_glossary_structure)
