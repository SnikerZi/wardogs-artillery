"""Switching capture backends without losing the working one.

A real session went this way: eight reads succeeded on BitBlt, then one crop
came back uniform, the app swapped itself onto DXGI, that backend raised
``No module named 'cv2'`` on its very first frame — and every read for the
rest of the session failed with the same error. The app looked dead.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.capture import Region, ScreenCapture  # noqa: E402

REGION = Region(10, 20, 40, 15)


class _Fake:
    """A backend whose frames and failures the test dictates."""

    def __init__(self, name, region_frame, screen_frame=None, raises=None):
        self.name = name
        self._region_frame = region_frame
        self._screen_frame = screen_frame if screen_frame is not None else region_frame
        self._raises = raises
        self.grabs = 0
        self.closed = False

    def grab(self, region):
        self.grabs += 1
        if self._raises is not None:
            raise self._raises
        return self._region_frame if region is not None else self._screen_frame

    def close(self):
        self.closed = True


def _textured(height=15, width=40):
    rng = np.random.default_rng(1)
    return rng.integers(0, 255, size=(height, width, 3), dtype=np.uint8)


def _uniform(height=15, width=40, value=17):
    return np.full((height, width, 3), value, dtype=np.uint8)


def _capture(monkeypatch, backends):
    """A ScreenCapture whose _make hands out the given fakes in order."""
    capture = ScreenCapture("auto")
    made = list(backends)
    monkeypatch.setattr(
        type(capture), "_make", lambda self, name: made.pop(0), raising=True
    )
    return capture


def test_a_uniform_crop_alone_does_not_change_backend(monkeypatch):
    """An empty chat panel is one flat colour; that is not a broken backend."""
    bitblt = _Fake("bitblt", region_frame=_uniform(), screen_frame=_textured(400, 600))
    capture = _capture(monkeypatch, [bitblt])

    frame = capture.grab(REGION)

    assert capture.active_backend == "bitblt"
    assert np.array_equal(frame, _uniform())
    assert capture.last_error is None


def test_a_blank_whole_screen_does_upgrade(monkeypatch):
    bitblt = _Fake("bitblt", region_frame=_uniform(), screen_frame=_uniform(400, 600))
    dxgi = _Fake("dxgi", region_frame=_textured())
    capture = _capture(monkeypatch, [bitblt, dxgi])

    frame = capture.grab(REGION)

    assert capture.active_backend == "dxgi"
    assert np.array_equal(frame, dxgi._region_frame)


def test_a_backend_that_cannot_grab_is_handed_back(monkeypatch):
    """The exact failure seen in the field: DXGI raises on its first frame."""
    bitblt = _Fake("bitblt", region_frame=_uniform(), screen_frame=_uniform(400, 600))
    dxgi = _Fake("dxgi", region_frame=None, raises=ImportError("No module named 'cv2'"))
    capture = _capture(monkeypatch, [bitblt, dxgi])

    frame = capture.grab(REGION)

    assert capture.active_backend == "bitblt", "a broken backend stayed in use"
    assert dxgi.closed
    assert np.array_equal(frame, _uniform())
    assert "cv2" in capture.last_error


def test_reads_keep_working_after_a_failed_upgrade(monkeypatch):
    """The part that made the app look dead rather than degraded."""
    bitblt = _Fake("bitblt", region_frame=_uniform(), screen_frame=_uniform(400, 600))
    dxgi = _Fake("dxgi", region_frame=None, raises=ImportError("No module named 'cv2'"))
    capture = _capture(monkeypatch, [bitblt, dxgi])
    capture.grab(REGION)

    # The chat comes back, so the crop has content again.
    bitblt._region_frame = _textured()
    for _ in range(3):
        frame = capture.grab(REGION)
        assert np.array_equal(frame, bitblt._region_frame)
    assert capture.active_backend == "bitblt"


def test_the_upgrade_is_attempted_only_once(monkeypatch):
    bitblt = _Fake("bitblt", region_frame=_uniform(), screen_frame=_uniform(400, 600))
    dxgi = _Fake("dxgi", region_frame=None, raises=ImportError("nope"))
    capture = _capture(monkeypatch, [bitblt, dxgi])

    for _ in range(3):
        capture.grab(REGION)
    # A second attempt would pop from an empty list and raise IndexError.
    assert capture.active_backend == "bitblt"


def test_an_explicit_bitblt_choice_is_never_overridden(monkeypatch):
    bitblt = _Fake("bitblt", region_frame=_uniform(), screen_frame=_uniform(400, 600))
    capture = ScreenCapture("bitblt")
    monkeypatch.setattr(type(capture), "_make", lambda self, name: bitblt)

    capture.grab(REGION)

    assert capture.active_backend == "bitblt"
