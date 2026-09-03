import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.vision.ocr import parse_coordinates  # noqa: E402

RANGE = (0.0, 163.84)


def test_reads_the_chat_line_form():
    # What the game's "mark coordinates" key puts in the chat input, and the
    # only form the app actually reads.
    point = parse_coordinates("x98.49, y110.30", RANGE)
    assert (point.x, point.y) == pytest.approx((98.49, 110.30))


def test_the_separator_may_not_be_followed_by_a_space():
    # Whether a space lands after the comma is decided by the segmenter's
    # spacing heuristic, not by the game, so both forms have to read.
    for text in ("x98.49,y110.30", "x98.49.y110.30", "x98.49,  y110.30"):
        point = parse_coordinates(text, RANGE)
        assert point is not None, text
        assert (point.x, point.y) == pytest.approx((98.49, 110.30))


def test_reads_the_wardogs_two_line_form():
    point = parse_coordinates("y67.91 x83.12", RANGE)
    assert (point.x, point.y) == pytest.approx((83.12, 67.91))


def test_label_order_does_not_matter():
    assert parse_coordinates("x83.12 y67.91", RANGE) == parse_coordinates("y67.91 x83.12", RANGE)


def test_ignores_neighbouring_hud_numbers():
    # "1 RNG 107 m" sits right next to the readout on the real map.
    point = parse_coordinates("4 1 RNG 107 m y67.91 19 m x83.12 [2/13]", RANGE)
    assert (point.x, point.y) == pytest.approx((83.12, 67.91))


def test_recovers_a_lost_decimal_point():
    point = parse_coordinates("y6791 x8312", RANGE)
    assert (point.x, point.y) == pytest.approx((83.12, 67.91))


def test_keeps_a_valid_reading_untouched():
    point = parse_coordinates("x9.50 y12.00", RANGE)
    assert (point.x, point.y) == pytest.approx((9.5, 12.0))


def test_rejects_values_off_the_map():
    assert parse_coordinates("x999.9 y500.0", RANGE) is None


def test_rejects_text_without_a_pair():
    assert parse_coordinates("RNG 107 m", RANGE) is None
    assert parse_coordinates("", RANGE) is None


def test_an_unlabelled_pair_is_refused():
    # WARDOGS prints y above x. A "just take the first two numbers" fallback
    # would read that pair in the wrong order and hand back a swapped, plausible,
    # completely wrong firing solution — so both labels are mandatory.
    assert parse_coordinates("83.12, 67.91", RANGE) is None
    assert parse_coordinates("67.91 83.12", RANGE) is None


def test_comma_decimal_separator():
    point = parse_coordinates("x83,12 y67,91", RANGE)
    assert (point.x, point.y) == pytest.approx((83.12, 67.91))


def test_rejects_a_number_touching_an_unreadable_glyph():
    # "?" marks a glyph the matcher refused to vouch for. Degrading
    # "x83.?2" into "83" would hand the player a wrong firing range.
    assert parse_coordinates("y67.91 x83.?2", RANGE) is None
    assert parse_coordinates("y67.9? x83.12", RANGE) is None
    assert parse_coordinates("y?7.91 x83.12", RANGE) is None


def test_clutter_before_the_label_is_tolerated():
    # R, N, G and m are not in the trained alphabet, so they come back as "?".
    point = parse_coordinates("1 ??? 107 ? y67.91 ?x83.12", RANGE)
    assert (point.x, point.y) == pytest.approx((83.12, 67.91))


def test_requires_both_axes():
    assert parse_coordinates("x83.12", RANGE) is None
    assert parse_coordinates("y67.91", RANGE) is None


def test_requires_exactly_two_decimals():
    # The readout always prints hundredths. Anything else came out of a
    # mangled binarisation: "x00.005" is a clipped "x90.05", not a position.
    assert parse_coordinates("y100.99 x00.005", RANGE) is None
    assert parse_coordinates("y67.9 x83.12", RANGE) is None
    assert parse_coordinates("y67.912 x83.12", RANGE) is None


def test_rejects_more_than_three_leading_digits():
    # The map tops out at 163.84, so a fourth digit is a misread.
    assert parse_coordinates("y1067.91 x83.12", RANGE) is None


# --- per-axis bounds -------------------------------------------------------

PLAYABLE_X = (20.0, 147.0)
PLAYABLE_Y = (16.0, 133.0)


def test_each_axis_is_bounded_separately():
    # x reaches 147 on Ozeti, y never gets near it — one shared range would
    # have to accept the union and would let this through.
    assert parse_coordinates("y140.00 x140.00", PLAYABLE_X, PLAYABLE_Y) is None
    point = parse_coordinates("y120.00 x140.00", PLAYABLE_X, PLAYABLE_Y)
    assert (point.x, point.y) == pytest.approx((140.0, 120.0))


def test_a_clipped_leading_digit_lands_outside_the_playable_area():
    # "y9.07" is what a mangled "y99.07" looks like; no player stands there.
    assert parse_coordinates("y9.07 x83.12", PLAYABLE_X, PLAYABLE_Y) is None
    assert parse_coordinates("y07.01 x80.12", PLAYABLE_X, PLAYABLE_Y) is None


def test_real_positions_still_read():
    for text, expected in (
        ("y67.91 x83.12", (83.12, 67.91)),
        ("y30.15 x45.20", (45.20, 30.15)),
        ("y110.04 x120.66", (120.66, 110.04)),
    ):
        point = parse_coordinates(text, PLAYABLE_X, PLAYABLE_Y)
        assert (point.x, point.y) == pytest.approx(expected)


# --- degrading gracefully as the glyphs get smaller ------------------------
#
# A full stop is 2x2 px where the pair's comma is 2x4, so segmentation loses
# the decimal points first and leaves the comma standing. That is the shape a
# reading takes on a screen smaller than the one the font bank was built on,
# and it is what the dot-less fallback exists for.


def test_recovers_lost_decimal_points_with_the_pair_comma_intact():
    point = parse_coordinates("x9849, y11030", RANGE)
    assert (point.x, point.y) == pytest.approx((98.49, 110.30))


def test_recovers_when_only_one_axis_lost_its_decimal_point():
    for text in ("x98.49, y11030", "x9849, y110.30"):
        point = parse_coordinates(text, RANGE)
        assert point is not None, text
        assert (point.x, point.y) == pytest.approx((98.49, 110.30)), text


def test_a_dotted_number_is_never_truncated_by_the_fallback():
    """"x110.30" must not come back as 110: the fallback may not shorten it."""
    point = parse_coordinates("x110.30, y98.49", RANGE)
    assert (point.x, point.y) == pytest.approx((110.30, 98.49))


def test_an_unreadable_glyph_still_refuses_a_dot_less_reading():
    for text in ("x98?9, y110.30", "x9849, y110?30", "x9849, y11?30"):
        assert parse_coordinates(text, RANGE) is None, text


def test_a_lost_comma_as_well_as_the_dots_still_reads():
    point = parse_coordinates("x9849 y11030", RANGE)
    assert (point.x, point.y) == pytest.approx((98.49, 110.30))


def test_a_gap_where_the_decimal_point_was_is_rejoined():
    for text, expect in (("x98 77, y110 21", (98.77, 110.21)),
                         ("x98 77. y110 21", (98.77, 110.21)),
                         ("y67 91 x83 12", (83.12, 67.91))):
        point = parse_coordinates(text, RANGE)
        assert point is not None, text
        assert (point.x, point.y) == pytest.approx(expect), text


def test_a_dot_less_reading_is_never_taken_at_face_value():
    """"y110" is 1.10 with its point knocked out, not 110.

    Reading it as 110 was a way to report a target 21 m from where it is: the
    readout always prints two decimals, so a bare 110 cannot mean 110.00. With
    the shipped bounds -- no player stands below Y 16 -- 1.10 is off the map
    and the reading is refused rather than guessed at.
    """
    played_x, played_y = (20.0, 147.0), (16.0, 133.0)
    assert parse_coordinates("x8312 y110", played_x, played_y) is None
    point = parse_coordinates("x8312 y11021", played_x, played_y)
    assert (point.x, point.y) == pytest.approx((83.12, 110.21))


def test_the_split_form_wins_over_the_bare_run_of_digits():
    """"y110 21" must read 110.21, not stop at 110."""
    point = parse_coordinates("x9884 y110 18", RANGE)
    assert (point.x, point.y) == pytest.approx((98.84, 110.18))
