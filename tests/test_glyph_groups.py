"""Group handling: a readout is one font, so decoding commits to one group."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.vision.glyphs import (  # noqa: E402
    CELL,
    GLYPH_FILE,
    USER_GROUP,
    GlyphSet,
    load_glyphs,
)
from wardogs_calc.vision.segment import Glyph  # noqa: E402


def _glyph(pattern: list[str]) -> Glyph:
    mask = np.array([[c == "#" for c in row] for row in pattern], dtype=bool)
    return Glyph(0, 0, mask.shape[1], mask.shape[0], mask)


BAR = _glyph(["##", "##", "##", "##"])
BOX = _glyph(["####", "#..#", "#..#", "####"])
DOT = _glyph(["##", "##"])


def test_extend_keeps_group_tags():
    a = GlyphSet()
    a.add("1", BAR, group="fontA")
    b = GlyphSet()
    b.add("1", BAR, group=USER_GROUP)
    a.extend(b)
    assert len(a) == 2
    assert set(a.groups) == {"fontA", USER_GROUP}


def test_templates_of_a_group_are_contiguous():
    # Decoding takes the per-group maximum as a slice of the score array, so
    # each group has to occupy one unbroken run of templates.
    glyphs = GlyphSet()
    glyphs.add("1", BAR, group="b")
    glyphs.add("0", BOX, group="a")
    glyphs.add("1", BAR, group="b")
    assert sorted(glyphs.groups) == glyphs.groups
    order = [t.group for t in glyphs.templates]
    assert order == sorted(order)


def test_decode_commits_to_the_group_that_explains_the_line():
    glyphs = GlyphSet()
    # A group that matches the shapes exactly...
    glyphs.add("1", BAR, group="right")
    glyphs.add("0", BOX, group="right")
    # ...and one that has the labels swapped.
    glyphs.add("0", BAR, group="wrong")
    glyphs.add("1", BOX, group="wrong")
    # A group scoring well on one glyph must not be picked when the other
    # glyph contradicts it — the whole line decides.
    group, matches = glyphs.decode([BAR, BOX], [1.0, 1.0])
    assert group == "right"
    assert [m.label for m in matches] == ["1", "0"]


def test_decode_returns_nothing_for_an_empty_line():
    glyphs = GlyphSet()
    glyphs.add("1", BAR, group="a")
    assert glyphs.decode([], []) == ("", [])


def test_a_short_glyph_can_only_be_punctuation():
    glyphs = GlyphSet()
    glyphs.add(".", DOT, group="a")
    glyphs.add("0", BOX, group="a")
    # A fragment of a digit normalises to a near-solid square just like a full
    # stop; only its height relative to the line separates them.
    assert glyphs.match(DOT, height_ratio=0.15).label == "."
    assert glyphs.match(DOT, height_ratio=1.0).label != "."


def test_a_tall_glyph_is_never_read_as_a_full_stop():
    glyphs = GlyphSet()
    glyphs.add(".", DOT, group="a")
    assert glyphs.match(BOX, height_ratio=1.0).label != "."


def test_load_glyphs_merges_bundled_and_trained(tmp_path):
    resource = tmp_path / "res"
    (resource / "vision" / "templates").mkdir(parents=True)
    bundled = GlyphSet()
    bundled.add("1", BAR, group="bundled_font")
    bundled.save(resource / "vision" / "templates" / GLYPH_FILE)

    user_dir = tmp_path / "user"
    user_dir.mkdir()
    trained = GlyphSet()
    trained.add("0", BOX, group=USER_GROUP)
    trained.save(user_dir / GLYPH_FILE)

    # Both banks are loaded, in separate groups: decoding commits to one
    # group per line, so a font trained on this machine wins outright where
    # it fits without its margin being collapsed by the bundled one.
    merged = load_glyphs(resource, user_dir)
    assert len(merged) == 2
    assert set(merged.groups) == {"bundled_font", USER_GROUP}


def test_load_glyphs_works_with_no_training_yet(tmp_path):
    resource = tmp_path / "res"
    (resource / "vision" / "templates").mkdir(parents=True)
    bundled = GlyphSet()
    bundled.add("1", BAR, group="bundled_font")
    bundled.save(resource / "vision" / "templates" / GLYPH_FILE)
    # Nothing trained yet, so the bundled bank is all there is.
    loaded = load_glyphs(resource, tmp_path / "empty")
    assert len(loaded) == 1
    assert set(loaded.groups) == {"bundled_font"}


def test_saved_groups_survive_a_round_trip(tmp_path):
    glyphs = GlyphSet()
    glyphs.add("1", BAR, group="fontA")
    glyphs.add("0", BOX, group=USER_GROUP)
    path = tmp_path / GLYPH_FILE
    glyphs.save(path)
    assert GlyphSet.load(path).groups == ["fontA", USER_GROUP]


def test_a_file_written_at_another_grid_size_still_loads(tmp_path):
    payload = {
        "cell": 8,
        "templates": [
            {"label": "1", "group": "old", "aspect": 0.5,
             "rows": ["01100000"] * 8},
        ],
    }
    path = tmp_path / GLYPH_FILE
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = GlyphSet.load(path)
    assert len(loaded) == 1
    assert loaded.templates[0].grid.shape == (CELL, CELL)


# --- the comma problem -----------------------------------------------------
# A comma and a full stop are the same handful of pixels once resampled onto a
# square grid. Kept as two labels they cancel each other out: a real trained
# font scored a stop identically against both, the decimal point started
# coming back as "?", and without it the number will not parse at all.


def test_a_comma_is_stored_as_a_full_stop():
    glyphs = GlyphSet()
    glyphs.add(",", BOX, group=USER_GROUP)
    assert glyphs.labels == {"."}


def test_a_comma_in_a_saved_file_folds_on_load(tmp_path):
    # Files trained before the fold must not keep two rival separator labels.
    path = tmp_path / GLYPH_FILE
    payload = {
        "cell": CELL,
        "templates": [
            {"label": ",", "rows": ["1" * CELL] * CELL, "aspect": 1.0, "group": USER_GROUP}
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert GlyphSet.load(path).labels == {"."}


def test_a_separator_never_competes_with_itself():
    """Both separator shapes under one label leaves the margin intact."""
    glyphs = GlyphSet()
    glyphs.add(".", BOX, group=USER_GROUP)
    glyphs.add(",", BAR, group=USER_GROUP)
    glyphs.set_min_margin(0.0)
    match = glyphs.match(BOX, 0.25)
    assert match.label == "."
    # No rival label exists any more, so the margin is maximal instead of
    # being cancelled to nothing by the other separator.
    assert match.runner_up == 0.0
    assert match.margin == 1.0
    assert match.confident
