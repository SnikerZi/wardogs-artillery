import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.ballistics import Point, load_weapons, solve  # noqa: E402


@pytest.fixture(scope="module")
def shipped():
    return load_weapons(ROOT / "src" / "wardogs_calc" / "firing_tables.json")


def test_both_weapons_carry_a_model(shipped):
    for weapon in shipped.values():
        assert weapon.corrects_height, weapon.key


def _ranges(weapon, margin=20.0):
    lo = weapon.min_range_m + margin
    hi = weapon.max_range_m - margin
    return [lo + (hi - lo) * i / 6 for i in range(7)]


@pytest.mark.parametrize("key", ["mortar", "sph2"])
def test_a_level_shot_reproduces_the_table(shipped, key):
    """The whole point of anchoring: no height, no change to the old answer."""
    weapon = shipped[key]
    for range_m in _ranges(weapon):
        flat = weapon.solve(range_m)
        level = weapon.solve(range_m, 0.0)
        assert [s.elevation_mil for s in level] == [s.elevation_mil for s in flat]


@pytest.mark.parametrize("key", ["mortar", "sph2"])
def test_a_level_shot_reads_the_table_at_its_own_range(shipped, key):
    weapon = shipped[key]
    for range_m in _ranges(weapon):
        for sol in weapon.solve(range_m, 0.0):
            if sol.in_range:
                assert sol.equivalent_range_m == pytest.approx(range_m, abs=0.5)


@pytest.mark.parametrize("key", ["mortar", "sph2"])
def test_uphill_reads_longer_and_downhill_shorter(shipped, key):
    """A climb costs range, a drop gives it back -- on every arc."""
    weapon = shipped[key]
    climb = 0.05 * weapon.max_range_m
    for range_m in _ranges(weapon, margin=0.15 * weapon.max_range_m):
        up = weapon.solve(range_m, climb)
        down = weapon.solve(range_m, -climb)
        for rising, falling in zip(up, down):
            if not (rising.in_range and falling.in_range):
                continue
            assert rising.equivalent_range_m > range_m
            assert falling.equivalent_range_m < range_m


@pytest.mark.parametrize("key", ["mortar", "sph2"])
def test_the_equivalent_range_grows_with_the_climb(shipped, key):
    weapon = shipped[key]
    middle = 0.5 * (weapon.min_range_m + weapon.max_range_m)
    step = 0.02 * weapon.max_range_m
    for index, arc in enumerate(weapon.arcs):
        seen = []
        for gain in [-3 * step, -step, 0.0, step, 3 * step]:
            sol = weapon.solve(middle, gain)[index]
            if sol.in_range:
                seen.append(sol.equivalent_range_m)
        assert seen == sorted(seen), arc.name
        assert len(seen) >= 3, arc.name


def test_a_height_no_trajectory_reaches_is_refused(shipped):
    """Straight up out of reach: a refusal, not a number from the table edge."""
    weapon = shipped["mortar"]
    for sol in weapon.solve(300.0, 5000.0):
        assert not sol.in_range
        assert "no trajectory reaches" in sol.note


def test_a_correction_that_leaves_the_envelope_says_so(shipped):
    """A climb can push the dial past the gun's reach, and the note explains it.

    Quoting the bare limit would leave the player comparing it against a range
    they can see is inside it.
    """
    solutions = shipped["mortar"].solve(680.0, 30.0)
    assert all(not s.in_range for s in solutions)
    assert all("beyond maximum range" in s.note for s in solutions)
    assert all("shoot like" in s.note for s in solutions)
    assert all(s.equivalent_range_m > 684.0 for s in solutions)


def test_running_out_of_reach_reads_differently_from_running_out_of_dial(shipped):
    """Near maximum range there is no trajectory left to climb on at all."""
    weapon = shipped["sph2"]
    solutions = weapon.solve(2600.0, 150.0)
    assert all(not s.in_range for s in solutions)
    assert all("no trajectory reaches" in s.note for s in solutions)
    assert all(s.equivalent_range_m is None for s in solutions)


def test_a_drop_can_bring_a_far_target_back_into_range(shipped):
    """The envelope is judged on the dial's range, so downhill reaches further."""
    weapon = shipped["sph2"]
    beyond = weapon.max_range_m + 60.0
    assert all(not s.in_range for s in weapon.solve(beyond))
    downhill = weapon.solve(beyond, -250.0)
    assert any(s.in_range for s in downhill)
    for sol in downhill:
        if sol.in_range:
            assert sol.equivalent_range_m <= weapon.max_range_m


def test_the_correction_is_worth_having(shipped):
    """A hundred metres of height is tens of mils, not a rounding difference."""
    weapon = shipped["sph2"]
    flat = weapon.solve(1800.0)[0]
    uphill = weapon.solve(1800.0, 100.0)[0]
    assert abs(uphill.elevation_mil - flat.elevation_mil) > 20.0


def test_solve_passes_the_height_through(shipped):
    mission = solve(Point(60.0, 60.0), Point(70.0, 60.0), shipped["sph2"],
                    height_gain_m=40.0)
    assert mission.height_gain_m == 40.0
    assert mission.range_m == pytest.approx(1000.0)
    assert mission.solutions[0].equivalent_range_m > 1000.0


def test_no_height_leaves_the_mission_uncorrected(shipped):
    mission = solve(Point(60.0, 60.0), Point(70.0, 60.0), shipped["sph2"])
    assert mission.height_gain_m is None
    assert mission.solutions[0].equivalent_range_m == pytest.approx(1000.0, abs=0.5)


def test_the_model_itself_reproduces_the_table_at_zero_height(shipped):
    """The anchoring, tested through the model rather than around it.

    Weapon.solve skips the correction outright for a zero height difference,
    so this goes at the bundle: asked for the flat range that shoots like a
    level shot, it has to hand back the range it was given.
    """
    for weapon in shipped.values():
        for arc in weapon.arcs:
            bundle = weapon._bundle(arc)
            lo = max(arc.min_range, weapon.min_range_m) + 5.0
            hi = min(arc.max_range, weapon.max_range_m) - 5.0
            errors = []
            for i in range(25):
                distance = lo + (hi - lo) * i / 24
                got = bundle.equivalent_flat_range(distance, 0.0)
                assert got is not None, (weapon.key, arc.name, distance)
                errors.append(abs(got - distance))
            assert max(errors) < 0.5, (weapon.key, arc.name, max(errors))
