"""The pocket plate of ``fake_pocket_plate``, able to pick: for ``query_geometry``.

Its faces gain ``GetSurface`` (every face is a plane), its edges ``GetBody``,
and the model a selection that ``SelectByID2`` and ``SelectByRay`` fill as
SolidWorks would:

* ``SelectByID2("", "FACE" | "EDGE", x, y, z, ...)`` selects a face the
  point lies on, or an edge within 0.1 mm of it;
* ``SelectByRay(x, y, z, dx, dy, dz, radius, type, ...)`` selects the first
  face the ray crosses (within ``radius`` of its rectangle), or the first
  edge passing within ``radius`` of the ray, and adds it to the selection
  even with ``Append`` false, as SOLIDWORKS 2026 SP4.1 did.

Every call is counted in the plate's :class:`fake_pocket_plate.Log`, picks
included. The shared helper is left as it is: the objects it builds are
given these classes, which only add members.
"""

from __future__ import annotations

import math
from typing import Any

from .fake_pocket_plate import (
    Dispatch,
    Edge,
    Extension,
    Face,
    Log,
    Model,
    SelectionManager,
    pocket_plate,
)

Point = tuple[float, float, float]


class Surface(Dispatch):
    """A plane: the only surface the plate has."""

    def IsPlane(self) -> bool:  # noqa: N802
        self.count("IsPlane")
        return True


class QueryFace(Face):
    def GetSurface(self) -> Surface:  # noqa: N802
        self.count("GetSurface")
        return Surface(self._log)


class QueryEdge(Edge):
    def GetBody(self) -> Any:  # noqa: N802
        self.count("GetBody")
        return self.faces[0].body if self.faces else None


class QuerySelection(SelectionManager):
    """A selection list the picks fill."""

    def __init__(self, log: Log) -> None:
        super().__init__(log)
        self.items: list[Any] = []

    def GetSelectedObjectCount2(self, _mark: int) -> int:  # noqa: N802
        self.count("GetSelectedObjectCount2")
        return len(self.items)

    def GetSelectedObject6(self, index: int, _mark: int) -> Any:  # noqa: N802
        self.count("GetSelectedObject6")
        return self.items[index - 1] if 0 < index <= len(self.items) else None


def _on_face(face: Face, p: Point) -> list[float]:
    """The point of a face's rectangle nearest ``p`` (no call counted)."""
    lo, hi = face.box
    return [min(max(c, a), b) for c, a, b in zip(p, lo, hi, strict=True)]


def _on_edge(edge: Edge, p: Point) -> list[float]:
    """The point of an edge nearest ``p`` (no call counted)."""
    ab = [q - a for a, q in zip(edge.a, edge.b, strict=True)]
    t = sum(u * (c - a) for u, c, a in zip(ab, p, edge.a, strict=True))
    t = min(1.0, max(0.0, t / sum(u * u for u in ab)))
    return [a + t * u for a, u in zip(edge.a, ab, strict=True)]


def _ray_hits_face(face: Face, start: Point, d: Point, radius: float) -> float | None:
    """Where (t) a ray crosses an axis-aligned flat face, or None."""
    lo, hi = face.box
    flat = next((k for k in range(3) if abs(hi[k] - lo[k]) < 1e-12), None)
    if flat is None or abs(d[flat]) < 1e-12:
        return None
    t = (lo[flat] - start[flat]) / d[flat]
    if t < 0:
        return None
    at = [start[k] + t * d[k] for k in range(3)]
    inside = all(
        lo[k] - radius <= at[k] <= hi[k] + radius for k in range(3) if k != flat
    )
    return t if inside else None


def _ray_near_edge(edge: Edge, start: Point, d: Point, radius: float) -> float | None:
    """Where (t) a ray passes within ``radius`` of an edge (sampled), or None."""
    best: float | None = None
    for step in range(41):
        s = step / 40
        p = [a + s * (b - a) for a, b in zip(edge.a, edge.b, strict=True)]
        t = sum((p[k] - start[k]) * d[k] for k in range(3))
        if t < 0:
            continue
        on_ray = [start[k] + t * d[k] for k in range(3)]
        if math.dist(on_ray, p) <= radius and (best is None or t < best):
            best = t
    return best


class QueryExtension(Extension):
    """Picks by point and by ray, and an id for faces as well as edges."""

    def __init__(self, log: Log, model: QueryModel) -> None:
        super().__init__(log, model.Extension.edges)
        self.model = model

    def GetPersistReference3(self, entity: Any) -> bytes:  # noqa: N802
        target = getattr(entity, "_entity", entity)
        if isinstance(target, Face):
            self.count("GetPersistReference3")
            faces = [f for b in self.model.bodies for f in b.faces]
            return (
                f"face:{next(i for i, f in enumerate(faces) if f is target)}".encode()
            )
        return super().GetPersistReference3(entity)

    def _select(self, entity: Any, add: bool = False) -> bool:
        if entity is None:
            return False
        items = self.model.SelectionManager.items if add else []
        self.model.SelectionManager.items = [*items, entity]
        return True

    def SelectByID2(  # noqa: N802
        self, _name: str, kind: str, x: float, y: float, z: float, *_rest: Any
    ) -> bool:
        self.count("SelectByID2")
        point = (x, y, z)
        if kind == "FACE":
            on = [
                f
                for f in self.model.faces()
                if math.dist(_on_face(f, point), point) < 1e-9
            ]
            return self._select(on[0] if on else None)
        near = [
            e
            for e in self.model.edges()
            if math.dist(_on_edge(e, point), point) <= 1e-4
        ]
        return self._select(near[0] if near else None)

    def SelectByRay(  # noqa: N802
        self,
        x: float,
        y: float,
        z: float,
        dx: float,
        dy: float,
        dz: float,
        radius: float,
        kind: int,
        *_rest: Any,
    ) -> bool:
        self.count("SelectByRay")
        start, d = (x, y, z), (dx, dy, dz)
        if kind == 2:
            hits = [
                (t, f)
                for f in self.model.faces()
                if (t := _ray_hits_face(f, start, d, radius)) is not None
            ]
        else:
            hits = [
                (t, e)
                for e in self.model.edges()
                if (t := _ray_near_edge(e, start, d, radius)) is not None
            ]
        if not hits:
            return False
        return self._select(min(hits, key=lambda pair: pair[0])[1], add=True)


class QueryModel(Model):
    """The plate with a selection the picks fill and ``ClearSelection2`` empties."""

    def faces(self) -> list[Face]:
        return [f for body in self.bodies for f in body.faces]

    def edges(self) -> list[Edge]:
        return [e for body in self.bodies for e in body.edges]

    def ClearSelection2(self, _all: bool) -> None:  # noqa: N802
        self.count("ClearSelection2")
        self.SelectionManager.items = []


def query_plate(log: Log | None = None) -> tuple[QueryModel, Log]:
    """``pocket_plate`` (1,944 edges, 812 faces), able to pick."""
    model, log = pocket_plate(log)
    model.__class__ = QueryModel
    for body in model.bodies:
        for face in body.faces:
            face.__class__ = QueryFace
        for edge in body.edges:
            edge.__class__ = QueryEdge
    selection = QuerySelection(log)
    model.SelectionManager = selection
    model.Extension = QueryExtension(log, model)  # type: ignore[arg-type]
    return model, log  # type: ignore[return-value]
