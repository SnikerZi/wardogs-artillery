"""Terrain height lookup, used to correct the firing solution for slope.

The game does not show the height of a marked point, so the height comes from
the terrain itself: the Landscape collision height field, sampled every 8 m and
stored per map under ``terrain/``.  See ``terrain/index.json`` for the file
format and the sampling error.

Only *differences* between two points are ever used.  The source height field
has no usable absolute datum -- it sits roughly 900 m below anything a player
would recognise as an altitude -- but it is internally consistent, so a
difference is exact while an absolute figure would be meaningless.  Heights are
therefore stored, and returned, as metres above each map's own lowest sample,
and nothing displays them on their own.
"""

from __future__ import annotations

import json
import lzma
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .config import resource_dir

#: Subdirectory of the bundled resources holding the grids.
TERRAIN_DIR = "terrain"


@dataclass(frozen=True)
class TerrainMap:
    """One map's height grid, ready to sample."""

    key: str
    label: str
    #: Quantised heights, ``[row, column]``, row 0 at the lowest game Y.
    grid: np.ndarray
    #: Game coordinates of ``grid[0, 0]`` and the spacing between samples.
    x0: float
    y0: float
    step: float
    #: ``height_m = height_min + value * quant``.
    height_min: float
    quant: float
    #: Playable area, for the "wrong map selected?" check.
    bounds: dict[str, float]

    @property
    def x1(self) -> float:
        return self.x0 + (self.grid.shape[1] - 1) * self.step

    @property
    def y1(self) -> float:
        return self.y0 + (self.grid.shape[0] - 1) * self.step

    def covers(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def height_at(self, x: float, y: float) -> float | None:
        """Bilinear height at a map position, or None if off the grid.

        The figure is metres above this map's lowest sample: meaningful only
        against another height from the same map.
        """
        if not self.covers(x, y):
            return None
        cx = (x - self.x0) / self.step
        cy = (y - self.y0) / self.step
        # Clamp the corner index, not the position: a point exactly on the last
        # row must interpolate inside the final cell, not off the end of it.
        j0 = min(int(cx), self.grid.shape[1] - 2)
        i0 = min(int(cy), self.grid.shape[0] - 2)
        fx = cx - j0
        fy = cy - i0
        q00 = float(self.grid[i0, j0])
        q10 = float(self.grid[i0, j0 + 1])
        q01 = float(self.grid[i0 + 1, j0])
        q11 = float(self.grid[i0 + 1, j0 + 1])
        top = q00 + (q10 - q00) * fx
        bottom = q01 + (q11 - q01) * fx
        return self.height_min + (top + (bottom - top) * fy) * self.quant


def _index_path() -> Path:
    return resource_dir() / TERRAIN_DIR / "index.json"


@lru_cache(maxsize=1)
def _index() -> dict:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"maps": {}}


def available_maps() -> dict[str, str]:
    """``{map_key: label}`` for every map with a height grid, in file order."""
    return {k: v.get("label", k) for k, v in _index().get("maps", {}).items()}


def _decode(blob: bytes, ny: int, nx: int) -> np.ndarray:
    """Undo the zigzag row-delta coding described in ``terrain/index.json``."""
    zz = np.frombuffer(lzma.decompress(blob), dtype="<u2")
    if zz.size != ny * nx:
        raise ValueError(f"terrain grid is {zz.size} samples, expected {ny * nx}")
    zz = zz.astype(np.int64).reshape(ny, nx)
    delta = (zz >> 1) ^ -(zz & 1)
    # Both passes are cumulative sums, so the whole grid unpacks without a
    # Python-level loop over its 2.3 million samples.
    out = np.empty_like(delta)
    out[:, 0] = np.cumsum(delta[:, 0])
    out[:, 1:] = out[:, 0:1] + np.cumsum(delta[:, 1:], axis=1)
    return out.astype(np.uint16)


@lru_cache(maxsize=2)
def load(key: str) -> TerrainMap | None:
    """Read one map's grid, or None if it is missing or unreadable.

    Cached: the grid is a few megabytes and decoding it takes about 90 ms, so
    it is decoded once per map and then shared.
    """
    spec = _index().get("maps", {}).get(key)
    if spec is None:
        return None
    try:
        blob = (resource_dir() / TERRAIN_DIR / spec["file"]).read_bytes()
        grid = _decode(blob, int(spec["ny"]), int(spec["nx"]))
    except (OSError, KeyError, ValueError, lzma.LZMAError):
        return None
    return TerrainMap(
        key=key,
        label=spec.get("label", key),
        grid=grid,
        x0=float(spec["x0"]),
        y0=float(spec["y0"]),
        step=float(spec["step_units"]),
        height_min=float(spec["height_min_m"]),
        quant=float(spec["quant_m"]),
        bounds=spec.get("playable_bounds", {}),
    )
