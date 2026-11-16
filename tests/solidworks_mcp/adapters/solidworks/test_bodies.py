"""The pure pieces of ``bodies``: shift offsets, transforms, messages, box arithmetic."""

from __future__ import annotations

import math

import pytest

from solidworks_mcp.adapters.solidworks import bodies as bd


def test_the_copies_are_the_tool_a_ring_at_each_side_and_two_along_the_axis() -> None:
    offsets = bd.shift_offsets(0.15, 32, "y")

    # The session's 66 copies, plus the tool itself.
    assert len(offsets) == 2 * 32 + 3
    assert offsets[0] == (0.0, 0.0, 0.0)
    ring = offsets[1:65]
    assert {o[1] for o in ring[:32]} == {0.15} and {o[1] for o in ring[32:]} == {-0.15}
    for x, _y, z in ring:
        assert math.hypot(x, z) == pytest.approx(0.15)
    assert offsets[-2:] == [(0.0, 0.15, 0.0), (0.0, -0.15, 0.0)]
    # The first direction points along the next axis after y: z.
    assert offsets[1] == (0.0, 0.15, 0.15)


def test_the_ring_turns_about_the_axis_asked() -> None:
    offsets = bd.shift_offsets(1.0, 4, "x")

    assert offsets[1:5] == [
        (1.0, 1.0, 0.0),
        (1.0, 0.0, 1.0),
        (1.0, -1.0, 0.0),
        (1.0, 0.0, -1.0),
    ]


def test_a_shift_is_an_identity_turn_with_a_translation() -> None:
    data = bd.translation_data((0.1, -0.2, 0.3))

    assert len(data) == 16
    assert data[:9] == [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0]
    assert data[9:13] == [0.1, -0.2, 0.3, 1.0] and data[13:] == [0.0] * 3


def test_a_turn_is_about_one_axis_in_radians() -> None:
    assert bd.rotation_angles("y", 90) == (0.0, pytest.approx(math.pi / 2), 0.0)
    assert bd.rotation_angles("z", -180)[2] == pytest.approx(-math.pi)


def test_the_messages_name_what_the_part_holds() -> None:
    assert bd.missing_bodies_message(["Label9"], ["Lid", "Label1"]) == (
        "Body(ies) not found: Label9. The part's solid bodies: Lid, Label1."
    )
    assert bd.missing_bodies_message(["A"], []).endswith("solid bodies: none.")
    assert bd.not_a_part_message("list_bodies", "assembly") == (
        "list_bodies needs an active part document (active document is assembly)."
    )


def test_the_folder_is_named_after_the_tools() -> None:
    assert bd.folder_name(["Label1"]) == "Negative of Label1"
    assert bd.folder_name(["Text@1", "B", "C"]) == "Negative of Text1 and 2 more"


def test_box_arithmetic() -> None:
    a, b = [0, 0, 0, 10, 10, 10], [5, 5, 5, 15, 15, 15]

    assert bd.box_volume(a) == 1000.0
    assert bd.box_volume([0, 0, 0, -1, 1, 1]) == 0.0
    assert bd.box_overlap(a, b) == [5, 5, 5, 10, 10, 10]
    assert bd.box_overlap(a, [10, 0, 0, 20, 10, 10]) is None  # touching only
    assert bd.overlap_volume(a, b) == 125.0
    assert bd.overlap_volume(a, [20, 0, 0, 30, 1, 1]) == 0.0
    assert bd.grow_box(a, 1) == [-1, -1, -1, 11, 11, 11]
    assert bd.translate_box(a, [1, 2, 3]) == [1, 2, 3, 11, 12, 13]
    assert bd.union_box(a, b) == [0, 0, 0, 15, 15, 15]
    assert bd.box_center(b) == [10, 10, 10]
    assert bd.box_area([0, 0, 0, 1, 2, 3]) == 22.0


def test_a_box_turns_about_an_axis_through_a_point() -> None:
    # A quarter turn about +Y sends +X to -Z (right-hand rule).
    assert bd.rotate_box([0, 0, 0, 20, 6, 10], "y", 90, [0, 0, 0]) == pytest.approx(
        [0, 0, -20, 10, 6, 0]
    )
    # About a point: the box turns about the axis through it.
    assert bd.rotate_box([10, 0, 0, 12, 1, 1], "z", 180, [10, 0, 0]) == pytest.approx(
        [8, -1, 0, 10, 0, 1]
    )


def test_a_box_splits_only_where_the_cut_crosses_it() -> None:
    assert bd.split_box([0, 0, 0, 10, 4, 4], 0, 3) == (
        [0, 0, 0, 3, 4, 4],
        [3, 0, 0, 10, 4, 4],
    )
    assert bd.split_box([0, 0, 0, 10, 4, 4], 0, 0) is None
    assert bd.split_box([0, 0, 0, 10, 4, 4], 1, 9) is None


def test_inside_allows_the_pad() -> None:
    outer = [0, 0, 0, 10, 10, 10]

    assert bd.box_inside([0, 0, 0, 5, 5, 5], outer, 0)
    assert bd.box_inside([-0.5, 0, 0, 5, 5, 5], outer, 1)
    assert not bd.box_inside([-2, 0, 0, 5, 5, 5], outer, 1)
    assert not bd.box_inside([0, 0, 0, 5, 5, 12], outer, 1)


def test_the_nearest_points_of_two_boxes() -> None:
    a, b = [0, 0, 0, 10, 10, 10], [12, 2, -5, 14, 4, -1]

    # Apart on x and z; overlapping on y, where the points sit in the middle.
    assert bd.nearest_points(a, b) == ([10, 3, 0], [12, 3, -1])
    assert bd.nearest_points(b, a) == ([12, 3, -1], [10, 3, 0])
