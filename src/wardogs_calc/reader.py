"""Ties capture and OCR into one 'give me a map point' call.

The coordinates come off the chat input line, where the game's "mark
coordinates" key writes them as ``x98.49, y110.30``: an opaque panel at a
fixed spot on screen, which stays put until the message is sent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from .capture import Region, ScreenCapture, screen_size
from .config import Config, base_dir, resource_dir
from .diagnostics import log
from .vision import GlyphSet, load_glyphs, read_coordinates
from .vision.ocr import Reading, recognise_text
from .vision.segment import binarize_variants

#: Share of the screen a box may span and still hold one line of the readout.
#: Height is the telling one: a line of text is wide and short, so a box as
#: tall as it is wide is a leftover from when the app read the map.
_MAX_REGION_WIDTH = 0.6
_MAX_REGION_HEIGHT = 0.25


def _is_text_sized(region: Region, screen: tuple[int, int]) -> bool:
    """Whether a saved box could plausibly hold one line of text."""
    if region.width < 8 or region.height < 8:
        return False
    screen_width, screen_height = screen
    if not (screen_width and screen_height):
        return True
    return (
        region.width <= _MAX_REGION_WIDTH * screen_width
        and region.height <= _MAX_REGION_HEIGHT * screen_height
    )


@dataclass
class ReadResult:
    reading: Reading | None
    error: str | None = None
    frame: np.ndarray | None = None
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.reading is not None


class CoordinateReader:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.capture = ScreenCapture(config.capture_backend)
        self.glyphs = self._load_bank()
        self._dump_dir = base_dir() / "debug"
        self._last_capture_error: str | None = None

    def _load_bank(self) -> GlyphSet:
        return load_glyphs(resource_dir(), base_dir(), self.config.match_margin)

    def reload_glyphs(self) -> None:
        self.glyphs = self._load_bank()
        log(f"glyph bank: {len(self.glyphs)} in {list(self.glyphs.groups)}")

    @property
    def trained(self) -> bool:
        return len(self.glyphs) > 0

    def _stored_region(self) -> Region | None:
        r = self.config.readout_region
        if not r or len(r) != 4:
            return None
        return Region(int(r[0]), int(r[1]), int(r[2]), int(r[3]))

    @property
    def region_ignored(self) -> bool:
        """Whether the saved box is being skipped for being far too big."""
        region = self._stored_region()
        return region is not None and not _is_text_sized(region, screen_size())

    def _region(self) -> Region | None:
        """The crop to OCR: the box drawn over the chat line, else the guess."""
        region = self._stored_region()
        if region is not None and _is_text_sized(region, screen_size()):
            return region
        rel = self.config.chat_region_rel
        if not rel or len(rel) != 4:
            return None
        width, height = screen_size()
        return Region(
            int(rel[0] * width),
            int(rel[1] * height),
            max(8, int(rel[2] * width)),
            max(8, int(rel[3] * height)),
        )

    @staticmethod
    def _expand(region: Region, factor: float = 2.2) -> Region:
        sw, sh = screen_size()
        cx = region.x + region.width / 2.0
        cy = region.y + region.height / 2.0
        w = min(sw, region.width * factor)
        h = min(sh, region.height * factor)
        return Region(
            int(max(0, min(sw - w, cx - w / 2))),
            int(max(0, min(sh - h, cy - h / 2))),
            int(w),
            int(h),
        )

    def _dump(self, frame: np.ndarray, tag: str) -> None:
        if not self.config.debug_dumps or frame is None:
            return
        try:
            from PIL import Image

            self._dump_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%H%M%S")
            Image.fromarray(frame).save(self._dump_dir / f"{stamp}_{tag}.png")
        except Exception:
            pass

    def read(self) -> ReadResult:
        region = self._region()
        if region is None:
            return ReadResult(None, "no readout area set — press Area")
        if not self.trained:
            return ReadResult(None, "no font templates loaded — press Train")

        source = "saved" if self._stored_region() is not None else "default"
        if self.region_ignored:
            source = "default (saved area rejected: wrong size)"
        log(f"read: area {region.as_tuple()} — {source}, screen {screen_size()}")

        started = time.perf_counter()
        # A miss gets one more go on a wider box around the same centre, which
        # is what rescues a hand-drawn box clipping the text and the shipped
        # guess landing slightly off.
        attempts = [region, self._expand(region)]

        last_frame: np.ndarray | None = None
        for attempt, box in enumerate(attempts):
            try:
                frame = self.capture.grab(box)
            except Exception as exc:
                log(f"  capture failed: {exc}")
                return ReadResult(None, f"screen capture failed: {exc}")
            last_frame = frame
            self._log_capture_state()
            reading = read_coordinates(
                frame,
                self.glyphs,
                tuple(self.config.valid_x),
                tuple(self.config.valid_y),
            )
            if reading is not None:
                elapsed = (time.perf_counter() - started) * 1000.0
                self._dump(frame, "ok")
                log(
                    f"  read {reading.point} in {elapsed:.0f} ms "
                    f"(text {reading.text!r}, font {reading.group}, "
                    f"threshold {reading.variant})"
                )
                return ReadResult(reading, None, frame, elapsed)
            self._dump(frame, f"fail{attempt}")
            self._log_miss(attempt, box, frame)

        elapsed = (time.perf_counter() - started) * 1000.0
        log(f"  no coordinates found, {elapsed:.0f} ms")
        return ReadResult(None, "no coordinates found", last_frame, elapsed)

    def _log_capture_state(self) -> None:
        """Say once when the capture backend has degraded under us."""
        error = self.capture.last_error
        if error and error != self._last_capture_error:
            self._last_capture_error = error
            log(f"  capture: {error} (backend {self.capture.active_backend})")

    def _log_miss(self, attempt: int, box: Region, frame: np.ndarray) -> None:
        """Record what the crop actually contained, which is the whole point.

        Without this a failed read leaves nothing behind: the screen has moved
        on and there is no way to tell an empty crop from unreadable glyphs.
        """
        try:
            variants = binarize_variants(frame)
            summary = ", ".join(f"{name} {int(mask.sum())}px" for name, mask in variants)
            # Reading the crop back costs as much as the attempt did, so only
            # the first box — the one that is meant to be right — is worth it.
            lines = (
                [text for text, _confidence in recognise_text(frame, self.glyphs)]
                if attempt == 0
                else None
            )
        except Exception as exc:  # diagnostics must not break a read
            log(f"  miss {attempt} on {box.as_tuple()}: parse failed ({exc})")
            return
        detail = "" if lines is None else f", recognised {lines if lines else 'nothing'}"
        log(
            f"  miss {attempt} on {box.as_tuple()}: "
            f"thresholds [{summary or 'none produced anything'}]{detail}"
        )

    def set_strictness(self, min_margin: float) -> None:
        self.config.match_margin = min_margin
        self.glyphs.set_min_margin(min_margin)
        log(f"match margin: {min_margin}")

    def close(self) -> None:
        self.capture.close()
