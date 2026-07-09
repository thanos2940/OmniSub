import subprocess
import os
import tempfile
import hashlib
from pathlib import Path
from typing import List, Dict, Optional
from utils.srt_parser import reconstruct_srt, parse_srt, timecode_to_ms
from utils.storage import load_global_config


class SubtitleEditUnavailable(Exception):
    """SubtitleEdit isn't configured/installed. Raised so callers can surface an
    actionable message instead of silently no-opping."""


# ---------------------------------------------------------------------------
# Auto-balance (done in-house)
#
# SubtitleEdit's command line only exposes /fixcommonerrors for line work; the
# GUI Batch-Convert actions "Auto balance lines", "Split/break long lines" and
# "Merge short lines" are NOT valid /convert parameters (see SubtitleEdit issue
# #7555). Passing them did nothing — and on some SE versions made the whole run
# exit non-zero, so NO fixes applied. We therefore run /fixcommonerrors via SE and
# perform the line balancing ourselves, here, so "Apply SubtitleEdit fixes" really
# does balance lines.
# ---------------------------------------------------------------------------

def _looks_like_dialogue(text: str) -> bool:
    """True for a multi-speaker cue (>=2 lines starting with a dash). Such cues must
    not be re-flattened/rebalanced or the speaker structure is destroyed."""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    return sum(1 for l in lines if l[:1] in ("-", "—", "–")) >= 2


def _balance_block(text: str, max_chars: int, max_lines: int) -> str:
    """Auto-balance one cue's text across up to ``max_lines`` lines. Short lines and
    dialogue cues are returned unchanged.

    For the common two-line case this picks the word boundary that minimises the
    longer line (then the line-length difference) — a true balance, unlike a greedy
    fill which overflows the last line. Falls back to the shared greedy wrapper for
    the rare >2-line case.
    """
    if not text or not text.strip():
        return text
    if _looks_like_dialogue(text):
        return text

    flat = " ".join(text.split())
    if len(flat) <= max_chars or max_lines <= 1:
        return flat

    words = flat.split(" ")
    if len(words) < 2:
        return flat

    if max_lines == 2:
        best = None  # (max_len, len_diff, rendered)
        for k in range(1, len(words)):
            l1 = " ".join(words[:k])
            l2 = " ".join(words[k:])
            cost = (max(len(l1), len(l2)), abs(len(l1) - len(l2)))
            if best is None or cost < best[0]:
                best = (cost, f"{l1}\n{l2}")
        return best[1]

    from utils.subtitle_conformance import wrap_text
    return wrap_text(flat, max_chars, max_lines)


def _balance_srt(srt_content: str) -> str:
    """Apply dialogue-aware auto-balancing to every cue of an SRT string and
    re-serialize. Cue count and timecodes are preserved (balancing only inserts
    line breaks within a cue)."""
    from utils.subtitle_conformance import default_limits
    limits = default_limits(load_global_config())
    cues = parse_srt(srt_content)
    if not cues:
        return srt_content
    parts = []
    for i, c in enumerate(cues, start=1):
        # parse_srt stores the cue text in 'original'.
        txt = _balance_block(c.get("original", "") or "", limits["max_chars_per_line"], limits["max_lines"])
        parts.append(f"{i}\n{c.get('timecode', '')}\n{txt}")
    return "\n\n".join(parts) + "\n"


def fix_subtitles_with_se(parsed_data: List[Dict], target_lang_code: Optional[str] = None) -> List[Dict]:
    """
    Run SubtitleEdit fixes on parsed subtitle data.

    This runs SubtitleEdit 'Fix common errors' (the only line-fix action SE exposes
    on the command line) and then balances long lines in-house. The fixes operate on
    the translated text (``reconstruct_srt`` prefers ``translated``); the fixed text is
    written back to ``translated`` AND to ``translations[target_lang_code]`` so
    downstream passes (conformance, final save) that read the per-language dict don't
    see it as empty and blank the line.
    """
    config = load_global_config()
    se_path = config.get("subtitle_edit_path")

    if not se_path or not os.path.exists(se_path):
        return parsed_data # Skip if SE not configured or not found

    from utils.subtitle_conformance import default_limits
    limits = default_limits(config)

    # 1. Reconstruct to temporary file
    srt_content = reconstruct_srt(parsed_data)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "input.srt"
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        
        # Use utf-8-sig for SRT files to ensure proper BOM handling if SE expects it, 
        # but SE is usually fine with utf-8. Omnisub uses utf-8.
        with open(input_file, 'w', encoding='utf-8') as f:
            f.write(srt_content)
            
        # 2. Run SubtitleEdit
        # Batch syntax: /convert <input-pattern> <format> [/options]. The input file
        # comes before the format name, and value options use colon syntax.
        # Only /fixcommonerrors is a valid /convert action — /splitlonglines,
        # /balancelines and /mergesametexts are GUI Batch-Convert options, not CLI
        # parameters (SubtitleEdit#7555), so they were removed. Line balancing is done
        # by _balance_block below.
        cmd = [
            se_path,
            "/convert", str(input_file), "subrip",
            "/fixcommonerrors",
            f"/outputfolder:{output_dir}",
            "/overwrite",
        ]
        
        try:
            # We use shell=False for security and subprocess.run for blocking execution
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            # 3. Read result back
            # SE usually keeps the same filename in the output folder
            result_file = output_dir / "input.srt"
            if result_file.exists():
                with open(result_file, 'r', encoding='utf-8-sig') as f:
                    fixed_content = f.read()
                
                fixed_data = parse_srt(fixed_content)
                
                # Map fixed text to 'translated' and try to restore 'original' for context
                # We use a map for quick lookup from the input data
                input_map = {entry['id']: entry for entry in parsed_data}

                for entry in fixed_data:
                    # parse_srt puts the (SE-fixed) text in 'original'; that text is the
                    # fixed *translation* here, since reconstruct_srt rendered 'translated'.
                    # Auto-balance long lines in-house (SE's CLI can't).
                    fixed_text = _balance_block(
                        entry["original"], limits["max_chars_per_line"], limits["max_lines"]
                    )
                    entry["translated"] = fixed_text

                    matched = input_map.get(entry["id"])
                    # Carry the matched source line's other-language translations, then
                    # overwrite this language with the fixed text. Without a populated
                    # translations dict, the conformance pass reads it as empty and wipes
                    # the line (the cause of "translation completed but editor is blank").
                    base_tr = dict((matched or {}).get("translations") or {})
                    if target_lang_code:
                        base_tr[target_lang_code] = fixed_text
                    entry["translations"] = base_tr

                    # Restore the real source text when the id still maps 1:1; for lines
                    # SE split into new cues there is no clean source, so leave as-is.
                    if matched:
                        entry["original"] = matched.get("original", "")

                return fixed_data
        except Exception as e:
            print(f"Error running SubtitleEdit: {e}")
            if hasattr(e, 'stderr'):
                print(f"SE Stderr: {e.stderr}")

    return parsed_data


# ---------------------------------------------------------------------------
# Manual per-track "Apply common fixes"
# ---------------------------------------------------------------------------

def _se_executable() -> Optional[str]:
    se_path = load_global_config().get("subtitle_edit_path")
    return se_path if (se_path and os.path.exists(se_path)) else None


def _run_se_cli(srt_content: str) -> str:
    """Run SubtitleEdit 'Fix common errors' on ``srt_content``, auto-balance long
    lines in-house, and return the re-read/fixed SRT text.

    Only /fixcommonerrors is passed to SE — its other line actions (split/balance/
    merge) are GUI-only and not valid CLI parameters (SubtitleEdit#7555). Balancing
    is therefore done by _balance_srt.

    Raises SubtitleEditUnavailable if SE isn't configured, RuntimeError if it
    produced no readable output.
    """
    se_path = _se_executable()
    if not se_path:
        raise SubtitleEditUnavailable(
            "SubtitleEdit is not configured. Set its executable path in Settings "
            "to use 'Apply common fixes'."
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "input.srt"
        output_dir = Path(tmpdir) / "output"
        output_dir.mkdir()
        input_file.write_text(srt_content, encoding="utf-8")

        # SubtitleEdit batch syntax: /convert <input-pattern> <format> [/options].
        # The input file (pattern) MUST come before the format name, and options that
        # take a value use colon syntax (/outputfolder:<dir>), not a separate arg.
        # Only /fixcommonerrors is a valid CLI action (see _balance_srt / SubtitleEdit#7555).
        cmd = [
            se_path, "/convert", str(input_file), "subrip",
            "/fixcommonerrors",
            f"/outputfolder:{output_dir}", "/overwrite",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"SubtitleEdit exited with code {result.returncode}. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )

        out_file = output_dir / "input.srt"
        if not out_file.exists():
            # SE occasionally varies the output filename; take the first .srt it wrote.
            cands = sorted(output_dir.glob("*.srt"))
            if cands:
                out_file = cands[0]
        if not out_file.exists():
            raise RuntimeError(
                "SubtitleEdit produced no output file "
                f"(stdout={result.stdout!r} stderr={result.stderr!r})."
            )
        return _balance_srt(out_file.read_text(encoding="utf-8-sig"))


def _tc_ms_range(timecode: str):
    try:
        a, b = timecode.split("-->")
        return timecode_to_ms(a.strip()), timecode_to_ms(b.strip())
    except Exception:
        return 0, 0


def _best_overlap_index(start: int, end: int, ranges) -> Optional[int]:
    """Index of the old cue with the greatest temporal overlap; falls back to the
    nearest start time when nothing overlaps (e.g. SE nudged a timecode)."""
    best_idx, best_ov = None, 0
    for idx, rs, re_ in ranges:
        ov = min(end, re_) - max(start, rs)
        if ov > best_ov:
            best_ov, best_idx = ov, idx
    if best_idx is None and ranges:
        nearest, ndist = None, None
        for idx, rs, _re in ranges:
            d = abs(rs - start)
            if ndist is None or d < ndist:
                ndist, nearest = d, idx
        best_idx = nearest
    return best_idx


def apply_common_fixes(parsed_data: List[Dict], track: str, lang_code: str, primary_code: str):
    """Apply SubtitleEdit common fixes to one track and rebuild the cue list from the
    re-read output. The line count may change (long lines get split into new cues).

    track == "source": fixed text becomes each cue's ``original``. Existing
        translations are carried onto the unchanged cues; cues newly created by a
        split are left untranslated and flagged for review.
    track == "target": fixed text becomes the cue's translation for ``lang_code``;
        the source ``original`` is re-attached by timecode overlap for editor context.

    Returns ``(new_data, stats)``. Raises SubtitleEditUnavailable if SE isn't set up.
    """
    base_stats = {"before": len(parsed_data), "after": len(parsed_data), "changed": False, "track": track}
    if not parsed_data:
        return list(parsed_data), base_stats

    is_source = (track == "source")

    def _target_text(e: Dict) -> str:
        t = (e.get("translations") or {}).get(lang_code)
        if not t and lang_code == primary_code:
            t = e.get("translated")
        return t or ""

    # 1. Render the chosen track to a valid SRT (timecodes preserved). For the target
    #    track, untranslated cues fall back to source text so the SRT stays valid and
    #    no cue is dropped; the source-fallback text is discarded again on rebuild.
    in_cues = []
    for e in parsed_data:
        tc = e.get("timecode", "")
        if not tc:
            continue
        text = (e.get("original", "") if is_source else (_target_text(e) or e.get("original", "")))
        text = (text or "").replace("\r", "").strip()
        if not text:
            continue
        in_cues.append((tc, text))
    if not in_cues:
        return list(parsed_data), base_stats

    srt_in = "\n\n".join(f"{i + 1}\n{tc}\n{txt}" for i, (tc, txt) in enumerate(in_cues)) + "\n"

    # 2. Run SubtitleEdit, re-read the (possibly re-segmented) output.
    fixed_content = _run_se_cli(srt_in)
    fixed = parse_srt(fixed_content)
    if not fixed:
        raise RuntimeError("SubtitleEdit output could not be parsed into cues.")

    # 3. Rebuild, re-attaching the counterpart track by timecode overlap.
    ranges = [(i, *_tc_ms_range(e.get("timecode", ""))) for i, e in enumerate(parsed_data)]

    new_data: List[Dict] = []
    old_used = set()  # >1 fixed cue mapping to one old cue ⇒ that old cue was split
    for nc in fixed:
        s, en = _tc_ms_range(nc["timecode"])
        m = _best_overlap_index(s, en, ranges)
        old = parsed_data[m] if (m is not None and 0 <= m < len(parsed_data)) else {}
        fixed_text = (nc.get("original") or "").strip()  # parse_srt stores text in 'original'
        first_for_old = (m is not None and m not in old_used)
        if m is not None:
            old_used.add(m)

        if is_source:
            had_translation = bool((old.get("translations") or {}) or old.get("translated"))
            carry = had_translation and first_for_old
            flagged = had_translation and not first_for_old
            entry = {
                "id": nc["id"],
                "timecode": nc["timecode"],
                "original": fixed_text,
                "translated": (old.get("translated") or "") if carry else "",
                "translations": dict(old.get("translations") or {}) if carry else {},
                "is_edited": False,
                "needs_review": flagged,
            }
            if flagged:
                entry["review_issues"] = "New line from a SubtitleEdit split — needs translation"
        else:
            had_target = bool(_target_text(old).strip())
            translations = dict(old.get("translations") or {})
            if had_target:
                translations[lang_code] = fixed_text
            else:
                translations.pop(lang_code, None)
            entry = {
                "id": nc["id"],
                "timecode": nc["timecode"],
                "original": old.get("original", "") or "",
                "translated": fixed_text if (lang_code == primary_code and had_target) else (old.get("translated") or ""),
                "translations": translations,
                "is_edited": True,
                "needs_review": False,
            }

        entry["src_hash"] = hashlib.sha256((entry["original"] or "").strip().encode("utf-8")).hexdigest()[:16]
        new_data.append(entry)

    stats = {
        "before": len(parsed_data),
        "after": len(new_data),
        "changed": len(new_data) != len(parsed_data),
        "track": track,
    }
    return new_data, stats
