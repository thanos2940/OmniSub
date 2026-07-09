"""
Cross-episode consistency report (Plan 12).

For each glossary term that has a defined target translation, scans all translated
episodes and finds lines where the source uses the term but the target does NOT
contain the canonical rendering — i.e. drift candidates. Fixing re-translates the
affected scenes (the glossary already carries the canonical target).
"""

import re
from typing import Dict, List

from utils import storage


def _word_re(term: str):
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def build_report(project_name: str) -> Dict:
    meta = storage.load_project_metadata(project_name) or {}
    terms = [t for t in meta.get("glossary", {}).get("terms", []) if t.get("term") and t.get("translation")]
    if not terms:
        return {"terms": [], "count": 0}

    target_lang = meta.get("target_language", "Greek")
    config = storage.load_global_config()
    morphology_normalization = config.get("morphology_normalization", True)
    morphology_stemming = config.get("morphology_stemming", True)

    from utils.text_normalize import tokenize_and_normalize, stem_greek, is_sublist

    # Pre-tokenize terms to avoid doing it repeatedly in the loop
    compiled_terms = []
    for t in terms:
        src = t["term"]
        tgt = t["translation"]
        src_tokens = tokenize_and_normalize(src, lang="English", stem_tokens=False)
        tgt_tokens = tokenize_and_normalize(tgt, lang=target_lang, stem_tokens=False)
        tgt_stems = [stem_greek(tk) for tk in tgt_tokens] if (morphology_stemming and target_lang.lower() == "greek") else None
        compiled_terms.append({
            "term": src,
            "translation": tgt,
            "src_tokens": src_tokens,
            "tgt_tokens": tgt_tokens,
            "tgt_stems": tgt_stems
        })

    agg = {t["term"]: {"canonical": t["translation"], "occurrences": 0, "with_canonical": 0, "drift": []} for t in terms}

    for ep in storage.list_episodes(project_name):
        data = storage.load_episode(project_name, ep)
        if not data:
            continue
        for i, line in enumerate(data.get("data", [])):
            o = line.get("original", "") or ""
            tr = line.get("translated", "") or ""
            if not tr.strip():
                continue
            
            # Tokenize / normalize this line once
            o_tokens = tokenize_and_normalize(o, lang="English", stem_tokens=False)
            tr_tokens = tokenize_and_normalize(tr, lang=target_lang, stem_tokens=False)
            tr_stems = [stem_greek(tk) for tk in tr_tokens] if (morphology_stemming and target_lang.lower() == "greek") else None
            
            for term_info in compiled_terms:
                src = term_info["term"]
                tgt = term_info["translation"]
                
                # Check if the source line contains the term
                if is_sublist(term_info["src_tokens"], o_tokens):
                    a = agg[src]
                    a["occurrences"] += 1
                    
                    # Check if the translated line contains the canonical target (exact or stemmed)
                    is_adherent = False
                    if is_sublist(term_info["tgt_tokens"], tr_tokens):
                        is_adherent = True
                    elif tr_stems is not None and term_info["tgt_stems"] is not None:
                        if is_sublist(term_info["tgt_stems"], tr_stems):
                            is_adherent = True
                            
                    if is_adherent:
                        a["with_canonical"] += 1
                    else:
                        a["drift"].append({"episode": ep, "line_index": i, "source": o, "target": tr})

    report = [
        {
            "term": s,
            "canonical": a["canonical"],
            "occurrences": a["occurrences"],
            "with_canonical": a["with_canonical"],
            "drift_count": len(a["drift"]),
            "drift": a["drift"][:50],
        }
        for s, a in agg.items() if a["occurrences"] > 0 and a["drift"]
    ]
    report.sort(key=lambda r: r["drift_count"], reverse=True)
    return {"terms": report, "count": len(report)}


STOPWORDS = {
    "the", "and", "but", "for", "with", "this", "that", "these", "those",
    "you", "him", "her", "them", "his", "their", "your", "our", "its",
    "was", "were", "are", "have", "has", "had", "been", "will", "would",
    "should", "could", "about", "there", "their", "here", "then", "than",
    "from", "into", "some", "more", "most", "just", "very", "what", "when",
    "where", "who", "why", "how", "other", "another", "some", "any", "all",
    "both", "each", "every"
}


def _extract_ngrams(text: str) -> List[str]:
    words = re.findall(r"\b[a-zA-Z]{3,20}\b", text)
    ngrams = []
    # 1-grams
    for w in words:
        if w.lower() not in STOPWORDS:
            ngrams.append(w.lower())
    # 2-grams
    for i in range(len(words) - 1):
        w1, w2 = words[i].lower(), words[i+1].lower()
        if w1 not in STOPWORDS or w2 not in STOPWORDS:
            ngrams.append(f"{w1} {w2}")
    # 3-grams
    for i in range(len(words) - 2):
        w1, w2, w3 = words[i].lower(), words[i+1].lower(), words[i+2].lower()
        if w1 not in STOPWORDS or w3 not in STOPWORDS:
            ngrams.append(f"{w1} {w2} {w3}")
    return list(set(ngrams))


def save_suggestions(project_name: str, new_suggestions: List[Dict]):
    import json
    suggestions_file = storage.PROJECTS_DIR / project_name / "suggestions.json"
    existing = []
    if suggestions_file.exists():
        try:
            with open(suggestions_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []
    
    sug_dict = {s["term"]: s for s in existing}
    for s in new_suggestions:
        sug_dict[s["term"]] = s
        
    with open(suggestions_file, 'w', encoding='utf-8') as f:
        json.dump(list(sug_dict.values()), f, indent=2, ensure_ascii=False)


async def build_intra_episode_ledger(
    project_name: str,
    parsed_srt: List[Dict],
    target_lang_code: str,
    target_lang_name: str,
    reconcile_min_freq: int = 2
) -> List[Dict]:
    """Scan parsed_srt, build occurrences, call GenAI to align renderings,
    and find terms with target drift (different stems).
    """
    ngram_to_lines = {}
    for idx, line in enumerate(parsed_srt):
        orig = line.get("original", "")
        if not orig:
            continue
        ngrams = _extract_ngrams(orig)
        for ng in ngrams:
            if ng not in ngram_to_lines:
                ngram_to_lines[ng] = []
            ngram_to_lines[ng].append(idx)
            
    candidates = []
    for ng, line_indices in ngram_to_lines.items():
        if len(line_indices) >= reconcile_min_freq:
            candidates.append((ng, line_indices))
            
    if not candidates:
        return []
        
    candidates.sort(key=lambda x: len(x[1]), reverse=True)
    candidates = candidates[:15]
    
    prompt_lines = []
    for term, line_indices in candidates:
        prompt_lines.append(f"Term: \"{term}\"")
        for line_idx in line_indices:
            line = parsed_srt[line_idx]
            orig = line.get("original", "").replace("\n", " ")
            trans = line.get("translations", {}).get(target_lang_code, "") or line.get("translated", "")
            trans = (trans or "").replace("\n", " ")
            prompt_lines.append(f"  Line {line_idx}: \"{orig}\" -> \"{trans}\"")
            
    prompt = f"""You are a translation alignment auditor. Given a list of English terms and the translated subtitle lines they appear in, identify the exact target language ({target_lang_name}) translation (rendering) used for each term in each line.
Do not translate the term yourself; extract the translation that was actually written in the translated text.

Subtitles:
{chr(10).join(prompt_lines)}

Output JSON format:
{{
  "terms": [
    {{
      "term": "english term",
      "occurrences": [
        {{ "line_index": 0, "rendering": "Greek/Target translation of the term" }}
      ]
    }}
  ]
}}
"""
    try:
        from pydantic import BaseModel, Field
        from typing import List
        
        class Occurrence(BaseModel):
            line_index: int = Field(description="0-based line index matching the input")
            rendering: str = Field(description="The exact translation of the term used in this line")
            
        class TermRenderings(BaseModel):
            term: str = Field(description="The English term")
            occurrences: List[Occurrence]
            
        class LedgerResponse(BaseModel):
            terms: List[TermRenderings]
            
        from utils.model_resolver import resolve_model
        metadata = storage.load_project_metadata(project_name) or {}
        model_name = resolve_model("consistency", metadata)
        
        from adk_agents.llm_factory import generate
        response_text = await generate(
            model_name=model_name,
            prompt=prompt,
            system_instruction="You are a professional subtitle consistency reviewer.",
            temperature=0.1,
            response_schema=LedgerResponse,
            role="consistency",
        )
        
        import json
        from utils.structured_output import strip_reasoning_blocks
        clean_text = strip_reasoning_blocks(response_text)
        
        clean_candidate = clean_text.strip()
        if clean_candidate.startswith("```json"):
            clean_candidate = clean_candidate[7:]
        elif clean_candidate.startswith("```"):
            clean_candidate = clean_candidate[3:]
        if clean_candidate.endswith("```"):
            clean_candidate = clean_candidate[:-3]
        clean_candidate = clean_candidate.strip()
        
        match = re.search(r'(\{.*\})|(\[.*\])', clean_candidate, re.DOTALL)
        if match:
            clean_candidate = match.group(0)
            
        parsed = json.loads(clean_candidate)
        terms_data = parsed.get("terms", [])
        
        from utils.text_normalize import stem_greek, strip_accents_and_normalize
        
        drift_findings = []
        new_suggestions = []
        
        proj_meta = storage.load_project_metadata(project_name) or {}
        glossary_terms = {t.get("term", "").lower(): t.get("translation", "") for t in proj_meta.get("glossary", {}).get("terms", []) if t.get("term")}
        
        for t_entry in terms_data:
            term = t_entry.get("term", "").strip()
            occurrences = t_entry.get("occurrences", [])
            if not term or not occurrences:
                continue
                
            stemmed_groups = {}
            for occ in occurrences:
                idx = occ.get("line_index")
                rendering = occ.get("rendering", "").strip()
                if not rendering or idx is None:
                    continue
                    
                if target_lang_name.lower() == "greek":
                    stem = stem_greek(rendering)
                else:
                    stem = strip_accents_and_normalize(rendering).lower()
                    
                if stem not in stemmed_groups:
                    stemmed_groups[stem] = []
                stemmed_groups[stem].append((idx, rendering))
                
            if len(stemmed_groups) > 1:
                glossary_translation = glossary_terms.get(term.lower())
                
                if glossary_translation:
                    canonical_rendering = glossary_translation
                    if target_lang_name.lower() == "greek":
                        canonical_stem = stem_greek(glossary_translation)
                    else:
                        canonical_stem = strip_accents_and_normalize(glossary_translation).lower()
                else:
                    sorted_stems = sorted(stemmed_groups.items(), key=lambda x: len(x[1]), reverse=True)
                    canonical_stem = sorted_stems[0][0]
                    raw_renderings = [r for _, r in sorted_stems[0][1]]
                    canonical_rendering = max(set(raw_renderings), key=raw_renderings.count)
                    
                minority_lines = []
                for stem, occ_list in stemmed_groups.items():
                    if stem != canonical_stem:
                        for idx, rendering in occ_list:
                            minority_lines.append({
                                "index": idx,
                                "original": parsed_srt[idx].get("original", ""),
                                "current_translation": parsed_srt[idx].get("translations", {}).get(target_lang_code, "") or parsed_srt[idx].get("translated", "")
                            })
                            
                if minority_lines:
                    drift_findings.append({
                        "term": term,
                        "canonical": canonical_rendering,
                        "minority_lines": minority_lines
                    })
                    
                    new_suggestions.append({
                        "term": term,
                        "suggested_translation": canonical_rendering,
                        "occurrences": len(occurrences),
                        "accepted": False
                    })
                    
        if new_suggestions:
            save_suggestions(project_name, new_suggestions)
            
        return drift_findings
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to build intra-episode ledger: {e}")
        return []
