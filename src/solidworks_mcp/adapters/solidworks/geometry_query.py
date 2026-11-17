"""What lies near a point of the active part, for ``query_geometry``.

The Dipper build (SOLIDWORKS 2026 SP4.1, a cradle of about 1,300 edges and
120 features) found a hang hole and a latch's faces with ``select_edges``
and a tight ``inside_box``: its output (circle centres and radii, segment
ends) was what it needed, but each call took 110-150 s before
``dipper-edges``, and it changed the selection. This module answers "what
is within r of this point" and leaves the selection as it was:

* **The part's kept index, when it holds the boxes.** ``select_edges`` and
  this module share one :class:`edge_index.PartIndex`, kept until the next
  rebuild. When it holds the face boxes of every body near the point, the
  faces whose box comes within the radius are measured
  (``GetClosestPointOn``), and of their edges those listed by two such
  faces: an edge lies on both its faces, so it is no nearer than either
  box. Nothing within the radius is missed (``complete``), and a query on an
  unchanged part reads its update stamp and the few entities it measures.
* **Else SolidWorks' own picks around the point.** ``SelectByID2`` at the
  point and ``IModelDocExtension.SelectByRay`` along +-X, +-Y and +-Z with
  the radius as the ray's, for faces (and edges), each pick read at once
  (``GetSelectedObject6``) and the selection cleared after it. They run
  only from an empty selection, which is what they leave: a pick could not
  be told from the user's own. The picks, and the edges of picked faces
  with few edges, are measured exactly. That is a few dozen calls however
  big the part, but a face no pick reaches is missed, so the reply says
  ``complete: false``. Picks that find nothing within the radius fall back
  to the index.
* **With ``read_all``, or after picks found nothing, the index from
  scratch**: body boxes, then face boxes (two calls a face, kept), as
  above.

The descriptions (a face's surface and its parameters, an edge's curve,
ends and length, persistent ids) are read only for what the reply lists,
and kept with the index.

Measured on SOLIDWORKS 2026 SP4.1 (``tests/scripts/probe_query_geometry.py``):

* ``CylinderParams`` is the axis point, the axis and the radius (metres);
  ``ConeParams`` an origin on the axis, the axis, the radius at the origin and the
  half angle in radians; ``SphereParams`` the centre and radius;
  ``TorusParams`` the centre, the axis and the major and minor radii.
  A surface of another kind reads uninitialised numbers there, so only the
  ``Is*`` check that says True is used. ``GetCurveParams2`` gives the start,
  the end and the start and end parameters (0 to 2 pi on a circle);
  ``GetLength3`` the length in metres.
* On a 172 x 108 mm plate of 2,904 edges, at a pocket's middle with a
  2.6 mm radius, the picks took 2.3 s and found 5 of the 6 faces (not the
  top face, reached only at a rim) and all 8 edges; the first ``read_all``
  27 s (1,206 face boxes) and found all; a query at another pocket then
  took 1.7 s, and at the same point 0.4 s. ``select_edges`` on the same
  pocket took 27 s with a fresh index and 0.5 s with it kept.
"""

from __future__ import annotations

import math
import weakref
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .. import sw_type_info
from . import limits
from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .edge_index import (
    BOX_SLACK_M,
    Box,
    IndexedBody,
    IndexedFace,
    PartIndex,
    Point,
    box_from_corners,
    distance_to,
    index_for,
)
from .edge_rules import edge_key
from .persist_ids import persist_ids

#: What ``kinds`` may ask for.
KINDS = ("faces", "edges", "both")

#: The most faces (and the most edges) one reply lists.
MAX_LIMIT = 50

#: The largest radius a query takes, mm.
MAX_RADIUS_MM = 1000.0

#: A picked face with at most this many edges has them measured too: a
#: hole's wall gives its two rims, a pocket's wall its four edges. A merged
#: plate's top face, with hundreds, is left to the index.
SMALL_FACE_EDGES = 16

#: ``swSelectType_e``: swSelEDGES and swSelFACES.
SEL_EDGES, SEL_FACES = 1, 2

#: The directions ``SelectByRay`` is fired along from the point.
RAY_DIRECTIONS: tuple[Point, ...] = (
    (1.0, 0.0, 0.0),
    (-1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, -1.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, 0.0, -1.0),
)


def query_problem(point_mm: Any, radius_mm: Any, kinds: Any, limit: Any) -> str | None:
    """What is wrong with a query, or None (shared by the live and mock adapters)."""
    numbers = isinstance(point_mm, list | tuple) and all(
        isinstance(c, int | float) and not isinstance(c, bool) for c in point_mm
    )
    if not numbers or len(point_mm) != 3:
        return "point_mm must be [x, y, z] in millimetres."
    if (
        isinstance(radius_mm, bool)
        or not isinstance(radius_mm, int | float)
        or not 0 < radius_mm <= MAX_RADIUS_MM
    ):
        return f"radius_mm must be more than 0 and at most {MAX_RADIUS_MM:g} mm."
    if kinds not in KINDS:
        return f"kinds must be one of {', '.join(KINDS)} (got {kinds!r})."
    if isinstance(limit, bool) or not isinstance(limit, int):
        return f"limit must be a whole number from 1 to {MAX_LIMIT}."
    if not 1 <= limit <= MAX_LIMIT:
        return f"limit must be a whole number from 1 to {MAX_LIMIT}."
    return None


# ---- pure geometry -----------------------------------------------------------------


def within(box: Box | None, point: Point, radius: float) -> bool:
    """Whether ``box`` comes within ``radius`` of ``point``; an unknown box does."""
    return distance_to(box, point) <= radius + BOX_SLACK_M


def farthest(box: Box | None, point: Point) -> float:
    """The distance from ``point`` to the farthest corner of ``box`` (inf when unknown)."""
    if box is None:
        return math.inf
    return math.sqrt(
        sum(
            max(abs(point[i] - box[0][i]), abs(box[1][i] - point[i])) ** 2
            for i in range(3)
        )
    )


def unit(values: Iterable[Any]) -> list[float] | None:
    """``values`` as a unit vector (6 decimals), or None for a zero or short one."""
    vector = [float(v) for v in values]
    length = math.sqrt(sum(c * c for c in vector))
    if len(vector) != 3 or length < 1e-12:
        return None
    return [round(c / length, 6) + 0.0 for c in vector]


def nearest_on_axis(
    origin: list[float], axis: list[float], point: Point
) -> list[float]:
    """The point of the line ``origin + t * axis`` (unit axis) nearest ``point``."""
    t = sum((p - o) * a for p, o, a in zip(point, origin, axis, strict=True))
    return [o + t * a for o, a in zip(origin, axis, strict=True)]


def mm(values: Iterable[Any]) -> list[float]:
    """Metres to millimetres, 6 decimals."""
    return [round(float(v) * 1000.0, 6) + 0.0 for v in values]


# ---- reading ---------------------------------------------------------------------


@dataclass(eq=False)
class Hit:
    """A face or edge within the radius.

    Attributes:
        entity: The ``IFace2`` or ``IEdge`` dispatch.
        distance: From the point, metres.
        closest: Its point nearest the point, metres.
        body: Its body, when known (the index path knows it).
        box: A face's box, when the index read it.
    """

    entity: Any
    distance: float
    closest: Point
    body: IndexedBody | None = None
    box: Box | None = None


@dataclass
class Found:
    """What a search found, and how many picks it made."""

    faces: list[Hit] = field(default_factory=list)
    edges: list[Hit] = field(default_factory=list)
    picks: int = 0


def measure(adapter: Any, index: PartIndex, entity: Any, point: Point) -> Hit | None:
    """``GetClosestPointOn`` from ``point``: a :class:`Hit`, or None when it doesn't read."""
    limits.check("measuring near the point")
    index.flag_once(entity, "GetClosestPointOn")
    closest = _attempt(adapter, lambda: list(entity.GetClosestPointOn(*point)))
    index.counts["closest_points"] += 1
    if not closest or len(closest) < 3:
        return None
    at = (float(closest[0]), float(closest[1]), float(closest[2]))
    return Hit(entity, math.dist(at, point), at)


def bodies_near(index: PartIndex, point: Point, radius: float) -> list[IndexedBody]:
    """The solid bodies whose box (``GetBodyBox``, kept) comes within the radius."""
    return [b for b in index.bodies() if within(index.body_box(b), point, radius)]


def boxes_kept(bodies: list[IndexedBody]) -> bool:
    """Whether the index already holds every face box of ``bodies``."""
    return all(
        body.faces is not None and all(face.box_read for face in body.faces)
        for body in bodies
    )


def index_search(
    adapter: Any,
    index: PartIndex,
    bodies: list[IndexedBody],
    point: Point,
    radius: float,
    kinds: str,
) -> Found:
    """Every face and edge of ``bodies`` within the radius, through the index's boxes.

    A face is measured when its box comes within the radius. An edge is
    measured when two such faces list it (or one whose box lies wholly within
    the radius); without one COM object per edge (the index's identity),
    every edge of those faces is. A body whose faces don't read has all its
    edges measured.
    """
    found = Found()
    near: list[tuple[IndexedFace, IndexedBody]] = []
    inside: set[int] = set()
    loose: list[tuple[Any, IndexedBody]] = []
    for body in bodies:
        faces = index.faces(body)
        if not faces:
            loose += [(edge, body) for edge in index.body_edges(body)]
            continue
        for face in faces:
            box = index.face_box(face)
            if not within(box, point, radius):
                continue
            near.append((face, body))
            if farthest(box, point) <= radius:
                inside.add(id(face))
    if kinds != "edges":
        for face, body in near:
            hit = measure(adapter, index, face.handle, point)
            if hit is not None and hit.distance <= radius:
                hit.body, hit.box = body, face.box
                found.faces.append(hit)
    if kinds != "faces":
        for edge, body in _edge_candidates(index, near, inside) + loose:
            hit = measure(adapter, index, edge, point)
            if hit is not None and hit.distance <= radius:
                hit.body = body
                found.edges.append(hit)
    return found


def _edge_candidates(
    index: PartIndex,
    near: list[tuple[IndexedFace, IndexedBody]],
    inside: set[int],
) -> list[tuple[Any, IndexedBody]]:
    """The edges of the near faces that may lie within the radius, with a body each."""
    index.read_edges([face for face, _body in near])
    if not index.edge_identity:
        return [(edge, body) for face, body in near for edge in face.edges or []]
    listed: Counter[int] = Counter()
    order: list[tuple[Any, IndexedBody]] = []
    for face, body in near:
        for edge in face.edges or []:
            if id(edge) not in listed:
                order.append((edge, body))
            listed[id(edge)] += 2 if id(face) in inside else 1
    return [(edge, body) for edge, body in order if listed[id(edge)] >= 2]


def pick_search(
    adapter: Any, index: PartIndex, point: Point, radius: float, kinds: str
) -> Found | None:
    """What SolidWorks' picks around the point reach, measured; None when it can't pick.

    Faces are picked for every query (their edges are where the edges come
    from), edges too unless only faces are asked for. Each pick that
    selects something is read at index 1 and cleared at once: on SOLIDWORKS
    2026 SP4.1 ``SelectByRay`` with ``Append`` false added to the selection
    (11 entities after 12 picks), so index 1 held the first pick every time
    and a query found only the pocket floor of six faces.

    Returns:
        Found | None: The hits within the radius, and how many picks were
        made; None when the selection isn't empty (or can't be read), so
        nothing may be picked.
    """
    from .features import _null_callout

    model = adapter.currentModel
    manager = _attempt(adapter, lambda: model.SelectionManager)
    if manager is None:
        return None
    _flag(adapter, manager, "GetSelectedObjectCount2", "GetSelectedObject6")
    count = _attempt(adapter, lambda: manager.GetSelectedObjectCount2(-1))
    if isinstance(count, bool) or count != 0:
        return None
    extension = _attempt(adapter, lambda: model.Extension)
    if extension is None:
        return None
    types = [("FACE", SEL_FACES)] + ([("EDGE", SEL_EDGES)] if kinds != "faces" else [])
    x, y, z = point
    found = Found()
    picked: list[tuple[str, Any]] = []
    for name, sel_type in types:
        tries = [
            lambda n=name: extension.SelectByID2(
                "", n, x, y, z, False, 0, _null_callout(), 0
            )
        ] + [
            lambda d=d, s=sel_type: extension.SelectByRay(
                x, y, z, d[0], d[1], d[2], radius, s, False, 0, 0
            )
            for d in RAY_DIRECTIONS
        ]
        for pick in tries:
            limits.check("picking around the point")
            found.picks += 1
            if not _attempt(adapter, pick, False):
                continue
            entity = _attempt(adapter, lambda: manager.GetSelectedObject6(1, -1))
            _attempt(adapter, lambda: model.ClearSelection2(True))
            if entity is not None and not any(
                sw_type_info.same_com_object(entity, seen) for _n, seen in picked
            ):
                picked.append((name, entity))
    _measure_picks(adapter, index, picked, point, radius, found, kinds != "faces")
    return found


def _measure_picks(
    adapter: Any,
    index: PartIndex,
    picked: list[tuple[str, Any]],
    point: Point,
    radius: float,
    found: Found,
    with_edges: bool,
) -> None:
    """Measure the picks, and (``with_edges``) the edges of picked faces with few edges."""
    edges: list[Any] = [entity for name, entity in picked if name == "EDGE"]
    for name, entity in picked:
        if name != "FACE":
            continue
        hit = measure(adapter, index, entity, point)
        if hit is None or hit.distance > radius:
            continue
        found.faces.append(hit)
        if not with_edges:
            continue
        index.flag_once(entity, "GetEdges")
        listed = as_list(_attempt(adapter, lambda e=entity: e.GetEdges()))
        if len(listed) <= SMALL_FACE_EDGES:
            edges += [
                edge
                for edge in listed
                if not any(sw_type_info.same_com_object(edge, seen) for seen in edges)
            ]
    for edge in edges:
        hit = measure(adapter, index, edge, point)
        if hit is not None and hit.distance <= radius:
            found.edges.append(hit)


# ---- describing ------------------------------------------------------------------


class _Kept:
    """Descriptions and ids kept with one index, by the handle they describe."""

    def __init__(self) -> None:
        self.faces: dict[int, tuple[Any, dict[str, Any]]] = {}
        self.edges: dict[int, tuple[Any, dict[str, Any]]] = {}
        self.ids: dict[int, tuple[Any, str | None]] = {}
        self.described = 0


_KEPT: weakref.WeakKeyDictionary[PartIndex, _Kept] = weakref.WeakKeyDictionary()


def kept_for(index: PartIndex) -> _Kept:
    """What this module keeps with ``index`` (dropped with it)."""
    kept = _KEPT.get(index)
    if kept is None:
        kept = _KEPT[index] = _Kept()
    return kept


def _is(adapter: Any, surface: Any, name: str) -> bool:
    _flag(adapter, surface, name)
    return bool(_attempt(adapter, lambda: getattr(surface, name)(), False))


def _params(adapter: Any, surface: Any, name: str, size: int) -> list[float] | None:
    """A surface's ``<name>Params`` property, or None when it doesn't read."""
    values = as_list(_attempt(adapter, lambda: getattr(surface, name)))
    if len(values) < size:
        return None
    try:
        return [float(v) for v in values[:size]]
    except (TypeError, ValueError):
        return None


def surface_of(adapter: Any, face: Any, surface: Any) -> dict[str, Any]:
    """A face's surface kind and parameters, in metres and radians.

    Plane: the face's outward ``normal`` (``IFace2.Normal``). Cylinder: its
    ``origin`` (a point on the axis), ``axis`` and ``radius``
    (``CylinderParams``). Cone: the same and ``half_angle`` (``ConeParams``,
    the radius at the origin). Sphere: ``center`` and ``radius``. Torus:
    ``center``, ``axis``, ``radius`` and ``minor_radius``. A B-spline
    surface is ``spline``; anything else ``other``.
    """
    if _is(adapter, surface, "IsPlane"):
        normal = unit(as_list(_attempt(adapter, lambda: face.Normal)) or [])
        if normal is None:
            params = _params(adapter, surface, "PlaneParams", 6)
            normal = unit(params[:3]) if params else None
        return {"kind": "plane", "normal": normal}
    for name, kind, size in (
        ("IsCylinder", "cylinder", 7),
        ("IsCone", "cone", 8),
        ("IsSphere", "sphere", 4),
        ("IsTorus", "torus", 8),
    ):
        if not _is(adapter, surface, name):
            continue
        params = _params(adapter, surface, f"{kind.capitalize()}Params", size)
        info: dict[str, Any] = {"kind": kind}
        if params is None:
            return info
        if kind == "sphere":
            info.update(center=params[0:3], radius=abs(params[3]))
            return info
        axis = unit(params[3:6])
        info["axis"] = axis
        key = "center" if kind == "torus" else "origin"
        info[key] = params[0:3]
        info["radius"] = abs(params[6])
        if kind == "cone":
            info["half_angle"] = params[7]
        if kind == "torus":
            info["minor_radius"] = abs(params[7])
        return info
    if _is(adapter, surface, "IsParametric"):
        return {"kind": "spline"}
    return {"kind": "other"}


def face_facts(
    adapter: Any, index: PartIndex, face: Any, box: Box | None
) -> dict[str, Any]:
    """What a face is, independent of the point: kind, parameters, area and box."""
    index.flag_once(face, "GetSurface")
    surface = _attempt(adapter, lambda: face.GetSurface())
    facts = {"kind": "other"} if surface is None else surface_of(adapter, face, surface)
    index.flag_once(face, "GetArea")
    area = _attempt(adapter, lambda: face.GetArea())
    if isinstance(area, int | float) and not isinstance(area, bool):
        facts["area_mm2"] = round(float(area) * 1e6, 6)
    if box is None:
        index.flag_once(face, "GetBox")
        box = box_from_corners(_attempt(adapter, lambda: face.GetBox()))
    if box is not None:
        facts["box_mm"] = mm([*box[0], *box[1]])
    return facts


def face_entry(facts: dict[str, Any], hit: Hit, point: Point) -> dict[str, Any]:
    """A face for the reply: its facts in mm and degrees, and where it is nearest."""
    entry: dict[str, Any] = {
        "kind": facts["kind"],
        "distance_mm": round(hit.distance * 1000.0, 6),
        "closest_mm": mm(hit.closest),
    }
    if facts.get("normal") is not None:
        entry["normal"] = facts["normal"]
    axis = facts.get("axis")
    if axis is not None:
        entry["axis"] = axis
    if facts["kind"] in ("cylinder", "cone") and axis and facts.get("origin"):
        entry["center_mm"] = mm(nearest_on_axis(facts["origin"], axis, point))
    if facts.get("center") is not None:
        entry["center_mm"] = mm(facts["center"])
    if facts["kind"] == "cone" and facts.get("origin") is not None:
        entry["origin_mm"] = mm(facts["origin"])
    if facts.get("radius") is not None:
        entry["radius_mm"] = round(facts["radius"] * 1000.0, 6)
    if facts.get("half_angle") is not None:
        entry["half_angle_deg"] = round(math.degrees(facts["half_angle"]), 6)
    if facts.get("minor_radius") is not None:
        entry["minor_radius_mm"] = round(facts["minor_radius"] * 1000.0, 6)
    for key in ("area_mm2", "box_mm"):
        if key in facts:
            entry[key] = facts[key]
    return entry


def _curve_length(adapter: Any, index: PartIndex, edge: Any) -> float | None:
    """An edge's true length, metres: ``GetCurveParams2``'s two parameters, ``GetLength3``.

    ``IEdge.GetCurveParams2`` gives the start and end points, then the start
    and end parameters ([6] and [7]); ``ICurve.GetLength3`` measures between
    them.
    """
    index.flag_once(edge, "GetCurveParams2")
    params = as_list(_attempt(adapter, lambda: edge.GetCurveParams2()))
    index.flag_once(edge, "GetCurve")
    curve = _attempt(adapter, lambda: edge.GetCurve())
    if len(params) < 8 or curve is None:
        return None
    _flag(adapter, curve, "GetLength3")
    length = _attempt(
        adapter, lambda: curve.GetLength3(float(params[6]), float(params[7]))
    )
    if isinstance(length, bool) or not isinstance(length, int | float) or length <= 0:
        return None
    return float(length)


def _other_curve(adapter: Any, index: PartIndex, edge: Any) -> str:
    """``ellipse``, ``spline`` (a B-curve) or ``other``, for an edge neither line nor circle."""
    index.flag_once(edge, "GetCurve")
    curve = _attempt(adapter, lambda: edge.GetCurve())
    if curve is None:
        return "other"
    if _is(adapter, curve, "IsEllipse"):
        return "ellipse"
    if _is(adapter, curve, "IsBcurve"):
        return "spline"
    return "other"


def edge_facts(adapter: Any, index: PartIndex, edge: Any) -> dict[str, Any]:
    """What an edge is: kind, ends, circle and length, in mm (``describe_edge`` and more).

    A circular edge with two different ends is an ``arc``; with none, or one
    end twice, a ``circle``. A line's length is end to end, a circle's its
    circumference; any other's is ``GetLength3`` between its parameters.
    """
    info = dict(index.describe(edge))
    start, end = info.get("start_mm"), info.get("end_mm")
    facts: dict[str, Any] = {}
    if info["curve"] == "linear":
        facts["kind"] = "line"
    elif info["curve"] == "circular":
        apart = start and end and math.dist(start, end) > 1e-6
        facts["kind"] = "arc" if apart else "circle"
    else:
        facts["kind"] = _other_curve(adapter, index, edge)
    for key in ("start_mm", "end_mm", "center_mm"):
        if info.get(key) is not None:
            facts[key] = [round(float(c), 6) + 0.0 for c in info[key]]
    if info.get("axis") is not None:
        facts["axis"] = info["axis"]
    if info.get("radius_mm") is not None:
        facts["radius_mm"] = round(abs(float(info["radius_mm"])), 6)
    if facts["kind"] == "line" and info.get("length_mm") is not None:
        facts["length_mm"] = round(float(info["length_mm"]), 6)
    elif facts["kind"] == "circle" and "radius_mm" in facts:
        facts["length_mm"] = round(2 * math.pi * facts["radius_mm"], 6)
    else:
        length = _curve_length(adapter, index, edge)
        if length is not None:
            facts["length_mm"] = round(length * 1000.0, 6)
    facts["key"] = edge_key(info)
    return facts


def _body_name(adapter: Any, index: PartIndex, hit: Hit) -> str | None:
    """The name of the hit's body: the index's, or ``GetBody`` matched to it."""
    body = hit.body
    if body is None:
        index.flag_once(hit.entity, "GetBody")
        handle = _attempt(adapter, lambda: hit.entity.GetBody())
        if handle is None:
            return None
        body = next(
            (
                b
                for b in index.bodies()
                if sw_type_info.same_com_object(b.handle, handle)
            ),
            IndexedBody(handle),
        )
        hit.body = body
    return index.body_name(body)


def _kept_facts(
    store: dict[int, tuple[Any, dict[str, Any]]], handle: Any, read: Any
) -> dict[str, Any]:
    key = id(handle)
    if key not in store:
        store[key] = (handle, read())
    return store[key][1]


def _ids(adapter: Any, kept: _Kept, entities: list[Any]) -> list[str | None]:
    """A persistent id for each entity (``GetPersistReference3``), read once each."""
    missing = [e for e in entities if id(e) not in kept.ids]
    if missing:
        ids = persist_ids(adapter, adapter.currentModel, missing)
        for entity, text in zip(missing, ids, strict=True):
            kept.ids[id(entity)] = (entity, text)
    return [kept.ids[id(e)][1] for e in entities]


def _faces_reply(
    adapter: Any,
    index: PartIndex,
    hits: list[Hit],
    point: Point,
    limit: int,
    unique: bool,
) -> tuple[list[dict[str, Any]], int]:
    """The nearest faces (up to ``limit``), and how many there are.

    Unless ``unique``, a face seen twice (two picks of one face through two
    wrappers) is told by its kind, box and area, so every hit is described.
    """
    kept = kept_for(index)
    hits = sorted(hits, key=lambda h: h.distance)
    out: list[tuple[Hit, dict[str, Any]]] = []
    seen: set[Any] = set()
    count = 0
    for hit in hits:
        if unique and len(out) >= limit:
            count += 1
            continue
        limits.check("describing faces")
        facts = _kept_facts(
            kept.faces,
            hit.entity,
            lambda h=hit: face_facts(adapter, index, h.entity, h.box),
        )
        if not unique:
            key = (
                facts["kind"],
                tuple(facts.get("box_mm") or ()),
                facts.get("area_mm2"),
            )
            if key in seen:
                continue
            seen.add(key)
        count += 1
        if len(out) < limit:
            out.append((hit, facts))
    entries = [face_entry(facts, hit, point) for hit, facts in out]
    _finish(adapter, index, kept, [h for h, _f in out], entries)
    return entries, count


def _edges_reply(
    adapter: Any,
    index: PartIndex,
    hits: list[Hit],
    limit: int,
    unique: bool,
) -> tuple[list[dict[str, Any]], int]:
    """The nearest edges (up to ``limit``), and how many there are.

    Unless ``unique``, an edge reached twice (through two faces, without one
    COM object per edge) is told by ``edge_key``, so every hit is described.
    """
    kept = kept_for(index)
    hits = sorted(hits, key=lambda h: h.distance)
    out: list[tuple[Hit, dict[str, Any]]] = []
    seen: set[Any] = set()
    count = 0
    for hit in hits:
        if unique and len(out) >= limit:
            count += 1
            continue
        facts = _kept_facts(
            kept.edges, hit.entity, lambda h=hit: edge_facts(adapter, index, h.entity)
        )
        if not unique and facts["key"] is not None:
            if facts["key"] in seen:
                continue
            seen.add(facts["key"])
        count += 1
        if len(out) < limit:
            out.append((hit, facts))
    entries = []
    for hit, facts in out:
        entry: dict[str, Any] = {
            "kind": facts["kind"],
            "distance_mm": round(hit.distance * 1000.0, 6),
            "closest_mm": mm(hit.closest),
        }
        entry.update({k: v for k, v in facts.items() if k not in ("kind", "key")})
        entries.append(entry)
    _finish(adapter, index, kept, [h for h, _f in out], entries)
    return entries, count


def _finish(
    adapter: Any,
    index: PartIndex,
    kept: _Kept,
    hits: list[Hit],
    entries: list[dict[str, Any]],
) -> None:
    """Add each listed entity's body and persistent id."""
    ids = _ids(adapter, kept, [hit.entity for hit in hits])
    for hit, entry, text in zip(hits, entries, ids, strict=True):
        entry["body"] = _body_name(adapter, index, hit)
        if text:
            entry["id"] = text


# ---- the query -------------------------------------------------------------------


def query_geometry(
    adapter: Any,
    point_mm: list[float],
    radius_mm: float,
    kinds: str = "both",
    limit: int = 10,
    read_all: bool = False,
) -> dict[str, Any]:
    """The faces and edges of the active part within ``radius_mm`` of ``point_mm``.

    Args:
        adapter: A connected adapter with a part as ``currentModel``.
        point_mm: ``[x, y, z]`` in the part's mm.
        radius_mm: How far from the point, mm.
        kinds: ``faces``, ``edges`` or ``both``.
        limit: The most faces, and the most edges, listed (nearest first).
        read_all: Search the index even when it holds no boxes yet (no picks).

    Returns:
        dict[str, Any]: ``point_mm``, ``radius_mm``, ``kinds``, ``method``
        (``index`` or ``pick``), ``complete``, ``faces`` and ``faces_found``,
        ``edges`` and ``edges_found`` (those asked for), and ``read`` (what
        this call read).

    Raises:
        ValueError: The query is malformed (:func:`query_problem`).
    """
    problem = query_problem(point_mm, radius_mm, kinds, limit)
    if problem:
        raise ValueError(problem)
    point = (
        float(point_mm[0]) / 1000.0,
        float(point_mm[1]) / 1000.0,
        float(point_mm[2]) / 1000.0,
    )
    radius = float(radius_mm) / 1000.0
    index = index_for(adapter)
    kept = kept_for(index)
    described_before = len(kept.faces) + len(kept.edges)
    bodies = bodies_near(index, point, radius)
    method = "index"
    picks = 0
    found = Found()
    if bodies and (read_all or boxes_kept(bodies)):
        found = index_search(adapter, index, bodies, point, radius, kinds)
    elif bodies:
        picked = pick_search(adapter, index, point, radius, kinds)
        picks = picked.picks if picked is not None else 0
        wanted = (
            []
            if picked is None
            else (picked.faces if kinds != "edges" else [])
            + (picked.edges if kinds != "faces" else [])
        )
        if picked is not None and wanted:
            found, method = picked, "pick"
        else:
            found = index_search(adapter, index, bodies, point, radius, kinds)
    reply: dict[str, Any] = {
        "point_mm": [float(c) for c in point_mm],
        "radius_mm": float(radius_mm),
        "kinds": kinds,
        "method": method,
        "complete": method == "index",
    }
    if kinds != "edges":
        reply["faces"], reply["faces_found"] = _faces_reply(
            adapter, index, found.faces, point, limit, unique=method == "index"
        )
    if kinds != "faces":
        reply["edges"], reply["edges_found"] = _edges_reply(
            adapter,
            index,
            found.edges,
            limit,
            unique=method == "index" and bool(index.edge_identity),
        )
    reply["read"] = {
        "kept_from_earlier_call": index.reused,
        "bodies_near": len(bodies),
        "picks": picks,
        "face_boxes": index.counts["face_boxes"],
        "measured": index.counts["closest_points"],
        "described": len(kept.faces) + len(kept.edges) - described_before,
    }
    return reply
