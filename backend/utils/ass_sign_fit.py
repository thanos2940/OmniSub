"""
Fit translated on-screen text (signs, info cards, labels) inside the video frame.

Sign events are pinned with ``\\pos`` and the typesetter sized each line for the
*English* text. libass does not re-wrap text pinned that way to the room around the pin,
and Greek is typically 20-40 % longer, so lines run off the edge of the screen. This
runs on the export copy only (the editor's data is never touched):

* measure each translated sign with the export font (Arial for Greek, see
  ``ass_parser._ass_target_font``) at its real size;
* if it does not fit the horizontal room around its pin, break it into lines at word
  boundaries — unless the extra lines would run into another sign shown at the same
  time (card lines are separate events stacked at fixed positions), in which case the
  line is condensed horizontally (``\\fscx``) instead, down to a legibility floor and
  then by font size.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_POS = re.compile(r"\\pos\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")
_MOVE = re.compile(r"\\move\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)")
_AN = re.compile(r"\\an([1-9])")
_FS = re.compile(r"\\fs([\d.]+)")
_FSCX = re.compile(r"\\fscx([\d.]+)")
_BOLD = re.compile(r"\\b(\d)")

SAFE_MARGIN = 0.04       # fraction of the frame width kept clear on each side
LINE_HEIGHT = 1.2        # line pitch relative to font size
MAX_LINES = 4
MIN_FONT_FRACTION = 0.72  # smallest font size, as a fraction of the original
MIN_CONDENSE = 62        # never squeeze below this \fscx %
FALLBACK_EM = 0.60       # average glyph width / font size when no font can be loaded

_font_cache: Dict[bool, object] = {}


def _load_font(bold: bool):
    if bold in _font_cache:
        return _font_cache[bold]
    font = None
    try:
        from PIL import ImageFont
        names = (["arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if bold
                 else ["arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
        for name in names:
            try:
                font = ImageFont.truetype(name, 100)
                break
            except OSError:
                continue
    except Exception:
        font = None
    _font_cache[bold] = font
    return font


def text_width(text: str, fs: float, bold: bool = False) -> float:
    """Rendered width in script pixels of one line at font size ``fs``."""
    if not text:
        return 0.0
    font = _load_font(bold)
    if font is not None:
        try:
            return font.getlength(text) * fs / 100.0
        except Exception:
            pass
    return len(text) * FALLBACK_EM * fs


def wrap_words(text: str, fs: float, bold: bool, limit: float) -> Optional[List[str]]:
    """Greedy word wrap of ``text`` (existing newlines kept) to ``limit`` px, or None if a
    single word is wider than the limit."""
    out: List[str] = []
    for hard in text.split("\n"):
        words = hard.split()
        if not words:
            out.append("")
            continue
        line = words[0]
        if text_width(line, fs, bold) > limit:
            return None
        for w in words[1:]:
            trial = f"{line} {w}"
            if text_width(trial, fs, bold) <= limit:
                line = trial
            else:
                out.append(line)
                line = w
                if text_width(line, fs, bold) > limit:
                    return None
        out.append(line)
    return out


class _Item:
    __slots__ = ("ev", "prefix", "text", "suffix", "x", "y", "an", "fs", "fscx", "bold", "start", "end", "lines")

    def __init__(self, ev, prefix, text, suffix, x, y, an, fs, fscx, bold, start, end):
        self.ev, self.prefix, self.text, self.suffix = ev, prefix, text, suffix
        self.x, self.y, self.an, self.fs, self.fscx, self.bold = x, y, an, fs, fscx, bold
        self.start, self.end = start, end
        self.lines = text.count("\n") + 1


def _anchor(an: int) -> str:
    return "left" if an in (1, 4, 7) else "right" if an in (3, 6, 9) else "center"


def _vband(it: _Item, lines: int) -> Tuple[float, float]:
    """Vertical extent (top, bottom) of ``lines`` lines of this item around its pin."""
    h = lines * it.fs * LINE_HEIGHT
    if it.an in (1, 2, 3):
        return it.y - h, it.y
    if it.an in (4, 5, 6):
        return it.y - h / 2, it.y + h / 2
    return it.y, it.y + h


def _hspan(it: _Item, width: float) -> Tuple[float, float]:
    a = _anchor(it.an)
    if a == "left":
        return it.x, it.x + width
    if a == "right":
        return it.x - width, it.x
    return it.x - width / 2, it.x + width / 2


def _room(it: _Item, frame_w: float) -> float:
    m = frame_w * SAFE_MARGIN
    a = _anchor(it.an)
    if a == "left":
        return frame_w - m - it.x
    if a == "right":
        return it.x - m
    return 2 * min(it.x - m, frame_w - m - it.x)


def _widest(it: _Item) -> float:
    return max((text_width(l, it.fs, it.bold) for l in it.text.split("\n")), default=0.0) * it.fscx / 100.0


def fit_sign_events(subs, raw_items: List[Tuple]) -> int:
    """Wrap / condense translated sign events so they fit the frame.

    ``raw_items``: ``(event, prefix_tags, plain_text, suffix_tags, start_ms, end_ms)``
    for each translated sign event. Rewrites ``event.text`` where needed and returns how
    many events were changed.
    """
    try:
        frame_w = float(subs.info.get("PlayResX") or 384)
    except (TypeError, ValueError):
        frame_w = 384.0

    from utils.ass_parser import _plain_to_ass_text

    items: List[_Item] = []
    for ev, prefix, text, suffix, start, end in raw_items:
        pm = _POS.search(prefix) or _MOVE.search(prefix)
        if not pm or not text.strip():
            continue  # un-pinned events are wrapped by the player inside the margins
        style = subs.styles.get(ev.style)
        an_m = _AN.search(prefix)
        an = int(an_m.group(1)) if an_m else int(getattr(style, "alignment", 2) or 2)
        fs_all = _FS.findall(prefix)
        fs = float(fs_all[-1]) if fs_all else float(getattr(style, "fontsize", 40) or 40)
        fx = _FSCX.findall(prefix)
        fscx = float(fx[-1]) if fx else float(getattr(style, "scalex", 100) or 100)
        b = _BOLD.findall(prefix)
        bold = (b[-1] == "1") if b else bool(getattr(style, "bold", False))
        items.append(_Item(ev, prefix, text, suffix, float(pm.group(1)), float(pm.group(2)), an, fs, fscx, bold, start, end))

    changed = 0
    for it in items:
        room = _room(it, frame_w)
        if room <= 0 or _widest(it) <= room:
            continue

        # 1. Wrap into extra lines if that stays clear of simultaneous signs.
        limit = room / (it.fscx / 100.0)
        lines = wrap_words(it.text, it.fs, it.bold, limit)
        new_text, extra = None, None
        if lines and len(lines) <= MAX_LINES and not _collides(it, items, len(lines), lines, frame_w):
            new_text = "\n".join(lines)
        else:
            # 2. Condense horizontally, then shrink the font, on the original line structure.
            w = _widest(it)
            factor = max(MIN_CONDENSE, int(100.0 * room / w * it.fscx / 100.0))
            extra = f"\\fscx{factor}"
            still = w * factor / it.fscx
            if still > room:
                # shrink the font too, but never below a legible fraction of the original
                extra += f"\\fs{max(int(it.fs * MIN_FONT_FRACTION), int(it.fs * room / still))}"
            new_text = it.text
        it.ev.text = it.prefix + (("{" + extra + "}") if extra else "") + _plain_to_ass_text(new_text) + it.suffix
        changed += 1
    return changed


def _collides(it: _Item, items: List[_Item], n_lines: int, lines: List[str], frame_w: float) -> bool:
    """Would ``n_lines`` lines of ``it`` land on another sign visible at the same time?"""
    if n_lines <= it.lines:
        return False
    old_top, old_bot = _vband(it, it.lines)
    new_top, new_bot = _vband(it, n_lines)
    width = max(text_width(l, it.fs, it.bold) for l in lines) * it.fscx / 100.0
    x0, x1 = _hspan(it, width)
    for other in items:
        if other is it or other.start >= it.end or other.end <= it.start:
            continue
        o_top, o_bot = _vband(other, other.lines)
        # only the newly occupied strip matters
        grew_top, grew_bot = (new_top, old_top) if new_top < old_top else (old_bot, new_bot)
        if o_bot <= grew_top or o_top >= grew_bot:
            continue
        ox0, ox1 = _hspan(other, _widest(other))
        if ox1 > x0 and ox0 < x1:
            return True
    return False
