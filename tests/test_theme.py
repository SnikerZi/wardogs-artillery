import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.ui.theme import ACCENTS, DARK, LIGHT, Theme, mix  # noqa: E402


def test_palette_follows_the_dark_flag():
    assert Theme(dark=True).bg == DARK.bg
    assert Theme(dark=False).bg == LIGHT.bg


def test_accent_falls_back_for_an_unknown_key():
    assert Theme(accent_key="does-not-exist").accent == ACCENTS["green"][1]


def test_every_accent_is_a_hex_colour():
    for _label, colour in ACCENTS.values():
        assert len(colour) == 7 and colour.startswith("#")
        int(colour[1:], 16)


def test_scaling_never_collapses_to_zero():
    tiny = Theme(scale=0.05)
    assert tiny.px(1) == 1
    assert tiny.px(0) == 1


def test_scaling_is_proportional():
    assert Theme(scale=2.0).px(10) == 20
    assert Theme(scale=1.0).px(10) == 10


def test_mix_endpoints_and_midpoint():
    assert mix("#000000", "#ffffff", 0.0) == "#000000"
    assert mix("#000000", "#ffffff", 1.0) == "#ffffff"
    assert mix("#000000", "#ffffff", 0.5) == "#808080"


def test_mix_clamps_out_of_range_ratios():
    assert mix("#102030", "#405060", -5) == "#102030"
    assert mix("#102030", "#405060", 5) == "#405060"


def test_light_and_dark_use_different_ink():
    assert Theme(dark=True).text != Theme(dark=False).text


def test_unknown_attribute_still_raises():
    with pytest.raises(AttributeError):
        Theme().no_such_colour
