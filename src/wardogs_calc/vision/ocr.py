"""Read an X/Y coordinate pair out of a HUD crop.

The game's "mark coordinates" key writes the pair into the chat input as one
line, ``x98.49, y110.30``, in near-white text on the panel's flat dark fill.
The channel chip and the send arrow sit on the same row, so recognition keys
off the ``x``/``y`` letters rather than position, and a crop is allowed to
carry neighbouring clutter.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import numpy as np

from ..ballistics import Point
from .glyphs import GlyphSet
from .segment import (
    binarize_variants,
    connected_components,
    drop_oversized,
    drop_text_cursor,
    group_lines,
    merge_stacked,
)

#: WARDOGS prints exactly two decimals and never more than three digits before
#: them (the map tops out at 163.84). Pinning the shape down this tightly is
#: what rejects a mangled "x00.005" that would otherwise parse as 0.005.
_VALUE = r"-?\d{1,3}[.,]\d{2}"

#: The lookbehind keeps a label from being read out of a larger token, so a
#: digit or letter in front of it disqualifies it. A full stop or comma does
#: not: the readout is one line, "x98.49, y110.30", and whether the comma is
#: followed by a space at all is decided by the segmenter's spacing heuristic,
#: not by the game — so the comma must never stand between us and the y.
#:
#: The trailing lookahead refuses a number that touches an unreadable glyph
#: ("?"): a half-read "x83.?2" must fail outright rather than degrade into 83.
_LABELLED = re.compile(
    r"(?<![0-9A-Za-z])([XY])\s*[:=]?\s*(" + _VALUE + r")(?![0-9?])",
    re.IGNORECASE,
)

#: Same, for a readout whose decimal point was lost to segmentation *and*
#: left a gap wide enough to read as a space, splitting the number in two.
#:
#: At 1080p the full stop is one or two lit pixels, so it goes missing on its
#: own merits, and the hole it leaves measures the same as the space after the
#: pair's comma -- both 5 px against a 4.7 px threshold on a real crop, so
#: they cannot be told apart by width. What can be told apart is the shape of
#: what is left: the readout always prints exactly two decimals, so a number
#: broken by one gap with exactly two digits after it can only be that number
#: with its point knocked out. Nothing else about the line has that shape.
_LABELLED_SPLIT = re.compile(
    r"(?<![0-9A-Za-z])([XY])\s*[:=]?\s*(-?\d{1,3})[ ](\d{2})(?![0-9?])",
    re.IGNORECASE,
)

#: Same, for a readout whose decimal point was lost to segmentation.
#:
#: The trailing separator test is ``(?![.,]\d)`` rather than ``(?![.,])``: a
#: separator only disqualifies the match when digits follow it, which is what
#: tells this number's own decimal point apart from the comma between the
#: pair. Refusing every following comma disabled this fallback in exactly the
#: case it exists for -- the readout is "x98.49, y110.30", so a reading that
#: lost its decimal points still carries that comma, and "x9849, y11030" was
#: refused while "x9849 y11030" parsed. The full stop is 2x2 px where the
#: comma is 2x4, so the stops go first and the comma is left standing.
_LABELLED_NO_DOT = re.compile(
    r"(?<![0-9A-Za-z])([XY])\s*[:=]?\s*(-?\d{3,5})(?![0-9?])(?![.,]\d)",
    re.IGNORECASE,
)


@dataclass
class Reading:
    point: Point
    text: str
    confidence: float
    #: Which binarisation and which font group read it. Diagnostics only, but
    #: the pair is what tells you why an unfamiliar font fails.
    variant: str = ""
    group: str = ""


def _line_metrics(line) -> tuple[list[float], float | None]:
    """Height ratios per glyph and the gap that counts as a space."""
    heights = [g.height for g in line]
    # 75th percentile, not the median: on a short line like "x83.12" a couple
    # of full stops would drag a median down and make the digits look oversized.
    line_height = float(np.percentile(heights, 75)) or 1.0
    ratios = [g.height / line_height for g in line]

    gaps = [b.x - a.right for a, b in zip(line, line[1:])]
    median_w = float(np.median([g.width for g in line])) or 1.0
    # A space is a gap clearly wider than this font's normal letter spacing;
    # a fixed fraction of glyph width misfires on loosely spaced faces and
    # splits "12" into "1 2".
    space_gap = (
        max(0.55 * median_w, float(np.median(gaps)) + 0.45 * median_w) if gaps else None
    )
    return ratios, space_gap


def _render_line(line, matches, space_gap: float | None) -> tuple[str, float]:
    """Characters of one line, plus its weakest match margin.

    Characters the matcher will not vouch for come back as ``?`` so the parser
    can refuse anything they touch.
    """
    chunks: list[str] = []
    worst = 1.0
    for i, (glyph, match) in enumerate(zip(line, matches)):
        if i and space_gap is not None and (glyph.x - line[i - 1].right) > space_gap:
            chunks.append(" ")
        if match.confident:
            chunks.append(match.label)
            worst = min(worst, match.margin)
        else:
            chunks.append("?")
    return "".join(chunks), worst


#: merge_stacked compares every blob with every other, so a mask full of
#: texture rather than text needs turning away before it — generously, since
#: this is a runaway guard and not a judgement about the content.
_MAX_BLOBS = 600

#: The readout is "x98.49, y110.30" — fifteen characters or so. Matching is
#: the expensive stage, roughly 9 ms per glyph against the whole bank, so
#: lines that cannot be the readout are dropped before it rather than decoded
#: and discarded afterwards.
_MIN_LINE_GLYPHS = 4
_MAX_LINE_GLYPHS = 32


def _lines_from_mask(
    mask: np.ndarray, glyphs: GlyphSet
) -> tuple[list[tuple[str, float]], str]:
    """Recognised text lines and the template group they were read with."""
    # min_pixels=2: a decimal point in a 19 px readout is barely 2x2 px, and
    # losing it turns "x83.12" into an unparseable "x8312".
    blobs = connected_components(mask, min_pixels=2)
    if len(blobs) > _MAX_BLOBS:
        return [], ""
    lines = [
        line
        for line in group_lines(drop_text_cursor(drop_oversized(merge_stacked(blobs))))
        if _MIN_LINE_GLYPHS <= len(line) <= _MAX_LINE_GLYPHS
    ]
    if not lines:
        return [], ""
    metrics = [_line_metrics(line) for line in lines]
    group, per_line = glyphs.decode_lines(
        lines, [ratios for ratios, _gap in metrics]
    )
    rendered = [
        _render_line(line, matches, gap)
        for line, matches, (_ratios, gap) in zip(lines, per_line, metrics)
    ]
    return rendered, group


def recognise_text(image: np.ndarray, glyphs: GlyphSet) -> list[tuple[str, float]]:
    """Text lines from the best-looking binarisation of ``image``."""
    if image.size == 0 or not len(glyphs):
        return []
    variants = binarize_variants(image)
    if not variants:
        return []
    return _lines_from_mask(variants[0][1], glyphs)[0]


def _in_range(value: float, low: float, high: float) -> float | None:
    return value if low <= value <= high else None


def _repair_decimal(raw: str, low: float, high: float) -> float | None:
    """Read a value whose decimal point was lost to segmentation.

    The point always goes back two digits from the right, and the literal
    reading is never used.  The readout prints exactly two decimals, so a
    dot-less ``11021`` is ``110.21`` and a dot-less ``110`` is ``1.10`` --
    which is off the map and refused.  Taking ``110`` at face value instead
    was a way to report 110.00 for a target at 110.21: in range, plausible,
    and 21 m out.
    """
    if "." in raw or "," in raw:
        return _in_range(float(raw.replace(",", ".")), low, high)
    digits = raw.lstrip("-")
    if len(digits) < 3:
        return None
    patched = float(f"{digits[:-2]}.{digits[-2:]}")
    if raw.startswith("-"):
        patched = -patched
    return _in_range(patched, low, high)


def parse_coordinates(
    text: str,
    valid_x: tuple[float, float] = (0.0, 163.84),
    valid_y: tuple[float, float] = (0.0, 163.84),
) -> Point | None:
    """Pull an X/Y pair out of recognised text.

    Both labels are required. There is deliberately no "just take the first
    two numbers" fallback: the chat line reads x before y while the map used
    to print y first, so any fallback resting on reading order would sooner or
    later swap the axes — a plausible, in-range, completely wrong solution.

    The two axes are bounded separately: the playable strip is much narrower
    than the terrain, and most misreads land outside it.

    Each axis is taken from the dotted form where it has one and the
    dot-less form otherwise, rather than requiring both axes to have
    degraded the same way. Segmentation loses a 2x2 px full stop on its own
    merits, so one axis keeping its decimal point while the other drops it is
    the ordinary case, not a corner one.
    """
    labelled: dict[str, str] = {}
    # Most specific first: a dotted reading is taken as it stands, a reading
    # split by the gap its lost point left is rejoined around that gap, and
    # only then is a bare run of digits given a point two from the right.
    # Order decides which reading of "y110 21" wins, and only the middle one
    # is right.
    for pattern in (_LABELLED, _LABELLED_SPLIT, _LABELLED_NO_DOT):
        for match in pattern.finditer(text):
            groups = match.groups()
            value = groups[1] if len(groups) < 3 else f"{groups[1]}.{groups[2]}"
            labelled.setdefault(match.group(1).upper(), value)
    if "X" in labelled and "Y" in labelled:
        x = _repair_decimal(labelled["X"], *valid_x)
        y = _repair_decimal(labelled["Y"], *valid_y)
        if x is not None and y is not None:
            return Point(x, y)
    return None


def _readings_from(
    image: np.ndarray,
    glyphs: GlyphSet,
    valid_x: tuple[float, float],
    valid_y: tuple[float, float],
) -> list[Reading]:
    """Every binarisation's opinion of what the crop says."""
    out: list[Reading] = []
    for name, mask in binarize_variants(image):
        lines, group = _lines_from_mask(mask, glyphs)
        if not lines:
            continue
        # Each line alone first, then the whole block, so the "y..." line and
        # the "x..." line pair up even though they sit on separate rows.
        candidates = list(lines)
        if len(lines) > 1:
            candidates.append(
                (" ".join(t for t, _c in lines), min(c for _t, c in lines))
            )
        for text, confidence in candidates:
            point = parse_coordinates(text, valid_x, valid_y)
            if point is not None:
                out.append(
                    Reading(point, text.strip(), confidence, variant=name, group=group)
                )
                break
    return out


def read_coordinates(
    image: np.ndarray,
    glyphs: GlyphSet,
    valid_x: tuple[float, float] = (0.0, 163.84),
    valid_y: tuple[float, float] = (0.0, 163.84),
    min_votes: int = 2,
) -> Reading | None:
    """Best-effort coordinate reading, or None when nothing plausible is there.

    Every binarisation gets a vote and the majority wins. Taking the first
    threshold that merely *parses* is not safe: an aggressive one clips the
    tail off a 9, reads it as a 0, and hands back a well-formed wrong number.
    Thresholds that mangle glyphs disagree with each other, while thresholds
    that read correctly all agree — so agreement is the signal.

    Returning None is what lets the hotkeys sit on keys the game also uses:
    pressing one with no coordinates on screen reads nothing and the stored
    point is left untouched.
    """
    if image.size == 0 or not len(glyphs):
        return None

    readings = _readings_from(image, glyphs, valid_x, valid_y)
    if not readings:
        return None
    votes = Counter((r.point.x, r.point.y) for r in readings)
    winner, count = votes.most_common(1)[0]
    if count < min(min_votes, len(readings)):
        return None
    agreeing = [r for r in readings if (r.point.x, r.point.y) == winner]
    return max(agreeing, key=lambda r: r.confidence)
