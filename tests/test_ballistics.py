import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wardogs_calc.ballistics import (  # noqa: E402
    Arc,
    Point,
    Weapon,
    azimuth_degrees,
    degrees_to_mils,
    load_weapons,
    range_metres,
    solve,
)


def test_one_unit_is_one_hundred_metres():
    assert range_metres(Point(0, 0), Point(1, 0)) == pytest.approx(100.0)
    assert range_metres(Point(0, 0), Point(0, 1)) == pytest.approx(100.0)


def test_range_uses_both_axes():
    # The pair visible in the reference screenshot, one grid cell away.
    got = range_metres(Point(83.12, 67.91), Point(84.12, 68.91))
    assert got == pytest.approx(100.0 * math.sqrt(2), abs=1e-6)


def test_hundredth_of_a_unit_is_one_metre():
    assert range_metres(Point(10.00, 10.00), Point(10.01, 10.00)) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "target,expected",
    [
        ((0, 1), 0.0),    # north
        ((1, 0), 90.0),   # east
        ((0, -1), 180.0),
        ((-1, 0), 270.0),
        ((1, 1), 45.0),
    ],
)
def test_azimuth_is_clockwise_from_north(target, expected):
    assert azimuth_degrees(Point(0, 0), Point(*target)) == pytest.approx(expected)


def test_mil_conversion_uses_nato_circle():
    assert degrees_to_mils(360.0) == pytest.approx(6400.0)
    assert degrees_to_mils(90.0) == pytest.approx(1600.0)


def _arc():
    return Arc("test", ((100.0, 800.0), (200.0, 600.0), (400.0, 200.0)))


def test_arc_interpolates_between_table_rows():
    assert _arc().elevation_for(150.0).elevation_mil == pytest.approx(700.0)
    assert _arc().elevation_for(300.0).elevation_mil == pytest.approx(400.0)


def test_arc_marks_out_of_range():
    too_close = _arc().elevation_for(50.0)
    too_far = _arc().elevation_for(9000.0)
    assert not too_close.in_range and "below 100" in too_close.note
    assert not too_far.in_range and "beyond 400" in too_far.note


def test_arc_endpoints_are_in_range():
    assert _arc().elevation_for(100.0).in_range
    assert _arc().elevation_for(400.0).in_range


def test_solve_reports_every_arc():
    weapon = Weapon("w", "W", (_arc(), Arc("high", ((100.0, 900.0), (400.0, 500.0)))))
    mission = solve(Point(0, 0), Point(0, 2), weapon)
    assert mission.range_m == pytest.approx(200.0)
    assert [s.arc for s in mission.solutions] == ["test", "high"]


def test_weapon_envelope_overrides_an_interpolatable_arc():
    # The shipped tables run past what the gun will actually fire, so a range
    # the arc can happily interpolate still has to be refused.
    arc = Arc("test", ((100.0, 800.0), (400.0, 200.0)))
    weapon = Weapon("w", "W", (arc,), min_range_m=150.0, max_range_m=300.0)
    assert arc.elevation_for(350.0).in_range
    solution = weapon.solve(350.0)[0]
    assert not solution.in_range and "maximum range" in solution.note
    assert not weapon.solve(120.0)[0].in_range


def test_the_envelope_is_quoted_rather_than_the_table_edge():
    # Below both the table and the envelope, the number the player can act on
    # is the gun's minimum, not where the table happens to start.
    weapon = Weapon(
        "w", "W", (Arc("test", ((100.0, 800.0), (400.0, 200.0))),), min_range_m=150.0
    )
    solution = weapon.solve(50.0)[0]
    assert not solution.in_range
    assert "150" in solution.note and "100" not in solution.note


def test_weapon_without_an_envelope_trusts_the_table():
    weapon = Weapon("w", "W", (Arc("test", ((100.0, 800.0), (400.0, 200.0))),))
    assert weapon.solve(350.0)[0].in_range


def test_weapon_chip_falls_back_to_the_full_label():
    assert Weapon("w", "Full name", ()).chip == "Full name"
    assert Weapon("w", "Full name", (), short="Name").chip == "Name"


# --- the shipped community tables ------------------------------------------


@pytest.fixture(scope="module")
def shipped():
    return load_weapons(ROOT / "src" / "wardogs_calc" / "firing_tables.json")


def test_bundled_tables_load_and_are_sorted(shipped):
    assert {"mortar", "sph2"} <= set(shipped)
    for weapon in shipped.values():
        for arc in weapon.arcs:
            ranges = [r for r, _ in arc.table]
            assert ranges == sorted(ranges)


def test_mortar_envelope_endpoints(shipped):
    mortar = shipped["mortar"]
    assert (mortar.min_range_m, mortar.max_range_m) == (132.0, 684.0)
    assert mortar.solve(132.0)[0].elevation_mil == pytest.approx(850.0)
    assert mortar.solve(684.0)[0].elevation_mil == pytest.approx(150.0)


def test_mortar_refuses_ranges_outside_its_envelope(shipped):
    mortar = shipped["mortar"]
    assert not mortar.solve(100.0)[0].in_range
    # 690 m is inside the table (which reaches 697) but past the envelope.
    assert not mortar.solve(690.0)[0].in_range


def test_mortar_elevation_falls_as_range_grows(shipped):
    arc = shipped["mortar"].arcs[0]
    mils = [arc.elevation_for(r).elevation_mil for r in (200, 300, 400, 500, 600)]
    assert mils == sorted(mils, reverse=True)


def test_sph2_has_both_arcs_with_a_low_arc_dead_zone(shipped):
    sph2 = shipped["sph2"]
    assert [arc.name for arc in sph2.arcs] == ["Low arc", "High arc"]
    low, high = sph2.solve(1000.0)
    assert not low.in_range, "the low arc does not reach below 1181 m"
    assert high.in_range


def test_sph2_endpoints(shipped):
    sph2 = shipped["sph2"]
    assert (sph2.min_range_m, sph2.max_range_m) == (780.0, 2629.0)
    assert sph2.solve(780.0)[1].elevation_mil == pytest.approx(1390.0)
    assert sph2.solve(1181.0)[0].elevation_mil == pytest.approx(20.0)
    assert all(not s.in_range for s in sph2.solve(3000.0))


def test_metres_per_unit_is_overridable():
    # Both shipped maps declare 100 m per unit, but the constant is exposed in
    # config.json in case a future map is scaled differently.
    assert range_metres(Point(0, 0), Point(1, 0), metres_per_unit=195.0) == pytest.approx(195.0)
    mission = solve(
        Point(0, 0), Point(0, 2), Weapon("w", "W", (_arc(),)), metres_per_unit=50.0
    )
    assert mission.range_m == pytest.approx(100.0)


def test_azimuth_is_independent_of_scale():
    assert azimuth_degrees(Point(10, 10), Point(11, 11)) == pytest.approx(45.0)
