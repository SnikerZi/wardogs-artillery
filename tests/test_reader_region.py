"""Which crop the reader takes, and which saved one it refuses.

Earlier versions read the map, so a config carried over can hold a box drawn
around the whole map. Treated as a chat line that box reads nothing, slowly,
for ever — so the size test matters as much as the geometry.
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.capture import Region, screen_size  # noqa: E402
from wardogs_calc.config import Config  # noqa: E402
from wardogs_calc.reader import CoordinateReader, _is_text_sized  # noqa: E402

SCREEN = (2560, 1440)

#: What the user's own config held after selecting the map by hand.
WHOLE_MAP = Region(839, 273, 880, 880)
CHAT_LINE = Region(357, 475, 662, 100)


def test_a_box_around_the_whole_map_is_refused():
    assert not _is_text_sized(WHOLE_MAP, SCREEN)


def test_a_box_around_one_line_is_accepted():
    assert _is_text_sized(CHAT_LINE, SCREEN)


def test_the_test_scales_with_the_screen():
    # The same pixel box is a line of text on a 4K screen and most of a
    # 1080p one, so the judgement cannot be in absolute pixels.
    big = Region(0, 0, 900, 300)
    assert _is_text_sized(big, (3840, 2160))
    assert not _is_text_sized(big, (1280, 720))


def test_height_is_what_gives_the_map_box_away():
    # It is not the area: 880x880 is only a fifth of a 1440p screen. What
    # rules it out is being as tall as it is wide.
    assert WHOLE_MAP.width * WHOLE_MAP.height < 0.25 * SCREEN[0] * SCREEN[1]
    assert _is_text_sized(Region(0, 0, 880, 120), SCREEN)
    assert not _is_text_sized(Region(0, 0, 880, 880), SCREEN)


def test_a_degenerate_box_is_refused():
    assert not _is_text_sized(Region(0, 0, 880, 2), SCREEN)
    assert not _is_text_sized(Region(0, 0, 0, 0), SCREEN)


def test_an_unknown_screen_size_refuses_only_the_degenerate():
    # Better to try a saved box than to reject every one of them.
    assert _is_text_sized(WHOLE_MAP, (0, 0))
    assert not _is_text_sized(Region(0, 0, 2, 2), (0, 0))


def _reader(**overrides) -> CoordinateReader:
    config = Config()
    for key, value in overrides.items():
        setattr(config, key, value)
    return CoordinateReader(config)


def test_a_saved_box_is_used_as_it_is():
    reader = _reader(readout_region=[100, 200, 300, 40])
    assert reader._region().as_tuple() == (100, 200, 300, 40)
    assert not reader.region_ignored


def test_the_stale_whole_map_box_is_ignored_and_reported():
    reader = _reader(readout_region=list(WHOLE_MAP.as_tuple()))
    assert reader.region_ignored
    assert reader._region().as_tuple() != WHOLE_MAP.as_tuple()


def test_without_a_saved_box_the_shipped_guess_is_used():
    reader = _reader(readout_region=None, chat_region_rel=[0.1, 0.2, 0.3, 0.05])
    width, height = screen_size()
    assert reader._region().as_tuple() == (
        int(0.1 * width),
        int(0.2 * height),
        int(0.3 * width),
        int(0.05 * height),
    )
    assert not reader.region_ignored


def test_a_malformed_guess_yields_no_region():
    assert _reader(readout_region=None, chat_region_rel=[])._region() is None


def test_the_default_weapon_exists_in_the_firing_tables():
    # The app quietly substitutes the first weapon for an unknown key, so a
    # wrong default is invisible at runtime and only shows up here.
    from wardogs_calc.ballistics import load_weapons
    from wardogs_calc.config import resource_dir

    weapons = load_weapons(resource_dir() / "firing_tables.json")
    assert Config().weapon in weapons


# --- the bank the reader ends up with --------------------------------------


def _train(tmp_path, labels="0123456789xy."):
    from wardogs_calc.vision.glyphs import GLYPH_FILE, USER_GROUP, GlyphSet
    from wardogs_calc.vision.segment import Glyph

    glyphs = GlyphSet()
    for index, label in enumerate(labels):
        mask = np.zeros((6, 4), dtype=bool)
        mask[index % 6, :] = True  # a distinct shape per label
        glyphs.add(label, Glyph(0, 0, 4, 6, mask), group=USER_GROUP)
    glyphs.save(tmp_path / GLYPH_FILE)


def test_a_trained_font_joins_the_bundled_bank_in_its_own_group(tmp_path, monkeypatch):
    import wardogs_calc.reader as reader_module
    from wardogs_calc.vision.glyphs import USER_GROUP

    _train(tmp_path)
    monkeypatch.setattr(reader_module, "base_dir", lambda: tmp_path)
    reader = reader_module.CoordinateReader(Config())

    # Separate groups, because decoding commits to one group per line: the
    # trained font wins whole lines rather than competing template by
    # template with a near-identical bundled one.
    assert USER_GROUP in reader.glyphs.groups
    assert len(reader.glyphs.groups) > 1


def test_the_bank_is_loaded_at_the_configured_margin(tmp_path, monkeypatch):
    import wardogs_calc.reader as reader_module

    monkeypatch.setattr(reader_module, "base_dir", lambda: tmp_path)
    config = Config()
    reader = reader_module.CoordinateReader(config)

    assert reader.glyphs.min_margin == config.match_margin

    reader.set_strictness(0.10)
    assert config.match_margin == 0.10
    assert reader.glyphs.min_margin == 0.10
