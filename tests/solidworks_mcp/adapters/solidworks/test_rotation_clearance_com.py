"""The rotation sweep's COM on a fake whose bodies are balls.

An arm ball (r 5 mm) 50 mm out on X turns about Z toward a stop ball (r 5 mm)
50 mm out on Y: their centres close to 10 mm apart, so they touch, at
``90 - 2 asin(0.1)`` = 78.52 degrees.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import io as io_module
from solidworks_mcp.adapters.solidworks import limits
from solidworks_mcp.adapters.solidworks import rotation_clearance as rc
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest

from .fake_turning import Adapter, Ball, BallFace, Component, World, lens_mm3, place

CONTACT_DEG = 90.0 - 2.0 * math.degrees(math.asin(0.1))
Z = [0.0, 0.0, 1.0]


@pytest.fixture(autouse=True)
def _plain_com(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fake objects need no interface flagging or dispatch wrapping."""
    monkeypatch.setattr(rc, "_sw_type_info", SimpleNamespace(flagged=lambda o, *_: o))
    monkeypatch.setattr(io_module, "_as_com", lambda _adapter, obj, _iface: obj)


def _part(pivot_x: float = 0.0) -> World:
    world = World(doc_type=1)
    world.ball("Arm", [pivot_x + 50.0, 0.0, 0.0], 5.0)
    world.ball("Stop", [pivot_x, 50.0, 0.0], 5.0)
    return world


def _bodies(world: World, **kw: Any) -> dict[str, Any]:
    args = {
        "moving": "Arm",
        "other": "Stop",
        "axis": Z,
        "pivot_mm": [0.0, 0.0, 0.0],
        "range_deg": [-90.0, 90.0],
        "step_deg": 5.0,
        "tolerance_deg": 0.05,
        **kw,
    }
    return rc.body_rotation_clearance(Adapter(world), **args)


def test_the_lens_and_placement_helpers() -> None:
    assert lens_mm3(20, 5, 5) == 0.0
    assert lens_mm3(0, 5, 3) == pytest.approx(4 / 3 * math.pi * 27)
    assert lens_mm3(5, 5, 5) == pytest.approx(math.pi * 25 * 125 / 60)
    assert place([0, 1, 0, -1, 0, 0, 0, 0, 1, 0.01, 0, 0], [1, 0, 0]) == [
        10.0,
        1.0,
        0.0,
    ]


def test_a_body_turns_into_the_stop() -> None:
    world = _part()

    found = _bodies(world)

    assert found["kind"] == "bodies"
    assert found["positive"]["first_contact_deg"] == pytest.approx(
        CONTACT_DEG, abs=0.05
    )
    assert found["negative"]["first_contact_deg"] is None
    assert found["start_clearance_mm"] == pytest.approx(math.hypot(50, 50) - 10)
    assert found["travel_per_step_mm"] == pytest.approx(
        math.hypot(55, 5) * math.radians(5), abs=1e-3
    )
    assert found["axis"] == Z and found["pivot_mm"] == [0.0, 0.0, 0.0]
    # Only temporary copies moved: the part's arm is where it was.
    assert world.bodies[0].centre == [50.0, 0.0, 0.0]
    assert world.copies > 0 and world.booleans > 0
    assert "CreateTransform" in world.app.utility.flagged


def test_a_pivot_off_the_origin() -> None:
    found = _bodies(_part(pivot_x=10.0), pivot_mm=[10.0, 0.0, 0.0])

    assert found["positive"]["first_contact_deg"] == pytest.approx(
        CONTACT_DEG, abs=0.05
    )


def test_the_nearest_points_are_on_the_balls() -> None:
    found = _bodies(_part(), range_deg=[0.0, 30.0], step_deg=10.0, tolerance_deg=1.0)

    sample = found["min_points_mm"]
    at = math.radians(found["min_clearance_deg"])
    centre = [50 * math.cos(at), 50 * math.sin(at), 0]
    assert math.dist(sample["moving"], centre) == pytest.approx(5.0, abs=1e-3)
    assert math.dist(sample["other"], [0, 50, 0]) == pytest.approx(5.0, abs=1e-3)


def test_without_a_distance_only_the_contact_is_found() -> None:
    world = _part()
    world.distance_fails = True

    found = _bodies(world)

    assert found["clearance_measured"] is False
    assert found["positive"]["first_contact_deg"] == pytest.approx(
        CONTACT_DEG, abs=0.05
    )


def test_a_copy_that_cannot_turn_is_an_error() -> None:
    world = _part()
    world.transform_fails = True

    with pytest.raises(RuntimeError, match="could not be turned"):
        _bodies(world)


def test_a_transform_that_cannot_be_made_is_an_error() -> None:
    world = _part()
    world.app.utility.CreateTransform = lambda data: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="CreateTransform gave nothing"):
        _bodies(world)


def test_a_body_that_cannot_be_copied_is_an_error() -> None:
    world = _part()
    world.bodies[0].Copy = lambda: None  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Copy gave nothing for Arm"):
        _bodies(world)


def test_the_same_body_twice_or_a_bad_range_is_refused() -> None:
    with pytest.raises(InvalidRequest, match="two different bodies"):
        _bodies(_part(), other="Arm")
    with pytest.raises(InvalidRequest, match="at most 0"):
        _bodies(_part(), range_deg=[5.0, 90.0])


# ---- components ----------------------------------------------------------------


def _assembly(
    origin: list[float] = (0.0, 0.0, 0.0),  # type: ignore[assignment]
    **lid: Any,
) -> World:
    world = World(doc_type=2)
    world.component("lid-1", [([50.0, 0.0, 0.0], 5.0)], list(origin), **lid)
    base_origin = [origin[0], origin[1] + 50.0, origin[2]]
    world.component("base-1", [([0.0, 0.0, 0.0], 5.0)], base_origin)
    return world


def _components(world: World, **kw: Any) -> dict[str, Any]:
    args = {
        "moving": "lid-1",
        "other": "base-1",
        "axis": Z,
        "pivot_mm": [0.0, 0.0, 0.0],
        "range_deg": [0.0, 90.0],
        "step_deg": 5.0,
        "tolerance_deg": 0.05,
        **kw,
    }
    return rc.component_rotation_clearance(Adapter(world), **args)


def test_a_lid_turns_into_its_base_and_is_put_back() -> None:
    world = _assembly()
    lid = world.components[0]
    start = list(lid.data)

    found = _components(world)

    assert found["kind"] == "components"
    assert found["positive"]["first_contact_deg"] == pytest.approx(
        CONTACT_DEG, abs=0.05
    )
    assert found["negative"] == {
        "to_deg": 0.0,
        "first_contact_deg": None,
        "free_to_deg": 0.0,
    }
    assert found["body_frame"] == "part"
    assert found["restored"] is True
    assert lid.data == start
    assert len(lid.placements) > 2 and lid.placements[-1] == start
    assert world.fixes == []


def test_a_placed_lid_turns_about_its_hinge() -> None:
    found = _components(_assembly([100.0, 0.0, 0.0]), pivot_mm=[100.0, 0.0, 0.0])

    assert found["positive"]["first_contact_deg"] == pytest.approx(
        CONTACT_DEG, abs=0.05
    )


def test_a_fixed_lid_is_floated_and_fixed_again() -> None:
    world = _assembly(fixed=True)

    _components(world, range_deg=[0.0, 20.0])

    assert world.fixes == [("float", "lid-1"), ("fix", "lid-1")]
    assert world.components[0].fixed is True


def test_mates_that_hold_the_lid_stop_the_sweep() -> None:
    world = _assembly(mates=2)
    world.frozen_after = 0
    start = list(world.components[0].data)

    with pytest.raises(RuntimeError, match="its 2 mate\\(s\\) likely hold it"):
        _components(world)

    assert world.components[0].data == start


def test_a_lid_held_by_something_else() -> None:
    world = _assembly()
    world.frozen_after = 0

    with pytest.raises(RuntimeError, match="a pattern or an in-context reference"):
        _components(world)


def test_a_lid_that_cannot_be_put_back_says_so() -> None:
    counted = _assembly()
    _components(counted, range_deg=[0.0, 20.0])
    world = _assembly()
    world.frozen_after = len(counted.components[0].placements) - 1

    found = _components(world, range_deg=[0.0, 20.0])

    assert found["restored"] is False


def test_a_stopped_sweep_still_puts_the_lid_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _assembly()
    start = list(world.components[0].data)
    calls = {"n": 0}

    def check(_what: str) -> None:
        calls["n"] += 1
        if calls["n"] > 4:
            raise limits.OperationStopped("time is up")

    monkeypatch.setattr(rc.limits, "check", check)

    with pytest.raises(limits.OperationStopped):
        _components(world)

    assert world.components[0].data == start


def test_overlapping_components_are_not_turned() -> None:
    world = World(doc_type=2)
    world.component("lid-1", [([0.0, 0.0, 0.0], 5.0)])
    world.component("base-1", [([3.0, 0.0, 0.0], 5.0)])

    found = _components(world)

    assert found["interfering_at_start"] is True
    assert found["restored"] is True
    assert world.components[0].placements == []


class _PlacedBall(Ball):
    """A component body that comes in assembly coordinates, faces in the part's."""

    def __init__(self, part_ball: Ball, data: list[float]) -> None:
        super().__init__(
            part_ball.world, part_ball.Name, place(data, part_ball.centre), 5.0
        )
        self.part_ball = part_ball

    def GetFaces(self) -> tuple[BallFace, ...]:  # noqa: N802
        self.need("GetFaces")
        return (BallFace(self.part_ball),)


def test_bodies_in_assembly_coordinates_are_turned_alone() -> None:
    world = _assembly([0.0, 20.0, 0.0])
    for component in world.components:
        placed = tuple(_PlacedBall(b, component.data) for b in component.balls)
        component.GetBodies2 = lambda kind, p=placed: p  # type: ignore[method-assign]

    found = _components(world, pivot_mm=[0.0, 20.0, 0.0])

    assert found["body_frame"] == "assembly"
    assert found["positive"]["first_contact_deg"] == pytest.approx(
        CONTACT_DEG, abs=0.05
    )


class _Unplaced(Component):
    Transform2 = None  # type: ignore[assignment]


def test_a_component_whose_placement_cannot_be_read() -> None:
    world = World(doc_type=2)
    world.components.append(_Unplaced(world, "lid-1", [([50.0, 0, 0], 5.0)]))
    world.component("base-1", [([0.0, 0, 0], 5.0)])
    with pytest.raises(RuntimeError, match="moving 'lid-1' could not be read"):
        _components(world)

    world = World(doc_type=2)
    world.component("lid-1", [([50.0, 0, 0], 5.0)])
    world.components.append(_Unplaced(world, "base-1", [([0.0, 0, 0], 5.0)]))
    with pytest.raises(RuntimeError, match="other 'base-1' could not be read"):
        _components(world)


def test_a_component_without_faces_is_an_error() -> None:
    world = _assembly()
    for ball in world.components[0].balls:
        ball.GetFaces = lambda: ()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="No face of moving 'lid-1'"):
        _components(world)


def test_a_face_without_a_twin_is_left_out() -> None:
    world = _assembly()
    lid = world.components[0]
    lid.balls.append(Ball(world, "lid-extra", [500.0, 0.0, 0.0], 1.0))
    twin = lid.GetCorrespondingEntity
    lid.GetCorrespondingEntity = lambda face: (  # type: ignore[method-assign]
        None if face.ball.Name == "lid-extra" else twin(face)
    )

    found = _components(world, range_deg=[0.0, 20.0])

    assert found["min_clearance_mm"] is not None


def test_the_same_component_twice_is_refused() -> None:
    with pytest.raises(InvalidRequest, match="two different components"):
        _components(_assembly(), other="lid-1")
