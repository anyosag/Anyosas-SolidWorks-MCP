"""Whether a sketch's closed contours cross (``sketch_contours``).

The Dipper: two rings 35.5 mm apart with r 18 made ``create_extrusion`` fail
with a bare "Failed to create extrusion feature".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import sketch_contours as sc


def _circle(x: float, y: float, r: float, **extra: Any) -> dict[str, Any]:
    return {
        "type": "arc",
        "start_mm": [x + r, y],
        "end_mm": [x + r, y],
        "center_mm": [x, y],
        **extra,
    }


def _rectangle(x1: float, y1: float, x2: float, y2: float) -> list[dict[str, Any]]:
    corners = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    lines = [
        {"type": "line", "start_mm": corners[i], "end_mm": corners[(i + 1) % 4]}
        for i in range(4)
    ]
    # Drawn out of order and one line reversed, as a sketch may hold them.
    lines[1]["start_mm"], lines[1]["end_mm"] = lines[1]["end_mm"], lines[1]["start_mm"]
    return [lines[2], lines[0], lines[3], lines[1]]


def _found(*segments: dict[str, Any]) -> list[str]:
    return sc.crossings(sc.contours(list(segments)))


def test_the_dippers_rings_cross() -> None:
    assert _found(_circle(0, 0, 18), _circle(35.5, 0, 18)) == [
        "the circle at (0, 0) r 18 and the circle at (35.5, 0) r 18"
    ]


@pytest.mark.parametrize(
    "second",
    [
        _circle(40, 0, 18),  # apart
        _circle(1, 0, 5),  # inside: a ring
        _circle(5, 5, 3, construction=True),  # construction
    ],
)
def test_circles_apart_or_nested_do_not(second: dict[str, Any]) -> None:
    assert _found(_circle(0, 0, 18), second) == []


def test_circles_that_touch_count() -> None:
    assert len(_found(_circle(0, 0, 18), _circle(36, 0, 18))) == 1


def test_a_loop_is_found_however_its_lines_are_stored() -> None:
    (loop,) = sc.contours(_rectangle(0, 0, 10, 4))

    assert isinstance(loop, sc.Loop)
    assert len(loop.points) == 4
    assert loop.describe() == "the 4-line loop from (0, 0) to (10, 4)"


def test_open_chains_and_odd_segments_are_not_contours() -> None:
    open_chain = _rectangle(0, 0, 10, 4)[:3]
    odd = [
        {"type": "line", "start_mm": [1, 1], "end_mm": [1, 1]},  # no length
        {"type": "arc", "start_mm": [0, 0], "end_mm": [1, 1], "center_mm": [0, 1]},
        {"type": "spline", "start_mm": [0, 0], "end_mm": [0, 0]},
        {"type": "line", "start_mm": None, "end_mm": [1, 1]},
        {"type": "text", "text": "A"},
    ]

    assert sc.contours(open_chain + odd) == []


@pytest.mark.parametrize(
    ("other", "crossed"),
    [
        (_rectangle(5, 2, 15, 6), True),  # overlapping
        (_rectangle(10, 0, 20, 4), True),  # sharing a side
        (_rectangle(20, 0, 30, 4), False),  # apart
        (_rectangle(2, 1, 4, 3), False),  # inside: a pocket
    ],
)
def test_loops(other: list[dict[str, Any]], crossed: bool) -> None:
    assert bool(_found(*_rectangle(0, 0, 10, 4), *other)) is crossed


@pytest.mark.parametrize(
    ("circle", "crossed"),
    [
        (_circle(10, 2, 3), True),  # over a side
        (_circle(5, 2, 1), False),  # a hole inside the plate
        (_circle(5, 2, 50), False),  # around the plate
        (_circle(40, 2, 3), False),  # apart
    ],
)
def test_a_circle_and_a_loop(circle: dict[str, Any], crossed: bool) -> None:
    found = _found(*_rectangle(0, 0, 10, 4), circle)

    assert bool(found) is crossed
    if crossed:
        assert found[0].startswith("the circle at (10, 2) r 3 and the 4-line loop")


def test_crossing_lines_without_a_shared_end_meet() -> None:
    plus = [
        {"type": "line", "start_mm": [0, 0], "end_mm": [10, 10]},
        {"type": "line", "start_mm": [10, 10], "end_mm": [10, 0]},
        {"type": "line", "start_mm": [10, 0], "end_mm": [0, 0]},
    ]
    other = [
        {"type": "line", "start_mm": [0, 8], "end_mm": [8, 0]},
        {"type": "line", "start_mm": [8, 0], "end_mm": [8, 8]},
        {"type": "line", "start_mm": [8, 8], "end_mm": [0, 8]},
    ]

    assert len(_found(*plus, *other)) == 1


def test_the_message_names_the_sketch_and_at_most_three_pairs() -> None:
    pairs = [f"pair {i}" for i in range(5)]

    text = sc.message("Sketch5", pairs)

    assert text == (
        "Sketch 'Sketch5': sketch contours intersect (pair 0; pair 1; pair 2; and "
        "2 more); an extrusion needs disjoint regions or one outline that is "
        "their union."
    )
    assert "and" not in sc.message("S", ["one"]).split("(")[1].split(")")[0]


class _Adapter:
    def __init__(self, model: Any) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


def _arc(x: float, y: float, r: float) -> Any:
    point = SimpleNamespace(X=(x + r) / 1000, Y=y / 1000)
    return SimpleNamespace(
        GetType=lambda: 1,
        GetStartPoint2=lambda: point,
        GetEndPoint2=lambda: point,
        GetCenterPoint2=lambda: SimpleNamespace(X=x / 1000, Y=y / 1000),
        ConstructionGeometry=False,
    )


def test_a_sketch_is_read_for_its_crossings() -> None:
    sketch = SimpleNamespace(
        GetSketchSegments=lambda: (_arc(0, 0, 18), _arc(35.5, 0, 18))
    )
    feature = SimpleNamespace(GetSpecificFeature2=lambda: sketch)
    model = SimpleNamespace(FeatureByName={"Rings": feature}.get)

    assert sc.crossings_in(_Adapter(model), "Rings") == [
        "the circle at (0, 0) r 18 and the circle at (35.5, 0) r 18"
    ]
    assert sc.crossings_in(_Adapter(model), "Missing") == []
    empty = SimpleNamespace(GetSpecificFeature2=lambda: None)
    assert (
        sc.crossings_in(_Adapter(SimpleNamespace(FeatureByName=lambda _n: empty)), "X")
        == []
    )
