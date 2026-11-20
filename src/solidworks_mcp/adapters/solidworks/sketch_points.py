"""Name what a sketch relation or dimension ties: entities, their points, the origin.

``add_sketch_constraint`` and ``add_sketch_dimension`` take the ids the
``add_*`` tools and ``edit_sketch`` return, and these references to points:

* ``"Origin"``: the part's origin point (case-insensitive);
* ``"<id>.center"`` (or ``.centre``): an arc's, circle's or ellipse's centre;
* ``"<id>.start"`` / ``"<id>.end"``: a line's or arc's end points.

Each resolves to a :class:`Target`: what SolidWorks is given (an
``ISketchSegment`` or ``ISketchPoint``), what kind it is, and where it is in
sketch metres when that reads. The origin is the sketch point of the tree's
``OriginProfileFeature`` (found by type, so a localized tree's "Origen"
works the same), whose sketch holds the one point.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .com_utils import as_list, reads_as_circle
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: The name of the origin, compared without case.
ORIGIN = "origin"

#: A point reference's suffix -> the ``ISketchSegment`` getter of that point.
POINT_GETTERS = {
    "center": "GetCenterPoint2",
    "centre": "GetCenterPoint2",
    "start": "GetStartPoint2",
    "end": "GetEndPoint2",
}

#: ``swSketchSegments_e`` -> the kind a segment is reported as.
SEGMENT_KINDS = {
    0: "line",
    1: "arc",
    2: "ellipse",
    3: "spline",
    4: "text",
    5: "parabola",
}

#: Kinds whose centre ``"<id>.center"`` names.
CENTRED_KINDS = frozenset({"arc", "circle", "ellipse"})

#: The tree feature that holds the origin point.
ORIGIN_FEATURE = "OriginProfileFeature"

#: The most features walked looking for the origin (it sits near the top).
_MAX_FEATURES = 10000


@dataclass(frozen=True)
class Target:
    """One thing a relation or dimension ties.

    Attributes:
        ref: How the caller named it, normalised (``"Circle_2.center"``).
        kind: ``"point"``, ``"line"``, ``"arc"``, ``"circle"``, ``"ellipse"``,
            ``"spline"``, ``"group"`` (a rectangle's or polygon's lines) or
            ``"segment"`` when the type doesn't read.
        obj: The COM object selected or passed to ``AddRelation``.
        xy: Where it is in sketch metres: a point's position; None for a
            segment or when it doesn't read.
        owner: For a point of an entity, that entity.
        entity_id: The registered id it came from (None for the origin).
    """

    ref: str
    kind: str
    obj: Any
    xy: tuple[float, float] | None = None
    owner: Any = None
    entity_id: str | None = None

    @property
    def is_point(self) -> bool:
        """Whether this is a point (the origin or a point of an entity)."""
        return self.kind == "point"

    @property
    def is_origin(self) -> bool:
        """Whether this is the origin."""
        return self.ref == "Origin"


def reference_help() -> str:
    """The sentence every unknown-reference error ends with."""
    return (
        "Use the ids add_* or edit_sketch returned, 'Origin', or a point of an "
        "entity: '<id>.center', '<id>.start', '<id>.end'."
    )


def split_reference(ref: str) -> tuple[str, str | None]:
    """``"Arc_1.center"`` -> ``("Arc_1", "center")``; ``"Arc_1"`` -> ``("Arc_1", None)``.

    Raises:
        ValueError: The part after the dot is not center, centre, start or end.
    """
    text = ref.strip()
    entity_id, dot, part = text.rpartition(".")
    if not dot:
        return text, None
    key = part.strip().lower()
    if key not in POINT_GETTERS:
        raise ValueError(
            f"Unknown point '{part}' in '{text}': use '{entity_id}.center', "
            f"'{entity_id}.start' or '{entity_id}.end'."
        )
    return entity_id.strip(), "center" if key == "centre" else key


def segment_kind(adapter: Any, entity: Any) -> str:
    """What a registered entity is: line, arc, circle, ellipse, ... (see :class:`Target`)."""
    if isinstance(entity, (list, tuple)):
        return "group"
    _flag(adapter, entity, "GetType", "IsCircle")
    kind = _attempt(adapter, lambda: entity.GetType())
    if kind == 1:
        closed = _attempt(adapter, lambda: entity.IsCircle())
        return "circle" if reads_as_circle(closed) else "arc"
    return SEGMENT_KINDS.get(kind, "segment") if isinstance(kind, int) else "segment"


def point_xy(adapter: Any, point: Any) -> tuple[float, float] | None:
    """An ``ISketchPoint``'s x and y in sketch metres, or None."""
    if point is None:
        return None
    x = _attempt(adapter, lambda: float(point.X))
    y = _attempt(adapter, lambda: float(point.Y))
    if x is None or y is None:
        return None
    return (x, y)


def _wrapped(adapter: Any, obj: Any) -> Any:
    """A raw ``PyIDispatch`` (an item of a returned array) wrapped for late binding."""
    if type(obj).__name__ != "PyIDispatch":
        return obj
    from .io import _dynamic

    return _attempt(adapter, lambda: _dynamic.Dispatch(obj), obj)


def origin_point(adapter: Any) -> Any:
    """The part's origin as an ``ISketchPoint``, or None when it can't be found.

    The origin is the ``OriginProfileFeature`` of the tree; its sketch
    (``GetSpecificFeature2``) holds one point (``GetSketchPoints2``).
    """
    model = adapter.currentModel
    _flag(adapter, model, "FirstFeature")
    feature = _attempt(adapter, lambda: model.FirstFeature())
    walked = 0
    while feature is not None and walked < _MAX_FEATURES:
        walked += 1
        _flag(adapter, feature, "GetTypeName2", "GetNextFeature", "GetSpecificFeature2")
        if _attempt(adapter, lambda f=feature: f.GetTypeName2()) == ORIGIN_FEATURE:
            sketch = _attempt(adapter, lambda f=feature: f.GetSpecificFeature2())
            _flag(adapter, sketch, "GetSketchPoints2")
            points = as_list(_attempt(adapter, lambda s=sketch: s.GetSketchPoints2()))
            return _wrapped(adapter, points[0]) if points else None
        feature = _attempt(adapter, lambda f=feature: f.GetNextFeature())
    return None


def _entity_point(adapter: Any, entity: Any, getter: str) -> Any:
    """``entity.<getter>()``: one of its ``ISketchPoint`` s, or None."""
    _flag(adapter, entity, getter)
    point = _attempt(adapter, lambda: adapter._get_attr_or_call(entity, getter))
    return _wrapped(adapter, point) if point is not None else None


def _cached_xy(adapter: Any, entity_id: str) -> tuple[float, float] | None:
    """The point an entity recorded when it was drawn (mm), in metres."""
    cached = getattr(adapter, "_sketch_entity_centers", {}).get(entity_id)
    if cached is None:
        return None
    return (cached[0] / 1000.0, cached[1] / 1000.0)


def point_of(adapter: Any, entity_id: str, entity: Any, part: str) -> Target:
    """The ``part`` point (center, start or end) of a registered entity.

    Raises:
        Exception: The entity is a group, has no such point, or SolidWorks
            gave none.
    """
    ref = f"{entity_id}.{part}"
    kind = segment_kind(adapter, entity)
    if kind == "group":
        raise Exception(
            f"'{entity_id}' is several segments (a rectangle or polygon): name "
            f"one of its lines instead of '{ref}'."
        )
    if part == "center" and kind not in CENTRED_KINDS and kind != "segment":
        raise Exception(f"'{entity_id}' is a {kind}: it has no centre ('{ref}').")
    if part != "center" and kind == "circle":
        raise Exception(
            f"'{entity_id}' is a full circle: it has no {part} point; use "
            f"'{entity_id}.center'."
        )
    point = _entity_point(adapter, entity, POINT_GETTERS[part])
    if point is None:
        raise Exception(f"SolidWorks gave no {part} point for '{entity_id}' ('{ref}').")
    xy = point_xy(adapter, point)
    if xy is None and part == "center":
        xy = _cached_xy(adapter, entity_id)
    return Target(ref, "point", point, xy, entity, entity_id)


def resolve(adapter: Any, ref: str) -> Target:
    """What ``ref`` names in the open sketch.

    Raises:
        Exception: ``Unknown sketch entity '<ref>'`` for an id not registered,
            or what :func:`point_of` raises.
    """
    text = (ref or "").strip()
    if text.lower() == ORIGIN:
        point = origin_point(adapter)
        if point is None:
            raise Exception(
                "SolidWorks gave no origin point (no OriginProfileFeature in the "
                "tree), so 'Origin' can't be used."
            )
        return Target("Origin", "point", point, (0.0, 0.0))
    try:
        entity_id, part = split_reference(text)
    except ValueError as exc:
        raise Exception(str(exc)) from exc
    entity = adapter._sketch_entities.get(entity_id)
    if entity is None:
        raise Exception(f"Unknown sketch entity '{text}'. {reference_help()}")
    if part is not None:
        return point_of(adapter, entity_id, entity, part)
    return Target(
        entity_id,
        segment_kind(adapter, entity),
        entity,
        None,
        None,
        entity_id,
    )


def as_centre(adapter: Any, target: Target) -> Target:
    """An arc's or circle's centre point in place of the curve; others as they are."""
    if target.kind not in ("arc", "circle") or target.entity_id is None:
        return target
    return point_of(adapter, target.entity_id, target.obj, "center")


def position(adapter: Any, target: Target) -> tuple[float, float] | None:
    """Where a target is, for placing dimension text, in sketch metres.

    A point's own position; for a segment, the point it recorded when drawn
    (a circle's centre, a line's midpoint), else its ends' midpoint.
    """
    if target.xy is not None:
        return target.xy
    if target.entity_id is not None:
        cached = _cached_xy(adapter, target.entity_id)
        if cached is not None:
            return cached
    ends = _attempt(
        adapter,
        lambda: adapter._sketch_geometry.read_segment_endpoints(target.obj),
    )
    if ends is None:
        return None
    (x1, y1, _z1), (x2, y2, _z2) = ends
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def with_position(adapter: Any, target: Target) -> Target:
    """``target`` with :func:`position` filled in."""
    if target.xy is not None:
        return target
    return replace(target, xy=position(adapter, target))


def signature(adapter: Any, target: Target) -> tuple[float, ...] | None:
    """Where a target's points are now, rounded to 1e-9 m, to see if a call moved it.

    A point gives its x, y; a segment the x, y of its start, end and centre
    (those that read). None when nothing reads.
    """
    if target.is_point:
        xy = point_xy(adapter, target.obj)
        return None if xy is None else tuple(round(v, 9) for v in xy)
    if target.kind == "group":
        return None
    values: list[float] = []
    for getter in ("GetStartPoint2", "GetEndPoint2", "GetCenterPoint2"):
        xy = point_xy(adapter, _entity_point(adapter, target.obj, getter))
        if xy is not None:
            values.extend(round(v, 9) for v in xy)
    return tuple(values) or None
