"""The projectile model behind the height correction.

Why a model at all
------------------
The firing tables say what to dial for a target at the gun's own height.  They
cannot say what to dial for a target 200 m above it, and on Bakurani that is
the normal case rather than the exception: between two points at the ranges the
SPH-2 covers, the height difference is over 100 m half the time.

What the model is
-----------------
A point mass under gravity and quadratic air drag -- the trajectory the game
itself integrates.  Its constants were recovered from the firing tables: the
drag exponent came out at 1.99 when left free, which is the strongest evidence
that this is the right shape of model, and the two SPH-2 arcs, which the same
constants have to satisfy at once, pin them down.  See ``firing_tables.json``
for the numbers and their fit residuals.

How it is used
--------------
Never for the elevation itself -- only to answer "what flat range does this
sloped shot behave like?".  The elevation then comes out of the table at that
equivalent range, so a level shot reproduces the table exactly, and the model
is left carrying only the part the table cannot know.  That keeps the fit's
couple of metres of range error out of the answer, since it cancels between the
sloped shot and the level one it is measured against.

Integration runs over horizontal distance rather than time, using

    dz/dx  = z'
    dz'/dx = -g / u^2
    du/dx  = -k * u * sqrt(1 + z'^2)

where ``u`` is horizontal speed.  The first two lines are exact for any drag
that pulls against the velocity, whatever its law; only the third assumes the
drag is quadratic.  Distance is what the tables and the map are keyed on, so
this form lands the samples exactly where they are needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Standard gravity. The model's own scale absorbs any difference from the
#: game's value: scaling g, v0^2 and 1/k together leaves every trajectory
#: shape untouched, so only the shape was ever identifiable, and it is all
#: the correction needs.
GRAVITY = 9.81

#: Trajectories traced per arc. The answer is interpolated between them, so
#: this sets a resolution, not a limit; 256 puts neighbouring traces about
#: 2 mil apart on the widest arc.
TRACES_PER_ARC = 256

#: Integration step over horizontal distance, in metres.
STEP_M = 2.0

#: How far below the gun a trace is still worth following, in metres. Deeper
#: than the relief of any map, so nothing a player can mark is cut off.
#:
#: A trace has to be dropped somewhere: integrating over distance is singular
#: for a shell falling vertically -- horizontal speed tends to zero, and the
#: curvature ``-g/u^2`` it divides by tends to infinity. Long past its impact a
#: trace approaches exactly that, so following it any further both means
#: nothing and overflows.
Z_FLOOR_M = -1500.0

#: Floor under the horizontal speed in the rate calculation, in metres per
#: second. It bounds the curvature the integrator has to take a step of, so a
#: trace heading for the singularity plunges past Z_FLOOR_M in one step and is
#: dropped there, instead of overflowing.
#:
#: Low on purpose: this must not touch a trajectory anyone would fire. A mortar
#: at its steepest leaves the tube with about 14 m/s of horizontal speed, and
#: clamping that would quietly bend the shots the correction exists for.
U_CLAMP_MS = 0.5


@dataclass(frozen=True)
class Projectile:
    """What the gun throws, and how its dial maps onto a launch angle.

    The dial is not the barrel angle: ``mil_scale`` and ``mil_offset`` are the
    affine map from the number in the HUD to the real launch angle, recovered
    with everything else. Nothing here is meaningful on its own -- these are
    the parameters of a fit, not measured quantities.
    """

    #: Muzzle speed, m/s.
    v0: float
    #: Quadratic drag coefficient, 1/m: the deceleration is ``k * speed^2``.
    drag_k: float
    #: launch angle = mil_scale * dial + mil_offset, radians.
    mil_scale: float
    mil_offset: float


def _trace(
    theta: np.ndarray, proj: Projectile, x_max: float, step: float = STEP_M
) -> tuple[np.ndarray, np.ndarray]:
    """Integrate every launch angle at once.

    Returns the shared x grid and z per angle, nan once a trace has been
    abandoned -- see ``Z_FLOOR_M``.  A shared grid is the point of integrating
    over distance: a solve is then interpolation down one column.
    """
    z = np.zeros_like(theta)
    slope = np.tan(theta)
    u = proj.v0 * np.cos(theta)
    n = int(math.ceil(x_max / step))
    xs = np.arange(n + 1) * step
    out = np.full((len(theta), n + 1), np.nan)
    out[:, 0] = 0.0
    live = np.ones(len(theta), bool)

    def rates(z, slope, u):
        uu = np.maximum(u, U_CLAMP_MS)
        return slope, -GRAVITY / (uu * uu), -proj.drag_k * uu * np.sqrt(1.0 + slope * slope)

    for i in range(n):
        if not live.any():
            break
        k1 = rates(z, slope, u)
        k2 = rates(z + 0.5 * step * k1[0], slope + 0.5 * step * k1[1], u + 0.5 * step * k1[2])
        k3 = rates(z + 0.5 * step * k2[0], slope + 0.5 * step * k2[1], u + 0.5 * step * k2[2])
        k4 = rates(z + step * k3[0], slope + step * k3[1], u + step * k3[2])
        z = z + step * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0]) / 6
        slope = slope + step * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1]) / 6
        u = u + step * (k1[2] + 2 * k2[2] + 2 * k3[2] + k4[2]) / 6
        live &= (z > Z_FLOOR_M) & np.isfinite(z) & np.isfinite(slope)
        out[live, i + 1] = z[live]
    return xs, out


def _first_descending_crossing(xs: np.ndarray, z: np.ndarray, level: float) -> np.ndarray:
    """Where each trace first drops through ``level``; nan if it never does."""
    known = np.isfinite(z)
    below = known & (z < level)
    above = known & (z >= level)
    step_down = below[:, 1:] & above[:, :-1]
    out = np.full(z.shape[0], np.nan)
    rows = np.nonzero(step_down.any(axis=1))[0]
    cols = step_down.argmax(axis=1)
    for r in rows:
        c = cols[r]
        z0, z1 = z[r, c], z[r, c + 1]
        out[r] = xs[c] + (level - z0) / (z1 - z0) * (xs[c + 1] - xs[c])
    return out


@dataclass(frozen=True)
class ArcBundle:
    """Traces spanning one arc, precomputed so a solve is only interpolation.

    Built once per arc: the traces depend on the weapon, not on the shot.
    """

    #: Dial settings, ascending.
    dials: np.ndarray
    #: Horizontal distance grid.
    xs: np.ndarray
    #: Height above the muzzle, ``[dial, x]``.
    z: np.ndarray
    #: Range at which each trace returns to the muzzle's height.
    flat_range: np.ndarray

    @classmethod
    def build(cls, proj: Projectile, dial_lo: float, dial_hi: float, x_max: float) -> "ArcBundle":
        # Ascending, whatever order the caller had them in: the tables are
        # sorted by range, which runs the dial backwards on a high arc, and
        # every lookup here interpolates against these.
        low, high = sorted((float(dial_lo), float(dial_hi)))
        dials = np.linspace(low, high, TRACES_PER_ARC)
        theta = proj.mil_scale * dials + proj.mil_offset
        xs, z = _trace(theta, proj, x_max)
        return cls(dials=dials, xs=xs, z=z, flat_range=_first_descending_crossing(xs, z, 0.0))

    def _height_at(self, distance: float) -> np.ndarray:
        """Height of every trace at one horizontal distance, nan where none."""
        if distance <= 0.0:
            return self.z[:, 0]
        pos = distance / (self.xs[1] - self.xs[0])
        j = min(int(pos), self.z.shape[1] - 2)
        f = pos - j
        return self.z[:, j] * (1.0 - f) + self.z[:, j + 1] * f

    def equivalent_flat_range(self, distance: float, height_gain: float) -> float | None:
        """Flat range that shoots like this sloped shot, or None if unreachable.

        A shot that has to travel ``distance`` horizontally while climbing
        ``height_gain`` behaves, on the dial, like a level shot to the range
        returned here.  Uphill that range is longer than the distance, downhill
        shorter.
        """
        if distance > self.xs[-1]:
            return None
        heights = self._height_at(distance)
        reach = heights - height_gain
        ok = np.isfinite(reach)
        if not ok.any():
            return None
        # Where the traces cross the target height. Within one arc the height
        # at a fixed distance moves monotonically with the dial, so there is
        # normally one crossing; if the arc turns over, the crossing closest to
        # the flat solution is the one on the same side of the turn.
        sign = np.sign(reach)
        cross = np.nonzero((sign[:-1] * sign[1:] < 0) & ok[:-1] & ok[1:])[0]
        if not len(cross):
            return None
        if len(cross) > 1:
            flat = self.equivalent_flat_range(distance, 0.0)
            if flat is not None:
                ref = float(np.interp(flat, self.flat_range, self.dials))
                cross = cross[np.argmin(np.abs(self.dials[cross] - ref))][None]
        i = int(cross[0])
        r0, r1 = reach[i], reach[i + 1]
        dial = self.dials[i] + (0.0 - r0) / (r1 - r0) * (self.dials[i + 1] - self.dials[i])
        # Map the dial back through the model's own flat table, so the number
        # handed on is a range the real table can be read at.
        good = np.isfinite(self.flat_range)
        if good.sum() < 2:
            return None
        return float(np.interp(dial, self.dials[good], self.flat_range[good]))
