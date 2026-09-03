"""The readout as a 1920x1080 screen actually draws it.

A real crop, off a real screen, kept because nothing synthetic reproduced the
problem it locks down: at 1080p the game draws its digits 6x11 where the
original template bank was captured at 9x13 — narrower relative to their
height, not merely smaller, because the rasteriser snaps stems to the pixel
grid at that size. Templates normalise onto a square, so a different aspect
is a different shape, and the 1440p bank read this line as '.0000 .y.y0062'.

The fixture is the app's own second attempt at the crop, which is what a read
falls back to; the tighter box the user had saved clips the leading 'x'.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.vision.glyphs import GlyphSet, bundled_glyph_file  # noqa: E402
from wardogs_calc.vision.ocr import read_coordinates  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "readout_1080p.png"
#: What the line in the fixture says.
TRUTH = (99.09, 109.62)
#: The shipped bounds, from Config.valid_x / valid_y.
VALID_X = (20.0, 147.0)
VALID_Y = (16.0, 133.0)


@pytest.fixture(scope="module")
def crop():
    Image = pytest.importorskip("PIL.Image", reason="Pillow reads the fixture")
    return np.array(Image.open(FIXTURE).convert("RGB"))


@pytest.fixture(scope="module")
def bank():
    glyphs = GlyphSet.load(bundled_glyph_file(ROOT / "src" / "wardogs_calc"))
    glyphs.set_min_margin(0.05)
    return glyphs


def test_the_bank_covers_both_render_sizes(bank):
    assert "wardogs" in bank.groups
    assert "wardogs-1080" in bank.groups


def test_every_character_of_the_readout_is_in_the_1080_group(bank):
    labels = {t.label for t in bank.templates if t.group == "wardogs-1080"}
    assert labels == set(".0123456789xy")


def test_a_1080p_readout_reads(crop, bank):
    reading = read_coordinates(crop, bank, VALID_X, VALID_Y)
    assert reading is not None, "the 1080p crop must read"
    assert (reading.point.x, reading.point.y) == pytest.approx(TRUTH)


def test_it_reads_with_the_group_built_for_that_size(crop, bank):
    """Not by luck off the 1440p templates: the line commits to one group."""
    reading = read_coordinates(crop, bank, VALID_X, VALID_Y)
    assert reading.group == "wardogs-1080"


def test_the_1440p_bank_alone_cannot_read_it(crop):
    """Guards the fixture: without the new group this is the failing case."""
    old = GlyphSet(
        [t for t in GlyphSet.load(bundled_glyph_file(ROOT / "src" / "wardogs_calc")).templates
         if t.group != "wardogs-1080"]
    )
    old.set_min_margin(0.05)
    assert read_coordinates(crop, old, VALID_X, VALID_Y) is None


# --- what a lost decimal point and a text caret do to a 1080p line ---------

SPLIT = Path(__file__).resolve().parent / "fixtures" / "readout_1080p_split.png"
CARET = Path(__file__).resolve().parent / "fixtures" / "readout_1080p_caret.png"


def _crop(path):
    Image = pytest.importorskip("PIL.Image", reason="Pillow reads the fixture")
    return np.array(Image.open(path).convert("RGB"))


def test_a_number_split_by_its_lost_decimal_point_reads(bank):
    """`x98.77, y110.21`, whose two full stops segmentation never found.

    At this size a full stop is one or two lit pixels, and the hole it leaves
    measures the same as the space after the pair's comma -- so the line comes
    through as "x98 77. y110 21" and has to be rejoined around the gaps.
    """
    reading = read_coordinates(_crop(SPLIT), bank, VALID_X, VALID_Y)
    assert reading is not None
    assert (reading.point.x, reading.point.y) == pytest.approx((98.77, 110.21))


def test_the_chat_caret_is_not_read_as_a_digit(bank):
    """`x98.84, y110.18` with the input cursor standing after it.

    The caret is a 1x18 bar where the text is 11 px, and matched as a glyph it
    came back as a 9 -- making "y110.18" into "y110189", six digits with no
    separator, which nothing can parse.
    """
    reading = read_coordinates(_crop(CARET), bank, VALID_X, VALID_Y)
    assert reading is not None
    assert (reading.point.x, reading.point.y) == pytest.approx((98.84, 110.18))
