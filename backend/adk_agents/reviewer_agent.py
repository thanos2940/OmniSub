"""
Reviewer Agent -- LLM-as-judge for translation quality assessment.

Scores translations on multiple dimensions and flags problems.
Uses a cheaper/smaller model than the translator for cost efficiency.

Scoring dimensions:
  - glossary: Correct use of glossary translations
  - naturalness: Reads like native target language, not machine translation
  - gender: Gendered forms (articles, adjectives, verbs) match the speaker
  - length: Appropriate length for subtitle display (CPS)
  - tone: Formality/register matches the source
"""

from google.adk.agents import Agent
from .llm_factory import create_model
from typing import Dict, List, Optional


def _build_glossary_ref(glossary: Dict, max_terms: int = 30) -> str:
    """Build a compact glossary reference for the reviewer.

    Only includes the most relevant terms (person names first,
    then other types) to stay within token budget.
    """
    if not glossary or not glossary.get("terms"):
        return "(none)"

    terms = glossary["terms"]

    # Prioritize person names — highest mistranslation risk
    persons = [t for t in terms if t.get("type") == "person"]
    others = [t for t in terms if t.get("type") != "person"]
    ordered = persons + others

    lines = []
    for t in ordered[:max_terms]:
        src = t.get("term", "")
        tgt = t.get("translation", src)
        gender = t.get("gender", "")
        t_type = t.get("type", "")
        keep = " [KEEP]" if t.get("keep_original") else ""
        meta_parts = []
        if t_type and t_type != "other":
            meta_parts.append(t_type)
        if gender and gender not in ("n/a", "unknown", ""):
            meta_parts.append(gender)
        meta_str = f" ({', '.join(meta_parts)})" if meta_parts else ""
        lines.append(f"- {src} -> {tgt}{meta_str}{keep}")

    return "\n".join(lines)


def create_reviewer_agent(
    model_name: str = "gemini-flash-lite-latest",
    target_language: str = "Greek",
    glossary: Dict = None,
    temperature: Optional[float] = None,
) -> Agent:
    """Create a Reviewer Agent that scores translation quality.

    Uses a low temperature for consistent, deterministic scoring.
    Designed to run on Flash (cheap, fast) rather than Pro.
    """
    glossary_ref = _build_glossary_ref(glossary)

    return Agent(
        name="ReviewerAgent",
        model=create_model(
            model_name,
            temperature=temperature if temperature is not None else 0.1,
        ),
        instruction=f"""You are a {target_language} subtitle translation quality reviewer.

For each source/translation pair you receive, score on these dimensions (0.0 to 1.0):
- glossary: Does it use the correct glossary terms declined into the grammatically correct case (Nominative, Genitive, Accusative, Vocative) with agreeing articles (ο/του/τον, η/της/την, το/του/το)? (1.0 = perfect inflected match; < 0.70 = raw nominative lemma forced into an inflected clause)
- naturalness: Does it read like natural spoken Modern Greek (δημοτική)? Are English conversational idioms ("fair enough", "give me a break", "come on", "you know", "cut it out", "my bad", etc.) adapted into natural spoken Greek idioms rather than literal calques? (1.0 = fluent native demotic Greek; < 0.80 = stiff machine translation / literal calque)
- gender: Are gendered forms (articles, adjectives, participles, verb forms) grammatically aligned with the speaker and referent? (1.0 = all correct)
- length: Is it an appropriate length and reading speed for subtitle display (CPS <= 17, CPL <= 42, <= 2 lines)? (1.0 = good CPS/CPL)
- tone: Does the formality/register match the source dialogue and character voice (e.g., formal πληθυντικός ευγενείας vs familiar ενικός)? (1.0 = perfect match)

Output ONLY a JSON array -- no commentary, no markdown fencing, no extra text:
[
  {{"index": 1, "scores": {{"glossary": 0.9, "naturalness": 0.8, "gender": 1.0, "length": 0.7, "tone": 0.9}}, "issues": "brief description of the problem", "suggestion": "corrected translation"}}
]

Rules:
- Only include entries where ANY score is below 0.85.
- If all scores are >= 0.85 for a line, omit it entirely.
- If everything looks good, output an empty array: []
- The "suggestion" field must contain a complete corrected translation in natural Demotic Greek, not just a note.
- Enforce Greek morphology: do NOT accept raw nominative glossary lemmas in inflected positions. Words must decline naturally according to their syntactic role in the sentence.
- Prioritize spoken Demotic Greek naturalness: penalize word-for-word calques of English idioms and phrasal verbs.
- Be strict about gender and number correctness -- {target_language} requires matching gender/case for articles, adjectives, and verb forms.
- Japanese names in Greek: Ensure Japanese character and proper names have their accent (τόνος) placed on the first syllable (e.g. Tanjiro -> Τάντζιρο, NOT Ταντζίρο; Zenitsu -> Ζένιτσου; Nezuko -> Νέζουκο; Inosuke -> Ίνοσουκε).
- Flag untranslated words left in the source language unless they are marked [KEEP] in the glossary.

Glossary reference:
{glossary_ref}""",
        tools=[],
        output_key="review_result",
    )


def build_review_prompt(
    line_pairs: List[Dict],
    target_language: str = "Greek",
) -> str:
    """Build a compact review prompt for a batch of source/translation pairs.

    Args:
        line_pairs: List of dicts with 'index', 'original', 'translated' keys.
        target_language: Target language name for context.
    """
    numbered = []
    for pair in line_pairs:
        idx = pair["index"]
        orig = pair["original"]
        trans = pair["translated"]
        numbered.append(f"{idx}| EN: {orig}\n   {target_language[:2].upper()}: {trans}")

    return f"Review these {target_language} subtitle translations:\n\n" + "\n".join(numbered)
