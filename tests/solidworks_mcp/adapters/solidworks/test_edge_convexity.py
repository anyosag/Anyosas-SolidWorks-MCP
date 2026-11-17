"""Convex, concave or tangent (``edge_convexity``).

The fakes answer ``GetClosestPointOn`` geometrically: flat rectangles,
cylinders (a hole's wall, a boss's wall, a fillet's quarter round) and a
sphere cap, each with its outward normal (``Normal`` when flat; a surface
and ``FaceInSurfaceSense`` when curved). A curved face's surface answers
``EvaluateAtPoint`` as SOLIDWORKS 2026 SP4.1 did: the surface normal first,
then two directions and two curvatures, with the face's outward normal the
surface normal reversed where ``FaceInSurfaceSense`` is True. Millimetres
in, metres to the COM fakes, as SolidWorks takes them.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import edge_convexity as ec

MM = 1e-3
Vector = tuple[float, float, float]


def _m(*c: float) -> Vector:
    return (c[0] * MM, c[1] * MM, c[2] * MM)


class _Plane:
    """A flat face: an axis-aligned rectangle ``lo``..``hi`` (mm), outward ``normal``."""

    def __init__(self, lo: Vector, hi: Vector, normal: Vector) -> None:
        self.lo, self.hi = _m(*lo), _m(*hi)
        self.Normal = list(normal)

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        return [
            min(max(c, a), b)
            for c, a, b in zip((x, y, z), self.lo, self.hi, strict=True)
        ] + [0.0, 0.0]


class _Surface:
    def __init__(self, normal_at: Any) -> None:
        self._normal_at = normal_at

    def EvaluateAtPoint(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        # Normal, first and second principal directions, two curvatures.
        return [*self._normal_at((x, y, z)), 9.0, 9.0, 9.0, 8.0, 8.0, 8.0, 0.0, 0.0]


class _Cylinder:
    """A cylinder about z through ``(cx, cy)``, radius ``r``, z from ``z0`` to ``z1`` (mm).

    ``inside`` is True for a boss and False for a hole. The surface's normal
    points away from the axis on both, as measured; a hole's face reads
    ``FaceInSurfaceSense`` True (its outward normal is the reverse), a
    boss's False.
    """

    Normal = [0.0, 0.0, 0.0]

    def __init__(
        self, cx: float, cy: float, r: float, z0: float, z1: float, inside: bool
    ) -> None:
        self.c, self.r = (cx * MM, cy * MM), r * MM
        self.z0, self.z1, self.inside = z0 * MM, z1 * MM, inside

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        angle = math.atan2(y - self.c[1], x - self.c[0])
        return [
            self.c[0] + self.r * math.cos(angle),
            self.c[1] + self.r * math.sin(angle),
            min(max(z, self.z0), self.z1),
        ]

    def GetSurface(self) -> _Surface:  # noqa: N802
        def radial(p: Vector) -> list[float]:
            d = (p[0] - self.c[0], p[1] - self.c[1])
            length = math.hypot(*d)
            return [d[0] / length, d[1] / length, 0.0]

        return _Surface(radial)

    def FaceInSurfaceSense(self) -> Any:  # noqa: N802
        return None if self.inside is None else not self.inside


class _Cone:
    """A cone about z narrowing upward: radius 3 mm at z 5, 1 mm at z 7.

    Its surface normal points out and up; it reads ``FaceInSurfaceSense``
    False, as the cone of the probe's revolve did.
    """

    Normal = [0.0, 0.0, 0.0]

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        angle = math.atan2(y, x)
        # Along the slant line from (3, 5) to (1, 7) in (r, z), mm.
        r, h = math.hypot(x, y) / MM, z / MM
        t = min(max(((r - 3) * -2 + (h - 5) * 2) / 8, 0.0), 1.0)
        radius, height = 3 - 2 * t, 5 + 2 * t
        return [
            radius * MM * math.cos(angle),
            radius * MM * math.sin(angle),
            height * MM,
        ]

    def GetSurface(self) -> _Surface:  # noqa: N802
        def slanted(p: Vector) -> list[float]:
            angle = math.atan2(p[1], p[0])
            k = math.sqrt(0.5)
            return [k * math.cos(angle), k * math.sin(angle), k]

        return _Surface(slanted)

    def FaceInSurfaceSense(self) -> bool:  # noqa: N802
        return False


class _QuarterRound:
    """A fillet: a quarter cylinder about y through (x, z) = (8, 8), radius 2 mm,
    from the top (z = 10) round to the side (x = 10)."""

    Normal = [0.0, 0.0, 0.0]

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        angle = math.atan2(x - 8 * MM, z - 8 * MM)  # 0 at the top, pi/2 at the side
        angle = min(max(angle, 0.0), math.pi / 2)
        return [8 * MM + 2 * MM * math.sin(angle), y, 8 * MM + 2 * MM * math.cos(angle)]


class _Cap:
    """A sphere cap: centre (0, 0, 1) mm, radius 5 mm, above z = 5 (rim radius 3)."""

    Normal = [0.0, 0.0, 0.0]

    def __init__(self, sense: Any = True) -> None:
        self._sense = sense

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        d = (x, y, z - 1 * MM)
        length = math.sqrt(sum(c * c for c in d))
        p = [5 * MM * c / length for c in d]
        p[2] += 1 * MM
        if p[2] < 5 * MM:
            angle = math.atan2(y, x)
            p = [3 * MM * math.cos(angle), 3 * MM * math.sin(angle), 5 * MM]
        return p

    def GetSurface(self) -> _Surface:  # noqa: N802
        def outward(p: Vector) -> list[float]:
            d = (p[0], p[1], p[2] - 1 * MM)
            length = math.sqrt(sum(c * c for c in d))
            return [c / length for c in d]

        return _Surface(outward)

    def FaceInSurfaceSense(self) -> Any:  # noqa: N802
        return self._sense


class _Edge:
    """An edge between two faces; ``closest`` for an arc's ``GetClosestPointOn``."""

    def __init__(self, faces: list[Any], closest: Any = None) -> None:
        self._faces, self._closest = faces, closest

    def GetTwoAdjacentFaces2(self) -> list[Any]:  # noqa: N802
        return self._faces

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        return list(self._closest((x, y, z)))


class _Adapter:
    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


def _line(a: Vector, b: Vector) -> dict[str, Any]:
    return {"curve": "linear", "start_mm": list(a), "end_mm": list(b)}


def _circle(center: Vector, radius: float, **ends: Any) -> dict[str, Any]:
    return {
        "curve": "circular",
        "center_mm": list(center),
        "axis": [0.0, 0.0, 1.0],
        "radius_mm": radius,
        **ends,
    }


def _read(faces: list[Any], info: dict[str, Any], closest: Any = None) -> Any:
    return ec.ConvexityReader(_Adapter()).read(_Edge(faces, closest), info)


_TOP = _Plane((0, 0, 10), (20, 20, 10), (0, 0, 1))
_SIDE = _Plane((20, 0, 0), (20, 20, 10), (1, 0, 0))


# ---- the three kinds -------------------------------------------------------------


@pytest.mark.parametrize("order", [0, 1])
def test_a_blocks_outer_edge_is_convex(order: int) -> None:
    faces = [_TOP, _SIDE] if order == 0 else [_SIDE, _TOP]

    assert _read(faces, _line((20, 0, 10), (20, 20, 10))) == "convex"


@pytest.mark.parametrize("order", [0, 1])
def test_a_pockets_floor_edge_is_concave(order: int) -> None:
    floor = _Plane((3, 3, 7), (7, 7, 7), (0, 0, 1))
    wall = _Plane((3, 3, 7), (3, 7, 10), (1, 0, 0))  # faces into the pocket
    faces = [floor, wall] if order == 0 else [wall, floor]

    assert _read(faces, _line((3, 3, 7), (3, 7, 7))) == "concave"


@pytest.mark.parametrize("order", [0, 1])
def test_a_holes_rim_is_convex(order: int) -> None:
    hole = _Cylinder(10, 10, 2, 5, 10, inside=False)
    faces = [_TOP, hole] if order == 0 else [hole, _TOP]

    assert _read(faces, _circle((10, 10, 10), 2)) == "convex"


def test_a_bosss_foot_is_concave() -> None:
    plate = _Plane((-20, -20, 0), (20, 20, 0), (0, 0, 1))
    boss = _Cylinder(0, 0, 3, 0, 5, inside=True)

    assert _read([boss, plate], _circle((0, 0, 0), 3)) == "concave"


def test_a_fillets_edge_is_tangent() -> None:
    top = _Plane((0, 0, 10), (8, 20, 10), (0, 0, 1))

    assert _read([top, _QuarterRound()], _line((8, 0, 10), (8, 20, 10))) == "tangent"


def test_two_curved_faces_use_the_surface_normal_and_its_sense() -> None:
    """A dome on a boss: the cylinder's normal comes from its surface."""
    boss = _Cylinder(0, 0, 3, 0, 5, inside=True)

    assert _read([boss, _Cap()], _circle((0, 0, 5), 3)) == "convex"


def test_a_cone_on_a_cylinder_is_convex_where_they_meet() -> None:
    """The probe's revolve: a cylinder with a cone on it, both read from surfaces."""
    cylinder = _Cylinder(0, 0, 3, 0, 5, inside=True)

    assert _read([cylinder, _Cone()], _circle((0, 0, 5), 3)) == "convex"
    assert _read([_Cone(), cylinder], _circle((0, 0, 5), 3)) == "convex"


def test_the_kind_is_kept_in_the_description() -> None:
    info = _line((20, 0, 10), (20, 20, 10))
    reader = ec.ConvexityReader(_Adapter())
    reader.read(_Edge([_TOP, _SIDE]), info)
    reads = reader.reads

    assert info["convexity"] == "convex"
    assert reader.read(_Edge([]), info) == "convex"
    assert reader.reads == reads


# ---- arcs: a point half way round ------------------------------------------------


def _arc_closest(center: Vector, r: float, start: float, sweep: float) -> Any:
    """``GetClosestPointOn`` of an arc about z from angle ``start``, ``sweep`` rad."""

    def closest(p: Vector) -> Vector:
        angle = (math.atan2(p[1] - center[1] * MM, p[0] - center[0] * MM) - start) % (
            2 * math.pi
        )
        if angle > sweep:
            angle = sweep if angle - sweep < 2 * math.pi - angle else 0.0
        a = start + angle
        return (
            center[0] * MM + r * MM * math.cos(a),
            center[1] * MM + r * MM * math.sin(a),
            center[2] * MM,
        )

    return closest


@pytest.mark.parametrize(
    ("start", "end", "first", "sweep"),
    [
        ((12, 10, 10), (10, 12, 10), 0.0, math.pi / 2),  # a quarter: between its ends
        # The long way round, from 90 degrees to 0: not through 45.
        ((10, 12, 10), (12, 10, 10), math.pi / 2, 3 * math.pi / 2),
        ((12, 10, 10), (8, 10, 10), 0.0, math.pi),  # a half: across the chord
    ],
)
def test_an_arc_rim_is_read_half_way_round(
    start: Vector, end: Vector, first: float, sweep: float
) -> None:
    hole = _Cylinder(10, 10, 2, 5, 10, inside=False)
    info = _circle((10, 10, 10), 2, start_mm=list(start), end_mm=list(end))
    closest = _arc_closest((10, 10, 10), 2, first, sweep)

    assert _read([_TOP, hole], info, closest) == "convex"


# ---- what can't be read ----------------------------------------------------------


@pytest.mark.parametrize(
    ("faces", "info"),
    [
        ([_TOP], _line((20, 0, 10), (20, 20, 10))),
        ([_TOP, None], _line((20, 0, 10), (20, 20, 10))),
        ([_TOP, _SIDE], {"curve": "other", "start_mm": [0, 0, 0], "end_mm": [1, 0, 0]}),
        ([_TOP, _SIDE], _line((1, 1, 1), (1, 1, 1))),
        ([_TOP, _SIDE], {"curve": "circular", "center_mm": [0, 0, 0]}),
        ([_TOP, _SIDE], {**_circle((0, 0, 0), 2), "axis": [0, 0, 0]}),
        (
            [_TOP, _SIDE],
            _circle((0, 0, 0), 2, start_mm=[0, 0, 0], end_mm=[0, 0, 0]),
        ),
        (
            [_Cylinder(0, 0, 3, 0, 5, inside=None), _Cap()],  # type: ignore[arg-type]
            _circle((0, 0, 5), 3),
        ),
        ([object(), object()], _line((20, 0, 10), (20, 20, 10))),
        ([_TOP, object()], _line((20, 0, 10), (20, 20, 10))),
    ],
)
def test_what_does_not_read_is_none(faces: list[Any], info: dict[str, Any]) -> None:
    assert _read(faces, info) is None


def test_an_arc_whose_point_does_not_read_is_none() -> None:
    info = _circle((10, 10, 10), 2, start_mm=[12, 10, 10], end_mm=[10, 12, 10])

    assert _read([_TOP, _SIDE], info, closest=lambda _p: []) is None


def test_a_face_whose_points_all_fall_on_the_edge_has_no_direction() -> None:
    edge_only = _Plane((20, 0, 10), (20, 20, 10), (1, 0, 0))

    assert _read([_TOP, edge_only], _line((20, 0, 10), (20, 20, 10))) is None


def test_a_curved_face_without_a_surface_has_no_normal() -> None:
    reader = ec.ConvexityReader(_Adapter())

    assert reader.curved_normal(object(), (0.0, 0.0, 0.0)) is None
    flat = type("S", (), {"EvaluateAtPoint": lambda self, *_p: [0, 0, 0, 0, 0, 0]})
    face = type(
        "F",
        (),
        {"GetSurface": lambda self: flat(), "FaceInSurfaceSense": lambda self: True},
    )()
    assert reader.curved_normal(face, (0.0, 0.0, 0.0)) is None


def test_kinds_and_perpendiculars() -> None:
    assert ec.kind_of((0.0, 0.0, -1.0), (0.0, 0.0, 1.0)) == "convex"
    assert ec.kind_of((0.0, 0.0, 1.0), (0.0, 0.0, 1.0)) == "concave"
    assert ec.kind_of((1.0, 0.0, 0.01), (0.0, 0.0, 1.0)) == "tangent"
    for e in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.6, 0.8, 0.0)):
        w = ec.perpendicular(e)
        assert sum(a * b for a, b in zip(w, e, strict=True)) == pytest.approx(0.0)
        assert math.hypot(*w) == pytest.approx(1.0)
