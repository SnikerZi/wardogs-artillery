import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc import terrain  # noqa: E402

DATA = ROOT / "src" / "wardogs_calc" / "terrain"


@pytest.fixture(scope="module")
def index():
    return json.loads((DATA / "index.json").read_text(encoding="utf-8"))


def test_both_maps_ship_a_grid(index):
    assert set(index["maps"]) == {"bakurani", "ozeti"}
    assert set(terrain.available_maps()) == {"bakurani", "ozeti"}


def test_payloads_match_their_checksums(index):
    for key, spec in index["maps"].items():
        blob = (DATA / spec["file"]).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == spec["sha256"], key


@pytest.mark.parametrize("key", ["bakurani", "ozeti"])
def test_grid_decodes_to_the_declared_shape(key, index):
    grid = terrain.load(key)
    assert grid is not None
    spec = index["maps"][key]
    assert grid.grid.shape == (spec["ny"], spec["nx"])


@pytest.mark.parametrize("key", ["bakurani", "ozeti"])
def test_heights_are_relative_to_the_map_floor(key):
    """Nothing may expose the source datum: it is 900 m off and meaningless."""
    grid = terrain.load(key)
    lowest = grid.height_at(*_argmin_position(grid))
    assert lowest == pytest.approx(0.0, abs=grid.quant)
    assert grid.height_min == 0.0


def _argmin_position(grid):
    flat = int(grid.grid.argmin())
    row, column = divmod(flat, grid.grid.shape[1])
    return grid.x0 + column * grid.step, grid.y0 + row * grid.step


@pytest.mark.parametrize("key", ["bakurani", "ozeti"])
def test_grid_covers_the_whole_playable_area(key):
    """A point a player can mark must never fall off the edge of the data."""
    grid = terrain.load(key)
    bounds = grid.bounds
    assert grid.x0 <= bounds["minX"] and grid.x1 >= bounds["maxX"]
    assert grid.y0 <= bounds["minY"] and grid.y1 >= bounds["maxY"]
    for x in (bounds["minX"], bounds["maxX"]):
        for y in (bounds["minY"], bounds["maxY"]):
            assert grid.height_at(x, y) is not None


def test_off_grid_returns_none():
    grid = terrain.load("bakurani")
    assert grid.height_at(grid.x0 - 0.01, grid.y0) is None
    assert grid.height_at(grid.x1 + 0.01, grid.y0) is None
    assert grid.height_at(grid.x0, grid.y1 + 0.01) is None


def test_the_far_corner_is_still_inside():
    """The last row and column interpolate inside the final cell, not past it."""
    grid = terrain.load("bakurani")
    assert grid.height_at(grid.x1, grid.y1) is not None


def test_sample_positions_return_their_own_value():
    """On a grid node the interpolation has to be the node, not a blend."""
    grid = terrain.load("bakurani")
    for row, column in ((0, 0), (5, 7), (700, 900), (1530, 1530)):
        x = grid.x0 + column * grid.step
        y = grid.y0 + row * grid.step
        expected = grid.height_min + float(grid.grid[row, column]) * grid.quant
        assert grid.height_at(x, y) == pytest.approx(expected, abs=1e-6)


def test_interpolation_stays_between_the_corners():
    grid = terrain.load("bakurani")
    row, column = 400, 400
    corners = [
        grid.height_min + float(grid.grid[row + dy, column + dx]) * grid.quant
        for dy in (0, 1)
        for dx in (0, 1)
    ]
    middle = grid.height_at(
        grid.x0 + (column + 0.5) * grid.step, grid.y0 + (row + 0.5) * grid.step
    )
    assert min(corners) <= middle <= max(corners)


def test_grids_are_cached_not_reread():
    assert terrain.load("bakurani") is terrain.load("bakurani")


def test_unknown_map_is_none():
    assert terrain.load("no-such-map") is None
