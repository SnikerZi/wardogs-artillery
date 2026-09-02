"""The record-a-hotkey path, driven through the real hook callback.

No synthetic key events are injected: the callback is invoked directly with a
genuine KBDLLHOOKSTRUCT, which is what Windows would hand it.
"""

import ctypes
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc import hotkeys  # noqa: E402
from wardogs_calc.hotkeys import (  # noqa: E402
    KBDLLHOOKSTRUCT,
    MSLLHOOKSTRUCT,
    VK_BY_NAME,
    VK_CONTROL,
    VK_ESCAPE,
    VK_MBUTTON,
    VK_RBUTTON,
    VK_WHEEL_DOWN,
    VK_WHEEL_UP,
    VK_XBUTTON1,
    VK_XBUTTON2,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_MBUTTONDOWN,
    WM_MOUSEWHEEL,
    WM_RBUTTONDOWN,
    WM_XBUTTONDOWN,
    HotkeyListener,
    _mouse_vk,
)

SWALLOWED = 1


@pytest.fixture(autouse=True)
def held_keys(monkeypatch):
    """Pin which modifiers count as held, and let a test change its mind.

    Recording builds the spec from whatever is physically down at that
    instant, so without this the outcome depends on the keyboard of whoever
    runs the suite: one stray Shift and ``f7`` records as ``shift+f7``.
    """
    down: set[int] = set()
    monkeypatch.setattr(hotkeys, "_modifier_down", lambda vk: vk in down)
    return down


def _key(vk: int):
    return ctypes.pointer(KBDLLHOOKSTRUCT(vkCode=vk))


def _drain(listener) -> list:
    """Run whatever the hook queued, the way the worker thread would."""
    results = []
    while not listener._queue.empty():
        item = listener._queue.get_nowait()
        if item is not None:
            item()
            results.append(item)
    return results


@pytest.fixture
def listener():
    return HotkeyListener()


def test_recording_reports_the_pressed_key(listener):
    seen = []
    listener.capture_next(seen.append)
    result = listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert seen == ["f7"]
    assert result == SWALLOWED, "a key being bound must not also reach the game"


def test_recording_disarms_after_one_key(listener):
    listener.capture_next(lambda _spec: None)
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert not listener.capturing


def test_modifiers_alone_do_not_end_recording(listener):
    # The user holds Ctrl, then reaches for the key: the hold must not be
    # mistaken for the binding.
    listener.capture_next(lambda _spec: None)
    listener._on_key(0, WM_KEYDOWN, _key(VK_CONTROL))
    assert listener.capturing
    assert not _drain(listener)


def test_escape_cancels_recording(listener):
    seen = []
    listener.capture_next(seen.append)
    listener._on_key(0, WM_KEYDOWN, _key(VK_ESCAPE))
    _drain(listener)
    assert seen == [None]
    assert not listener.capturing


def test_cancel_capture_disarms(listener):
    listener.capture_next(lambda _spec: None)
    listener.cancel_capture()
    assert not listener.capturing


def test_a_bound_key_is_dispatched(listener):
    fired = []
    listener.bind("f7", lambda: fired.append("gun"))
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert fired == ["gun"]


def test_a_bound_key_still_reaches_the_game(listener):
    listener.bind("f7", lambda: None)
    assert listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"])) != SWALLOWED


def test_unbound_keys_do_nothing(listener):
    listener.bind("f7", lambda: pytest.fail("wrong key fired"))
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f8"]))
    assert not _drain(listener)


def test_key_release_is_ignored(listener):
    listener.bind("f7", lambda: pytest.fail("fired on key-up"))
    listener._on_key(0, WM_KEYUP, _key(VK_BY_NAME["f7"]))
    assert not _drain(listener)


def test_clear_removes_bindings(listener):
    listener.bind("f7", lambda: pytest.fail("fired after clear"))
    listener.clear()
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    assert not _drain(listener)


def test_recording_takes_priority_over_an_existing_binding(listener):
    # Rebinding the key that is currently bound must record, not fire.
    listener.bind("f7", lambda: pytest.fail("binding fired while recording"))
    seen = []
    listener.capture_next(seen.append)
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert seen == ["f7"]


# --- mouse buttons ---------------------------------------------------------

WM_MOUSEMOVE = 0x0200
WHEEL_DELTA = 120


def _mouse(data: int = 0):
    return ctypes.pointer(MSLLHOOKSTRUCT(mouseData=data))


@pytest.mark.parametrize(
    "message,data,expected",
    [
        (WM_RBUTTONDOWN, 0, VK_RBUTTON),
        (WM_MBUTTONDOWN, 0, VK_MBUTTON),
        (WM_XBUTTONDOWN, 1 << 16, VK_XBUTTON1),
        (WM_XBUTTONDOWN, 2 << 16, VK_XBUTTON2),
        (WM_MOUSEWHEEL, WHEEL_DELTA << 16, VK_WHEEL_UP),
        (WM_MOUSEWHEEL, (0x10000 - WHEEL_DELTA) << 16, VK_WHEEL_DOWN),
    ],
)
def test_mouse_events_map_to_virtual_keys(message, data, expected):
    assert _mouse_vk(message, data) == expected


def test_unknown_x_button_is_ignored():
    assert _mouse_vk(WM_XBUTTONDOWN, 9 << 16) is None


def test_recording_accepts_a_side_button(listener):
    seen = []
    listener.capture_next(seen.append)
    result = listener._on_mouse(0, WM_XBUTTONDOWN, _mouse(1 << 16))
    _drain(listener)
    assert seen == ["mouse4"]
    assert result == SWALLOWED, "the click that binds a button must not reach the game"


def test_recording_accepts_the_wheel(listener):
    seen = []
    listener.capture_next(seen.append)
    listener._on_mouse(0, WM_MOUSEWHEEL, _mouse(WHEEL_DELTA << 16))
    _drain(listener)
    assert seen == ["wheel_up"]


def test_a_bound_mouse_button_is_dispatched(listener):
    fired = []
    listener.bind("mouse5", lambda: fired.append("target"))
    listener._on_mouse(0, WM_XBUTTONDOWN, _mouse(2 << 16))
    _drain(listener)
    assert fired == ["target"]


def test_a_bound_mouse_button_still_reaches_the_game(listener):
    listener.bind("mouse5", lambda: None)
    assert listener._on_mouse(0, WM_XBUTTONDOWN, _mouse(2 << 16)) != SWALLOWED


def test_mouse_movement_is_ignored(listener):
    # This one fires hundreds of times a second; it must not even be decoded.
    listener.capture_next(lambda _spec: pytest.fail("mouse move ended recording"))
    listener._on_mouse(0, WM_MOUSEMOVE, _mouse())
    assert listener.capturing
    assert not _drain(listener)


def test_left_button_is_not_bindable():
    # It is how the app itself is clicked, including the recorder button.
    assert 0x01 not in VK_BY_NAME.values()


def test_both_hooks_actually_install():
    # ctypes argtypes are strict enough to reject one proc type while
    # accepting the other, which would leave mouse bindings silently dead.
    listener = HotkeyListener()
    listener.start()
    try:
        deadline = time.time() + 3.0
        while time.time() < deadline and not (listener._hook and listener._mouse_hook):
            time.sleep(0.02)
        assert listener._hook, "keyboard hook not installed"
        assert listener._mouse_hook, "mouse hook not installed"
    finally:
        listener.stop()


def test_a_held_modifier_becomes_part_of_the_binding(listener, held_keys):
    held_keys.add(VK_CONTROL)
    seen = []
    listener.capture_next(seen.append)
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert seen == ["ctrl+f7"]


def test_a_combo_binding_needs_its_modifier_held(listener, held_keys):
    fired = []
    listener.bind("ctrl+f7", lambda: fired.append("gun"))

    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert fired == [], "fired without Ctrl"

    held_keys.add(VK_CONTROL)
    listener._on_key(0, WM_KEYDOWN, _key(VK_BY_NAME["f7"]))
    _drain(listener)
    assert fired == ["gun"]
