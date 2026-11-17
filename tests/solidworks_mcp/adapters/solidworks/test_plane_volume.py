"""The volume on each side of a plane: the exact box cut, and the COM on a fake part.

The fake part is a 40 x 10 x 40 block, y 0..10; a fill line at y 4 leaves
40 x 6 x 40 = 9600 mm3 above it and 6400 mm3 below. A cup (40 x 30 x 40 with
a 30 x 30 pocket open at the top, floor 5 mm thick) filled to y 20 holds
30 x 15 x 30 = 13500 mm3, 13.5 ml.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import plane_volume as pv
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest

from .fake_bodies import Harness
from .fake_volume import RefPlane, VolumeWorld

# ---- the exact box cut -------------------------------------------------------------


@pytest.mark.parametrize(
    ("normal", "offset", "expected"),
    [
        ([0, 1, 0], 4.0, 6400.0),
        ([0, -1, 0], -4.0, 9600.0),
        ([0, 1, 0], -1.0, 0.0),
        ([0, 1, 0], 20.0, 16000.0),
        ([0, 0, 0], 0.0, 16000.0),
        ([0, 0, 0], -1.0, 0.0),
    ],
)
def test_a_block_cut_square(
    normal: list[float], offset: float, expected: float
) -> None:
    box = [0.0, 0.0, 0.0, 40.0, 10.0, 40.0]

    assert pv.box_volume_below(box, normal, offset) == pytest.approx(expected)


def test_a_cube_cut_on_its_diagonals() -> None:
    cube = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    third = 1 / math.sqrt(3)

    # Through the centre: half; through a corner's three neighbours: 1/6.
    assert pv.box_volume_below(cube, [third] * 3, 1.5 * third) == pytest.approx(0.5)
    assert pv.box_volume_below(cube, [1, 1, 1], 1.0) == pytest.approx(1 / 6)
    assert pv.box_volume_below(cube, [-1, -1, -1], -2.0) == pytest.approx(1 / 6)
    assert pv.box_volume_below(cube, [1, 1, 0], 1.0) == pytest.approx(0.5)


def test_the_cubes_reach_past_the_box() -> None:
    size, foot = pv.cube_size_m([0, 0, 0, 0.04, 0.01, 0.04], [0, 0.004, 0], [0, 1, 0])

    assert foot == pytest.approx([0.02, 0.004, 0.02])
    corner = math.dist([0, 0, 0], foot)
    assert size == pytest.approx(2 * math.dist([0, 0.01, 0], foot) + 0.01)
    assert size > 2 * corner


@pytest.mark.parametrize(
    ("plane", "point", "normal", "fragment"),
    [
        ("Top Plane", [0, 0, 0], None, "not both"),
        (None, [0, 0, 0], None, "give plane"),
        (None, None, [0, 1, 0], "give plane"),
    ],
)
def test_a_request_must_name_one_plane(
    plane: str | None, point: Any, normal: Any, fragment: str
) -> None:
    assert fragment in (pv.plane_problem(plane, point, normal) or "")
    with pytest.raises(InvalidRequest, match=fragment):
        pv.check_plane(plane, point, normal)


def test_a_plane_named_or_given() -> None:
    assert pv.plane_problem("Top", None, None) is None
    pv.check_plane(None, [0, 0, 0], [0, 1, 0])
    assert pv.mass_g(6400.0, 1.15) == pytest.approx(7.36)
    assert pv.mass_g(None, 1.15) is None and pv.mass_g(1.0, None) is None


# ---- the COM, on a fake part --------------------------------------------------------


@pytest.fixture(autouse=True)
def _plain_com(monkeypatch: pytest.MonkeyPatch) -> None:
    from solidworks_mcp.adapters.solidworks import density

    monkeypatch.setattr(
        density,
        "_sw_type_info",
        type("T", (), {"flagged": staticmethod(lambda o, *_: o)}),
    )


def _block() -> VolumeWorld:
    world = VolumeWorld({"Boss-Extrude1": [0.0, 0.0, 0.0, 40.0, 10.0, 40.0]})
    world.add_plane("Fill line", [0.0, 4.0, 0.0], [0.0, 1.0, 0.0])
    world.add_plane("Top Plane", [0.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    world.document_density = 1150.0
    return world


def _cup() -> VolumeWorld:
    world = VolumeWorld({"Cup": [0.0, 0.0, 0.0, 40.0, 30.0, 40.0]})
    world.bodies[0].holes.append([5.0, 5.0, 5.0, 35.0, 31.0, 35.0])
    world.add_plane("Fill line", [0.0, 20.0, 0.0], [0.0, 1.0, 0.0])
    return world


def _measure(world: VolumeWorld, **kw: Any) -> dict[str, Any]:
    return pv.volume_by_plane(Harness(world), **kw)


@pytest.mark.parametrize("base_centre", [True, False])
def test_a_fill_line_splits_the_block(base_centre: bool) -> None:
    world = _block()
    world.base_centre = base_centre

    found = _measure(world, plane="Fill line")

    assert found["above"]["volume_mm3"] == pytest.approx(9600.0)
    assert found["below"]["volume_mm3"] == pytest.approx(6400.0)
    assert found["below"]["mass_g"] == pytest.approx(7.36)
    assert found["total_volume_mm3"] == pytest.approx(16000.0)
    assert found["check_ok"] is True and abs(found["check_mm3"]) < 1e-6
    assert found["plane"] == {
        "name": "Fill line",
        "origin_mm": [0.0, 4.0, 0.0],
        "normal": [0.0, 1.0, 0.0],
    }
    assert found["density_source"] == "solidworks"
    assert found["density_g_cm3"] == pytest.approx(1.15)
    assert found["bodies"][0]["below_mm3"] == pytest.approx(6400.0)
    assert "enclosed_mm3" not in found["below"]
    # Two cubes made, and the part's own body never in a boolean (the fake
    # refuses Operations2 on it).
    assert world.cubes == 2
    assert "CreateBodyFromBox3" in world.app.modeler.flagged


def test_a_default_plane_by_its_short_name() -> None:
    found = _measure(_block(), plane="Top")

    assert found["plane"]["name"] == "Top Plane"
    assert found["above"]["volume_mm3"] == pytest.approx(16000.0)
    assert found["below"]["volume_mm3"] == 0.0


def test_a_point_and_a_normal_pointing_down() -> None:
    found = _measure(
        _block(), point_mm=[0.0, 4.0, 0.0], normal=[0.0, -1.0, 0.0], density_g_cm3=2.0
    )

    assert found["above"]["volume_mm3"] == pytest.approx(6400.0)
    assert found["below"]["mass_g"] == pytest.approx(19.2)
    assert found["density_source"] == "input"
    assert found["plane"]["name"] is None


def test_what_a_cup_holds_to_the_line() -> None:
    world = _cup()

    found = _measure(world, plane="Fill line", enclosed=True)

    assert found["below"]["enclosed_mm3"] == pytest.approx(13500.0)
    assert found["below"]["enclosed_ml"] == pytest.approx(13.5)
    assert found["below"]["pockets"] == 1
    # Above the line the pocket opens out of the cup: nothing is enclosed.
    assert found["above"]["enclosed_mm3"] == 0.0
    solid = 40 * 30 * 40 - 30 * 25 * 30
    assert found["total_volume_mm3"] == pytest.approx(solid)


def test_a_sealed_void_is_not_what_the_part_holds() -> None:
    world = VolumeWorld({"Block": [0.0, 0.0, 0.0, 40.0, 30.0, 40.0]})
    world.bodies[0].holes.append([10.0, 2.0, 10.0, 30.0, 8.0, 30.0])
    world.add_plane("Fill line", [0.0, 20.0, 0.0], [0.0, 1.0, 0.0])

    found = _measure(world, plane="Fill line", enclosed=True)

    assert found["below"]["enclosed_mm3"] == 0.0
    assert found["below"]["pockets"] == 0


def test_bodies_far_from_a_pocket_and_touching_it() -> None:
    world = _cup()
    world.bodies.append(type(world.bodies[0])(world, "Far", [100.0, 0, 0, 110, 10, 10]))
    world.bodies.append(type(world.bodies[0])(world, "Plug", [10.0, 0, 10, 20, 5, 20]))

    found = _measure(world, plane="Fill line", enclosed=True)

    assert found["below"]["enclosed_mm3"] == pytest.approx(13500.0)
    assert [b["name"] for b in found["bodies"]] == ["Cup", "Far", "Plug"]


def test_named_bodies_only() -> None:
    world = _block()
    world.bodies.append(
        type(world.bodies[0])(world, "Lid", [0.0, 20.0, 0.0, 40.0, 30.0, 40.0])
    )

    found = _measure(world, plane="Fill line", bodies=["Lid"])

    assert found["above"]["volume_mm3"] == pytest.approx(16000.0)
    assert [b["name"] for b in found["bodies"]] == ["Lid"]


def test_a_mismatch_between_the_sides_and_the_volume_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pv, "intersect_mm3", lambda adapter, a, b: 1.0)

    found = _measure(_block(), plane="Fill line")

    assert found["check_ok"] is False
    assert found["check_mm3"] == pytest.approx(2.0 - 16000.0)


@pytest.mark.parametrize(
    ("density_hidden", "mass_fails", "expected"),
    [(True, False, 1.15), (False, True, None)],
)
def test_the_part_density_from_mass_and_volume(
    density_hidden: bool, mass_fails: bool, expected: float | None
) -> None:
    world = _block()
    world.density_hidden, world.mass_fails = density_hidden, mass_fails

    found = _measure(world, plane="Fill line")

    assert found["density_g_cm3"] == (
        pytest.approx(expected) if expected is not None else None
    )
    assert found["density_source"] == ("solidworks" if expected else None)


def test_no_density_without_mass_or_volume() -> None:
    world = _block()
    world.density_hidden = True
    world.document_density = 0.0

    assert _measure(world, plane="Fill line")["density_g_cm3"] is None


@pytest.mark.parametrize(
    ("setup", "error", "fragment"),
    [
        (lambda w: None, LookupError, "Plane not found: Nowhere"),
        (lambda w: w.add_feature("Nowhere", "Extrusion"), LookupError, "not a plane"),
        (
            lambda w: w.add_feature("Nowhere", "RefPlane"),
            LookupError,
            "could not be read",
        ),
    ],
)
def test_a_plane_that_cannot_be_used(setup: Any, error: type, fragment: str) -> None:
    world = _block()
    setup(world)

    with pytest.raises(error, match=fragment):
        _measure(world, plane="Nowhere")


@pytest.mark.parametrize(
    ("switch", "fragment"),
    [
        ("modeler_fails", "GetModeler gave nothing"),
        ("box_fails", "made no box"),
        ("cube_mass_fails", "centre could not be read"),
        ("cube_move_fails", "could not be moved onto the plane"),
    ],
)
def test_a_cube_that_cannot_be_made(switch: str, fragment: str) -> None:
    world = _block()
    setattr(world, switch, True)

    with pytest.raises(RuntimeError, match=fragment):
        _measure(world, plane="Fill line")


def test_a_cut_that_fails_is_an_error() -> None:
    world = _cup()
    world.cut_raises = True

    with pytest.raises(RuntimeError, match="Operations2 \\(cut\\) failed on Cup"):
        _measure(world, plane="Fill line", enclosed=True)


def test_a_pocket_whose_far_point_cannot_be_read_is_left_out() -> None:
    world = _cup()
    world.extreme_fails = True

    found = _measure(world, plane="Fill line", enclosed=True)

    assert found["below"]["enclosed_mm3"] == 0.0


def test_bodies_that_cannot_be_measured() -> None:
    empty = VolumeWorld({})
    empty.add_plane("Top Plane", [0.0, 0.0, 0.0], [0.0, 1.0, 0.0])
    with pytest.raises(RuntimeError, match="no solid body"):
        _measure(empty, plane="Top Plane")
    with pytest.raises(InvalidRequest, match="names a body twice"):
        _measure(_block(), plane="Top Plane", bodies=["Boss-Extrude1"] * 2)
    with pytest.raises(LookupError, match="Body\\(ies\\) not found: Lid"):
        _measure(_block(), plane="Top Plane", bodies=["Lid"])
    world = _block()
    world.bodies[0].GetBodyBox = lambda: ()  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="box could not be read"):
        _measure(world, plane="Top Plane")


def test_a_plane_transform_gives_its_point_and_normal() -> None:
    plane = RefPlane([0.0, 0.02, 0.0], [0.0, 1.0, 0.0])

    assert plane.Transform.ArrayData[6:12] == [0.0, 1.0, 0.0, 0.0, 0.02, 0.0]
