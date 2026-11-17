"""View orientations as matrices (``view_matrix``): pure maths, no COM.

The Dipper part is modelled Z-up, so every standard view showed it on end; the
session rotated it -90 degrees about X in a throwaway assembly for each render.
"""

from __future__ import annotations

import math

import pytest

from solidworks_mcp.adapters.solidworks import view_matrix as vm

R2, R3, R6 = math.sqrt(2.0), math.sqrt(3.0), math.sqrt(6.0)

#: The isometric view's Orientation3 rotation, as SolidWorks users report it.
ISOMETRIC = [
    [1 / R2, -1 / R6, 1 / R3],
    [0.0, 2 / R6, 1 / R3],
    [-1 / R2, -1 / R6, 1 / R3],
]


def _times(point: list[float], rows: list[list[float]]) -> list[float]:
    """A model point in the view: ``p . R``."""
    return [sum(point[k] * rows[k][j] for k in range(3)) for j in range(3)]


def _approx(rows: list[list[float]]) -> list[object]:
    return [pytest.approx(row, abs=1e-12) for row in rows]


def test_the_front_view_is_the_identity() -> None:
    assert vm.standard_rows("front") == _approx(
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    )


def test_the_isometric_view_is_solidworks_matrix() -> None:
    assert vm.standard_rows("isometric") == _approx(ISOMETRIC)


@pytest.mark.parametrize(
    ("name", "right", "up"),
    [
        ("back", [-1, 0, 0], [0, 1, 0]),
        ("left", [0, 0, 1], [0, 1, 0]),
        ("right", [0, 0, -1], [0, 1, 0]),
        ("top", [1, 0, 0], [0, 0, -1]),
        ("bottom", [1, 0, 0], [0, 0, 1]),
    ],
)
def test_each_standard_view_shows_these_axes(
    name: str, right: list[float], up: list[float]
) -> None:
    shown = vm.axes(vm.standard_rows(name))

    assert shown["right"] == pytest.approx(right)
    assert shown["up"] == pytest.approx(up)


def test_in_the_right_view_model_x_comes_toward_the_viewer() -> None:
    """Pitfall #42's right view: model X and Z swap in view coordinates."""
    rows = vm.standard_rows("right")

    assert _times([1.0, 0.0, 0.0], rows) == pytest.approx([0.0, 0.0, 1.0])
    assert _times([0.0, 0.0, 1.0], rows) == pytest.approx([-1.0, 0.0, 0.0])


def test_dimetric_and_trimetric_come_from_solidworks() -> None:
    assert vm.standard_rows("dimetric") is None
    assert vm.standard_rows("trimetric") is None
    assert vm.STANDARD_VIEW_IDS["trimetric"] == 8
    assert vm.STANDARD_VIEW_IDS["dimetric"] == 9


@pytest.mark.parametrize(
    ("name", "toward", "up"),
    [
        ("front", [0, -1, 0], [0, 0, 1]),
        ("top", [0, 0, 1], [0, 1, 0]),
        ("right", [1, 0, 0], [0, 0, 1]),
        ("bottom", [0, 0, -1], [0, -1, 0]),
    ],
)
def test_z_up_turns_the_standard_views(
    name: str, toward: list[float], up: list[float]
) -> None:
    shown = vm.axes(vm.z_up(vm.standard_rows(name)))

    assert shown["toward_viewer"] == pytest.approx(toward)
    assert shown["up"] == pytest.approx(up)


def test_the_z_up_isometric_looks_from_one_minus_one_one() -> None:
    """The same view the session got by turning the part -90 degrees about X."""
    turned = vm.z_up(vm.standard_rows("isometric"))

    assert turned == _approx(vm.look_from([1, -1, 1], [0, 0, 1]))
    assert vm.axes(turned)["up"][2] > 0.8  # +Z is (mostly) up


def test_z_up_is_the_part_turned_minus_ninety_about_x() -> None:
    """A point turned -90 degrees about X, then viewed, lands where z_up puts it."""
    c, s = math.cos(math.radians(-90)), math.sin(math.radians(-90))
    turn = [[1, 0, 0], [0, c, s], [0, -s, c]]  # rows: images of X, Y, Z
    point = [3.0, 5.0, 7.0]
    iso = vm.standard_rows("isometric")

    assert _times(point, vm.z_up(iso)) == pytest.approx(
        _times(_times(point, turn), iso)
    )


def test_a_custom_view_from_a_direction_and_an_up() -> None:
    rows = vm.look_from([0, -2, 0], [0, 0, 5])

    shown = vm.axes(rows)
    assert shown == {
        "right": pytest.approx([1, 0, 0]),
        "up": pytest.approx([0, 0, 1]),
        "toward_viewer": pytest.approx([0, -1, 0]),
    }


def test_an_up_at_a_slant_is_made_square_to_the_view() -> None:
    rows = vm.look_from([0, 0, 1], [1, 1, 0])

    assert vm.axes(rows)["up"] == pytest.approx([1 / R2, 1 / R2, 0])
    assert vm.axes(rows)["right"] == pytest.approx([1 / R2, -1 / R2, 0])


@pytest.mark.parametrize(
    ("direction", "up_axis", "expected"),
    [
        ([1, 1, 1], "y", [0, 1, 0]),
        ([1, -1, 1], "z", [0, 0, 1]),
        ([0, 3, 0], "y", [0, 0, -1]),  # from above: as the top view
        ([0, -3, 0], "y", [0, 0, 1]),  # from below: as the bottom view
        ([0, 0, 2], "z", [0, 1, 0]),
        ([0, 0, -2], "z", [0, -1, 0]),
    ],
)
def test_the_default_up(
    direction: list[float], up_axis: str, expected: list[float]
) -> None:
    assert vm.default_up(direction, up_axis) == pytest.approx(expected)


def test_looking_straight_down_a_z_up_part_matches_its_top_view() -> None:
    assert vm.custom_rows([0, 0, 1], None, "z") == _approx(
        vm.z_up(vm.standard_rows("top"))
    )


@pytest.mark.parametrize(
    ("call", "words"),
    [
        (lambda: vm.look_from([0, 0, 0], [0, 1, 0]), "view_direction must not be"),
        (lambda: vm.look_from([0, 0, 1], [0, 0, 0]), "up must not be"),
        (lambda: vm.look_from([0, 1, 0], [0, -2, 0]), "up lies along"),
        (lambda: vm.look_from([0, 1], [0, 0, 1]), "three numbers"),
        (lambda: vm.default_up([1, 0, 0], "x"), "up_axis is 'y' or 'z'"),
    ],
)
def test_bad_vectors_are_refused(call: object, words: str) -> None:
    with pytest.raises(ValueError, match=words):
        call()  # type: ignore[operator]


def test_picture_rows_for_a_direction() -> None:
    rows = vm.picture_rows("isometric", [0, 0, 1], None, "y")

    assert rows == _approx(vm.standard_rows("front"))


@pytest.mark.parametrize(
    ("orientation", "up_axis"),
    [("isometric", "y"), ("current", "y"), ("dimetric", "z"), ("top", "z")],
)
def test_picture_rows_leaves_the_standard_views_to_solidworks(
    orientation: str, up_axis: str
) -> None:
    assert vm.picture_rows(orientation, None, None, up_axis) is None


@pytest.mark.parametrize(
    ("args", "words"),
    [
        (("front", None, None, "x"), "up_axis is"),
        (("front", None, [0, 0, 1], "y"), "up goes with view_direction"),
        (("current", None, None, "z"), "'current' isn't one"),
        (("iso", None, None, "z"), "'iso' isn't one"),
    ],
)
def test_picture_rows_refuses(args: tuple, words: str) -> None:
    with pytest.raises(ValueError, match=words):
        vm.picture_rows(*args)


def test_a_transform_keeps_its_translation_and_scale() -> None:
    data = [9.0] * 9 + [0.1, 0.2, 0.3, 2.5, 0.0, 0.0, 0.0]
    rows = vm.standard_rows("right")

    values = vm.with_rows(data, rows)

    assert values[:9] == [x for row in rows for x in row]
    assert values[9:] == [0.1, 0.2, 0.3, 2.5, 0.0, 0.0, 0.0]


@pytest.mark.parametrize("data", [None, (1.0, 2.0), ("a",) * 16])
def test_without_a_readable_transform_the_rest_is_plain(data: object) -> None:
    values = vm.with_rows(data, vm.standard_rows("front"))

    assert values[9:] == [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]


def test_rows_read_from_array_data() -> None:
    data = tuple(range(16))

    assert vm.rows_from_array(data) == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    assert vm.rows_from_array((1.0, 2.0)) is None
    assert vm.rows_from_array(None) is None
    assert vm.rows_from_array(("x",) * 9) is None


def test_same_within_the_tolerance() -> None:
    rows = vm.standard_rows("front")
    nudged = [[v + 1e-9 for v in row] for row in rows]

    assert vm.same(rows, nudged)
    assert not vm.same(rows, vm.standard_rows("right"))


def test_parallel_vectors() -> None:
    assert vm.parallel([0, 0, 1], [0, 0, -4])
    assert not vm.parallel([0, 0, 1], [0, 1, 1])
