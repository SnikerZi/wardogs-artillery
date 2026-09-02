"""End-to-end check of segmentation -> glyph matching -> parsing.

Uses a rendered stand-in for the WARDOGS HUD: near-white text over a noisy
grey background, laid out as the game does it (``y..`` above, ``x..`` below).
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from wardogs_calc.vision.glyphs import GlyphSet  # noqa: E402
from wardogs_calc.vision.ocr import read_coordinates  # noqa: E402
from wardogs_calc.vision.segment import (  # noqa: E402
    binarize_variants,
    connected_components,
    drop_oversized,
    merge_stacked,
)

ALPHABET = "0123456789.xy"
TEXT_RGB = (245, 245, 245)


def _font():
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, 26)
        except OSError:
            continue
    return ImageFont.load_default()


FONT = _font()


def _render(lines, noisy=True, pad=14):
    width, height = 240, 40 + 34 * len(lines)
    rng = np.random.default_rng(7)
    if noisy:
        # Stand-in for the grey satellite map underneath the readout.
        base = rng.integers(95, 165, size=(height, width, 3), dtype=np.uint8)
    else:
        base = np.full((height, width, 3), 120, dtype=np.uint8)
    image = Image.fromarray(base)
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((pad, 12 + 34 * i), line, font=FONT, fill=TEXT_RGB)
    return np.asarray(image, dtype=np.uint8)


def _render_chat_line(text="x98.49, y110.30", width=480, height=60):
    """The chat input as the game draws it: one line on a flat dark panel."""
    image = Image.fromarray(np.full((height, width, 3), 28, dtype=np.uint8))
    ImageDraw.Draw(image).text((20, height // 2 - 17), text, font=FONT, fill=TEXT_RGB)
    return np.asarray(image, dtype=np.uint8)


def _train_alphabet() -> GlyphSet:
    """Mirror what the in-app trainer does: learn every binarisation variant.

    Templates taken from one threshold and matched against another is the
    failure mode this guards against.
    """
    glyphs = GlyphSet()
    for char in ALPHABET:
        crop = _render([char], noisy=False, pad=8)
        for _name, mask in binarize_variants(crop):
            blobs = drop_oversized(merge_stacked(connected_components(mask, min_pixels=2)))
            if not blobs:
                continue
            glyphs.add(char, max(blobs, key=lambda g: g.mask.sum()))
    assert glyphs.labels == set(ALPHABET)
    return glyphs


@pytest.fixture(scope="module")
def trained() -> GlyphSet:
    return _train_alphabet()


def test_reads_the_two_line_readout(trained):
    image = _render(["y67.91", "x83.12"])
    reading = read_coordinates(image, trained)
    assert reading is not None, "coordinates were not recognised"
    assert (reading.point.x, reading.point.y) == pytest.approx((83.12, 67.91))


def test_survives_neighbouring_hud_clutter(trained):
    image = _render(["y67.91", "1 107", "x83.12"])
    reading = read_coordinates(image, trained)
    assert reading is not None
    assert (reading.point.x, reading.point.y) == pytest.approx((83.12, 67.91))


def test_reads_the_chat_line(trained):
    reading = read_coordinates(_render_chat_line(), trained)
    assert reading is not None, "the chat line was not read"
    assert (reading.point.x, reading.point.y) == pytest.approx((98.49, 110.30))


def test_a_generous_box_keeps_more_than_one_binarisation(trained):
    """The agreement vote must survive a loosely drawn box.

    One line of text covers a fixed few hundred pixels, so a relative floor on
    mask coverage drops every bright variant once the box gets large, leaving
    Otsu alone — and a single variant makes read_coordinates agree with itself.
    """
    wide = _render_chat_line(width=1456, height=411)
    assert len(binarize_variants(wide)) >= 2
    reading = read_coordinates(wide, trained)
    assert reading is not None
    assert (reading.point.x, reading.point.y) == pytest.approx((98.49, 110.30))


def test_returns_none_when_the_map_is_closed(trained):
    rng = np.random.default_rng(3)
    noise = rng.integers(60, 150, size=(90, 240, 3), dtype=np.uint8)
    assert read_coordinates(noise, trained) is None


def test_untrained_glyph_set_reads_nothing():
    assert read_coordinates(_render(["y67.91", "x83.12"]), GlyphSet()) is None


def test_glyph_set_survives_a_save_load_round_trip(trained, tmp_path):
    path = tmp_path / "glyphs.json"
    trained.save(path)
    reloaded = GlyphSet.load(path)
    assert len(reloaded) == len(trained)
    reading = read_coordinates(_render(["y67.91", "x83.12"]), reloaded)
    assert reading is not None
    assert (reading.point.x, reading.point.y) == pytest.approx((83.12, 67.91))


def test_a_crop_full_of_texture_is_turned_away_quickly(trained):
    """Terrain instead of a chat panel must cost milliseconds, not seconds.

    Labelling is a per-pixel Python loop and merging compares every blob with
    every other, so an unbounded crop of game world used to cost ~9 s per
    hotkey press. The read must still refuse it — just cheaply.
    """
    rng = np.random.default_rng(4)
    world = rng.integers(90, 250, size=(411, 787, 3), dtype=np.uint8)
    world[:130] = rng.integers(200, 255, size=(130, 787, 3), dtype=np.uint8)

    started = time.perf_counter()
    assert read_coordinates(world, trained) is None
    assert (time.perf_counter() - started) < 1.0
