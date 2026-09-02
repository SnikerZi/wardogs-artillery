"""CoordinateReader end to end, with a stubbed screen grab."""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

pytest.importorskip("PIL")
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from wardogs_calc.config import Config  # noqa: E402
from wardogs_calc.reader import CoordinateReader  # noqa: E402
from wardogs_calc.vision.glyphs import GlyphSet  # noqa: E402
from wardogs_calc.vision.segment import (  # noqa: E402
    binarize_variants,
    connected_components,
    drop_oversized,
    merge_stacked,
)

ALPHABET = "0123456789.xy"
BG, FG = 110, 245


def _font():
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, 20)
        except OSError:
            continue
    pytest.skip("no scalable font available")


FONT = _font()


def _render(lines: list[str]) -> np.ndarray:
    width = max(FONT.getbbox(t)[2] for t in lines) + 28
    image = Image.new("RGB", (width, 20 + 32 * len(lines)), (BG,) * 3)
    draw = ImageDraw.Draw(image)
    for i, text in enumerate(lines):
        draw.text((14, 8 + 32 * i), text, font=FONT, fill=(FG,) * 3)
    return np.asarray(image, dtype=np.uint8)


def _bank() -> GlyphSet:
    glyphs = GlyphSet()
    for char in ALPHABET:
        crop = _render([char])
        for _name, mask in binarize_variants(crop):
            blobs = drop_oversized(merge_stacked(connected_components(mask, min_pixels=2)))
            if blobs:
                glyphs.add(char, max(blobs, key=lambda g: g.mask.sum()), group="test_font")
    return glyphs


class _StubCapture:
    """Stands in for the screen; counts grabs so we can assert on them."""

    def __init__(self, frame: np.ndarray) -> None:
        self.frame = frame
        self.grabs = 0
        self.active_backend = "stub"
        self.last_error: str | None = None

    def grab(self, _region=None) -> np.ndarray:
        self.grabs += 1
        return self.frame

    def close(self) -> None:
        pass


def _reader(frame: np.ndarray, tmp_path: Path) -> CoordinateReader:
    config = Config()
    config.readout_region = [0, 0, frame.shape[1], frame.shape[0]]
    reader = CoordinateReader(config)
    reader.glyphs = _bank()
    reader.capture = _StubCapture(frame)
    return reader


def test_reads_a_rendered_readout(tmp_path):
    reader = _reader(_render(["y67.91", "x83.12"]), tmp_path)
    result = reader.read()
    assert result.ok, result.error
    assert (result.reading.point.x, result.reading.point.y) == pytest.approx((83.12, 67.91))


def test_repeated_reads_keep_working(tmp_path):
    reader = _reader(_render(["y67.91", "x83.12"]), tmp_path)
    for _ in range(3):
        result = reader.read()
        assert result.ok, result.error
        assert (result.reading.point.x, result.reading.point.y) == pytest.approx(
            (83.12, 67.91)
        )


def test_hud_clutter_beside_the_readout_is_ignored(tmp_path):
    reader = _reader(_render(["y67.91", "1 107", "x83.12"]), tmp_path)
    result = reader.read()
    assert result.ok, result.error
    assert (result.reading.point.x, result.reading.point.y) == pytest.approx((83.12, 67.91))


def test_a_closed_map_reads_nothing(tmp_path):
    rng = np.random.default_rng(11)
    noise = rng.integers(60, 150, size=(90, 240, 3), dtype=np.uint8)
    reader = _reader(noise, tmp_path)
    result = reader.read()
    assert not result.ok
    assert result.error


def test_positions_outside_the_playable_area_are_refused(tmp_path):
    # y=7.01 is off the map strip; this is what a clipped "y77.01" looks like.
    reader = _reader(_render(["y7.01", "x83.12"]), tmp_path)
    assert not reader.read().ok


