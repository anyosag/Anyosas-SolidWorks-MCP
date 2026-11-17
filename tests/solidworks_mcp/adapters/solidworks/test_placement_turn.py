"""A turn about any axis through a pivot: the rows, the placement, the transform."""

from __future__ import annotations

import math
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import placement as pl

_IDENTITY = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 0, 0, 0]


def _point(data: list[float], p: list[float]) -> list[float]:
    """Where ``p`` goes under a placement's 16 values: ``p . R + t``."""
    rows = [data[0:3], data[3:6], data[6:9]]
    return [sum(p[k] * rows[k][j] for k in range(3)) + data[9 + j] for j in range(3)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("x", [1.0, 0.0, 0.0]),
        ("-Y", [0.0, -1.0, 0.0]),
        (" +z ", [0.0, 0.0, 1.0]),
        ([0, 3, 4], [0.0, 0.6, 0.8]),
    ],
)
def test_an_axis_by_name_or_vector(value: Any, expected: list[float]) -> None:
    assert pl.axis_vector(value) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "fragment"),
    [("w", "not 'w'"), ([0, 0, 0], "zero vector"), ([1, 2], "three numbers")],
)
def test_a_bad_axis_is_refused(value: Any, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        pl.axis_vector(value)


def _flat(rows: list[list[float]]) -> list[float]:
    return [x for row in rows for x in row]


def test_a_quarter_turn_about_y_matches_the_euler_rows() -> None:
    assert _flat(pl.axis_rotation_rows([0, 1, 0], 90)) == pytest.approx(
        _flat(pl.rotation_rows_from_deg(0, 90, 0))
    )


def test_a_third_turn_about_the_diagonal_cycles_the_axes() -> None:
    rows = pl.axis_rotation_rows([1, 1, 1], 120)

    # X goes to Y, Y to Z and Z to X (right-hand rule about [1, 1, 1]).
    assert _flat(rows) == pytest.approx([0, 1, 0, 0, 0, 1, 1, 0, 0], abs=1e-12)
    assert pl.rotation_problem(rows) is None


def test_a_turn_about_a_pivot_moves_the_origin_round_it() -> None:
    data = pl.turned(_IDENTITY, [0, 0, 1], [10, 0, 0], 90)

    # The origin, 10 to the left of the pivot, swings below it.
    assert data[9:12] == pytest.approx([10, -10, 0])
    assert _point(data, [10, 0, 0]) == pytest.approx([10, 0, 0])
    assert data[12:] == [1.0, 0.0, 0.0, 0.0]


def test_a_turn_composes_with_where_the_part_already_is() -> None:
    start = pl.transformed(_IDENTITY, [5, 0, 0], pl.rotation_rows_from_deg(0, 0, 90))
    corner = [1.0, 2.0, 3.0]
    at_start = _point(start, corner)

    data = pl.turned(start, [1, 0, 0], [0, 0, 0], 180)

    # The placed point, turned half a turn about X: y and z change sign.
    expected = [at_start[0], -at_start[1], -at_start[2]]
    assert _point(data, corner) == pytest.approx(expected)


def test_a_turn_alone_is_the_turn_about_the_pivot() -> None:
    data = pl.turn_data([0, 0, 1], [0.05, 0, 0], 180)

    assert _point(data, [0.1, 0, 0]) == pytest.approx([0.0, 0.0, 0.0], abs=1e-12)


def test_a_turn_needs_sixteen_values() -> None:
    with pytest.raises(ValueError, match="16 values"):
        pl.turned([1.0] * 12, [0, 0, 1], [0, 0, 0], 10)


class _Component:
    def __init__(self, data: list[float] | None) -> None:
        self.Transform2 = type("T", (), {"ArrayData": data})() if data else None


class _Adapter:
    def _attempt(self, fn: Any, default: Any = None) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001 - mirrors PyWin32Adapter
            return default


def test_a_placement_asked_then_turned() -> None:
    component = _Component(list(_IDENTITY))

    origin, rows = pl.turned_placement(
        _Adapter(), component, [0.1, 0, 0], None, [0, 0, 1], [0, 0, 0], 90
    )

    assert origin == pytest.approx([0.0, 0.1, 0.0])
    assert rows[0] == pytest.approx([0.0, 1.0, 0.0])


def test_a_placement_kept_then_turned() -> None:
    start = pl.transformed(_IDENTITY, [0, 0, 0.2])
    component = _Component(start)

    origin, rows = pl.turned_placement(
        _Adapter(),
        component,
        None,
        pl.rotation_rows_from_deg(0, 0, 0),
        [1, 0, 0],
        [0, 0, 0],
        -90,
    )

    assert origin == pytest.approx([0.0, 0.2, 0.0], abs=1e-12)
    assert math.isclose(rows[1][2], -1.0)


def test_a_placement_that_cannot_be_read() -> None:
    with pytest.raises(RuntimeError, match="could not be read"):
        pl.turned_placement(
            _Adapter(), _Component(None), None, None, [0, 0, 1], [0, 0, 0], 5
        )
