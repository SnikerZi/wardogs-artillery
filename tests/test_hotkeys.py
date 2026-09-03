import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.config import Config  # noqa: E402
from wardogs_calc.hotkeys import (  # noqa: E402
    NAME_BY_VK,
    VK_BY_NAME,
    HotkeyError,
    edits_text,
    parse_hotkey,
    pretty_hotkey,
)

MODIFIER_VKS = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B}


def test_every_recordable_key_round_trips():
    # Recording produces NAME_BY_VK[vk]; that string is saved to config.json
    # and parsed back on the next launch. If any key failed to survive that
    # trip, the binding would silently stop working after a restart.
    for vk, name in NAME_BY_VK.items():
        assert parse_hotkey(name) == (vk, frozenset()), name


def test_modifiers_are_parsed_in_any_order():
    assert parse_hotkey("ctrl+shift+f1") == parse_hotkey("shift+ctrl+f1")


def test_modifier_set_is_captured():
    vk, mods = parse_hotkey("ctrl+alt+numpad5")
    assert vk == VK_BY_NAME["numpad5"]
    assert mods == {MODIFIER_VKS["ctrl"], MODIFIER_VKS["alt"]}



def test_win_modifier_is_supported():
    _vk, mods = parse_hotkey("win+z")
    assert mods == {MODIFIER_VKS["win"]}


def test_case_and_whitespace_are_tolerated():
    assert parse_hotkey(" Ctrl + F1 ") == parse_hotkey("ctrl+f1")


@pytest.mark.parametrize("spec", ["", "+", "ctrl+", "ctrl+shift", "notakey", "f1+f2"])
def test_rejects_malformed_specs(spec):
    with pytest.raises(HotkeyError):
        parse_hotkey(spec)


@pytest.mark.parametrize(
    "spec,shown",
    [
        ("1", "1"),
        ("f12", "F12"),
        ("ctrl+f1", "Ctrl + F1"),
        ("shift+numpad5", "Shift + Num 5"),
        ("ctrl+shift+alt+z", "Ctrl + Shift + Alt + Z"),
        ("esc", "Esc"),
        ("pagedown", "PgDn"),
    ],
)
def test_pretty_rendering(spec, shown):
    assert pretty_hotkey(spec) == shown


def test_pretty_handles_an_empty_binding():
    assert pretty_hotkey("") == "—"


def test_canonical_names_win_over_aliases():
    # "esc" and "escape" share a VK; the display name must be stable.
    assert NAME_BY_VK[VK_BY_NAME["esc"]] == "esc"


# --- keys that would disturb the chat input --------------------------------
# Coordinates are read off the chat line while it holds keyboard focus, so a
# hotkey that types, deletes or submits corrupts the very text being read.


def test_a_bare_character_key_disturbs_the_field():
    for spec in ("1", "a", "space", "numpad3", ".", "shift+2"):
        assert edits_text(spec), spec


def test_ctrl_alt_and_win_suppress_the_character():
    for spec in ("ctrl+1", "alt+a", "win+2", "ctrl+shift+3"):
        assert not edits_text(spec), spec


def test_editing_keys_are_unsafe_whatever_the_modifier():
    # Ctrl+Enter still sends in most chats and Ctrl+Backspace still eats a word.
    for spec in ("enter", "tab", "backspace", "delete", "ctrl+enter", "ctrl+backspace"):
        assert edits_text(spec), spec


def test_function_keys_and_mouse_buttons_are_safe():
    for spec in ("f9", "f10", "f11", "ctrl+f9", "mouse4", "mouse5", "wheel_up", "esc"):
        assert not edits_text(spec), spec


def _default_specs(config: Config) -> list[str]:
    return [
        getattr(config, name)
        for name in vars(config)
        if name.startswith("hotkey_")
    ]


def test_the_shipped_defaults_leave_the_chat_line_alone():
    config = Config()
    for spec in _default_specs(config):
        assert not edits_text(spec), spec
        parse_hotkey(spec)


def test_the_shipped_defaults_do_not_clash():
    # The settings page refuses a spec already bound elsewhere, which would
    # make one of the defaults unbindable rather than merely odd.
    specs = _default_specs(Config())
    assert len(specs) == len(set(specs)) == 4


def test_the_defaults_avoid_steams_screenshot_key():
    assert "f12" not in _default_specs(Config())
