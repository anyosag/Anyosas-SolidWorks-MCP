"""A fake SolidWorks part with about 2,000 edges, counting every COM call.

Body ``Plate``: a 200 x 160 x 10 mm plate (z 0..10) with a 16 x 10 grid of
4 x 4 x 3 mm pockets cut into its top: the merged-body case, where the top
face carries every pocket's four rim edges (640 of them) and has a box as
big as the plate. Body ``Block``: a 10 mm cube at x 300..310, far away.

A face's ``wrappers`` says what its ``GetEdges`` hands back:

* ``"same"``: the same Python objects each time;
* ``"equal"``: new wrappers whose ``_oleobj_`` compare and hash equal, as
  pywin32 wraps one COM object SolidWorks keeps per entity;
* ``"equal_unhashed"``: the same, but each ``_oleobj_`` hashed apart;
* ``"new"``: new wrappers of new objects, as a SolidWorks that made a COM
  object per read would.

Every member call counts one round trip in :attr:`Log.calls` by name, and
every ``_FlagAsMethod`` one as ``flag:<name>``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

Point = tuple[float, float, float]
MM = 1e-3

GRID = (16, 10)
POCKET, DEPTH, PITCH, MARGIN = 4.0, 3.0, 12.0, 10.0


class Log:
    """COM round trips seen by the fakes."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    @property
    def total(self) -> int:
        return sum(self.calls.values())

    @property
    def flags(self) -> int:
        return sum(n for name, n in self.calls.items() if name.startswith("flag:"))


class Dispatch:
    """A fake dispatch: member calls and flags are counted."""

    def __init__(self, log: Log) -> None:
        self._log = log

    def _FlagAsMethod(self, name: str) -> None:  # noqa: N802
        self._log.calls[f"flag:{name}"] += 1

    def count(self, name: str) -> None:
        self._log.calls[name] += 1


class Vertex(Dispatch):
    def __init__(self, log: Log, point: Point) -> None:
        super().__init__(log)
        self._point = point

    def GetPoint(self) -> list[float]:  # noqa: N802
        self.count("GetPoint")
        return list(self._point)


class Curve(Dispatch):
    def IsLine(self) -> bool:  # noqa: N802
        self.count("IsLine")
        return True

    def IsCircle(self) -> bool:  # noqa: N802
        self.count("IsCircle")
        return False


class Ole:
    """``_oleobj_``: equal for wrappers of one entity; hashed per wrapper or not."""

    def __init__(self, entity: Any, consistent_hash: bool) -> None:
        self.entity, self._consistent = entity, consistent_hash

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ole) and other.entity is self.entity

    def __hash__(self) -> int:
        return id(self.entity) if self._consistent else id(self)


class Edge(Dispatch):
    """A straight edge from ``a`` to ``b`` (metres)."""

    def __init__(self, log: Log, a: Point, b: Point) -> None:
        super().__init__(log)
        self.a, self.b = a, b
        self.selected = 0
        self.faces: list[Face] = []  # the face listing it first, then the other
        self.refuse_select = 0  # how many Select4 calls to refuse

    def GetTwoAdjacentFaces2(self) -> list[Face]:  # noqa: N802
        self.count("GetTwoAdjacentFaces2")
        return list(self.faces)

    def GetCurve(self) -> Curve:  # noqa: N802
        self.count("GetCurve")
        return Curve(self._log)

    def GetStartVertex(self) -> Vertex:  # noqa: N802
        self.count("GetStartVertex")
        return Vertex(self._log, self.a)

    def GetEndVertex(self) -> Vertex:  # noqa: N802
        self.count("GetEndVertex")
        return Vertex(self._log, self.b)

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        self.count("GetClosestPointOn")
        ab = [q - p for p, q in zip(self.a, self.b, strict=True)]
        ap = [q - p for p, q in zip(self.a, (x, y, z), strict=True)]
        t = sum(u * v for u, v in zip(ab, ap, strict=True)) / sum(u * u for u in ab)
        t = min(1.0, max(0.0, t))
        return [a + t * d for a, d in zip(self.a, ab, strict=True)] + [t]

    def Select4(self, _append: bool, _data: Any) -> bool:  # noqa: N802
        self.count("Select4")
        if self.refuse_select > 0:
            self.refuse_select -= 1
            return False
        self.selected += 1
        return True


class Wrapper(Dispatch):
    """A fresh wrapper of an entity, as SolidWorks may hand one back each time.

    ``identity`` is what its ``_oleobj_`` compares by: the entity, or a new
    object for a SolidWorks that makes a COM object per read.
    """

    def __init__(
        self, log: Log, entity: Any, consistent_hash: bool, identity: Any = None
    ) -> None:
        super().__init__(log)
        self._entity = entity
        self._oleobj_ = Ole(entity if identity is None else identity, consistent_hash)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._entity, name)


class Face(Dispatch):
    """A flat axis-aligned rectangle: ``box`` (metres), outward ``normal``.

    Attributes:
        wrappers: What ``GetEdges`` hands back (the module docstring).
    """

    def __init__(
        self, log: Log, box: tuple[Point, Point], normal: Point, edges: list[Edge]
    ) -> None:
        super().__init__(log)
        self.box, self._normal, self.edges = box, normal, edges
        self.wrappers = "same"
        self.body: Body | None = None

    def GetBody(self) -> Body | None:  # noqa: N802
        self.count("GetBody")
        return self.body

    def GetBox(self) -> list[float]:  # noqa: N802
        self.count("GetBox")
        return [*self.box[0], *self.box[1]]

    def GetEdges(self) -> list[Any]:  # noqa: N802
        self.count("GetEdges")
        if self.wrappers == "same":
            return list(self.edges)
        hashed = self.wrappers != "equal_unhashed"
        return [
            Wrapper(self._log, e, hashed, object() if self.wrappers == "new" else None)
            for e in self.edges
        ]

    @property
    def Normal(self) -> list[float]:  # noqa: N802
        self.count("Normal")
        return list(self._normal)

    def GetArea(self) -> float:  # noqa: N802
        self.count("GetArea")
        size = [h - lo for lo, h in zip(*self.box, strict=True)]
        flat = sorted(size)
        return flat[1] * flat[2]

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        self.count("GetClosestPointOn")
        lo, hi = self.box
        return [min(max(c, a), b) for c, a, b in zip((x, y, z), lo, hi, strict=True)]


class Body(Dispatch):
    def __init__(
        self, log: Log, name: str, faces: list[Face], edges: list[Edge]
    ) -> None:
        super().__init__(log)
        self._name, self.faces, self.edges = name, faces, edges

    @property
    def Name(self) -> str:  # noqa: N802
        self.count("Name")
        return self._name

    def GetBodyBox(self) -> list[float]:  # noqa: N802
        self.count("GetBodyBox")
        lows = [min(f.box[0][i] for f in self.faces) for i in range(3)]
        highs = [max(f.box[1][i] for f in self.faces) for i in range(3)]
        return [*lows, *highs]

    def GetFaces(self) -> list[Face]:  # noqa: N802
        self.count("GetFaces")
        return list(self.faces)

    def GetEdges(self) -> list[Edge]:  # noqa: N802
        self.count("GetEdges")
        return list(self.edges)

    def GetEdgeCount(self) -> int:  # noqa: N802
        self.count("GetEdgeCount")
        return len(self.edges)


class SelectionManager(Dispatch):
    def __init__(self, log: Log) -> None:
        super().__init__(log)
        self.selected = 0

    def CreateSelectData(self) -> str:  # noqa: N802
        self.count("CreateSelectData")
        return "data"

    def GetSelectedObjectCount2(self, _mark: int) -> int:  # noqa: N802
        self.count("GetSelectedObjectCount2")
        return self.selected


class Feature(Dispatch):
    def __init__(self, log: Log, faces: list[Face]) -> None:
        super().__init__(log)
        self.faces = faces

    def GetFaces(self) -> list[Face]:  # noqa: N802
        self.count("GetFaces")
        return list(self.faces)


class Extension(Dispatch):
    """Persist references ``b"edge:<n>"``, resolved while the edge is there."""

    def __init__(self, log: Log, edges: list[Edge]) -> None:
        super().__init__(log)
        self.edges = edges
        self.gone: set[int] = set()

    def GetPersistReference3(self, entity: Any) -> bytes:  # noqa: N802
        self.count("GetPersistReference3")
        target = getattr(entity, "_entity", entity)
        return (
            f"edge:{next(i for i, e in enumerate(self.edges) if e is target)}".encode()
        )

    def GetObjectByPersistReference3(self, ref: Any, _code: Any) -> Any:  # noqa: N802
        self.count("GetObjectByPersistReference3")
        number = int(bytes(getattr(ref, "value", ref)).decode().split(":")[1])
        return None if number in self.gone else self.edges[number]


class Model(Dispatch):
    def __init__(self, log: Log, bodies: list[Body]) -> None:
        super().__init__(log)
        self.bodies = bodies
        self.stamp: int | None = 7
        self.SelectionManager = SelectionManager(log)
        self.features: dict[str, Feature] = {}
        self.Extension = Extension(log, [e for b in bodies for e in b.edges])
        for body in bodies:
            for face in body.faces:
                face.body = body
                for edge in face.edges:
                    edge.faces.append(face)

    def GetBodies2(self, _kind: int, _visible: bool) -> list[Body]:  # noqa: N802
        self.count("GetBodies2")
        return list(self.bodies)

    def GetUpdateStamp(self) -> int:  # noqa: N802
        self.count("GetUpdateStamp")
        if self.stamp is None:
            raise AttributeError("GetUpdateStamp")
        return self.stamp

    def GetType(self) -> int:  # noqa: N802
        return 1

    def ClearSelection2(self, _all: bool) -> None:  # noqa: N802
        self.count("ClearSelection2")

    def FeatureByName(self, name: str) -> Feature | None:  # noqa: N802
        self.count("FeatureByName")
        return self.features.get(name)


def _p(x: float, y: float, z: float) -> Point:
    return (x * MM, y * MM, z * MM)


def _box(lo: Point, hi: Point) -> tuple[Point, Point]:
    return (
        tuple(min(a, b) for a, b in zip(lo, hi, strict=True)),  # type: ignore[return-value]
        tuple(max(a, b) for a, b in zip(lo, hi, strict=True)),
    )


def _block(
    log: Log, lo: tuple[float, float, float], hi: tuple[float, float, float]
) -> tuple[list[Face], list[Edge], list[Edge], list[Edge]]:
    """A box's six faces and its 12 edges (top four, bottom four, verticals)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    top = [
        Edge(log, _p(*corners[i], z1), _p(*corners[(i + 1) % 4], z1)) for i in range(4)
    ]
    bottom = [
        Edge(log, _p(*corners[i], z0), _p(*corners[(i + 1) % 4], z0)) for i in range(4)
    ]
    upright = [Edge(log, _p(*c, z0), _p(*c, z1)) for c in corners]
    sides = []
    normals = [(0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0)]
    for i in range(4):
        a, b = corners[i], corners[(i + 1) % 4]
        box = _box(_p(*a, z0), _p(*b, z1))
        sides.append(
            Face(
                log,
                box,
                normals[i],
                [top[i], bottom[i], upright[i], upright[(i + 1) % 4]],
            )
        )
    faces = [
        Face(log, _box(_p(x0, y0, z1), _p(x1, y1, z1)), (0.0, 0.0, 1.0), list(top)),
        Face(log, _box(_p(x0, y0, z0), _p(x1, y1, z0)), (0.0, 0.0, -1.0), list(bottom)),
        *sides,
    ]
    return faces, top, bottom, upright


def pocket_corner(i: int, j: int) -> tuple[float, float]:
    """The low corner of pocket (i, j), mm."""
    return MARGIN + PITCH * i, MARGIN + PITCH * j


def pocket_box_mm(i: int, j: int, slack: float = 1.0) -> list[list[float]]:
    """An ``inside_box`` around pocket (i, j) and nothing else, mm."""
    x0, y0 = pocket_corner(i, j)
    return [
        [x0 - slack, y0 - slack, 10.0 - DEPTH - slack],
        [x0 + POCKET + slack, y0 + POCKET + slack, 10.0 + slack],
    ]


def _pocket(log: Log, i: int, j: int) -> tuple[list[Face], list[Edge], list[Edge]]:
    """A pocket's five faces, its 12 edges, and its four rims (on the top face)."""
    x0, y0 = pocket_corner(i, j)
    x1, y1 = x0 + POCKET, y0 + POCKET
    z0, z1 = 10.0 - DEPTH, 10.0
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    rims = [
        Edge(log, _p(*corners[k], z1), _p(*corners[(k + 1) % 4], z1)) for k in range(4)
    ]
    floor = [
        Edge(log, _p(*corners[k], z0), _p(*corners[(k + 1) % 4], z0)) for k in range(4)
    ]
    upright = [Edge(log, _p(*c, z0), _p(*c, z1)) for c in corners]
    normals = [(0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0)]
    walls = [
        Face(
            log,
            _box(_p(*corners[k], z0), _p(*corners[(k + 1) % 4], z1)),
            normals[k],
            [rims[k], floor[k], upright[k], upright[(k + 1) % 4]],
        )
        for k in range(4)
    ]
    bottom = Face(
        log, _box(_p(x0, y0, z0), _p(x1, y1, z0)), (0.0, 0.0, 1.0), list(floor)
    )
    return [*walls, bottom], [*rims, *floor, *upright], rims


def pocket_plate(log: Log | None = None) -> tuple[Model, Log]:
    """The plate with its 160 pockets and the far block: 1,944 edges, 812 faces."""
    log = log or Log()
    faces, top, bottom, upright = _block(log, (0.0, 0.0, 0.0), (200.0, 160.0, 10.0))
    edges = [*top, *bottom, *upright]
    for i in range(GRID[0]):
        for j in range(GRID[1]):
            pocket_faces, pocket_edges, rims = _pocket(log, i, j)
            faces[0].edges.extend(rims)  # the top face carries every rim
            faces.extend(pocket_faces)
            edges.extend(pocket_edges)
    plate = Body(log, "Plate", faces, edges)
    block_faces, *block_edges = _block(log, (300.0, 0.0, 0.0), (310.0, 10.0, 10.0))
    block = Body(log, "Block", block_faces, [e for part in block_edges for e in part])
    model = Model(log, [plate, block])
    # The plate's extrude made its six outer faces; each pocket is a cut.
    model.features["Boss-Extrude1"] = Feature(log, faces[:6])
    for n in range(GRID[0] * GRID[1]):
        model.features[f"Cut-Extrude{n + 1}"] = Feature(
            log, faces[6 + 5 * n : 11 + 5 * n]
        )
    model.features["Boss-Extrude2"] = Feature(log, block_faces)
    return model, log


def pocket_faces(model: Model, i: int, j: int) -> list[Face]:
    """Pocket (i, j)'s four walls and floor."""
    n = i * GRID[1] + j
    return model.bodies[0].faces[6 + 5 * n : 11 + 5 * n]


def point_on(face: Face) -> list[float]:
    """The middle of a face's box, mm."""
    return [(a + b) / 2 / MM for a, b in zip(*face.box, strict=True)]
