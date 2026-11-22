"""Interference from the components' bodies, intersected in the kernel: no UI.

SolidWorks' Interference Detection came up on screen at each
``check_interference``; the engine instead intersects temporary copies of
the bodies, placed in the assembly by each component's ``Transform2``. What
it can't answer for certain raises ``Unreadable``, and SolidWorks' own
detection runs instead (``test_io_interference``).
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks import interference, limits
from solidworks_mcp.adapters.solidworks.interference import (
    BODY_INTERSECTION,
    Unreadable,
    asked_pair,
    body_interferences,
    box_interferences,
    fallback_note,
    found_reply,
)
from solidworks_mcp.adapters.solidworks.io import interferences_among

from .fake_interference import QUARTER_TURN_Z, Adapter, World, cube


@pytest.fixture(autouse=True)
def _wrapping_is_the_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "solidworks_mcp.adapters.solidworks.io._dynamic",
        SimpleNamespace(Dispatch=lambda obj: obj),
    )


def _check(world: World, **options: Any) -> list[dict[str, Any]]:
    return body_interferences(Adapter(world), world.assembly, **options)


def _volumes(found: list[dict[str, Any]]) -> list[float]:
    return [item["volume_mm3"] for item in found]


def _overlapping(world: World) -> None:
    """Two 10 mm cubes, the second 5 mm along x: 500 mm3 shared."""
    world.add("block-1", world.body(cube((0, 0, 0), 10)))
    world.add("block-2", world.body(cube((0, 0, 0), 10)), origin_mm=(5.0, 0.0, 0.0))


# ---- the pure pieces --------------------------------------------------------------


@pytest.mark.parametrize(
    "asked",
    [set(), {"a"}, {"b"}, {"z"}, {"a", "b"}, {"a", "c"}, {"a", "b", "c"}],
    ids=["none", "a", "b", "absent", "a+b", "a+c", "three"],
)
@pytest.mark.parametrize("pair", [("a", "b"), ("a", "a"), ("b", "c")])
def test_the_pairs_asked_are_those_interferences_among_keeps(
    asked: set[str], pair: tuple[str, str]
) -> None:
    item = {"components": list(pair)}
    kept = interferences_among([item], asked) if asked else [item]

    assert asked_pair(*pair, asked) is bool(kept)


def test_the_note_says_solidworks_detection_ran_and_why() -> None:
    assert fallback_note("nut-1 is lightweight, so its bodies aren't loaded") == (
        "SolidWorks' Interference Detection was used, so it may have shown "
        "briefly: nut-1 is lightweight, so its bodies aren't loaded."
    )


def test_the_reply_has_the_managers_keys_and_the_method() -> None:
    details = [{"components": ["a-1", "b-1"], "component_count": 2, "volume_mm3": 2.5}]

    assert found_reply(details, {"b-1", "a-1"}) == {
        "interference_found": True,
        "interference_count": 1,
        "interferences": details,
        "coincident_treated_as_interference": False,
        "scope": ["a-1", "b-1"],
        "tolerance_applied": None,
        "method": BODY_INTERSECTION,
    }
    assert found_reply([], set())["scope"] == "whole assembly"
    assert found_reply([], set())["interference_found"] is False


def test_boxes_that_share_a_volume_interfere_by_it() -> None:
    boxes = {
        "a-1": cube((0, 0, 0), 10),
        "b-1": cube((5, 0, 0), 10),
        "c-1": cube((10, 0, 0), 10),  # touches a-1, overlaps b-1
        "d-1": cube((50, 0, 0), 10),
    }

    found = box_interferences(boxes)
    touching = box_interferences(boxes, touching=True)

    assert [(i["components"], i["volume_mm3"]) for i in found] == [
        (["a-1", "b-1"], 500.0),
        (["b-1", "c-1"], 500.0),
    ]
    assert [(i["components"], i["volume_mm3"]) for i in touching] == [
        (["a-1", "b-1"], 500.0),
        (["a-1", "c-1"], 0.0),
        (["b-1", "c-1"], 500.0),
    ]
    assert box_interferences(boxes, {"c-1"}) == found[1:]


# ---- what is found ----------------------------------------------------------------


def test_two_overlapping_components_are_one_interference_of_the_shared_volume() -> None:
    world = World()
    _overlapping(world)

    found = _check(world)

    assert found == [
        {
            "components": ["block-1", "block-2"],
            "component_count": 2,
            "volume_mm3": 500.0,
        }
    ]
    assert world.booleans == 1


def test_each_body_is_placed_once_by_its_components_transform() -> None:
    world = World()
    world.add("plate-1", world.body(cube((0, 0, 0), 10)))
    # In its part, the pin is 20 mm away along x; placed 15 mm back, it
    # overlaps the plate by 5 mm. Its copy moves by the component's
    # transform, the plate's (at the origin, unturned) not at all.
    pin = world.add(
        "pin-1", world.body(cube((20, 0, 0), 10)), origin_mm=(-15.0, 0.0, 0.0)
    )

    found = _check(world)

    assert _volumes(found) == [500.0]
    assert world.placements == [pin.data]


def test_a_turned_component_is_intersected_where_it_sits() -> None:
    world = World()
    world.add("base-1", world.body([0.0, 0.0, 0.0, 10.0, 30.0, 10.0]))
    # A 30 x 2 x 2 bar along the part's x; a quarter turn about z lays it
    # along the assembly's y (x from -2 to 0), and 1 mm along x into the base.
    world.add(
        "bar-1",
        world.body([0.0, 0.0, 0.0, 30.0, 2.0, 2.0]),
        rows=QUARTER_TURN_Z,
        origin_mm=(1.0, 0.0, 0.0),
    )

    found = _check(world)

    assert _volumes(found) == [pytest.approx(60.0)]  # 1 x 30 x 2


def test_components_apart_cost_no_copy_and_no_boolean() -> None:
    world = World()
    world.add("a-1", world.body(cube((0, 0, 0), 10)))
    world.add("b-1", world.body(cube((0, 0, 0), 10)), origin_mm=(50.0, 0.0, 0.0))
    world.add("c-1", world.body(cube((0, 0, 0), 10)), origin_mm=(10.0, 0.0, 0.0))

    assert _check(world) == []
    # c-1 only touches a-1 face to face: touching boxes can't share a volume.
    assert (world.copies, world.booleans) == (0, 0)


def test_only_the_bodies_whose_boxes_overlap_are_intersected() -> None:
    world = World()
    # Two bodies each, at either end: the components' boxes overlap, but
    # only the near ends do.
    world.add(
        "left-1",
        world.body(cube((0, 0, 0), 10)),
        world.body(cube((40, 0, 0), 10)),
    )
    world.add(
        "right-1",
        world.body(cube((45, 0, 0), 10)),
        world.body(cube((100, 0, 0), 10)),
    )

    found = _check(world)

    assert _volumes(found) == [500.0]
    assert world.booleans == 1


def test_each_piece_of_the_overlap_is_one_interference() -> None:
    world = World()
    # A U-shaped bracket (two legs, 10 mm apart) on a bar across both legs.
    world.add(
        "bracket-1",
        world.body(
            [0.0, 0.0, 0.0, 5.0, 10.0, 10.0], [15.0, 0.0, 0.0, 20.0, 10.0, 10.0]
        ),
    )
    world.add("bar-1", world.body([0.0, 8.0, 0.0, 20.0, 12.0, 10.0]))

    found = _check(world)

    assert _volumes(found) == [100.0, 100.0]
    assert all(item["components"] == ["bracket-1", "bar-1"] for item in found)


def test_an_overlap_too_small_to_count_is_no_interference() -> None:
    world = World()
    world.add("a-1", world.body(cube((0, 0, 0), 10)))
    world.add("b-1", world.body(cube((0, 0, 0), 10)), origin_mm=(10.0 - 1e-9, 0, 0))

    assert _check(world) == []
    assert world.booleans == 1


def test_two_identical_bodies_share_the_whole_body() -> None:
    world = World()
    world.same_body = True
    world.add("block-1", world.body(cube((0, 0, 0), 10)))
    world.add("block-2", world.body(cube((0, 0, 0), 10)))

    assert _volumes(_check(world)) == [1000.0]


@pytest.mark.parametrize("code", [bd.NO_ERROR, bd.NO_INTERSECT, 5, 6, "unread"])
def test_an_intersection_with_no_piece_and_a_clean_code_is_no_overlap(
    code: Any,
) -> None:
    world = World()
    _overlapping(world)
    world.code = code

    assert _check(world) == []


def test_a_multibody_parts_own_bodies_are_checked_against_each_other() -> None:
    world = World()
    world.add(
        "bracket-1",
        world.body(cube((0, 0, 0), 10)),
        world.body(cube((5, 0, 0), 10)),
        world.body(cube((50, 0, 0), 10)),
    )
    world.add("far-1", world.body(cube((500, 0, 0), 10)))

    found = _check(world)

    assert found == [
        {
            "components": ["bracket-1", "bracket-1"],
            "component_count": 2,
            "volume_mm3": 500.0,
        }
    ]
    assert _check(world, include_multibody=False) == []


def test_a_sub_assembly_is_read_down_to_its_parts_in_tree_order() -> None:
    world = World()
    world.add("plate-1", world.body(cube((0, 0, 0), 10)))
    inner = world.component(
        "hinge-1/pin-1", world.body(cube((0, 0, 0), 10)), origin_mm=(5.0, 0, 0)
    )
    leaf = world.component("hinge-1/leaf-1", world.body(cube((0, 0, 0), 10)))
    world.add("hinge-1", children=[inner, leaf])

    found = _check(world)

    assert [item["components"] for item in found] == [
        ["plate-1", "hinge-1/pin-1"],
        ["plate-1", "hinge-1/leaf-1"],
        ["hinge-1/pin-1", "hinge-1/leaf-1"],
    ]


# ---- what is left out -------------------------------------------------------------


def test_a_hidden_component_is_left_out_only_when_hidden_ones_are_ignored() -> None:
    world = World()
    world.add("shown-1", world.body(cube((0, 0, 0), 10)))
    world.add("hidden-1", world.body(cube((5, 0, 0), 10)), visible=0)
    sub_child = world.component("cover-1/lid-1", world.body(cube((5, 0, 0), 10)))
    world.add("cover-1", visible=0, children=[sub_child])

    assert _check(world, ignore_hidden=True) == []
    assert len(_check(world)) == 3


def test_a_hidden_body_is_left_out_only_when_hidden_ones_are_ignored() -> None:
    world = World()
    hidden = world.body(cube((5, 0, 0), 10))
    hidden.Visible = False
    world.add("tool-1", world.body(cube((50, 0, 0), 10)), hidden)
    world.add("block-1", world.body(cube((0, 0, 0), 10)))

    assert _check(world, ignore_hidden=True) == []
    assert _volumes(_check(world)) == [500.0]


def test_a_suppressed_component_is_left_out_with_all_below_it() -> None:
    world = World()
    world.add("block-1", world.body(cube((0, 0, 0), 10)))
    world.add("off-1", world.body(cube((5, 0, 0), 10)), state=0)
    child = world.component("sub-1/part-1", world.body(cube((5, 0, 0), 10)))
    world.add("sub-1", state=0, children=[child])

    assert _check(world) == []
    assert child.bodies_read == 0


def test_a_part_with_no_solid_body_adds_nothing() -> None:
    world = World()
    world.add("sketch-1")
    _overlapping(world)

    assert _volumes(_check(world)) == [500.0]


def test_a_body_whose_box_does_not_read_is_intersected_anyway() -> None:
    world = World()
    unboxed = world.body(cube((0, 0, 0), 10))
    unboxed.box_fails = True
    world.add("a-1", unboxed)
    world.add("b-1", world.body(cube((5, 0, 0), 10)))
    world.add("far-1", world.body(cube((500, 0, 0), 10)))

    assert _volumes(_check(world)) == [500.0]
    assert world.booleans == 2  # against b-1 and far-1: its box is unknown


def test_a_scaled_components_boxes_are_not_trusted() -> None:
    world = World()
    world.add("a-1", world.body(cube((0, 0, 0), 10)))
    scaled = world.add("far-1", world.body(cube((500, 0, 0), 10)))
    assert scaled.data is not None
    scaled.data[12] = 2.0  # the placed box leaves the scale out

    assert _check(world) == []
    assert world.booleans == 1


# ---- a components list ------------------------------------------------------------


def _three_in_a_row(world: World) -> None:
    """a-1, b-1 and c-1, 4 mm apart: each overlaps the next, and a-1 c-1 too."""
    for k, name in enumerate(("a-1", "b-1", "c-1")):
        world.add(name, world.body(cube((0, 0, 0), 10)), origin_mm=(4.0 * k, 0, 0))


def test_one_component_listed_is_checked_against_every_other() -> None:
    world = World()
    _three_in_a_row(world)

    found = _check(world, asked={"b-1"})

    assert [item["components"] for item in found] == [["a-1", "b-1"], ["b-1", "c-1"]]


def test_two_listed_are_checked_only_against_each_other() -> None:
    world = World()
    _three_in_a_row(world)

    found = _check(world, asked={"a-1", "c-1"})

    assert [item["components"] for item in found] == [["a-1", "c-1"]]
    assert world.booleans == 1


def test_components_outside_the_pairs_asked_are_not_read() -> None:
    world = World()
    _overlapping(world)
    lightweight = world.add("nut-1", state=1)  # its bodies aren't loaded

    assert _volumes(_check(world, asked={"block-1", "block-2"})) == [500.0]
    assert lightweight.bodies_read == 0
    assert _check(world, asked={"absent-1"}) == []
    assert lightweight.bodies_read == 0


# ---- what the kernel can't answer: SolidWorks' detection runs instead ------------------


def _lightweight(world: World) -> None:
    world.add("nut-1", state=1)


def _fully_lightweight(world: World) -> None:
    world.add("nut-1", state=4)


def _state_unread(world: World) -> None:
    world.add("nut-1", state=None)


def _bodies_raise(world: World) -> None:
    world.add("nut-1", world.body(cube((0, 0, 0), 10))).bodies_error = RuntimeError(
        "Member not found"
    )


def _children_raise(world: World) -> None:
    world.add("sub-1").children_error = RuntimeError("Member not found")


def _unplaced(world: World) -> None:
    world.add("nut-1", world.body(cube((0, 0, 0), 10))).data = None


def _components_raise(world: World) -> None:
    world.assembly.components_error = RuntimeError("Member not found")


def _components_none(world: World) -> None:
    world.top = None


def _no_copy(world: World) -> None:
    world.copies_left = 0


def _no_second_copy(world: World) -> None:
    world.copies_left = 2  # the two placed copies, then nothing to intersect


def _no_move(world: World) -> None:
    world.move_fails = True


def _no_utility(world: World) -> None:
    world.app.utility = None


def _boolean_raises(world: World) -> None:
    world.boolean_error = RuntimeError("Operations2 failed")


def _partial_coincidence(world: World) -> None:
    world.code = 1040


def _boolean_fails(world: World) -> None:
    world.code = 1058


def _volume_unread(world: World) -> None:
    world.volume_fails = True


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (_lightweight, "nut-1 is lightweight, so its bodies aren't loaded"),
        (_fully_lightweight, "nut-1 is lightweight, so its bodies aren't loaded"),
        (_state_unread, "nut-1 shows no body and its state could not be read"),
        (_bodies_raise, "the bodies of nut-1 could not be read"),
        (_children_raise, "the components of sub-1 could not be read"),
        (_unplaced, "the placement of nut-1 could not be read"),
        (_components_raise, "the assembly's components could not be read"),
        (_components_none, "the assembly's components could not be read"),
        (_no_copy, "a body of block-1 could not be copied into place (IBody2.Copy"),
        (_no_second_copy, "IBody2.Copy gave nothing for block-1 and block-2"),
        (_no_move, "a body of block-2 could not be copied into place (A copy"),
        (
            _no_utility,
            "a body of block-2 could not be copied into place (ISldWorks.GetMathUtility",
        ),
        (_boolean_raises, "SolidWorks could not intersect block-1 and block-2"),
        (_partial_coincidence, "intersecting block-1 and block-2 gave error code 1040"),
        (_boolean_fails, "intersecting block-1 and block-2 gave error code 1058"),
        (_volume_unread, "the volume shared by block-1 and block-2 could not be read"),
    ],
    ids=[
        "lightweight",
        "fully lightweight",
        "state",
        "bodies",
        "sub-assembly",
        "placement",
        "components",
        "no components",
        "copy",
        "second copy",
        "move",
        "math utility",
        "boolean raises",
        "partial coincidence",
        "boolean fails",
        "volume",
    ],
)
def test_what_the_kernel_cant_answer_is_unreadable(make: Any, reason: str) -> None:
    world = World()
    _overlapping(world)
    make(world)

    with pytest.raises(Unreadable, match="^" + re.escape(reason)):
        _check(world)


def test_identical_bodies_whose_volume_does_not_read_are_unreadable() -> None:
    world = World()
    world.same_body = True
    world.volume_fails = True
    world.add("block-1", world.body(cube((0, 0, 0), 10)))
    world.add("block-2", world.body(cube((0, 0, 0), 10)))

    with pytest.raises(Unreadable, match="volume shared by block-1 and block-2"):
        _check(world)


def test_two_bodies_of_one_part_are_named_so() -> None:
    world = World()
    world.code = 1058
    world.add(
        "bracket-1", world.body(cube((0, 0, 0), 10)), world.body(cube((5, 0, 0), 10))
    )

    with pytest.raises(Unreadable, match="intersecting two bodies of bracket-1"):
        _check(world)


def test_a_component_that_cant_be_wrapped_is_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(
        "solidworks_mcp.adapters.solidworks.io._dynamic",
        SimpleNamespace(Dispatch=lambda obj: None),
    )
    world = World()
    _overlapping(world)

    with pytest.raises(Unreadable, match="a component of the assembly"):
        _check(world)


def test_a_tree_past_the_component_limit_is_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(interference, "MAX_COMPONENTS", 2)
    world = World()
    _three_in_a_row(world)

    with pytest.raises(Unreadable, match="more than 2 components"):
        _check(world)


@pytest.mark.parametrize("at", ["walk", "pairs"])
def test_the_check_stops_when_the_operation_is_stopped(monkeypatch, at: str) -> None:
    calls: list[str] = []

    def stop(doing: str) -> None:
        calls.append(doing)
        # The walk checks once per component (two); the pairs once each.
        if at == "walk" or len(calls) > 2:
            raise limits.OperationStopped(f"Stopped {doing}")

    monkeypatch.setattr(limits, "check", stop)
    world = World()
    _overlapping(world)

    with pytest.raises(limits.OperationStopped, match="checking interference"):
        _check(world)
    assert world.booleans == 0
    assert len(calls) == (1 if at == "walk" else 3)
