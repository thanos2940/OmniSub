"""
Subtitle format router.

A thin dispatch layer over the SRT and ASS adapters so the rest of the pipeline
can parse / reconstruct a subtitle file without branching on its extension. Both
adapters return entries in the same shape
(``{id, timecode, original, translated, is_edited, needs_review}``), so every
downstream consumer — scene chunking, translation memory, glossary, review UI —
works regardless of source format.
"""

from __future__ import annotations

from typing import List, Dict, Optional

from utils import srt_parser
from utils import ass_parser

SRT = "srt"
ASS = "ass"


def detect_format(filename: str = "", content: str = "") -> str:
    """Determine the subtitle format from a filename and/or its content.

    The filename extension wins when recognised; otherwise we sniff the content
    for an ASS section header. Defaults to SRT.
    """
    name = (filename or "").lower()
    if name.endswith((".ass", ".ssa")):
        return ASS
    if name.endswith(".srt"):
        return SRT

    head = (content or "").lstrip("﻿").lstrip()
    if head.startswith("[Script Info]") or "\n[Events]" in (content or ""):
        return ASS
    return SRT


def parse_subtitle(content: str, filename: str = "", fmt: Optional[str] = None) -> List[Dict]:
    """Parse subtitle content into pipeline entries, auto-detecting the format."""
    fmt = fmt or detect_format(filename, content)
    if fmt == ASS:
        return ass_parser.parse_ass(content)
    return srt_parser.parse_srt(content)


def reconstruct_subtitle(
    parsed_data: List[Dict],
    fmt: Optional[str] = None,
    filename: str = "",
    original_content: Optional[str] = None,
    font_scale: float = 1.0,
) -> str:
    """Reconstruct a subtitle file from pipeline entries.

    For ASS, pass ``original_content`` (the source .ass) to preserve styles,
    script info, comments and untranslated karaoke/drawing events.

    ``font_scale`` is forwarded to ``reconstruct_ass`` to adjust style font
    sizes for target languages whose glyphs render larger (e.g. Greek).
    """
    fmt = fmt or detect_format(filename, original_content or "")
    if fmt == ASS:
        return ass_parser.reconstruct_ass(parsed_data, original_content, font_scale=font_scale)
    return srt_parser.reconstruct_srt(parsed_data)
