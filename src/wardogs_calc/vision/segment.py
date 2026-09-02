"""Turn a small HUD crop into individual glyph bitmaps.

Deliberately dependency-light: numpy only.  The crops we work on are a few
hundred pixels wide, so a plain scanline union-find is comfortably fast and
saves ~35 MB of OpenCV in the packaged exe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Glyph:
    """One connected blob, plus its normalised bitmap."""

    x: int
    y: int
    width: int
    height: int
    mask: np.ndarray  # bool, shape (height, width)

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def cy(self) -> float:
        return self.y + self.height / 2.0


def to_gray(rgb: np.ndarray) -> np.ndarray:
    if rgb.ndim == 2:
        return rgb.astype(np.float32)
    return (
        0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    ).astype(np.float32)


def otsu_threshold(gray: np.ndarray) -> float:
    hist, edges = np.histogram(gray, bins=256, range=(0.0, 255.0))
    total = gray.size
    if total == 0:
        return 128.0
    centers = (edges[:-1] + edges[1:]) / 2.0
    weight_bg = np.cumsum(hist)
    weight_fg = total - weight_bg
    sum_all = float((hist * centers).sum())
    sum_bg = np.cumsum(hist * centers)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_bg = np.where(weight_bg > 0, sum_bg / np.maximum(weight_bg, 1), 0.0)
        mean_fg = np.where(
            weight_fg > 0, (sum_all - sum_bg) / np.maximum(weight_fg, 1), 0.0
        )
        variance = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
    variance = np.nan_to_num(variance)
    return float(centers[int(np.argmax(variance))])


def binarize(rgb: np.ndarray, invert: bool | None = None) -> np.ndarray:
    """Boolean mask where True marks glyph pixels, polarity chosen by Otsu.

    ``invert=None`` assumes text covers the minority of the crop — true for
    any HUD readout.
    """
    gray = to_gray(rgb)
    thr = otsu_threshold(gray)
    bright = gray > thr
    if invert is None:
        invert = bright.mean() > 0.5
    return ~bright if invert else bright


#: A readout is one or two short lines: roughly 600 set pixels at the size the
#: game draws it. Both bounds are absolute counts rather than shares of the
#: crop, because the text keeps its size however generously the box is drawn.
#:
#: The ceiling is what keeps the app responsive. Labelling is a per-set-pixel
#: Python loop, so a mask carrying terrain instead of text costs seconds: a
#: box holding sky and brick reached 67k set pixels and three seconds. Ten
#: times the text is already far more than any readout, and rejecting the mask
#: on a numpy sum costs nothing.
_MIN_TEXT_PIXELS = 40
_MAX_TEXT_PIXELS = 6000


def binarize_variants(rgb: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Several plausible text masks, best-guess first.

    The readout is near-white text on the chat panel's flat dark fill, so a
    high absolute cut isolates it far better than Otsu — Otsu on such a crop
    tends to split the panel instead of the text.  We hand the caller a
    shortlist and let recognition decide which one actually parsed.
    """
    gray = to_gray(rgb)
    variants: list[tuple[str, np.ndarray]] = []
    for cut in (225.0, 205.0, 240.0, 180.0):
        mask = gray > cut
        if _MIN_TEXT_PIXELS <= mask.sum() <= _MAX_TEXT_PIXELS:
            variants.append((f"bright>{cut:.0f}", mask))
    # Otsu earns a place even over the ceiling: it splits the crop into halves
    # by its own histogram, so on a dark chat panel it isolates the text when
    # every absolute cut has missed it.
    thr = otsu_threshold(gray)
    otsu_bright = gray > thr
    otsu = ~otsu_bright if otsu_bright.mean() > 0.5 else otsu_bright
    if otsu.sum() <= _MAX_TEXT_PIXELS:
        variants.append(("otsu", otsu))
    return variants


def drop_oversized(glyphs: list["Glyph"], factor: float = 2.6) -> list["Glyph"]:
    """Remove blobs far taller than the typical one — map icons, not letters."""
    if len(glyphs) < 3:
        return glyphs
    median_h = float(np.median([g.height for g in glyphs]))
    if median_h <= 0:
        return glyphs
    return [g for g in glyphs if g.height <= factor * median_h]


def connected_components(mask: np.ndarray, min_pixels: int = 4) -> list[Glyph]:
    """Label 8-connected blobs with a scanline union-find."""
    height, width = mask.shape
    labels = np.zeros((height, width), dtype=np.int32)
    parent: list[int] = [0]

    def find(a: int) -> int:
        root = a
        while parent[root] != root:
            root = parent[root]
        while parent[a] != root:  # path compression
            parent[a], a = root, parent[a]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for y in range(height):
        row = mask[y]
        if not row.any():
            continue
        for x in np.flatnonzero(row):
            neighbours = []
            if x > 0 and labels[y, x - 1]:
                neighbours.append(labels[y, x - 1])
            if y > 0:
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < width and labels[y - 1, nx]:
                        neighbours.append(labels[y - 1, nx])
            if neighbours:
                label = min(neighbours)
                labels[y, x] = label
                for other in neighbours:
                    union(label, other)
            else:
                parent.append(len(parent))
                labels[y, x] = len(parent) - 1

    if len(parent) == 1:
        return []

    remap = np.array([find(i) for i in range(len(parent))], dtype=np.int32)
    flat = remap[labels]

    # Group the set pixels by label with one scan and one sort. Testing each
    # label against the whole image instead costs labels x pixels, which on
    # noisy content means a thousand full-frame comparisons — measured at
    # 960 ms for a crop holding 1071 lit pixels and nine real blobs.
    ys, xs = np.nonzero(flat)
    if ys.size == 0:
        return []
    ids = flat[ys, xs]
    order = np.argsort(ids, kind="stable")
    ids, ys, xs = ids[order], ys[order], xs[order]
    bounds = np.concatenate(([0], np.flatnonzero(np.diff(ids)) + 1, [ids.size]))

    glyphs: list[Glyph] = []
    for begin, end in zip(bounds[:-1], bounds[1:]):
        if end - begin < min_pixels:
            continue
        blob_ys, blob_xs = ys[begin:end], xs[begin:end]
        y0, y1 = int(blob_ys.min()), int(blob_ys.max()) + 1
        x0, x1 = int(blob_xs.min()), int(blob_xs.max()) + 1
        mask_of = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        mask_of[blob_ys - y0, blob_xs - x0] = True
        glyphs.append(Glyph(x=x0, y=y0, width=x1 - x0, height=y1 - y0, mask=mask_of))
    glyphs.sort(key=lambda g: (g.x, g.y))
    return glyphs


def merge_stacked(
    glyphs: list[Glyph], overlap: float = 0.6, max_gap: float = 0.45
) -> list[Glyph]:
    """Fuse vertically split blobs — an ``i`` dot, a stroke broken by aliasing.

    Two blobs merge when their horizontal spans overlap by ``overlap`` of the
    narrower one *and* one sits clear above the other by less than ``max_gap``
    of the taller.  Both tests earn their keep: the gap ceiling keeps text on
    one row from fusing with text on the row above it, and the requirement
    that the two be vertically disjoint stops a decimal point from fusing into
    the digit beside it.
    """
    if not glyphs:
        return []
    merged: list[Glyph] = []
    used = [False] * len(glyphs)
    for i, g in enumerate(glyphs):
        if used[i]:
            continue
        x0, y0, x1, y1 = g.x, g.y, g.right, g.bottom
        group = [g]
        changed = True
        while changed:  # a merge can bring a third fragment into reach
            changed = False
            for j in range(len(glyphs)):
                if used[j] or j == i:
                    continue
                h = glyphs[j]
                span = min(x1, h.right) - max(x0, h.x)
                if span <= overlap * min(x1 - x0, h.width):
                    continue
                # Merge only marks that sit one ABOVE the other: an i-dot, a
                # stroke split by antialiasing. A full stop beside a digit
                # overlaps it vertically, and fusing those two eats the
                # decimal point out of "x83.12".
                separation = max(h.y - y1, y0 - h.bottom)
                tallest = max(y1 - y0, h.height)
                if not (-0.12 * tallest <= separation <= max_gap * tallest):
                    continue
                used[j] = True
                group.append(h)
                x0, x1 = min(x0, h.x), max(x1, h.right)
                y0, y1 = min(y0, h.y), max(y1, h.bottom)
                changed = True
        used[i] = True
        if len(group) == 1:
            merged.append(g)
            continue
        canvas = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        for part in group:
            canvas[part.y - y0 : part.bottom - y0, part.x - x0 : part.right - x0] |= part.mask
        merged.append(Glyph(x0, y0, x1 - x0, y1 - y0, canvas))
    merged.sort(key=lambda g: g.x)
    return merged


def group_lines(glyphs: list[Glyph], tolerance: float = 0.5) -> list[list[Glyph]]:
    """Split glyphs into text lines by vertical centre proximity."""
    if not glyphs:
        return []
    ordered = sorted(glyphs, key=lambda g: g.cy)
    median_h = float(np.median([g.height for g in ordered]))
    lines: list[list[Glyph]] = [[ordered[0]]]
    for g in ordered[1:]:
        if abs(g.cy - lines[-1][-1].cy) <= tolerance * median_h:
            lines[-1].append(g)
        else:
            lines.append([g])
    for line in lines:
        line.sort(key=lambda g: g.x)
    return lines
