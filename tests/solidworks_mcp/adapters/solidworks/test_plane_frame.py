"""A plane's frame, and model points projected onto it (pure maths)."""

from __future__ import annotations

import math

import pytest

from solidworks_mcp.adapters.solidworks import plane_frame as pf
from solidworks_mcp.adapters.solidworks.plane_frame import Frame, OffPlaneError

#: The Top Plane: sketch x = +X, y = -Z, normal +Y (row vectors), origin 0.
_TOP = [1, 0, 0, 0, 0, -1, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0]

#: A vertical plane through (20, 0, 10) mm turned 30 degrees about +Y, as the
#: Dipper's cage faces were: x = (cos 30, 0, -sin 30), y = +Y.
_C, _S = math.cos(math.radians(30)), math.sin(math.radians(30))
_CAGE = Frame((20.0, 0.0, 10.0), (_C, 0.0, -_S), (0.0, 1.0, 0.0), (_S, 0.0, _C))


def test_a_transform_gives_its_rows_and_origin_in_mm() -> None:
    frame = Frame.from_transform([*_TOP[:9], 0.01, 0.02, -0.03, 1, 0, 0, 0])

    assert frame == Frame((10.0, 20.0, -30.0), (1, 0, 0), (0, 0, -1), (0, 1, 0))


@pytest.mark.parametrize(
    "data", [None, "abc", [1, 2], [2, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], [1, "x"]]
)
def test_what_is_not_a_rigid_transform_gives_none(data: object) -> None:
    assert Frame.from_transform(data) is None


def test_a_mapping_round_trips() -> None:
    assert Frame.from_mapping(_CAGE.as_dict()) == Frame(
        *(
            tuple(round(c, 9) + 0.0 for c in v)
            for v in (_CAGE.origin_mm, _CAGE.x, _CAGE.y, _CAGE.normal)
        )
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"x": [1, 0, 0], "y": [0, 1, 0], "normal": [0, 0, 1]},
        {"x": [2, 0, 0], "y": [0, 1, 0], "normal": [0, 0, 1], "origin_mm": [0, 0, 0]},
    ],
)
def test_a_mapping_without_a_frame_gives_none(data: object) -> None:
    assert Frame.from_mapping(data) is None


def test_points_go_to_the_sketch_and_back() -> None:
    model = _CAGE.to_model(3.0, 4.0)

    x, y, height = _CAGE.to_sketch(model)

    assert (round(x, 9), round(y, 9), round(height, 9)) == (3.0, 4.0, 0.0)
    assert _CAGE.to_sketch(_CAGE.to_model(0, 0, 2.5))[2] == pytest.approx(2.5)


def test_on_the_top_plane_sketch_y_is_minus_z() -> None:
    top = Frame.from_transform(_TOP)

    (point,), off = pf.project(top, [[5, 0, -7]])

    assert point == (5.0, 7.0)
    assert off == 0.0


def test_a_point_off_the_plane_is_refused_by_name_and_distance() -> None:
    near = _CAGE.to_model(1, 1, 0.005)
    far = _CAGE.to_model(1, 1, -0.5)

    flat, off = pf.project(_CAGE, [near], ["center"])
    assert off == pytest.approx(0.005)
    with pytest.raises(OffPlaneError) as caught:
        pf.project(_CAGE, [near, far], ["start", "end"])

    assert str(caught.value).startswith("end [")
    assert "is 0.5 mm below the sketch plane" in str(caught.value)
    assert "tolerance 0.01 mm" in str(caught.value)


def test_an_unlabelled_point_is_numbered_and_above_is_said() -> None:
    with pytest.raises(OffPlaneError, match="point 0 .* mm above"):
        pf.project(_CAGE, [_CAGE.to_model(0, 0, 1)])
    with pytest.raises(ValueError, match="three numbers"):
        pf.project(_CAGE, [[1, 2]])


def test_a_frame_through_a_point_is_square_and_right_handed() -> None:
    frame = Frame.through((1.0, 2.0, 3.0), (0.0, 0.0, 5.0))

    assert frame.normal == (0.0, 0.0, 1.0)
    assert frame.x == (1.0, 0.0, 0.0)
    assert frame.y == pytest.approx(pf.cross(frame.normal, frame.x))
    tilted = Frame.through((0.0, 0.0, 0.0), (_S, 0.0, _C))
    assert pf.dot(tilted.x, tilted.normal) == pytest.approx(0.0)
    assert pf.cross(tilted.x, tilted.y) == pytest.approx(tilted.normal)


def test_an_offset_moves_along_the_normal() -> None:
    assert Frame.from_transform(_TOP).offset(4).origin_mm == (0.0, 4.0, 0.0)


def test_describe_reads_like_create_sketch() -> None:
    assert Frame.from_transform(_TOP).describe()["summary"] == (
        "sketch x = model +X, sketch y = model -Z, normal = model +Y"
    )


@pytest.mark.parametrize("bad", [None, "xyz", [1, 2], [1, "a", 3], (1, 2, 3, 4)])
def test_vectors_need_three_numbers(bad: object) -> None:
    with pytest.raises(ValueError, match="three numbers"):
        pf.vector(bad, "normal")


def test_vector_helpers() -> None:
    assert pf.unit((0, 3, 4)) == pytest.approx((0.0, 0.6, 0.8))
    with pytest.raises(ValueError, match="has no length"):
        pf.unit((0, 0, 0), "the normal")
    assert pf.distance_to_line((0, 5, 0), (0, 0, 0), (1, 0, 0)) == 5.0
    assert pf.two_points([0, 0, 0], [0, 0, 1]) == ((0, 0, 0), (0, 0, 1))
    with pytest.raises(ValueError, match="same point"):
        pf.two_points([1, 1, 1], [1, 1, 1.0001])
