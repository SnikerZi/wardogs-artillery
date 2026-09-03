import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.vision.segment import (  # noqa: E402
    Glyph,
    binarize_variants,
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


# --- a screen that draws the HUD smaller than the font bank was built on ---


def _line_crop(lit_pixels, size=(140, 499), fill=24, ink=245):
    """A dark panel crop carrying `lit_pixels` bright pixels."""
    crop = np.full(size, float(fill))
    flat = crop.reshape(-1)
    flat[: lit_pixels] = ink
    return np.repeat(crop[:, :, None], 3, axis=2).astype(np.uint8)


def test_the_floor_no_longer_deletes_a_faint_line():
    """22 lit pixels is what a 0.75x render leaves in the higher cuts.

    The old floor of 40 threw those variants away and left recognition with
    one opinion, which is also the one thing its cross-threshold agreement
    check cannot work with.
    """
    names = [name for name, _ in binarize_variants(_line_crop(22))]
    assert len(names) >= 3, names
    assert any(name.startswith("bright>2") for name in names), names


def test_a_crop_with_almost_nothing_lit_offers_no_cut_variant():
    """Otsu is deliberately exempt from the floor; the cuts are not."""
    variants = binarize_variants(_line_crop(3))
    cuts = [name for name, _mask in variants if name != "otsu"]
    assert cuts == [], cuts


def test_percentile_cuts_follow_the_text_down():
    """Text peaking well below 180 is invisible to every absolute cut."""
    crop = np.full((60, 200), 20.0)
    crop[20:32, 10:150:3] = 150.0          # faint strokes, under every cut
    rgb = np.repeat(crop[:, :, None], 3, axis=2).astype(np.uint8)
    names = [name for name, _ in binarize_variants(rgb)]
    assert any(name.startswith("p") for name in names), names


def test_scenery_still_produces_no_usable_mask():
    """The percentile cuts must not turn terrain into a candidate line."""
    rng = np.random.default_rng(4)
    scene = np.linspace(200, 90, 140)[:, None] + rng.normal(0, 18, (140, 499))
    rgb = np.repeat(np.clip(scene, 0, 255)[:, :, None], 3, axis=2).astype(np.uint8)
    for _name, mask in binarize_variants(rgb):
        assert mask.sum() <= 6000
