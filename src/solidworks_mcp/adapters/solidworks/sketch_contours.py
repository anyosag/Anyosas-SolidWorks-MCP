"""Whether a sketch's closed contours cross, read from its segment geometry.

The Dipper: two rings 35.5 mm apart with r 18 in one sketch made
``create_extrusion`` fail with a bare "Failed to create extrusion feature".
SolidWorks can't tell the regions of contours that cross or touch apart, so
it builds nothing. When an extrude or a cut fails, the sketch is read
(``feature_editing.describe_segments``, sketch mm) and its contours checked:

* a circle is an arc whose start and end meet;
* a loop is a chain of lines that closes on itself, endpoint to endpoint;
* two circles cross when their distance lies between the difference and
  the sum of their radii (nested and apart are fine; touching counts);
* a line of one contour crosses a circle when the circle's edge lies
  between its nearest and farthest point from the centre, and crosses a
  line of another loop when the two segments meet.

Construction geometry and open chains are left out. Pure geometry, apart
from :func:`crossings_in`, which reads the sketch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: Points closer than this (mm) are the same point.
TOLERANCE_MM = 1e-4

Point = tuple[float, float]


@dataclass(frozen=True)
class Circle:
    """A closed circle in sketch mm."""

    center: Point
    radius: float

    def describe(self) -> str:
        return (
            f"the circle at ({self.center[0]:g}, {self.center[1]:g}) r {self.radius:g}"
        )


@dataclass(frozen=True)
class Loop:
    """A closed chain of lines in sketch mm, as its corner points in order."""

    points: tuple[Point, ...]

    def edges(self) -> list[tuple[Point, Point]]:
        return [
            (self.points[i], self.points[(i + 1) % len(self.points)])
            for i in range(len(self.points))
        ]

    def describe(self) -> str:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (
            f"the {len(self.points)}-line loop from ({min(xs):g}, {min(ys):g}) "
            f"to ({max(xs):g}, {max(ys):g})"
        )


Contour = Circle | Loop


def _same(a: Point, b: Point) -> bool:
    return math.dist(a, b) <= TOLERANCE_MM


def _point(value: Any) -> Point | None:
    if isinstance(value, list | tuple) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return None


def contours(segments: list[dict[str, Any]]) -> list[Contour]:
    """The closed contours among described segments.

    Args:
        segments: ``describe_segment`` dicts (``type``, ``start_mm``,
            ``end_mm``, ``center_mm``, ``construction``; a ``circle`` gives
            ``center_mm`` and ``radius_mm``).

    Returns:
        list[Contour]: Each full circle, then each closed loop of lines.
    """
    circles: list[Contour] = []
    lines: list[tuple[Point, Point]] = []
    for info in segments:
        if info.get("construction"):
            continue
        radius = info.get("radius_mm")
        if info.get("type") == "circle" and isinstance(radius, (int, float)):
            centre = _point(info.get("center_mm"))
            if centre is not None:
                circles.append(Circle(centre, float(radius)))
            continue
        start, end = _point(info.get("start_mm")), _point(info.get("end_mm"))
        if start is None or end is None:
            continue
        center = _point(info.get("center_mm"))
        if info.get("type") == "arc" and center is not None and _same(start, end):
            circles.append(Circle(center, math.dist(center, start)))
        elif info.get("type") == "line" and not _same(start, end):
            lines.append((start, end))
    return circles + _loops(lines)


def _loops(lines: list[tuple[Point, Point]]) -> list[Contour]:
    """The closed chains among ``lines``, each line used once."""
    unused = list(lines)
    loops: list[Contour] = []
    while unused:
        first = unused.pop(0)
        chain = [first[0], first[1]]
        while not _same(chain[-1], chain[0]):
            following = next(
                (
                    line
                    for line in unused
                    if _same(line[0], chain[-1]) or _same(line[1], chain[-1])
                ),
                None,
            )
            if following is None:
                break
            unused.remove(following)
            chain.append(
                following[1] if _same(following[0], chain[-1]) else following[0]
            )
        if len(chain) > 3 and _same(chain[-1], chain[0]):
            loops.append(Loop(tuple(chain[:-1])))
    return loops


def _circles_cross(a: Circle, b: Circle) -> bool:
    distance = math.dist(a.center, b.center)
    return (
        abs(a.radius - b.radius) - TOLERANCE_MM
        <= distance
        <= a.radius + b.radius + TOLERANCE_MM
    )


def _segment_distance(point: Point, a: Point, b: Point) -> float:
    """The distance from ``point`` to the segment ``a``-``b``."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = (
        0.0
        if length == 0
        else ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length
    )
    t = min(1.0, max(0.0, t))
    return math.dist(point, (a[0] + t * dx, a[1] + t * dy))


def _line_meets_circle(a: Point, b: Point, circle: Circle) -> bool:
    nearest = _segment_distance(circle.center, a, b)
    farthest = max(math.dist(circle.center, a), math.dist(circle.center, b))
    return nearest - TOLERANCE_MM <= circle.radius <= farthest + TOLERANCE_MM


def _orientation(p: Point, q: Point, r: Point) -> float:
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])


def _segments_meet(a: Point, b: Point, c: Point, d: Point) -> bool:
    """Whether the segments ``a``-``b`` and ``c``-``d`` cross or touch."""
    if (
        min(
            _segment_distance(c, a, b),
            _segment_distance(d, a, b),
            _segment_distance(a, c, d),
            _segment_distance(b, c, d),
        )
        <= TOLERANCE_MM
    ):
        return True
    d1, d2 = _orientation(a, b, c), _orientation(a, b, d)
    d3, d4 = _orientation(c, d, a), _orientation(c, d, b)
    return d1 * d2 < 0 and d3 * d4 < 0


def _cross(first: Contour, second: Contour) -> bool:
    if isinstance(first, Circle) and isinstance(second, Circle):
        return _circles_cross(first, second)
    if isinstance(first, Loop) and isinstance(second, Loop):
        return any(
            _segments_meet(a, b, c, d)
            for a, b in first.edges()
            for c, d in second.edges()
        )
    loop = cast(Loop, first if isinstance(first, Loop) else second)
    circle = cast(Circle, second if isinstance(first, Loop) else first)
    return any(_line_meets_circle(a, b, circle) for a, b in loop.edges())


def crossings(found: list[Contour]) -> list[str]:
    """Each pair of contours that cross or touch, described."""
    return [
        f"{first.describe()} and {second.describe()}"
        for index, first in enumerate(found)
        for second in found[index + 1 :]
        if _cross(first, second)
    ]


def segments_in(adapter: Any, sketch_name: str) -> list[dict[str, Any]]:
    """The described segments of the sketch named ``sketch_name`` (none when unread)."""
    from .feature_editing import describe_segments

    model = adapter.currentModel
    feature = _attempt(adapter, lambda: model.FeatureByName(sketch_name))
    if feature is None:
        return []
    _flag(adapter, feature, "GetSpecificFeature2")
    sketch = _attempt(adapter, lambda: feature.GetSpecificFeature2())
    if sketch is None:
        return []
    return [info for _segment, info in describe_segments(adapter, sketch)]


def crossings_in(adapter: Any, sketch_name: str) -> list[str]:
    """The crossing contours of the sketch named ``sketch_name`` (none when unread)."""
    return crossings(contours(segments_in(adapter, sketch_name)))


def union_check_in(adapter: Any, sketch_name: str) -> dict[str, Any]:
    """The sketch's crossing contours, and why its regions' union isn't safe.

    Returns:
        dict[str, Any]: ``crossing``, the crossing pairs (none when unread),
        and ``refusal``, ``sketch_regions.union_refusal`` for them (None when
        none cross, or building every region is their union).
    """
    from .sketch_regions import union_refusal

    described = segments_in(adapter, sketch_name)
    found = contours(described)
    crossing = crossings(found)
    return {
        "crossing": crossing,
        "refusal": union_refusal(described, found) if crossing else None,
    }


def message(sketch_name: str, found: list[str]) -> str:
    """The sentence a failed extrude or cut gets when its contours cross."""
    shown = "; ".join(found[:3]) + (
        f"; and {len(found) - 3} more" if len(found) > 3 else ""
    )
    return (
        f"Sketch {sketch_name!r}: sketch contours intersect ({shown}); an extrusion "
        "needs disjoint regions or one outline that is their union (regions: "
        "'all' builds that union)."
    )
