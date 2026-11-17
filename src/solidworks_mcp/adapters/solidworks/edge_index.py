"""The active part's bodies, faces and edges, read once for ``select_edges``.

``select_edges`` with no source read every edge of the part: about a dozen
COM calls an edge (its curve, two vertices and their points, and a flag for
each fresh dispatch). On the Dipper's 2-body master, about 2,275 edges, that
took about 260 s a call, however tight its ``inside_box``. :class:`PartIndex`
reads the part lazily and keeps what it has read:

* each body's box (``IBody2.GetBodyBox``) and each face's (``IFace2.GetBox``),
  so a rule's ``inside_box`` leaves out the bodies and faces that can't hold
  an edge inside it before any edge is read (:meth:`PartIndex.edges_in_reach`);
* each face's edges (``IFace2.GetEdges``), each edge once. SolidWorks hands
  back a new wrapper for an edge each time, and ``same_com_object`` tells
  them apart without a call to SolidWorks; that it can is checked once per
  index (:meth:`PartIndex.edge_identity`), and without it nothing is merged
  before it is described;
* each edge's description (``face_selection.describe_edge``), read once.

An edge lies on both faces it bounds, so it can lie inside a box only if
both faces' boxes reach the box. With the identity checked, an edge listed by
only one face near the box is left out unread. On a merged body one face
carries hundreds of edges (a plate with letters cut in it) and has a box as
big as the part: this leaves out every one of its edges whose other face is
far from the box. A face whose box lies wholly inside the box keeps all its
edges, so an edge bounding the same face on both sides (a seam) is not lost
on a small face.

The index is kept on the adapter (``_edge_index``) until the document's
update stamp (``IModelDoc2.GetUpdateStamp``) changes, i.e. until the next
rebuild, so the next rule on the same part reads nothing again. Without a
stamp nothing is kept. None of this has run on SolidWorks yet:
``tests/scripts/probe_dipper_edges.py`` (step ``index``) checks it against
the old path on a part with a few thousand edges.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import sw_type_info
from . import limits
from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .face_selection import describe_edge

#: ``((xmin, ymin, zmin), (xmax, ymax, zmax))`` in metres.
Box = tuple[tuple[float, float, float], tuple[float, float, float]]
Point = tuple[float, float, float]

#: Slack on every box test, metres. A face box SolidWorks rounds, or an edge
#: a little off its face, is never ruled out; it only costs a few more reads.
BOX_SLACK_M = 1e-4

#: ``inside_box``'s own slack (``edge_rules``' 1e-3 mm), metres.
INSIDE_SLACK_M = 1e-6

#: A point this close to a face is on it (as ``face_selection``), metres.
ON_FACE_M = 1e-5


# ---- boxes (pure) ------------------------------------------------------------


def box_from_corners(raw: Any) -> Box | None:
    """The box from two opposite corners ``[x1, y1, z1, x2, y2, z2]`` (metres)."""
    values = as_list(raw)
    if len(values) < 6:
        return None
    try:
        c = [float(v) for v in values[:6]]
    except (TypeError, ValueError):
        return None
    low = (min(c[0], c[3]), min(c[1], c[4]), min(c[2], c[5]))
    high = (max(c[0], c[3]), max(c[1], c[4]), max(c[2], c[5]))
    return low, high


def box_from_mm(box_mm: list[list[float]]) -> Box:
    """``inside_box`` (two corners in mm) in metres."""
    low, high = ([float(c) / 1000.0 for c in corner] for corner in box_mm)
    return (low[0], low[1], low[2]), (high[0], high[1], high[2])


def reaches(box: Box | None, target: Box, slack: float = BOX_SLACK_M) -> bool:
    """Whether ``box`` comes within ``slack`` of ``target``; an unknown box does."""
    if box is None:
        return True
    return all(
        box[0][i] <= target[1][i] + slack and box[1][i] >= target[0][i] - slack
        for i in range(3)
    )


def holds(target: Box, box: Box | None, slack: float = INSIDE_SLACK_M) -> bool:
    """Whether ``box`` lies wholly inside ``target``; an unknown box doesn't."""
    if box is None:
        return False
    return all(
        box[0][i] >= target[0][i] - slack and box[1][i] <= target[1][i] + slack
        for i in range(3)
    )


def distance_to(box: Box | None, point: Point) -> float:
    """Distance from ``point`` to ``box``: 0 inside it, or for an unknown box."""
    if box is None:
        return 0.0
    gaps = (max(box[0][i] - point[i], 0.0, point[i] - box[1][i]) for i in range(3))
    return math.sqrt(sum(g * g for g in gaps))


# ---- one handle per COM object -------------------------------------------------


def identity_hash(obj: Any) -> Any:
    """A hash of the object behind a wrapper (its ``_oleobj_``), or None.

    None when it can't be hashed; such wrappers are compared one by one.
    """
    target = getattr(obj, "_oleobj_", obj)
    try:
        return hash(target)
    except TypeError:
        return None


class Canon:
    """The first wrapper seen for each COM object, by ``same_com_object``.

    Wrappers are bucketed by :func:`identity_hash`, which must agree with
    ``same_com_object`` for two wrappers of one object; when it doesn't
    (:attr:`hashed` False), they are all compared one by one.
    """

    def __init__(self) -> None:
        self._buckets: dict[Any, list[Any]] = {}
        self.hashed = True

    def get(self, obj: Any) -> Any:
        """``obj``'s first wrapper: an earlier one for the same object, or ``obj``."""
        key = identity_hash(obj) if self.hashed else None
        bucket = self._buckets.setdefault(key, [])
        for seen in bucket:
            if sw_type_info.same_com_object(seen, obj):
                return seen
        bucket.append(obj)
        return obj


# ---- the index -----------------------------------------------------------------


@dataclass(eq=False)
class IndexedFace:
    """A face, its box and its edges, each read when first needed.

    Attributes:
        handle: The ``IFace2`` dispatch.
        box: Its ``GetBox``, once :attr:`box_read`.
        edges: Its ``GetEdges``, one handle per edge (:class:`Canon`) when the
            identity holds.
    """

    handle: Any
    box: Box | None = None
    box_read: bool = False
    edges: list[Any] | None = None


@dataclass(eq=False)
class IndexedBody:
    """A solid body, its box, name, faces and edges, each read when first needed."""

    handle: Any
    box: Box | None = None
    box_read: bool = False
    name: str | None = None
    name_read: bool = False
    faces: list[IndexedFace] | None = None
    edges: list[Any] | None = None


@dataclass
class Reach:
    """The edges that may lie inside a box (:meth:`PartIndex.edges_in_reach`).

    Attributes:
        edges: Each once (with the identity checked), in body, face and
            edge order.
        faces_left_out: Faces whose box doesn't reach the box: none of their
            edges was read.
        edges_left_out: Edges read but listed by only one face near the box.
        bodies_left_out: Bodies whose box doesn't reach the box.
    """

    edges: list[Any] = field(default_factory=list)
    faces_left_out: int = 0
    edges_left_out: int = 0
    bodies_left_out: int = 0


class PartIndex:
    """The active part's bodies, faces and edges, read lazily and kept.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        model: The part (``IModelDoc2``) this index reads.
        stamp: Its ``GetUpdateStamp`` when the index was made.

    Attributes:
        counts: Reads made through this index: ``edges_described``,
            ``face_boxes``, ``body_boxes``, ``closest_points``.
        reused: Whether this index was kept from an earlier call.
        edge_identity: Whether two reads of a face's edges gave the same COM
            objects (None until checked).
    """

    def __init__(self, adapter: Any, model: Any, stamp: int | None) -> None:
        self._adapter = adapter
        self.model = model
        self.stamp = stamp
        self.counts: Counter[str] = Counter()
        self.reused = False
        self.edge_identity: bool | None = None
        self._bodies: list[IndexedBody] | None = None
        self._edges = Canon()
        # id(edge) -> its description; the edge is held so its id stays its.
        self._info: dict[int, tuple[Any, dict[str, Any]]] = {}
        self._flagged: dict[int, tuple[Any, set[str]]] = {}

    # -- reads ---------------------------------------------------------------

    def flag_once(self, obj: Any, name: str) -> None:
        """Flag ``name`` on ``obj`` unless this index already has (one round trip)."""
        held, names = self._flagged.setdefault(id(obj), (obj, set()))
        if name not in names:
            names.add(name)
            _flag(self._adapter, held, name)

    def bodies(self) -> list[IndexedBody]:
        """The part's solid bodies (``GetBodies2(swSolidBody, False)``)."""
        if self._bodies is None:
            model = self.model
            _flag(self._adapter, model, "GetBodies2")
            raw = as_list(_attempt(self._adapter, lambda: model.GetBodies2(0, False)))
            self._bodies = [IndexedBody(body) for body in raw]
        return self._bodies

    def body_box(self, body: IndexedBody) -> Box | None:
        """``IBody2.GetBodyBox``, read once."""
        if not body.box_read:
            body.box_read = True
            self.flag_once(body.handle, "GetBodyBox")
            body.box = box_from_corners(
                _attempt(self._adapter, lambda: body.handle.GetBodyBox())
            )
            self.counts["body_boxes"] += 1
        return body.box

    def body_name(self, body: IndexedBody) -> str | None:
        """``IBody2.Name`` (a property), read once."""
        if not body.name_read:
            body.name_read = True
            name = _attempt(self._adapter, lambda: body.handle.Name)
            body.name = str(name) if name else None
        return body.name

    def body_named(self, name: str) -> IndexedBody:
        """The solid body called ``name``.

        Raises:
            LookupError: No body has that name; the message lists theirs.
        """
        for body in self.bodies():
            if self.body_name(body) == name:
                return body
        names = [n for n in (self.body_name(b) for b in self.bodies()) if n]
        raise LookupError(
            f"No solid body named {name!r}; the part's bodies are "
            f"{', '.join(names) or 'none'}. A feature renames the bodies it "
            "changes, so list_bodies names them as they are now."
        )

    def faces(self, body: IndexedBody) -> list[IndexedFace]:
        """A body's faces (``IBody2.GetFaces``), read once."""
        if body.faces is None:
            self.flag_once(body.handle, "GetFaces")
            raw = as_list(_attempt(self._adapter, lambda: body.handle.GetFaces()))
            body.faces = [IndexedFace(face) for face in raw]
        return body.faces

    def face_box(self, face: IndexedFace) -> Box | None:
        """``IFace2.GetBox``, read once."""
        if not face.box_read:
            limits.check("reading face boxes")
            face.box_read = True
            self.flag_once(face.handle, "GetBox")
            face.box = box_from_corners(
                _attempt(self._adapter, lambda: face.handle.GetBox())
            )
            self.counts["face_boxes"] += 1
        return face.box

    def _raw_edges(self, face: Any) -> list[Any]:
        self.flag_once(face, "GetEdges")
        return as_list(_attempt(self._adapter, lambda: face.GetEdges()))

    def read_edges(self, faces: list[IndexedFace]) -> None:
        """Read the edges of each face not read yet, one handle per edge.

        The first time any face lists edges, the identity is checked on the
        shortest list (:meth:`_check_identity`).
        """
        pending = [face for face in faces if face.edges is None]
        lists = [(face, self._raw_edges(face.handle)) for face in pending]
        if self.edge_identity is None:
            self._check_identity(lists)
        for face, raw in lists:
            face.edges = (
                [self._edges.get(e) for e in raw] if self.edge_identity else raw
            )

    def _check_identity(self, lists: list[tuple[IndexedFace, list[Any]]]) -> None:
        """Read the shortest list again: are its edges the same COM objects?

        True also needs :func:`identity_hash` to give both reads one hash;
        when it doesn't, the handles are compared one by one.
        """
        sized = [(face, raw) for face, raw in lists if raw]
        if not sized:
            return
        face, first = min(sized, key=lambda pair: len(pair[1]))
        again = self._raw_edges(face.handle)
        same = len(again) == len(first) and all(
            sw_type_info.same_com_object(a, b)
            for a, b in zip(first, again, strict=True)
        )
        self.edge_identity = same
        if same and any(
            identity_hash(a) is None or identity_hash(a) != identity_hash(b)
            for a, b in zip(first, again, strict=True)
        ):
            self._edges.hashed = False

    def face_edges(self, face: IndexedFace) -> list[Any]:
        """A face's edges (:meth:`read_edges`)."""
        self.read_edges([face])
        return face.edges or []

    def body_edges(self, body: IndexedBody) -> list[Any]:
        """A body's edges (``IBody2.GetEdges``), read once, each once."""
        if body.edges is None:
            self.flag_once(body.handle, "GetEdges")
            raw = as_list(_attempt(self._adapter, lambda: body.handle.GetEdges()))
            body.edges = (
                [self._edges.get(e) for e in raw] if self.edge_identity else raw
            )
        return body.edges

    def canonical(self, edge: Any) -> Any:
        """The handle this index keeps for ``edge`` (itself without the identity)."""
        return self._edges.get(edge) if self.edge_identity else edge

    def describe(self, edge: Any) -> dict[str, Any]:
        """``describe_edge``, read once per handle; the same dict each time."""
        key = id(edge)
        if key not in self._info:
            limits.check("reading edges")
            self._info[key] = (edge, describe_edge(self._adapter, edge))
            self.counts["edges_described"] += 1
        return self._info[key][1]

    def distance(self, entity: Any, point: Point) -> float | None:
        """``GetClosestPointOn`` distance from ``point`` (metres), or None."""
        limits.check("measuring faces")
        self.flag_once(entity, "GetClosestPointOn")
        closest = _attempt(
            self._adapter, lambda: list(entity.GetClosestPointOn(*point))
        )
        self.counts["closest_points"] += 1
        if not closest or len(closest) < 3:
            return None
        return math.dist([float(c) for c in closest[:3]], point)

    # -- queries -------------------------------------------------------------

    def edges_in_reach(
        self, box: Box, bodies: list[IndexedBody] | None = None
    ) -> Reach:
        """The edges of ``bodies`` (all) that may lie inside ``box`` (metres).

        Bodies and faces whose boxes don't reach it are left out unread. With
        the identity checked, an edge must be listed by two faces near the
        box, or by one whose box lies wholly inside it; without it, every
        edge of the faces near the box is kept (the same edge may come twice).
        A body whose faces don't read gives all its edges (``GetEdges``).
        """
        reach = Reach()
        near: list[IndexedFace] = []
        whole: set[int] = set()
        faceless: list[Any] = []
        for body in self.bodies() if bodies is None else bodies:
            if not reaches(self.body_box(body), box):
                reach.bodies_left_out += 1
                continue
            if not self.faces(body):
                faceless.extend(self.body_edges(body))
                continue
            for face in self.faces(body):
                face_box = self.face_box(face)
                if not reaches(face_box, box):
                    reach.faces_left_out += 1
                    continue
                near.append(face)
                if holds(box, face_box):
                    whole.add(id(face))
        self.read_edges(near)
        if not self.edge_identity:
            reach.edges = [edge for face in near for edge in face.edges or []]
            reach.edges += faceless
            return reach
        listed: Counter[int] = Counter()
        order: list[Any] = []
        for face in near:
            for edge in face.edges or []:
                if id(edge) not in listed:
                    order.append(edge)
                listed[id(edge)] += 2 if id(face) in whole else 1
        reach.edges = [edge for edge in order if listed[id(edge)] >= 2]
        reach.edges_left_out = len(order) - len(reach.edges)
        reach.edges += faceless
        return reach

    def face_at(
        self, point_mm: list[float], bodies: list[IndexedBody] | None = None
    ) -> IndexedFace:
        """The face through ``point_mm``, measuring only faces whose box is near it.

        The closest within 0.01 mm; on a tie the first in body and face order,
        as ``FaceEdgeSelector.face_at_point`` picks.

        Raises:
            LookupError: No face passes within 0.01 mm of the point.
        """
        point = (
            float(point_mm[0]) / 1000.0,
            float(point_mm[1]) / 1000.0,
            float(point_mm[2]) / 1000.0,
        )
        reach = ON_FACE_M + BOX_SLACK_M
        best: tuple[float, IndexedFace] | None = None
        for body in self.bodies() if bodies is None else bodies:
            if distance_to(self.body_box(body), point) > reach:
                continue
            for face in self.faces(body):
                if distance_to(self.face_box(face), point) > reach:
                    continue
                measured = self.distance(face.handle, point)
                if measured is not None and (best is None or measured < best[0]):
                    best = (measured, face)
        if best is None or best[0] > ON_FACE_M:
            raise LookupError(f"No face found at point {list(point_mm)} mm.")
        return best[1]


def update_stamp(adapter: Any, model: Any) -> int | None:
    """``IModelDoc2.GetUpdateStamp``: changes whenever the model is rebuilt."""
    _flag(adapter, model, "GetUpdateStamp")
    value = _attempt(adapter, lambda: model.GetUpdateStamp())
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def index_for(adapter: Any) -> PartIndex:
    """The active part's index: the one kept, while its update stamp holds.

    A new index is kept on the adapter (``_edge_index``) when the stamp
    reads; without one nothing is kept, so nothing is read from a part that
    may have changed. A kept index's ``counts`` start again from 0, so they
    count one call's reads.
    """
    model = adapter.currentModel
    stamp = update_stamp(adapter, model)
    held = getattr(adapter, "_edge_index", None)
    if (
        isinstance(held, PartIndex)
        and stamp is not None
        and held.stamp == stamp
        and sw_type_info.same_com_object(held.model, model)
    ):
        held.reused = True
        held.counts.clear()
        return held
    index = PartIndex(adapter, model, stamp)
    adapter._edge_index = index if stamp is not None else None
    return index


def forget(adapter: Any) -> None:
    """Drop the kept index: the next call reads the part again."""
    adapter._edge_index = None
