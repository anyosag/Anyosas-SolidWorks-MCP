"""Read a feature's or a sketch's definition without changing the model.

``get_feature_definition`` and ``get_sketch`` read what ``edit_feature`` and
``edit_sketch`` change, so an agent can look inside a model it didn't build
(opened read-only, say) without marking it modified:

* a feature: ``IFeature.GetDefinition`` gives its ``*FeatureData`` object,
  whose getters are read without ``AccessSelections`` (which only an edit
  needs) and never written back (no ``ModifyDefinition``);
* a sketch: ``IFeature.GetSpecificFeature2`` gives the ``ISketch``; its
  segments (``GetSketchSegments``) and text (``ISketchText``) are read
  without ``EditSketch``, so the sketch is never opened;
* every feature: its display dimensions, by the names ``set_dimension``
  takes.

A type with no reader here still gives its type, suppression state and
dimensions. Enum values are from SolidWorks 2026's ``swconst``
(``swEndConditions_e``, ``swRevolveType_e``, ``swChamferType_e``,
``swSimpleFilletType_e``).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .feature_editing import (
    SEGMENT_ARC,
    SEGMENT_PREFIXES,
    _feature,
    describe_circle,
    describe_segment,
    edge_count,
    feature_kind,
)

#: ``swEndConditions_e``.
END_CONDITIONS = {
    0: "Blind",
    1: "ThroughAll",
    2: "ThroughNext",
    3: "UpToVertex",
    4: "UpToSurface",
    5: "OffsetFromSurface",
    6: "MidPlane",
    7: "UpToBody",
    9: "ThroughAllBoth",
    10: "UpToSelection",
    11: "UpToNext",
}

#: ``swRevolveType_e``; the two-direction types have a second angle.
REVOLVE_TYPES = {
    0: "one_direction",
    1: "mid_plane",
    2: "two_direction",
    3: "one_direction_360",
    4: "mid_plane_360",
    5: "two_direction_360",
}
_TWO_DIRECTION_REVOLVES = frozenset({2, 5})

#: ``swChamferType_e``.
CHAMFER_TYPES = {
    1: "angle_distance",
    2: "distance_distance",
    3: "vertex",
    16: "equal_distance",
}

#: ``swSimpleFilletType_e``.
FILLET_TYPES = {0: "constant_radius", 2: "face", 3: "full_round"}

#: The ``*FeatureData`` methods read here (the rest are properties).
_DATA_METHODS = (
    "GetDepth",
    "GetEndCondition",
    "GetDraftWhileExtruding",
    "GetDraftAngle",
    "GetDraftOutward",
    "IsThinFeature",
    "GetWallThickness",
    "IsBossFeature",
    "GetRevolutionAngle",
    "GetEdgeChamferDistance",
    "GetVertexChamferDistance",
    "IsDirection2Specified",
    "GetPatternFeatureCount",
    "GetSkippedItemCount",
)

#: ``ISketchText`` and ``ITextFormat`` methods (``Text`` and the format's
#: values are properties).
_TEXT_METHODS = ("GetTextFormat", "GetCoordinates", "GetUseDocTextFormat")

#: The most dimensions read from one feature.
MAX_DIMENSIONS = 500


def _mm(value: Any) -> float | None:
    """Metres as mm, or None when ``value`` isn't a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value) * 1000.0, 6)


def _deg(value: Any) -> float | None:
    """Radians as degrees, or None when ``value`` isn't a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(math.degrees(float(value)), 6)


def _flag_value(value: Any) -> bool | None:
    """A COM boolean as bool, or None when it wasn't read."""
    return None if value is None else bool(value)


def _count(value: Any) -> int | None:
    """A COM integer as int, or None when ``value`` isn't one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _named(names: dict[int, str], value: Any) -> Any:
    """An enum value by name; an unknown value as given."""
    code = _count(value)
    return names.get(code, value) if code is not None else value


def _extrude_direction(adapter: Any, data: Any, forward: bool) -> dict[str, Any]:
    """One direction of an extrude or cut: depth, end condition and draft."""
    direction: dict[str, Any] = {
        "depth_mm": _mm(_attempt(adapter, lambda: data.GetDepth(forward))),
        "end_condition": _named(
            END_CONDITIONS, _attempt(adapter, lambda: data.GetEndCondition(forward))
        ),
    }
    drafted = _attempt(adapter, lambda: data.GetDraftWhileExtruding(forward))
    direction["draft"] = _flag_value(drafted)
    if drafted:
        direction["draft_angle_deg"] = _deg(
            _attempt(adapter, lambda: data.GetDraftAngle(forward))
        )
        direction["draft_outward"] = _flag_value(
            _attempt(adapter, lambda: data.GetDraftOutward(forward))
        )
    return direction


def _thin(adapter: Any, data: Any, values: dict[str, Any]) -> None:
    """Add ``thin`` and, for a thin feature, ``wall_thickness_mm``."""
    thin = _attempt(adapter, lambda: data.IsThinFeature())
    values["thin"] = _flag_value(thin)
    if thin:
        values["wall_thickness_mm"] = _mm(
            _attempt(adapter, lambda: data.GetWallThickness(True))
        )


def read_extrude(adapter: Any, data: Any, cut: bool | None = None) -> dict[str, Any]:
    """An extrude's or cut's ``IExtrudeFeatureData2``, in mm and degrees.

    ``cut`` comes from the feature's type ("Extrusion" or "Cut"):
    ``IsBossFeature`` read False on a boss extrude (SolidWorks 2026 SP4.1
    probe, 2026-11-16), so it is asked only when the type doesn't say.
    """
    both = _flag_value(_attempt(adapter, lambda: data.BothDirections))
    values: dict[str, Any] = {
        "direction1": _extrude_direction(adapter, data, True),
        "both_directions": both,
        "reverse_direction": _flag_value(
            _attempt(adapter, lambda: data.ReverseDirection)
        ),
    }
    if both:
        values["direction2"] = _extrude_direction(adapter, data, False)
    _thin(adapter, data, values)
    boss = (
        not cut if cut is not None else _attempt(adapter, lambda: data.IsBossFeature())
    )
    if boss is not None:
        values["cut"] = not boss
        if boss:
            values["merge"] = _flag_value(_attempt(adapter, lambda: data.Merge))
        else:
            values["flip_side_to_cut"] = _flag_value(
                _attempt(adapter, lambda: data.FlipSideToCut)
            )
    return values


def read_revolve(adapter: Any, data: Any) -> dict[str, Any]:
    """A revolve's or revolved cut's ``IRevolveFeatureData2``, in degrees."""
    kind = _attempt(adapter, lambda: data.Type)
    values: dict[str, Any] = {
        "angle_deg": _deg(_attempt(adapter, lambda: data.GetRevolutionAngle(True))),
        "revolve_type": _named(REVOLVE_TYPES, kind),
        "reverse_direction": _flag_value(
            _attempt(adapter, lambda: data.ReverseDirection)
        ),
    }
    if _count(kind) in _TWO_DIRECTION_REVOLVES:
        values["angle2_deg"] = _deg(
            _attempt(adapter, lambda: data.GetRevolutionAngle(False))
        )
    _thin(adapter, data, values)
    return values


def read_fillet(adapter: Any, data: Any) -> dict[str, Any]:
    """A fillet's ``ISimpleFilletFeatureData2``: radius (mm) and edge count."""
    return {
        "radius_mm": _mm(_attempt(adapter, lambda: data.DefaultRadius)),
        "edge_count": edge_count(adapter, data),
        "fillet_type": _named(FILLET_TYPES, _attempt(adapter, lambda: data.Type)),
        "multiple_radius": _flag_value(
            _attempt(adapter, lambda: data.IsMultipleRadius)
        ),
        "tangent_propagation": _flag_value(
            _attempt(adapter, lambda: data.PropagateToTangentFaces)
        ),
    }


def read_chamfer(adapter: Any, data: Any) -> dict[str, Any]:
    """A chamfer's ``IChamferFeatureData2``: distances (mm) or angle, edge count.

    ``GetEdgeChamferDistance(0)`` is the first distance (the only one for an
    angle-distance chamfer, per the API help); ``(1)`` the second.
    """
    kind = _count(_attempt(adapter, lambda: data.Type))
    values: dict[str, Any] = {
        "chamfer_type": CHAMFER_TYPES.get(kind, kind) if kind is not None else None,
        "edge_count": edge_count(adapter, data),
    }
    if kind == 3:
        values["vertex_distances_mm"] = [
            _mm(_attempt(adapter, lambda s=side: data.GetVertexChamferDistance(s)))
            for side in (0, 1, 2)
        ]
        return values
    values["distance_mm"] = _mm(
        _attempt(adapter, lambda: data.GetEdgeChamferDistance(0))
    )
    if kind in (1, None):
        values["angle_deg"] = _deg(_attempt(adapter, lambda: data.EdgeChamferAngle))
    if kind in (2, None):
        values["distance2_mm"] = _mm(
            _attempt(adapter, lambda: data.GetEdgeChamferDistance(1))
        )
    return values


def _pattern_counts(adapter: Any, data: Any, values: dict[str, Any]) -> None:
    """Add how many features the pattern repeats and instances it skips."""
    values["patterned_features"] = _count(
        _attempt(adapter, lambda: data.GetPatternFeatureCount())
    )
    values["skipped_instances"] = _count(
        _attempt(adapter, lambda: data.GetSkippedItemCount())
    )


def read_linear_pattern(adapter: Any, data: Any) -> dict[str, Any]:
    """A linear pattern's ``ILinearPatternFeatureData``: counts and spacing (mm)."""
    values: dict[str, Any] = {
        "direction1": {
            "count": _count(_attempt(adapter, lambda: data.D1TotalInstances)),
            "spacing_mm": _mm(_attempt(adapter, lambda: data.D1Spacing)),
            "reverse_direction": _flag_value(
                _attempt(adapter, lambda: data.D1ReverseDirection)
            ),
        }
    }
    if _attempt(adapter, lambda: data.IsDirection2Specified()):
        values["direction2"] = {
            "count": _count(_attempt(adapter, lambda: data.D2TotalInstances)),
            "spacing_mm": _mm(_attempt(adapter, lambda: data.D2Spacing)),
            "reverse_direction": _flag_value(
                _attempt(adapter, lambda: data.D2ReverseDirection)
            ),
        }
    _pattern_counts(adapter, data, values)
    return values


def read_circular_pattern(adapter: Any, data: Any) -> dict[str, Any]:
    """A circular pattern's ``ICircularPatternFeatureData``: count and angle.

    With ``equal_spacing`` the angle is the total the instances spread over;
    without it, the angle between neighbours (``Spacing``, in radians).
    """
    equal = _flag_value(_attempt(adapter, lambda: data.EqualSpacing))
    values: dict[str, Any] = {
        "count": _count(_attempt(adapter, lambda: data.TotalInstances)),
        "spacing_deg": _deg(_attempt(adapter, lambda: data.Spacing)),
        "equal_spacing": equal,
        "spacing_is": None if equal is None else ("total" if equal else "step"),
        "reverse_direction": _flag_value(
            _attempt(adapter, lambda: data.ReverseDirection)
        ),
    }
    if _attempt(adapter, lambda: data.Direction2):
        values["direction2"] = {
            "count": _count(_attempt(adapter, lambda: data.TotalInstances2)),
            "spacing_deg": _deg(_attempt(adapter, lambda: data.Spacing2)),
            "symmetric": _flag_value(_attempt(adapter, lambda: data.Symmetric)),
        }
    _pattern_counts(adapter, data, values)
    return values


#: ``feature_kind`` -> the reader of its definition.
READERS: dict[str, Callable[[Any, Any], dict[str, Any]]] = {
    "Extrusion": lambda adapter, data: read_extrude(adapter, data, cut=False),
    "Cut": lambda adapter, data: read_extrude(adapter, data, cut=True),
    "ICE": read_extrude,
    "Revolution": read_revolve,
    "RevCut": read_revolve,
    "Fillet": read_fillet,
    "Chamfer": read_chamfer,
    "LPattern": read_linear_pattern,
    "CirPattern": read_circular_pattern,
}

#: ``IFeature.GetTypeName2`` of a 2D and a 3D sketch.
SKETCH_KINDS = {"ProfileFeature": "2D", "3DProfileFeature": "3D"}

#: What ``get_feature_definition`` reads, for the note on other types.
_READ_KINDS = (
    "extrudes, cuts, revolves, fillets, chamfers, linear and circular patterns "
    "and sketches"
)


def feature_dimensions(adapter: Any, feature: Any) -> list[dict[str, Any]]:
    """A feature's display dimensions: ``name`` (as ``set_dimension`` takes it),
    ``value`` and ``units`` (mm, deg or count).
    """
    from .documentation import _strip_document_suffix
    from .io import dimension_kind, from_system_value

    _flag(adapter, feature, "GetFirstDisplayDimension", "GetNextDisplayDimension")
    dimensions: list[dict[str, Any]] = []
    display = _attempt(adapter, lambda: feature.GetFirstDisplayDimension())
    while display is not None and len(dimensions) < MAX_DIMENSIONS:
        _flag(adapter, display, "GetDimension2")
        dimension = _attempt(adapter, lambda d=display: d.GetDimension2(0))
        if dimension is not None:
            kind, unit, factor = dimension_kind(adapter, dimension)
            system = _attempt(adapter, lambda d=dimension: d.SystemValue)
            dimensions.append(
                {
                    "name": _strip_document_suffix(
                        _attempt(adapter, lambda d=dimension: d.FullName)
                    ),
                    "value": (
                        from_system_value(float(system), kind, factor)
                        if isinstance(system, (int, float))
                        else None
                    ),
                    "units": unit,
                }
            )
        display = _attempt(
            adapter, lambda d=display: feature.GetNextDisplayDimension(d)
        )
    return dimensions


def describe_text(adapter: Any, text: Any) -> dict[str, Any]:
    """A sketch text's string, font, height and position (``ISketchText``).

    Returns:
        dict[str, Any]: ``text``, ``position_mm`` (sketch x, y),
        ``uses_document_font`` and, from its ``ITextFormat``, ``font``,
        ``height_mm``, ``height_points`` (when given in points),
        ``angle_deg``, ``bold`` and ``italic``; what can't be read is None.
    """
    _flag(adapter, text, *_TEXT_METHODS)
    value = _attempt(adapter, lambda: text.Text)
    coordinates = [
        c
        for c in as_list(_attempt(adapter, lambda: text.GetCoordinates()))
        if isinstance(c, (int, float))
    ]
    info: dict[str, Any] = {
        "text": value if isinstance(value, str) else None,
        "position_mm": (
            [round(float(c) * 1000.0, 6) for c in coordinates[:2]]
            if len(coordinates) >= 2
            else None
        ),
        "uses_document_font": _flag_value(
            _attempt(adapter, lambda: text.GetUseDocTextFormat())
        ),
    }
    text_format = _attempt(adapter, lambda: text.GetTextFormat())
    if text_format is None:
        return info
    _flag(adapter, text_format, "IsHeightSpecifiedInPts")
    font = _attempt(adapter, lambda: text_format.TypeFaceName)
    info["font"] = font if isinstance(font, str) else None
    info["height_mm"] = _mm(_attempt(adapter, lambda: text_format.CharHeight))
    if _attempt(adapter, lambda: text_format.IsHeightSpecifiedInPts()):
        info["height_points"] = _count(
            _attempt(adapter, lambda: text_format.CharHeightInPts)
        )
    info["angle_deg"] = _deg(_attempt(adapter, lambda: text_format.Escapement))
    info["bold"] = _flag_value(_attempt(adapter, lambda: text_format.Bold))
    info["italic"] = _flag_value(_attempt(adapter, lambda: text_format.Italic))
    return info


def _dispatchable(adapter: Any, segment: Any) -> Any:
    """A segment as a dispatch late binding can call (``io._as_com`` for a raw one).

    Items of a returned array can arrive as raw ``PyIDispatch``, on which
    method flagging does nothing (see ``io._as_com``).
    """
    if type(segment).__name__ != "PyIDispatch":
        return segment
    from .io import _as_com

    return _as_com(adapter, segment, "ISketchSegment") or segment


def sketch_segments(adapter: Any, sketch: Any) -> list[dict[str, Any]]:
    """Every segment of an ``ISketch``, read without opening the sketch.

    Each is :func:`feature_editing.describe_segment`'s description with its
    ``index``; a text segment (``swSketchTEXT``) also has
    :func:`describe_text`'s details. When ``GetSketchSegments`` lists no
    text, ``GetSketchTextSegments`` is read too.
    """
    _flag(adapter, sketch, "GetSketchSegments", "GetSketchTextSegments")
    described: list[dict[str, Any]] = []
    for segment in as_list(_attempt(adapter, lambda: sketch.GetSketchSegments())):
        segment = _dispatchable(adapter, segment)
        info = describe_segment(adapter, segment)
        if info["type"] == "text":
            info.update(describe_text(adapter, segment))
        described.append({"index": len(described), **info})
    if any(s["type"] == "text" for s in described):
        return described
    for text in as_list(_attempt(adapter, lambda: sketch.GetSketchTextSegments())):
        text = _dispatchable(adapter, text)
        described.append(
            {"index": len(described), "type": "text", **describe_text(adapter, text)}
        )
    return described


def sketch_axes(adapter: Any, sketch: Any) -> dict[str, Any] | None:
    """Where the sketch's x, y and normal point in the model, and its origin.

    The inverse of ``ISketch.ModelToSketchTransform``, as ``create_sketch``
    and the cut check read it (``sketch.axes_from_transform``).
    """
    from .sketch import axes_from_transform

    transform = _attempt(adapter, lambda: sketch.ModelToSketchTransform)
    if transform is None:
        return None
    _flag(adapter, transform, "Inverse")
    inverse = _attempt(adapter, lambda: transform.Inverse())
    if inverse is None:
        return None
    return axes_from_transform(_attempt(adapter, lambda: inverse.ArrayData))


def _parents(adapter: Any, feature: Any) -> list[dict[str, str]]:
    """The features a sketch is built on (``IFeature.GetParents``): name, type."""
    _flag(adapter, feature, "GetParents")
    parents = []
    for parent in as_list(_attempt(adapter, lambda: feature.GetParents())):
        _flag(adapter, parent, "GetTypeName2")
        parents.append(
            {
                "name": str(_attempt(adapter, lambda p=parent: p.Name) or ""),
                "type": str(_attempt(adapter, lambda p=parent: p.GetTypeName2()) or ""),
            }
        )
    return parents


def _sketch_of(adapter: Any, feature: Any, name: str) -> Any:
    """The ``ISketch`` of a sketch feature.

    Raises:
        RuntimeError: SolidWorks gave none.
    """
    _flag(adapter, feature, "GetSpecificFeature2")
    sketch = _attempt(adapter, lambda: feature.GetSpecificFeature2())
    if sketch is None:
        raise RuntimeError(f"SolidWorks gave no sketch for {name!r}.")
    return sketch


def _segment_counts(segments: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for segment in segments:
        counts[segment["type"]] = counts.get(segment["type"], 0) + 1
    return counts


#: ``get_sketch``'s ``detail`` values.
SKETCH_DETAILS = ("full", "summary")


def read_sketch(adapter: Any, name: str, detail: str = "full") -> dict[str, Any]:
    """A sketch's plane, axes and segments, without opening it for editing.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        name: The sketch's tree name.
        detail: ``"full"`` (every segment's points) or ``"summary"``
            (:func:`summarize_sketch`).

    Returns:
        dict[str, Any]: ``sketch``, ``kind`` (2D or 3D), ``plane`` (the
        reference plane it is on, None on a face), ``references`` (its parent
        features: name and type), ``axes`` (model coordinates; None when
        unreadable), ``segments`` (each with ``index``, ``type``, its points
        in sketch mm, ``construction`` and, for text, the text's details; a
        full circle is ``circle`` with ``center_mm`` and ``radius_mm``) and
        ``segment_counts``. A summary has what :func:`summarize_sketch` gives
        in place of ``segments`` and ``axes``.

    Raises:
        LookupError: No such feature.
        ValueError: It isn't a sketch, or ``detail`` is neither value.
        RuntimeError: SolidWorks gave no sketch.
    """
    if detail not in SKETCH_DETAILS:
        raise ValueError(f"detail must be 'full' or 'summary', not {detail!r}.")
    feature = _feature(adapter, name)
    kind = str(_attempt(adapter, lambda: feature.GetTypeName2()) or "")
    if kind not in SKETCH_KINDS:
        raise ValueError(
            f"{name!r} is a {kind or 'feature'}, not a sketch; "
            "get_feature_definition reads other features."
        )
    sketch = _sketch_of(adapter, feature, name)
    references = _parents(adapter, feature)
    if detail == "summary":
        return {
            "sketch": name,
            "kind": SKETCH_KINDS[kind],
            "plane": next(
                (p["name"] for p in references if p["type"] == "RefPlane"), None
            ),
            "references": references,
            "detail": "summary",
            **summarize_sketch(adapter, feature, sketch),
        }
    segments = sketch_segments(adapter, sketch)
    return {
        "sketch": name,
        "kind": SKETCH_KINDS[kind],
        "plane": next((p["name"] for p in references if p["type"] == "RefPlane"), None),
        "references": references,
        "axes": sketch_axes(adapter, sketch),
        "segments": segments,
        "segment_counts": _segment_counts(segments),
    }


def _plain_dispatch(adapter: Any, item: Any) -> Any:
    """An array item callable under late binding, its methods flagged one by one later.

    ``_dispatchable`` flags the whole ``ISketchSegment`` interface on each
    item (about 27 ms); the summary reads three or four members, so it
    wraps the item only and flags those.
    """
    if type(item).__name__ != "PyIDispatch":
        return item
    from .io import _dynamic

    return _attempt(adapter, lambda: _dynamic.Dispatch(item), item)


def summarize_sketch(adapter: Any, feature: Any, sketch: Any) -> dict[str, Any]:
    """What defines a sketch, read with as few calls per segment as it can.

    Each segment gives its type (``GetType``) and id (``GetID``, to name it
    in relations); a full circle also its centre and radius
    (``feature_editing.describe_circle``). Lines, arcs and the rest are only
    counted, with no end points read: a 174-circle sketch took 25 s and a
    very long reply in full.

    Returns:
        dict[str, Any]: ``segment_counts`` (by type, ``circle`` apart from
        ``arc``), ``circles`` (each ``index``, ``center_mm``,
        ``radius_mm``), ``dimensions`` (``feature_dimensions``: name, value,
        units), ``relations`` (each ``type`` and its ``entities``: ``kind``
        and ``segment``, the index ``edit_sketch``'s ids follow, or a
        point's ``at_mm``), ``relation_counts``, ``dimension_relations``
        and ``sketch_relations.constrained_status``'s definition state.
    """
    from . import sketch_relations

    _flag(adapter, sketch, "GetSketchSegments")
    counts: dict[str, int] = {}
    circles: list[dict[str, Any]] = []
    index_of: dict[tuple[int, ...], int] = {}
    segments = as_list(_attempt(adapter, lambda: sketch.GetSketchSegments()))
    for index, raw in enumerate(segments):
        segment = _plain_dispatch(adapter, raw)
        _flag(adapter, segment, "GetType")
        kind = _attempt(adapter, lambda s=segment: s.GetType())
        name = SEGMENT_PREFIXES.get(kind, "Segment").lower()
        circle = describe_circle(adapter, segment) if kind == SEGMENT_ARC else None
        if circle is not None:
            name = "circle"
            circles.append(
                {"index": index, **{k: v for k, v in circle.items() if k != "type"}}
            )
        counts[name] = counts.get(name, 0) + 1
        key = sketch_relations.entity_key(adapter, segment)
        if key is not None:
            index_of[key] = index
    return {
        "segment_counts": counts,
        "circles": circles,
        "dimensions": feature_dimensions(adapter, feature),
        **sketch_relations.list_relations(adapter, sketch, index_of),
        **sketch_relations.constrained_status(adapter, sketch),
    }


def _sketch_summary(adapter: Any, feature: Any, name: str) -> dict[str, Any]:
    """A sketch feature's definition: plane, segment counts and its texts."""
    sketch = _sketch_of(adapter, feature, name)
    references = _parents(adapter, feature)
    segments = sketch_segments(adapter, sketch)
    return {
        "plane": next((p["name"] for p in references if p["type"] == "RefPlane"), None),
        "segment_counts": _segment_counts(segments),
        "texts": [
            {k: v for k, v in s.items() if k != "index"}
            for s in segments
            if s["type"] == "text"
        ],
    }


def _absorbed_sketch(adapter: Any, feature: Any) -> dict[str, Any] | None:
    """The sketch a feature is built from: its name and its texts, if any."""
    from .features import _absorbed_sketches

    names = _absorbed_sketches(adapter, feature)
    if not names:
        return None
    sketch_feature = _attempt(
        adapter, lambda: adapter.currentModel.FeatureByName(names[0])
    )
    if sketch_feature is None:
        return {"name": names[0]}
    _flag(adapter, sketch_feature, "GetSpecificFeature2")
    sketch = _attempt(adapter, lambda: sketch_feature.GetSpecificFeature2())
    if sketch is None:
        return {"name": names[0]}
    texts = [
        s.get("text")
        for s in sketch_segments(adapter, sketch)
        if s["type"] == "text" and s.get("text")
    ]
    return {"name": names[0], **({"texts": texts} if texts else {})}


def feature_definition(adapter: Any, name: str) -> dict[str, Any]:
    """A feature's parameters, read without editing it.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        name: The feature's tree name.

    Returns:
        dict[str, Any]: ``feature``, ``type`` (``GetTypeName2``; an
        Instant3D extrude's real type), ``suppressed``, ``known_type``
        (whether its parameters are read), ``parameters`` (see the readers
        above; a sketch's ``plane``, ``segment_counts`` and ``texts``),
        ``sketch`` (the sketch it is built from, with its ``texts``) when it
        has one, ``dimensions``, and a ``note`` when the parameters couldn't
        be read.

    Raises:
        LookupError: No such feature.
    """
    feature = _feature(adapter, name)
    kind = feature_kind(adapter, feature)
    _flag(adapter, feature, "IsSuppressed")
    reply: dict[str, Any] = {
        "feature": name,
        "type": kind,
        "suppressed": _flag_value(_attempt(adapter, lambda: feature.IsSuppressed())),
        "known_type": kind in READERS or kind in SKETCH_KINDS,
        "parameters": {},
    }
    if kind in SKETCH_KINDS:
        reply["parameters"] = _sketch_summary(adapter, feature, name)
    elif kind in READERS:
        data = _attempt(adapter, lambda: feature.GetDefinition())
        if data is None:
            reply["note"] = f"SolidWorks gave no definition for {name!r}."
        else:
            _flag(adapter, data, *_DATA_METHODS)
            reply["parameters"] = READERS[kind](adapter, data)
        sketch = _absorbed_sketch(adapter, feature)
        if sketch is not None:
            reply["sketch"] = sketch
    else:
        reply["note"] = (
            f"get_feature_definition reads the parameters of {_READ_KINDS}; a "
            f"{kind or 'feature'} gives its dimensions only."
        )
    reply["dimensions"] = feature_dimensions(adapter, feature)
    return reply
