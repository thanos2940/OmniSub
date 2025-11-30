"""
Glossary Tools for ADK Agents

Provides validation and utility functions for glossary management.
"""

from typing import Dict, List
from google.adk.tools import FunctionTool


def validate_glossary(glossary: dict) -> dict:
    """
    Validate glossary structure and required fields.
    
    Args:
        glossary: Glossary dictionary to validate
        
    Returns:
        Dictionary with valid flag, errors list, warnings list, and term_count
    """
    errors = []
    warnings = []
    
    if "terms" not in glossary:
        errors.append("Missing 'terms' key")
    elif not isinstance(glossary["terms"], list):
        errors.append("'terms' must be a list")
    else:
        for i, term in enumerate(glossary["terms"]):
            if not isinstance(term, dict):
                errors.append(f"Term {i} is not a dictionary")
                continue
            
            if "term" not in term:
                errors.append(f"Term {i} missing 'term' field")
            if "translation" not in term:
                errors.append(f"Term {i} missing 'translation' field")
            if "context" not in term:
                warnings.append(f"Term {i} ('{term.get('term', 'unknown')}') missing context")
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "term_count": len(glossary.get("terms", []))
    }


def merge_glossaries(existing: Dict, new: Dict) -> dict:
    """
    Merge new glossary terms into existing glossary, avoiding duplicates.
    
    Args:
        existing: Current glossary with terms
        new: New glossary with additional terms
        
    Returns:
        Dictionary with merged glossary and statistics
    """
    existing_terms = existing.get("terms", [])
    existing_names = {t.get("term", "").lower() for t in existing_terms}
    
    new_terms = new.get("terms", [])
    added_terms = []
    
    for term in new_terms:
        term_name = term.get("term", "").lower()
        if term_name and term_name not in existing_names:
            existing_terms.append(term)
            existing_names.add(term_name)
            added_terms.append(term.get("term"))
    
    return {
        "status": "success",
        "glossary": {"terms": existing_terms},
        "added_count": len(added_terms),
        "added_terms": added_terms,
        "total_count": len(existing_terms)
    }


validate_glossary_tool = FunctionTool(validate_glossary)
merge_glossaries_tool = FunctionTool(merge_glossaries)
