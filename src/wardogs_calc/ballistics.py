"""Geometry and firing-solution math for WARDOGS indirect fire.

Coordinate system
-----------------
WARDOGS shows map positions as an X/Y pair on a 0..163.84 scale that spans the
full 16.384 x 16.384 km terrain.  One coordinate unit is therefore exactly
100 m, 0.10 is ten metres and 0.01 is one metre.  Y grows towards the north.

Height
------
The tables are keyed on horizontal range and were measured on level ground, so
on their own they answer the wrong question: Bakurani has a kilometre of relief
across it, and between two points at the ranges the SPH-2 covers the height
difference is over 100 m half the time.  Given the height difference, the
projectile model in ``trajectory`` converts the sloped shot into the level shot
that needs the same dial -- its *equivalent range* -- and the elevation is then
read from the table there.  A level shot has an equivalent range equal to its
own, so it reads the table exactly as before; uphill reads longer, downhill
shorter.  Everything a weapon refuses to fire is judged on the equivalent
range too, since that is what the dial is actually set to.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from .trajectory import ArcBundle, Projectile

#: Metres per one unit of the in-game X/Y readout.
METRES_PER_UNIT = 100.0

#: Full terrain extent in readout units (16.384 km / 100 m).
MAP_EXTENT_UNITS = 163.84

#: NATO mils in a full circle.
MILS_PER_CIRCLE = 6400.0

_DEG_TO_MIL = MILS_PER_CIRCLE / 360.0


@dataclass(frozen=True)
class Point:
    """A position on the map, in readout units."""

    x: float
    y: float

    def __str__(self) -> str:
        return f"X {self.x:.2f}  Y {self.y:.2f}"


@dataclass(frozen=True)
class Solution:
    """One firing solution produced for a weapon/arc pair."""

    arc: str
    elevation_mil: float
    in_range: bool
    note: str = ""
    #: Range the table was read at. Equal to the target's own range for a level
    #: shot, longer uphill, shorter downhill. None when there is no solution.
    equivalent_range_m: float | None = None


@dataclass(frozen=True)
class FireMission:
    """Everything the UI needs to display for a gun/target pair."""

    gun: Point
    target: Point
    range_m: float
    azimuth_deg: float
    azimuth_mil: float
    solutions: tuple[Solution, ...]
    #: Target height minus gun height, in metres; None when the terrain could
    #: not be sampled, in which case the solutions are level-ground ones.
    height_gain_m: float | None = None


def range_metres(gun: Point, target: Point, metres_per_unit: float = METRES_PER_UNIT) -> float:
    """Ground distance between two map positions, in metres."""
    dx = (target.x - gun.x) * metres_per_unit
    dy = (target.y - gun.y) * metres_per_unit
    return math.hypot(dx, dy)


def azimuth_degrees(gun: Point, target: Point) -> float:
    """Bearing from gun to target: 0 deg is north (+Y), growing clockwise.

    Scale cancels out here, so no metres_per_unit is needed.
    """
    return math.degrees(math.atan2(target.x - gun.x, target.y - gun.y)) % 360.0


def degrees_to_mils(degrees: float) -> float:
    return degrees * _DEG_TO_MIL


@dataclass(frozen=True)
class Arc:
    """A single trajectory of a weapon: a monotonic range -> elevation table."""

    name: str
    #: Ascending-by-range (range_m, elevation_mil) pairs.
    table: tuple[tuple[float, float], ...]

    @property
    def min_range(self) -> float:
        return self.table[0][0]

    @property
    def max_range(self) -> float:
        return self.table[-1][0]

    def elevation_for(self, range_m: float) -> Solution:
        """Linearly interpolate the elevation for ``range_m``.

        Outside the table the nearest end value is returned with ``in_range``
        set to False, so the UI can show *why* there is no solution.
        """
        if range_m < self.min_range:
            return Solution(
                self.name, self.table[0][1], False, f"no solution below {self.min_range:.0f} m"
            )
        if range_m > self.max_range:
            return Solution(
                self.name, self.table[-1][1], False, f"no solution beyond {self.max_range:.0f} m"
            )
        for (r0, m0), (r1, m1) in zip(self.table, self.table[1:]):
            if r0 <= range_m <= r1:
                span = r1 - r0
                t = 0.0 if span == 0 else (range_m - r0) / span
                return Solution(self.name, m0 + t * (m1 - m0), True)
        return Solution(self.name, self.table[-1][1], True)


@dataclass(frozen=True)
class Weapon:
    key: str
    label: str
    arcs: tuple[Arc, ...]
    short: str = ""
    #: What the weapon will actually fire. The tables run past this at both
    #: ends, so the envelope is tracked separately from the table extent.
    min_range_m: float | None = None
    max_range_m: float | None = None
    #: Projectile model for the height correction. Without one the weapon
    #: still solves, level-ground only.
    projectile: Projectile | None = None
    #: Traces per arc, built on first use: they depend on the weapon, not on
    #: the shot, and cost about 50 ms an arc.
    _bundles: dict[str, ArcBundle] = field(
        default_factory=dict, compare=False, repr=False
    )

    @property
    def chip(self) -> str:
        return self.short or self.label

    @property
    def corrects_height(self) -> bool:
        return self.projectile is not None

    def _bundle(self, arc: Arc) -> ArcBundle:
        if arc.name not in self._bundles:
            assert self.projectile is not None
            reach = max(arc.max_range, self.max_range_m or 0.0)
            self._bundles[arc.name] = ArcBundle.build(
                self.projectile,
                arc.table[0][1],
                arc.table[-1][1],
                reach * 1.25,
            )
        return self._bundles[arc.name]

    def _outside_envelope(self, range_m: float, equivalent: bool) -> str | None:
        """Why the weapon will not fire this range, or None if it will.

        ``range_m`` is the equivalent range -- the dial's range -- so when a
        height correction moved it, the note says so rather than quoting a
        figure the player cannot see anywhere on screen.
        """
        limit = None
        if self.min_range_m is not None and range_m < self.min_range_m:
            limit = f"below minimum range ({self.min_range_m:.0f} m)"
        elif self.max_range_m is not None and range_m > self.max_range_m:
            limit = f"beyond maximum range ({self.max_range_m:.0f} m)"
        if limit and equivalent:
            return f"{limit}: the slope makes it shoot like {range_m:.0f} m"
        return limit

    def solve(
        self, range_m: float, height_gain_m: float | None = None
    ) -> tuple[Solution, ...]:
        """Solutions for every arc, corrected for height where possible.

        ``height_gain_m`` is the target's height above the gun. None, or no
        projectile model, gives the level-ground answer.
        """
        correct = bool(height_gain_m) and self.projectile is not None
        solutions = []
        for arc in self.arcs:
            equivalent: float | None = range_m
            if correct:
                assert height_gain_m is not None
                equivalent = self._bundle(arc).equivalent_flat_range(
                    range_m, height_gain_m
                )
            if equivalent is None:
                # No launch angle in this arc passes through the target at all,
                # which is a different failure from being out of range: the
                # dial has the reach, the trajectory does not have the shape.
                where = "above" if (height_gain_m or 0) > 0 else "below"
                solutions.append(
                    Solution(
                        arc.name,
                        arc.table[-1][1],
                        False,
                        f"no trajectory reaches {abs(height_gain_m or 0):.0f} m "
                        f"{where} the gun at this range",
                    )
                )
                continue
            solution = replace(
                arc.elevation_for(equivalent), equivalent_range_m=equivalent
            )
            # The weapon envelope always wins over the arc's own verdict. The
            # tables deliberately run past what the gun will fire, so an arc
            # left to speak for itself both interpolates rows that cannot be
            # shot and, out of range, quotes the table edge rather than the
            # limit the player actually has.
            envelope = self._outside_envelope(equivalent, correct)
            if envelope:
                solution = replace(solution, in_range=False, note=envelope)
            solutions.append(solution)
        return tuple(solutions)


def load_weapons(path: Path) -> dict[str, Weapon]:
    """Read ``firing_tables.json`` into Weapon objects."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    weapons: dict[str, Weapon] = {}
    for key, spec in raw["weapons"].items():
        arcs = tuple(
            Arc(
                name=arc["name"],
                table=tuple(
                    (float(r), float(m))
                    for r, m in sorted(arc["table"], key=lambda pair: pair[0])
                ),
            )
            for arc in spec["arcs"]
        )
        model = spec.get("model")
        weapons[key] = Weapon(
            key=key,
            label=spec["label"],
            arcs=arcs,
            short=spec.get("short", ""),
            min_range_m=spec.get("min_range_m"),
            max_range_m=spec.get("max_range_m"),
            projectile=(
                Projectile(
                    v0=float(model["v0_ms"]),
                    drag_k=float(model["drag_k_per_m"]),
                    mil_scale=float(model["mil_to_rad"]),
                    mil_offset=float(model["mil_zero_rad"]),
                )
                if model
                else None
            ),
        )
    return weapons


def solve(
    gun: Point,
    target: Point,
    weapon: Weapon,
    metres_per_unit: float = METRES_PER_UNIT,
    height_gain_m: float | None = None,
) -> FireMission:
    rng = range_metres(gun, target, metres_per_unit)
    az = azimuth_degrees(gun, target)
    return FireMission(
        gun=gun,
        target=target,
        range_m=rng,
        azimuth_deg=az,
        azimuth_mil=degrees_to_mils(az),
        solutions=weapon.solve(rng, height_gain_m),
        height_gain_m=height_gain_m,
    )
