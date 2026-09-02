"""Screen capture with two interchangeable backends.

bitblt : GDI copy of the desktop via ``mss``.  Fast, dependency-light, and
         correct for windowed and borderless-fullscreen games.
dxgi   : Desktop Duplication via ``dxcam``.  Needed when the game holds an
         exclusive-fullscreen swapchain, where a GDI copy comes back black.

``auto`` starts on bitblt and permanently switches to dxgi the first time a
grab comes back blank.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass

import numpy as np

_DPI_SET = False


def ensure_dpi_awareness() -> None:
    """Report physical pixels, so screen coordinates match captured pixels."""
    global _DPI_SET
    if _DPI_SET:
        return
    user32 = ctypes.windll.user32
    try:
        # PER_MONITOR_AWARE_V2
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    _DPI_SET = True


def screen_size() -> tuple[int, int]:
    ensure_dpi_awareness()
    user32 = ctypes.windll.user32
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def cursor_pos() -> tuple[int, int]:
    ensure_dpi_awareness()

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


@dataclass
class Region:
    x: int
    y: int
    width: int
    height: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height


class CaptureError(RuntimeError):
    pass


class _BitBltBackend:
    name = "bitblt"

    def __init__(self) -> None:
        import mss  # imported lazily so a missing dep names itself clearly

        self._mss_mod = mss
        self._local = None

    def _sct(self):
        # mss instances are not thread-safe; make one per thread.
        import threading

        if self._local is None:
            self._local = threading.local()
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = self._mss_mod.mss()
            self._local.sct = sct
        return sct

    def grab(self, region: Region | None) -> np.ndarray:
        sct = self._sct()
        if region is None:
            monitor = sct.monitors[0]
        else:
            monitor = {
                "left": region.x,
                "top": region.y,
                "width": region.width,
                "height": region.height,
            }
        shot = sct.grab(monitor)
        # BGRA -> RGB
        arr = np.asarray(shot, dtype=np.uint8)
        return arr[:, :, 2::-1].copy()

    def close(self) -> None:
        pass


class _DxgiBackend:
    name = "dxgi"

    def __init__(self) -> None:
        import dxcam

        # BGRA, not RGB: asking dxcam to convert makes it reach for OpenCV,
        # which is not in the packaged exe — the grab then fails with
        # "No module named 'cv2'" at the first frame rather than at startup.
        # Reordering four channels in numpy costs nothing and needs nothing.
        self._camera = dxcam.create(output_color="BGRA")
        if self._camera is None:
            raise CaptureError("dxcam could not create a capture device")

    def grab(self, region: Region | None) -> np.ndarray:
        box = None
        if region is not None:
            box = (
                region.x,
                region.y,
                region.x + region.width,
                region.y + region.height,
            )
        frame = self._camera.grab(region=box)
        if frame is None:
            # dxcam returns None when the desktop has not changed since the
            # last grab; a fresh full-frame read always produces something.
            frame = self._camera.grab()
            if frame is None:
                raise CaptureError("dxcam returned an empty frame")
            if region is not None:
                frame = frame[
                    region.y : region.y + region.height,
                    region.x : region.x + region.width,
                ]
        # BGRA -> RGB
        return np.ascontiguousarray(frame[:, :, 2::-1])

    def close(self) -> None:
        try:
            self._camera.release()
        except Exception:
            pass


def _is_blank(frame: np.ndarray) -> bool:
    """True when a grab produced a uniform image — the classic GDI failure."""
    if frame.size == 0:
        return True
    return int(frame.max()) - int(frame.min()) < 4


class ScreenCapture:
    """Backend-agnostic grabber that degrades gracefully."""

    def __init__(self, backend: str = "auto") -> None:
        ensure_dpi_awareness()
        self._requested = backend
        self._backend = None
        self._tried_dxgi = False
        self.last_error: str | None = None

    @property
    def active_backend(self) -> str:
        return self._backend.name if self._backend else "none"

    def _make(self, name: str):
        if name == "dxgi":
            return _DxgiBackend()
        return _BitBltBackend()

    def _ensure_backend(self):
        if self._backend is None:
            preferred = "bitblt" if self._requested == "auto" else self._requested
            try:
                self._backend = self._make(preferred)
            except Exception as exc:
                if preferred == "bitblt":
                    raise CaptureError(f"could not start screen capture: {exc}") from exc
                self.last_error = str(exc)
                self._backend = self._make("bitblt")
        return self._backend

    def _upgrade_to_dxgi(self, current) -> bool:
        """Swap in DXGI, keeping ``current`` alive in case it has to come back."""
        if self._tried_dxgi or self._requested == "bitblt":
            return False
        self._tried_dxgi = True
        try:
            self._backend = self._make("dxgi")
        except Exception as exc:
            self.last_error = (
                "the screen came back blank and DXGI capture is unavailable "
                f"({exc}). Switch the game to borderless windowed mode."
            )
            self._backend = current
            return False
        return True

    def grab(self, region: Region | None = None) -> np.ndarray:
        backend = self._ensure_backend()
        frame = backend.grab(region)
        if not _is_blank(frame):
            return frame

        # A uniform crop is usually just a uniform crop: an empty chat panel is
        # one flat colour. Only a blank *whole screen* means the GDI copy is
        # coming back empty, which is the one thing DXGI is here to fix.
        if region is not None and not _is_blank(backend.grab(None)):
            return frame

        if not self._upgrade_to_dxgi(backend):
            return frame
        try:
            return self._backend.grab(region)
        except Exception as exc:
            # A replacement that cannot grab at all is worse than what it
            # replaced. Going back matters more than the upgrade did: left in
            # place, a broken backend fails every read for the rest of the
            # session, and the app looks dead rather than degraded.
            self.last_error = f"DXGI capture does not work ({exc}), staying on BitBlt"
            try:
                self._backend.close()
            except Exception:
                pass
            self._backend = backend
            return frame

    def close(self) -> None:
        if self._backend:
            self._backend.close()
            self._backend = None
