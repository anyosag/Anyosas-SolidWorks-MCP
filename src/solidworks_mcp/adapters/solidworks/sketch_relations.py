"""Read a sketch's relations and how defined it is.

* ``ISketch.GetConstrainedStatus()`` gives ``swConstrainedStatus_e``: under,
  fully or over defined (1 unknown, 2 under, 3 fully, 4 over, 5 no solution,
  6 invalid solution, 7 automatic solve off). The API gives no count of the
  under-defined entities; ``ISketchRelationManager.GetRelationsCount`` with
  ``swSketchRelationFilterType_e`` 2 (over-defining) and 1 (dangling) counts
  the relations at fault.
* ``ISketchRelationManager.GetRelations(0)`` lists every relation
  (``ISketchRelation``): ``GetRelationType()`` (``swConstraintType_e``),
  ``GetEntities()`` and ``GetEntitiesType()``
  (``swSketchRelationEntityTypes_e``). A dimension is a relation too
  (distance, diameter, ...); those are counted, not listed, since a sketch's
  dimensions are read by name elsewhere.

An entity is named by the index of its segment in the sketch's segment list
(matched on ``GetID()``, a pair of numbers unique in the sketch), and a point
by where it is.
"""

from __future__ import annotations

from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``swConstrainedStatus_e`` -> ``definition_state``.
CONSTRAINED_STATES = {
    1: "unknown",
    2: "under_defined",
    3: "fully_defined",
    4: "over_defined",
    5: "no_solution",
    6: "invalid_solution",
    7: "autosolve_off",
}

#: ``definition_state`` -> ``is_fully_defined`` (None: can't tell).
FULLY_DEFINED = {
    "fully_defined": True,
    "under_defined": False,
    "over_defined": False,
    "no_solution": False,
    "invalid_solution": False,
}

#: ``swSketchRelationFilterType_e``.
FILTER_ALL = 0
FILTER_DANGLING = 1
FILTER_OVER_DEFINING = 2

#: ``swConstraintType_e`` -> the name a relation is reported by (the names
#: ``add_sketch_constraint`` takes where it takes them).
RELATION_TYPES = {
    1: "distance",
    2: "angle",
    3: "radius",
    4: "horizontal",
    5: "vertical",
    6: "tangent",
    7: "parallel",
    8: "perpendicular",
    9: "coincident",
    10: "concentric",
    11: "symmetric",
    12: "midpoint",
    13: "intersection",
    14: "equal",
    15: "diameter",
    16: "offset_edge",
    17: "fix",
    25: "horizontal_points",
    26: "vertical_points",
    27: "collinear",
    28: "coradial",
    32: "use_edge",
    40: "pierce",
    41: "double_distance",
    42: "merge_points",
    43: "angle_3_points",
    44: "arc_length",
    48: "along_x",
    49: "along_y",
    57: "patterned",
    84: "double_angle",
}

#: Relations that are dimensions: counted, not listed.
DIMENSION_RELATIONS = frozenset({1, 2, 3, 15, 41, 43, 44, 84})

#: ``swSketchRelationEntityTypes_e`` -> an entity's kind.
ENTITY_KINDS = {
    0: "unknown",
    1: "sub_sketch",
    2: "point",
    3: "line",
    4: "arc",
    5: "ellipse",
    6: "parabola",
    7: "spline",
    8: "hatch",
    9: "text",
    10: "plane",
    11: "cylinder",
    12: "sphere",
    13: "surface",
    14: "dimension",
}

#: The most relations listed one by one; past it only their counts.
MAX_LISTED_RELATIONS = 200


def relation_name(value: Any) -> str:
    """A ``swConstraintType_e`` value's name, ``type_<n>`` when it has none here."""
    if isinstance(value, int):
        return RELATION_TYPES.get(value, f"type_{value}")
    return "unknown"


def relation_manager(adapter: Any, sketch: Any) -> Any:
    """The sketch's ``ISketchRelationManager``, flagged, or None."""
    manager = _attempt(adapter, lambda: sketch.RelationManager)
    _flag(adapter, manager, "GetRelations", "GetRelationsCount", "AddRelation")
    return manager


def relation_count(adapter: Any, manager: Any, kind: int = FILTER_ALL) -> int | None:
    """``GetRelationsCount(kind)``, or None when it doesn't read."""
    if manager is None:
        return None
    count = _attempt(adapter, lambda: manager.GetRelationsCount(kind))
    return int(count) if isinstance(count, (int, float)) else None


def constrained_status(adapter: Any, sketch: Any) -> dict[str, Any]:
    """How defined a sketch is (``ISketch.GetConstrainedStatus``).

    Returns:
        dict[str, Any]: ``is_fully_defined`` (None when it can't tell),
        ``definition_state`` (:data:`CONSTRAINED_STATES`, ``unknown`` when
        nothing reads), ``source`` and ``raw_status``; plus
        ``over_defining_relations`` and ``dangling_relations`` when they read.
    """
    _flag(adapter, sketch, "GetConstrainedStatus")
    raw = _attempt(adapter, lambda: sketch.GetConstrainedStatus())
    state = (
        CONSTRAINED_STATES.get(raw, "unknown") if isinstance(raw, int) else "unknown"
    )
    reply: dict[str, Any] = {
        "is_fully_defined": FULLY_DEFINED.get(state),
        "definition_state": state,
        "source": "ISketch.GetConstrainedStatus"
        if isinstance(raw, int)
        else "unavailable",
        "raw_status": raw if isinstance(raw, int) else None,
    }
    manager = relation_manager(adapter, sketch)
    for key, kind in (
        ("over_defining_relations", FILTER_OVER_DEFINING),
        ("dangling_relations", FILTER_DANGLING),
    ):
        count = relation_count(adapter, manager, kind)
        if count is not None:
            reply[key] = count
    return reply


def entity_key(adapter: Any, entity: Any) -> tuple[int, ...] | None:
    """``GetID()`` of a sketch segment or point as a tuple, or None."""
    _flag(adapter, entity, "GetID")
    raw = _attempt(adapter, lambda: entity.GetID())
    values = [v for v in as_list(raw) if isinstance(v, (int, float))]
    return tuple(int(v) for v in values) or None


def _wrapped(adapter: Any, obj: Any) -> Any:
    if type(obj).__name__ != "PyIDispatch":
        return obj
    from .io import _dynamic

    return _attempt(adapter, lambda: _dynamic.Dispatch(obj), obj)


def _describe_entity(
    adapter: Any, entity: Any, kind_value: Any, index_of: dict[tuple[int, ...], int]
) -> dict[str, Any]:
    """One entity of a relation: ``kind``, and ``segment`` (its index) or ``at_mm``."""
    kind = (
        ENTITY_KINDS.get(kind_value, "unknown")
        if isinstance(kind_value, int)
        else "unknown"
    )
    entity = _wrapped(adapter, entity)
    info: dict[str, Any] = {"kind": kind}
    if kind == "point":
        x = _attempt(adapter, lambda: float(entity.X))
        y = _attempt(adapter, lambda: float(entity.Y))
        if x is not None and y is not None:
            info["at_mm"] = [round(x * 1000.0, 6), round(y * 1000.0, 6)]
        return info
    key = entity_key(adapter, entity) if index_of else None
    if key is not None and key in index_of:
        info["segment"] = index_of[key]
    return info


def list_relations(
    adapter: Any,
    sketch: Any,
    index_of: dict[tuple[int, ...], int] | None = None,
    limit: int = MAX_LISTED_RELATIONS,
) -> dict[str, Any]:
    """A sketch's geometric relations, each with what it ties.

    Args:
        adapter: The adapter.
        sketch: An ``ISketch`` (need not be open).
        index_of: Segment ``GetID()`` -> its index in the listing, to name a
            relation's segments by index.
        limit: The most relations described one by one.

    Returns:
        dict[str, Any]: ``relations`` (each ``type`` and ``entities``: each
        ``kind`` and ``segment`` or ``at_mm``), ``relation_counts`` (by
        type, the dimension relations included), ``dimension_relations``
        and ``relations_truncated`` when more than ``limit`` were found.
    """
    manager = relation_manager(adapter, sketch)
    raw = (
        _attempt(adapter, lambda: manager.GetRelations(FILTER_ALL)) if manager else None
    )
    relations: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    dimension_relations = 0
    truncated = False
    for relation in as_list(raw):
        relation = _wrapped(adapter, relation)
        _flag(adapter, relation, "GetRelationType", "GetEntities", "GetEntitiesType")
        value = _attempt(adapter, lambda r=relation: r.GetRelationType())
        name = relation_name(value)
        counts[name] = counts.get(name, 0) + 1
        if value in DIMENSION_RELATIONS:
            dimension_relations += 1
            continue
        if len(relations) >= limit:
            truncated = True
            continue
        entities = as_list(_attempt(adapter, lambda r=relation: r.GetEntities()))
        kinds = as_list(_attempt(adapter, lambda r=relation: r.GetEntitiesType()))
        relations.append(
            {
                "type": name,
                "entities": [
                    _describe_entity(
                        adapter,
                        entity,
                        kinds[i] if i < len(kinds) else None,
                        index_of or {},
                    )
                    for i, entity in enumerate(entities)
                ],
            }
        )
    reply: dict[str, Any] = {
        "relations": relations,
        "relation_counts": counts,
        "dimension_relations": dimension_relations,
    }
    if truncated:
        reply["relations_truncated"] = True
    return reply
