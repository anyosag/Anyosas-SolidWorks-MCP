"""Convex, concave or tangent: how an edge's two faces meet at a point on it.

A convex edge (a block's outer edge, a hole's rim) has material inside the
angle its faces make, under 180 degrees; a concave one (a pocket's floor
edge, the foot of a boss) over 180; a tangent one (where a fillet meets its
faces) 180. ``select_edges``' ``convexity`` keeps one kind.

At a point P on the edge, with the edge's direction e there (from its line
or circle, as ``describe_edge`` reads them):

* the two faces are ``IEdge.GetTwoAdjacentFaces2``;
* one face's outward normal n: ``IFace2.Normal`` when it is flat (the
  ``side`` rule's normal); for a curved one, ``ISurface.EvaluateAtPoint``'s
  surface normal, reversed when ``IFace2.FaceInSurfaceSense`` says the face
  runs against its surface;
* the direction t into the other face, at right angles to the edge: the
  closest points on that face (``IFace2.GetClosestPointOn``) to P moved a
  little each way at right angles to e lie inside the face for the moves
  that lead into it, and on the edge (at P) for the others, so their offsets
  from P add up along t.

The material near the first face lies on its -n side, so the second face
leads away from it at a convex edge (t . n < 0) and toward it at a concave
one; at a tangent edge t lies in the first face (|t . n| under sin 1 deg).
An edge whose faces or geometry don't read is None (not kept).

Only ``IFace2.Normal`` and ``GetClosestPointOn`` have run on SolidWorks in
this code; ``tests/scripts/probe_dipper_edges.py`` (step ``convexity``)
checks the rest on a block with a pocket, a hole, a boss and a fillet.
"""

from __future__ import annotations

import math
from typing import Any

from . import limits
from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

Vector = tuple[float, float, float]

CONVEXITIES = ("convex", "concave", "tangent")

#: How far from P the probe points go, metres (0.02 mm): well inside the
#: narrowest face a part has (a 0.2 mm chamfer), far above SolidWorks'
#: 1e-8 m tolerance.
STEP_M = 2e-5

#: Faces meeting within 1 degree of flat are tangent.
_TANGENT_SIN = math.sin(math.radians(1.0))


def _add(a: Vector, b: Vector, k: float = 1.0) -> Vector:
    return (a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2])


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _unit(v: Vector) -> Vector | None:
    length = math.sqrt(_dot(v, v))
    if length < 1e-12:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def _metres(point_mm: list[float]) -> Vector:
    return (point_mm[0] / 1000.0, point_mm[1] / 1000.0, point_mm[2] / 1000.0)


def _scaled(v: Vector) -> Vector:
    """``v`` made unit; ``v`` must not be zero."""
    length = math.sqrt(_dot(v, v))
    return (v[0] / length, v[1] / length, v[2] / length)


def perpendicular(e: Vector) -> Vector:
    """A unit vector at right angles to the unit vector ``e``.

    ``e`` crossed with the axis it is least along, which is never parallel
    to it.
    """
    axis = min(range(3), key=lambda i: abs(e[i]))
    other = (
        1.0 if axis == 0 else 0.0,
        1.0 if axis == 1 else 0.0,
        1.0 if axis == 2 else 0.0,
    )
    return _scaled(_cross(e, other))


def kind_of(t: Vector, n: Vector) -> str:
    """Convex, concave or tangent, from the direction ``t`` into the second
    face and the first face's outward normal ``n`` (both unit)."""
    along = _dot(t, n)
    if abs(along) < _TANGENT_SIN:
        return "tangent"
    return "convex" if along < 0 else "concave"


class ConvexityReader:
    """Read edges' convexity on the active part.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        flag_once: ``(obj, name)``: flag a member once (``PartIndex.flag_once``).
    """

    def __init__(self, adapter: Any, flag_once: Any = None) -> None:
        self._adapter = adapter
        self._flag_once = flag_once or (lambda obj, name: _flag(adapter, obj, name))
        self.reads = 0

    def _closest(self, entity: Any, point: Vector) -> Vector | None:
        limits.check("reading edge convexity")
        self._flag_once(entity, "GetClosestPointOn")
        self.reads += 1
        found = _attempt(self._adapter, lambda: list(entity.GetClosestPointOn(*point)))
        if not found or len(found) < 3:
            return None
        return (float(found[0]), float(found[1]), float(found[2]))

    def point_and_direction(
        self, edge: Any, info: dict[str, Any]
    ) -> tuple[Vector, Vector] | None:
        """A point inside the edge and its direction there, metres; None for a curve
        that is neither a line nor a circle, or geometry that didn't read."""
        start, end = info.get("start_mm"), info.get("end_mm")
        if info.get("curve") == "linear" and start and end:
            a, b = _metres(start), _metres(end)
            direction = _unit(_add(b, a, -1.0))
            if direction is None:
                return None
            return _add(a, _add(b, a, -1.0), 0.5), direction
        center = info.get("center_mm")
        axis, radius = info.get("axis"), info.get("radius_mm")
        if info.get("curve") != "circular" or not center or not axis or not radius:
            return None
        c = _metres(center)
        n = _unit((float(axis[0]), float(axis[1]), float(axis[2])))
        if n is None:
            return None
        r = float(radius) / 1000.0
        if start and end:
            # Half way round the arc: between its ends, or across a half circle.
            a, b = _add(_metres(start), c, -1.0), _add(_metres(end), c, -1.0)
            towards = _unit(_add(a, b)) or _unit(_cross(n, a))
            if towards is None:
                return None
            point = _add(c, towards, r)
            on_edge = self._closest(edge, point)
            if on_edge is None:
                return None
            if math.dist(on_edge, point) > 1e-6:  # the arc runs the other way
                point = _add(c, towards, -r)
        else:
            point = _add(c, perpendicular(n), r)
        # The point is r from the centre, at right angles to the axis.
        return point, _scaled(_cross(n, _add(point, c, -1.0)))

    def direction_into(self, face: Any, point: Vector, e: Vector) -> Vector | None:
        """The unit direction into ``face`` from ``point``, at right angles to ``e``."""
        w1 = perpendicular(e)
        w2 = _cross(e, w1)
        total: Vector = (0.0, 0.0, 0.0)
        for d in (w1, w2, (-w1[0], -w1[1], -w1[2]), (-w2[0], -w2[1], -w2[2])):
            found = self._closest(face, _add(point, d, STEP_M))
            if found is None:
                return None
            total = _add(total, _add(found, point, -1.0))
        total = _add(total, e, -_dot(total, e))
        if math.sqrt(_dot(total, total)) < STEP_M * 0.1:
            return None
        return _unit(total)

    def flat_normal(self, face: Any) -> Vector | None:
        """``IFace2.Normal`` (outward), or None for a curved face."""
        raw = as_list(_attempt(self._adapter, lambda: face.Normal))
        self.reads += 1
        if len(raw) < 3:
            return None
        return _unit((float(raw[0]), float(raw[1]), float(raw[2])))

    def curved_normal(self, face: Any, point: Vector) -> Vector | None:
        """A curved face's outward normal at ``point``, or None when it doesn't read."""
        self._flag_once(face, "GetSurface")
        self._flag_once(face, "FaceInSurfaceSense")
        surface = _attempt(self._adapter, lambda: face.GetSurface())
        if surface is None:
            return None
        _flag(self._adapter, surface, "EvaluateAtPoint")
        values = as_list(
            _attempt(self._adapter, lambda: surface.EvaluateAtPoint(*point))
        )
        same = _attempt(self._adapter, lambda: face.FaceInSurfaceSense())
        self.reads += 3
        if len(values) < 6 or not isinstance(same, bool):
            return None
        normal = _unit((float(values[3]), float(values[4]), float(values[5])))
        if normal is None:
            return None
        return normal if same else (-normal[0], -normal[1], -normal[2])

    def read(self, edge: Any, info: dict[str, Any]) -> str | None:
        """``"convex"``, ``"concave"``, ``"tangent"`` or None; kept in ``info``."""
        if "convexity" in info:
            return info["convexity"]
        info["convexity"] = self._read(edge, info)
        return info["convexity"]

    def _read(self, edge: Any, info: dict[str, Any]) -> str | None:
        self._flag_once(edge, "GetTwoAdjacentFaces2")
        faces = as_list(_attempt(self._adapter, lambda: edge.GetTwoAdjacentFaces2()))
        self.reads += 1
        if len(faces) != 2 or any(face is None for face in faces):
            return None
        located = self.point_and_direction(edge, info)
        if located is None:
            return None
        point, e = located
        first, second = faces
        normal = self.flat_normal(first)
        if normal is None:
            flat = self.flat_normal(second)
            if flat is not None:
                first, second, normal = second, first, flat
            else:
                normal = self.curved_normal(first, point)
        if normal is None:
            return None
        t = self.direction_into(second, point, e)
        return None if t is None else kind_of(t, normal)
