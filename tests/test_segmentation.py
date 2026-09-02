import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.vision.segment import (  # noqa: E402
    Glyph,
    connected_components,
    drop_oversized,
    group_lines,
    merge_stacked,
)


def _blob(x, y, w, h) -> Glyph:
    return Glyph(x, y, w, h, np.ones((h, w), dtype=bool))


def test_stacked_text_lines_are_not_glued_together():
    # "y" above "x" at the same x span, one line apart: overlap alone would
    # fuse the whole WARDOGS readout into a single row of blobs.
    upper, lower = _blob(15, 17, 11, 19), _blob(15, 51, 11, 19)
    assert len(merge_stacked([upper, lower])) == 2


def test_an_accent_dot_is_fused_with_its_stem():
    stem, dot = _blob(10, 20, 4, 14), _blob(10, 14, 4, 3)
    merged = merge_stacked([dot, stem])
    assert len(merged) == 1
    assert (merged[0].y, merged[0].bottom) == (14, 34)


def test_connected_components_finds_separate_marks():
    mask = np.zeros((20, 40), dtype=bool)
    mask[5:15, 3:9] = True
    mask[5:15, 20:26] = True
    blobs = connected_components(mask)
    assert len(blobs) == 2
    assert [b.x for b in blobs] == [3, 20]


def test_diagonally_touching_pixels_are_one_blob():
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True
    mask[5:8, 5:8] = True
    assert len(connected_components(mask)) == 1


def test_group_lines_splits_two_rows():
    blobs = [_blob(0, 10, 8, 16), _blob(12, 10, 8, 16), _blob(0, 50, 8, 16)]
    lines = group_lines(blobs)
    assert [len(line) for line in lines] == [2, 1]


def test_drop_oversized_removes_map_icons():
    text = [_blob(i * 12, 10, 8, 16) for i in range(5)]
    icon = _blob(80, 0, 60, 60)
    assert icon not in drop_oversized(text + [icon])
    assert len(drop_oversized(text + [icon])) == 5
