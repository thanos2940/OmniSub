"""
Source Subtitle Cleaning Preprocess (Plan 25).
"""

import re
import hashlib
from typing import List, Dict, Tuple, Optional
from utils.srt_parser import timecode_to_ms
from utils.subtitle_io import detect_format, parse_subtitle, reconstruct_subtitle
from utils import storage


def normalize_orig_indexes(v) -> List[int]:
    """Normalize a ``clean_to_orig_map`` value to a list of ints.

    Production values are lists (a cleaned cue can map to several raw cues after the
    merge pass); older/test data may store a bare int. Single source of truth so the
    reader (reconstruct_cleaned_srt) and writer (delete-lines) agree on the shape.
    """
    if isinstance(v, list):
        return [int(x) for x in v]
    return [int(v)]


def clean_srt(
    parsed: List[Dict],
    options: Optional[Dict] = None
) -> Tuple[List[Dict], Dict[int, int]]:
    """Clean the source subtitles based on provided options.

    Returns:
        A tuple: (cleaned_parsed_list, clean_to_orig_index_map)
    """
    if options is None:
        options = {}

    strip_sdh = options.get("strip_sdh", True)
    preserve_italics = options.get("preserve_italics", True)
    merge_split_cues = options.get("merge_split_cues", True)

    cleaned = []
    clean_to_orig = {}

    for idx, entry in enumerate(parsed):
        orig_text = entry.get("original") or ""
        
        # 1. Clean formatting tags
        text = orig_text
        if preserve_italics:
            # Strip all tags except <i> and </i>
            text = text.replace("<i>", "__I_START__").replace("</i>", "__I_END__")
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace("__I_START__", "<i>").replace("__I_END__", "</i>")
        else:
            text = re.sub(r'<[^>]+>', '', text)

        # 2. Clean SDH cues
        if strip_sdh:
            # Strip bracketed cues [...] and (...)
            text = re.sub(r'\[[^\]]*\]', '', text)
            text = re.sub(r'\([^\)]*\)', '', text)
            # Strip musical notes
            text = re.sub(r'[♪♫#]+', '', text)
            # Strip speaker labels
            text = re.sub(r'^[A-Z][a-zA-Z\s]{1,15}:', '', text)

        # Clean whitespace
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)
        
        cleaned_entry = {**entry, "original": text}
        cleaned.append(cleaned_entry)
        clean_to_orig[len(cleaned) - 1] = [idx]

    # 3. Heuristic merge of split cues
    if merge_split_cues:
        merged = []
        merged_map = {}
        i = 0
        while i < len(cleaned):
            curr = cleaned[i]
            
            if not curr["original"].strip():
                i += 1
                continue
                
            merged_orig_indices = clean_to_orig[i].copy()
            while i + 1 < len(cleaned):
                nxt = cleaned[i + 1]
                if not nxt["original"].strip():
                    i += 1
                    continue
                
                curr_clean = re.sub(r'<[^>]+>', '', curr["original"]).strip()
                ends_with_punctuation = curr_clean and curr_clean[-1] in (".", "?", "!", "\"", "”", "…")
                
                try:
                    curr_end_ms = _get_end_ms(curr.get("timecode", ""))
                    nxt_start_ms = _get_start_ms(nxt.get("timecode", ""))
                    gap_ms = nxt_start_ms - curr_end_ms
                except Exception:
                    gap_ms = 1000.0

                if not ends_with_punctuation and gap_ms < 500:
                    curr["original"] = f"{curr['original']} {nxt['original']}"
                    curr["timecode"] = _merge_timecodes(curr["timecode"], nxt["timecode"])
                    merged_orig_indices.extend(clean_to_orig[i + 1])
                    i += 1
                else:
                    break
            
            merged.append(curr)
            merged_map[len(merged) - 1] = merged_orig_indices
            i += 1
            
        cleaned = merged
        clean_to_orig = merged_map

    return cleaned, clean_to_orig


def _get_start_ms(timecode: str) -> int:
    a, _ = timecode.split("-->")
    return timecode_to_ms(a.strip())


def _get_end_ms(timecode: str) -> int:
    _, b = timecode.split("-->")
    return timecode_to_ms(b.strip())


def _merge_timecodes(t1: str, t2: str) -> str:
    a, _ = t1.split("-->")
    _, b = t2.split("-->")
    return f"{a.strip()} --> {b.strip()}"


def import_and_clean_srt(
    project_name: str,
    episode_name: str,
    raw_srt_content: str,
    filename: Optional[str] = None,
    options: Optional[Dict] = None,
    fingerprint: Optional[str] = None,
    target_srt_content: Optional[str] = None,
    extra_metadata: Optional[Dict] = None,
) -> Dict:
    """Save raw SRT, parse, clean it based on config, save cleaned data and map,
    and return the metadata and line count.

    ``extra_metadata`` is merged into the episode metadata *before* it is persisted.
    Callers that enrich the result (the sync engine adding arr_media_path and friends)
    must use this rather than saving a second time: an interruption between the two
    writes leaves an episode that looks imported but has no media path, which no later
    sync heals and which prune skips because arr_source is absent too.
    """
    global_config = storage.load_global_config()
    if options is None:
        options = {
            "strip_sdh": global_config.get("strip_sdh", True),
            "preserve_italics": global_config.get("preserve_italics", True),
            "merge_split_cues": global_config.get("merge_split_cues", True),
        }

    fmt = detect_format(filename=filename, content=raw_srt_content)
    storage.save_original_subtitle(project_name, episode_name, raw_srt_content, filename=filename)
    parsed_raw = parse_subtitle(raw_srt_content, filename=filename or "")

    # ASS/SSA bypasses source cleaning + cue-merging (design decision D1): the SRT merge
    # pass folds adjacent events into one and drops the merged-away event, which
    # reconstruct_ass then leaves as untranslated source text. ASS keeps a strict 1:1
    # event mapping; styles/karaoke are preserved via the original document.
    is_ass = fmt in ("ass", "ssa")
    clean_enabled = global_config.get("source_clean_enabled", True) and not is_ass
    
    if clean_enabled:
        cleaned_data, clean_to_orig = clean_srt(parsed_raw, options)
    else:
        cleaned_data = parsed_raw
        clean_to_orig = {i: i for i in range(len(parsed_raw))}
        
    # Populate src_hash for new cleaned data
    for entry in cleaned_data:
        orig_text = entry.get("original", "").strip()
        entry["src_hash"] = hashlib.sha256(orig_text.encode("utf-8")).hexdigest()[:16]

    # Plan 26: Incremental carry-over
    old_data = storage.load_episode(project_name, episode_name)
    carried_over = False
    if old_data and global_config.get("incremental_retranslate_enabled", True):
        old_lines = old_data.get("data", [])
        for entry in old_lines:
            if "src_hash" not in entry:
                orig_text = entry.get("original", "").strip()
                entry["src_hash"] = hashlib.sha256(orig_text.encode("utf-8")).hexdigest()[:16]
        
        min_ratio = global_config.get("incremental_min_unchanged_ratio", 0.5)
        cleaned_data, carried_over = align_and_carry_translations(old_lines, cleaned_data, min_ratio)
        
    if fingerprint is None:
        fingerprint = "clean_" + hashlib.sha1(raw_srt_content.encode("utf-8")).hexdigest()[:16]
    
    ep_meta = storage.load_episode_metadata(project_name, episode_name) or {}
    ep_meta["original_filename"] = filename or ep_meta.get("original_filename") or f"{episode_name}.{fmt}"
    ep_meta["line_count"] = len(cleaned_data)
    ep_meta["raw_line_count"] = len(parsed_raw)
    ep_meta["arr_sub_fingerprint"] = fingerprint
    ep_meta["clean_to_orig_map"] = {str(k): v for k, v in clean_to_orig.items()}
    # A fresh import re-establishes the raw-based remap, so drop any prior
    # "authoritative cue list" flag left by a manual SubtitleEdit fix.
    ep_meta.pop("structure_authoritative", None)
    
    if target_srt_content:
        parsed_target = parse_subtitle(target_srt_content, fmt=fmt)
        n_aligned = align_target_subtitles(cleaned_data, parsed_target)
        # Only mark completed if timecode alignment actually placed translations.
        # A shifted/different-framerate target can overlap zero source cues, which
        # would otherwise flag the episode "completed" with an empty translation.
        if n_aligned > 0:
            ep_meta["translated"] = True
            ep_meta["translation_status"] = "completed"
        else:
            ep_meta["translated"] = False
            ep_meta["translation_status"] = "pending"
    else:
        ep_meta["translated"] = False
        ep_meta["translation_status"] = "pending"
        
    ep_meta.pop("translation_failed", None)
    ep_meta.pop("translation_error", None)

    if extra_metadata:
        ep_meta.update(extra_metadata)

    storage.save_episode(project_name, episode_name, cleaned_data, ep_meta)

    
    cp = storage.episode_dir(project_name, episode_name) / "checkpoint.json"
    if cp.exists():
        try:
            cp.unlink()
        except Exception:
            pass
            
    return {"line_count": len(cleaned_data), "raw_line_count": len(parsed_raw), "metadata": ep_meta}


def align_target_subtitles(cleaned_source_data: List[Dict], parsed_target_data: List[Dict]) -> int:
    """Align parsed target subtitles to cleaned source subtitles using timecodes.
    Modifies cleaned_source_data in-place by updating the 'translated' field.

    Returns the number of source cues that received a translation, so callers can
    tell a real alignment from a zero-overlap one (e.g. a shifted/different-framerate
    target where nothing matched).
    """
    aligned = 0
    if not cleaned_source_data or not parsed_target_data:
        return aligned

    for tgt_cue in parsed_target_data:
        tc = tgt_cue.get("timecode", "")
        if not tc:
            continue
        try:
            tgt_start = _get_start_ms(tc)
            tgt_end = _get_end_ms(tc)
        except Exception:
            continue
            
        best_match_idx = -1
        max_overlap = 0
        
        for idx, src_cue in enumerate(cleaned_source_data):
            src_tc = src_cue.get("timecode", "")
            if not src_tc:
                continue
            try:
                src_start = _get_start_ms(src_tc)
                src_end = _get_end_ms(src_tc)
            except Exception:
                continue
                
            overlap_start = max(tgt_start, src_start)
            overlap_end = min(tgt_end, src_end)
            overlap = overlap_end - overlap_start
            
            if overlap > max_overlap:
                max_overlap = overlap
                best_match_idx = idx
                
        if best_match_idx != -1 and max_overlap > 0:
            existing = cleaned_source_data[best_match_idx].get("translated", "")
            new_text = tgt_cue.get("original", "").strip()
            # If a source cue spans multiple target cues, join them
            if existing:
                cleaned_source_data[best_match_idx]["translated"] = f"{existing} {new_text}"
            else:
                cleaned_source_data[best_match_idx]["translated"] = new_text
            aligned += 1

    return aligned


def _ass_font_scale(lang_code: Optional[str]) -> float:
    """Return the ASS font-size multiplier for the target language.

    Some scripts (Greek, CJK, Arabic, …) render visually larger than Latin at
    the same nominal font size. This map stores per-language scale factors so
    the exported .ass looks proportional to the original.
    """
    _SCALE = {
        "el": 0.85,  # Greek
    }
    return _SCALE.get(lang_code or "", 1.0)


# Splitting a cue below this per-part duration would just flash text at the viewer —
# such cues keep their (balanced) 3rd line instead.
_MIN_SPLIT_PART_MS = 700


def _split_row_for_export(row: Dict, parts: List[str]) -> List[Dict]:
    """Turn one over-long cue into len(parts) cues sharing the original duration.

    The boundary is proportional to each part's visible text length (reading speed
    stays constant across the parts). Shallow-copies the row; for ASS also copies the
    ``_ass`` block with the new timings and a ``split`` flag so ``reconstruct_ass``
    knows to retime the original event and clone it for the extra parts.
    """
    from utils.subtitle_conformance import _visible_len
    from utils.ass_parser import _ms_to_srt_timecode

    start = _get_start_ms(row.get("timecode", ""))
    end = _get_end_ms(row.get("timecode", ""))
    total_ms = end - start
    weights = [max(1, _visible_len(p.replace("\n", " "))) for p in parts]
    total_w = sum(weights)

    out: List[Dict] = []
    t = start
    for i, (part, w) in enumerate(zip(parts, weights)):
        t2 = end if i == len(parts) - 1 else t + int(total_ms * w / total_w)
        new_row = dict(row)
        new_row["translated"] = part
        new_row["timecode"] = _ms_to_srt_timecode(t, t2)
        if isinstance(row.get("_ass"), dict):
            meta = dict(row["_ass"])
            meta["start_ms"], meta["end_ms"] = t, t2
            meta["split"] = True
            new_row["_ass"] = meta
        out.append(new_row)
        t = t2
    return out


def _autobalance_for_export(rows: List[Dict], fmt: str) -> None:
    """SubtitleEdit-style auto-balance + long-cue splitting on the EXPORT rows.

    Runs here — inside the shared reconstruction path — so every export seam (live
    worker, batch lane, downloads, delete re-export) emits balanced lines, not just
    ``auto_export_translated_subtitle``. Two passes:
      1. auto-balance: re-wrap violating cues into balanced lines;
      2. auto-split: cues that would still need more than ``max_lines`` lines are cut
         into extra cues sharing the original duration (proportional to text length).
    For ASS only plain dialogue events are touched; positioned signs and karaoke
    passthrough keep their exact text and timing. Mutates ``rows`` in place (the
    callers pass freshly-built export copies, never the editor data).
    """
    try:
        gc = storage.load_global_config()
        if not gc.get("autobalance_export_lines", True):
            return
        from utils.subtitle_conformance import (
            default_limits, autobalance_rows, split_text_parts, is_dash_dialogue,
        )
        limits = default_limits(gc)
        max_lines = limits["max_lines"]

        def eligible(r: Dict) -> bool:
            if fmt in ("ass", "ssa"):
                a = r.get("_ass") or {}
                return a.get("kind") == "dialogue" and a.get("translatable") is not False
            return True

        targets = [r for r in rows if eligible(r)]
        autobalance_rows(targets, limits)

        # Pass 2 — split cues that still exceed the line cap into extra cues.
        new_rows: List[Dict] = []
        split_any = False
        for r in rows:
            t = r.get("translated") or ""
            if (
                eligible(r) and t.strip() and not is_dash_dialogue(t)
                and len([l for l in t.split("\n") if l.strip()]) > max_lines
            ):
                dur = _get_end_ms(r.get("timecode", "")) - _get_start_ms(r.get("timecode", ""))
                parts = split_text_parts(t, limits["max_chars_per_line"], max_lines)
                if len(parts) > 1 and dur >= _MIN_SPLIT_PART_MS * len(parts):
                    new_rows.extend(_split_row_for_export(r, parts))
                    split_any = True
                    continue
            new_rows.append(r)

        if split_any:
            for i, r in enumerate(new_rows):
                r["id"] = str(i + 1)
            rows[:] = new_rows
    except Exception:
        # Balancing is cosmetic — a failure must never block writing the subtitle.
        pass


def reconstruct_cleaned_srt(
    project_name: str,
    episode_name: str,
    cleaned_data: List[Dict],
    target_lang_code: Optional[str] = None
) -> str:
    """Reconstruct SRT using the original timing/structure, filling in
    translations from the cleaned/translated data.
    """
    metadata = storage.load_episode_metadata(project_name, episode_name) or {}

    raw_content, ext = storage.load_original_subtitle(project_name, episode_name)
    fmt = ext.lstrip('.') if ext else "srt"

    # Font-size scaling for ASS targets whose glyphs are wider than Latin.
    font_scale = _ass_font_scale(target_lang_code) if fmt in ("ass", "ssa") else 1.0

    def _rows_from_cleaned():
        rows = []
        for i, entry in enumerate(cleaned_data):
            if target_lang_code:
                txt = (entry.get("translations", {}) or {}).get(target_lang_code) or entry.get("translated") or ""
            else:
                txt = entry.get("translated") or ""
            rows.append({
                "id": str(i + 1),
                "timecode": entry.get("timecode", ""),
                "translated": txt,
                "original": entry.get("original", ""),
            })
        return rows

    # After manual SubtitleEdit fixes, the cleaned cue list is authoritative — its
    # line count/timecodes may no longer match the original raw file (long lines were
    # split into new cues), so the raw-based remap below can't represent it. Emit the
    # cue list directly instead, so the splits survive into the exported file.
    if metadata.get("structure_authoritative"):
        rows = _rows_from_cleaned()
        _autobalance_for_export(rows, fmt)
        return reconstruct_subtitle(rows, fmt=fmt, original_content=raw_content, font_scale=font_scale, target_lang_code=target_lang_code)

    if not raw_content:
        # Shallow-copy: cleaned_data is the caller's live row list (often the editor
        # data) and export-only balancing must not leak back into it.
        rows = [dict(e) for e in cleaned_data]
        _autobalance_for_export(rows, fmt)
        return reconstruct_subtitle(rows, fmt=fmt, font_scale=font_scale, target_lang_code=target_lang_code)

    parsed_raw = parse_subtitle(raw_content, fmt=fmt)
    clean_to_orig_str = metadata.get("clean_to_orig_map", {})

    # No map at all (episode imported before the map existed): the raw remap below
    # would treat every original cue as unmapped. Emit the cleaned/translated cue
    # list directly instead — it's the only structure we can trust here.
    if not clean_to_orig_str:
        rows = _rows_from_cleaned()
        _autobalance_for_export(rows, fmt)
        return reconstruct_subtitle(rows, fmt=fmt, original_content=raw_content, font_scale=font_scale, target_lang_code=target_lang_code)

    clean_to_orig = {int(k): normalize_orig_indexes(v) for k, v in clean_to_orig_str.items()}

    orig_to_clean = {}
    for c_idx, o_indices in clean_to_orig.items():
        for idx_in_list, o_idx in enumerate(o_indices):
            orig_to_clean[o_idx] = {"clean_idx": c_idx, "is_primary": idx_in_list == 0}

    # Cues the user deleted in the editor — dropped from the output entirely
    deleted_orig = {int(i) for i in metadata.get("deleted_orig_indexes") or []}

    kept_entries = []
    for orig_idx, orig_entry in enumerate(parsed_raw):
        if orig_idx in deleted_orig:
            continue

        mapping = orig_to_clean.get(orig_idx)
        if mapping is None:
            # This original cue has no cleaned counterpart: it was removed by source
            # cleaning (SDH, karaoke/lyrics stripped to nothing) or absorbed into a
            # merged cue by an older import whose map only recorded the primary index.
            # DROP it. Keeping it would resurrect the untranslated English source text
            # inside the translated subtitle — and for merged-away fragments, overlap
            # the already-translated merged cue on screen (mixed EN+target output).
            continue

        clean_idx = mapping["clean_idx"]
        is_primary = mapping["is_primary"]

        if not is_primary:
            # This original cue was merged into the primary cue, so skip it
            continue

        if clean_idx >= len(cleaned_data):
            # Stale map pointing past the cleaned data — nothing trustworthy to
            # emit for this cue; dropping beats exporting untranslated source text.
            continue

        clean_entry = cleaned_data[clean_idx]

        # Apply merged timecode
        if "timecode" in clean_entry:
            orig_entry["timecode"] = clean_entry["timecode"]

        translation = ""
        if target_lang_code:
            translation = clean_entry.get("translations", {}).get(target_lang_code, "")
            if not translation and clean_entry.get("translated"):
                # Fallback to translated if language matches or translations dict is missing it
                translation = clean_entry.get("translated")
        else:
            translation = clean_entry.get("translated") or ""
        orig_entry["translated"] = translation

        kept_entries.append(orig_entry)

    # Renumber sequentially so deletions don't leave gaps in cue numbering
    for i, entry in enumerate(kept_entries):
        entry["id"] = str(i + 1)

    # Output-level fix (export copy only): make layered ASS typesetting — the same sign
    # drawn on several stacked layers — share one translation so the layers can't diverge.
    if fmt in ("ass", "ssa"):
        from utils.ass_parser import unify_layer_duplicates
        unify_layer_duplicates(kept_entries)

    # Output-level fix: SE-style auto-balance of over-long / over-stacked lines.
    # kept_entries are freshly parsed from the raw file, so mutation is export-only.
    _autobalance_for_export(kept_entries, fmt)

    return reconstruct_subtitle(kept_entries, fmt=fmt, original_content=raw_content, font_scale=font_scale, target_lang_code=target_lang_code)


def align_and_carry_translations(
    old_lines: List[Dict],
    new_lines: List[Dict],
    min_unchanged_ratio: float = 0.5
) -> Tuple[List[Dict], bool]:
    """Align old and new lines by src_hash using LCS.
    Carries over translations for unchanged lines.
    Returns: (new_lines_with_carried_translations, is_incremental)
    """
    n = len(old_lines)
    m = len(new_lines)
    
    if n == 0 or m == 0:
        return new_lines, False

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if old_lines[i-1].get("src_hash") == new_lines[j-1].get("src_hash"):
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
                
    matches = []
    i, j = n, m
    while i > 0 and j > 0:
        if old_lines[i-1].get("src_hash") == new_lines[j-1].get("src_hash"):
            matches.append((i-1, j-1))
            i -= 1
            j -= 1
        elif dp[i-1][j] >= dp[i][j-1]:
            i -= 1
        else:
            j -= 1
            
    matches.reverse()
    
    match_count = len(matches)
    ratio = match_count / m if m > 0 else 0.0
    
    if ratio < min_unchanged_ratio:
        return new_lines, False
        
    for old_idx, new_idx in matches:
        old_line = old_lines[old_idx]
        new_line = new_lines[new_idx]
        
        new_line["translations"] = dict(old_line.get("translations", {}))
        if old_line.get("translated"):
            new_line["translated"] = old_line["translated"]
            
        new_line["from_tm"] = old_line.get("from_tm", False)
        new_line["tm_user_edited"] = old_line.get("tm_user_edited", False)
        new_line["tm_origin_episode"] = old_line.get("tm_origin_episode", "")
        
        if "needs_review" in old_line:
            new_line["needs_review"] = old_line["needs_review"]
        if "review_issues" in old_line:
            new_line["review_issues"] = old_line["review_issues"]
            
    return new_lines, True
