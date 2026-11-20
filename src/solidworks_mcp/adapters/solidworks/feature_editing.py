"""Edit an existing model in place: feature parameters, the rollback bar, sketches.

Deleting a feature to rebuild it takes its dependents with it (the Cellvis
dish rack lost two fillets that way). These change it where it stands:

* :func:`edit_feature`: an extrude's or cut's depth, end condition and
  direction, a revolve's angle, a fillet's radius, through the feature's
  definition (``IFeature.GetDefinition``, ``AccessSelections``, the
  ``Set*`` calls, ``IFeature.ModifyDefinition``). The values are read back
  after the rebuild, so the reply says what SolidWorks now holds. It also
  replaces a fillet's or chamfer's edges (``Edges`` on
  ``ISimpleFilletFeatureData2`` / ``IChamferFeatureData2``) with the edges
  selected now or those an edge query matches, gathered before
  ``AccessSelections``, which may clear the selection (D5 of the
  ``cellvis-assembly-and-print-checks`` change, probe 2).
* :func:`set_rollback`: ``IFeatureManager.EditRollback`` moves the rollback
  bar before or after a feature, or back to the end; new features go in at
  the bar.
* :func:`open_sketch` / :func:`delete_sketch_entities`: open an existing
  sketch for editing (``IModelDoc2.EditSketch``) with an id for each of its
  segments, so the ``add_*`` tools draw into it and its segments can be
  deleted by id; :func:`delete_all_sketch_entities` deletes every segment.

Enum values are from SolidWorks 2026's ``swconst.tlb``.
"""

from __future__ import annotations

import math
from typing import Any

from .. import sw_type_info as _sw_type_info
from .com_utils import as_list, reads_as_circle
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .edge_rules import (
    RULE_KEYS,
    EdgeRuleSelector,
    expand_query,
    match_rules,
    no_match_message_rules,
    validate_edge_query,
)
from .ref_plane_edit import PLANE_EDITS, REF_PLANE, edit_plane

#: ``swEndConditions_e`` by the name the tool takes (lower case, no spaces).
END_CONDITIONS: dict[str, tuple[str, int]] = {
    "blind": ("Blind", 0),
    "throughall": ("ThroughAll", 1),
    "midplane": ("MidPlane", 6),
    "throughallboth": ("ThroughAllBoth", 9),
    "uptonext": ("UpToNext", 11),
}
_END_CONDITION_NAMES = {value: name for name, value in END_CONDITIONS.values()}

#: ``swMoveRollbackBarTo_e``.
ROLLBACK_TO_END = 1
ROLLBACK_BEFORE = 3
ROLLBACK_AFTER = 4

#: ``GetTypeName2`` of an extrude made with Instant3D, boss and cut alike
#: (live, SolidWorks 2026: Boss-Extrudes and Cut-Extrudes). The API help
#: says ``IFeature.GetTypeName`` gives such a feature's underlying type.
INSTANT3D = "ICE"

_EXTRUDE_EDITS = frozenset({"depth", "end_condition", "reverse_direction"})

#: The changes that replace a fillet's or chamfer's edges, one at a time:
#: ``edges: "selection"`` or ``edge_rules`` (a ``select_edges`` query).
EDGE_CHANGES = frozenset({"edges", "edge_rules"})

#: ``IFeature.GetTypeName2`` -> which parameters ``edit_feature`` changes.
#: "ICE" stays only when ``GetTypeName`` gives no extrude type; boss and
#: cut take the same changes.
EDITABLE: dict[str, frozenset[str]] = {
    "Extrusion": _EXTRUDE_EDITS,
    "Cut": _EXTRUDE_EDITS,
    INSTANT3D: _EXTRUDE_EDITS,
    "Revolution": frozenset({"angle", "reverse_direction"}),
    "RevCut": frozenset({"angle", "reverse_direction"}),
    "Fillet": frozenset({"radius"}) | EDGE_CHANGES,
    "Chamfer": EDGE_CHANGES,
    REF_PLANE: PLANE_EDITS,
}

#: ``swSelectType_e.swSelEDGES``: what ``GetSelectedObjectType3`` says of an edge.
SELECTED_EDGE = 1

#: The keys an ``edge_rules`` query may give: ``select_edges``' own.
EDGE_QUERY_KEYS = (*RULE_KEYS, "any_of")

_EXTRUDE_KINDS = ("Extrusion", "Cut", INSTANT3D)

#: ``GetTypeName`` names of an Instant3D extrude's underlying type, lower
#: case; it may not use ``GetTypeName2``'s capitals ("CUT" was reported).
_UNDERLYING = {"extrusion": "Extrusion", "boss": "Extrusion", "cut": "Cut"}


def instant3d_type(older: Any) -> str:
    """An "ICE" feature's type from its ``GetTypeName``: Extrusion, Cut or ICE."""
    return _UNDERLYING.get(str(older or "").lower(), INSTANT3D)


def feature_kind(adapter: Any, feature: Any) -> str:
    """A feature's ``GetTypeName2``, or for an Instant3D extrude its real type.

    Returns:
        str: ``"Extrusion"`` or ``"Cut"`` for an "ICE" feature whose
        ``GetTypeName`` names one, else ``GetTypeName2`` as given.
    """
    kind = str(_attempt(adapter, lambda: feature.GetTypeName2()) or "")
    if kind != INSTANT3D:
        return kind
    return instant3d_type(_attempt(adapter, lambda: feature.GetTypeName()))


#: ``swSketchSegments_e`` -> the id prefix a segment gets.
SEGMENT_PREFIXES = {
    0: "Line",
    1: "Arc",
    2: "Ellipse",
    3: "Spline",
    4: "Text",
    5: "Parabola",
}

_DATA_MEMBERS = (
    "AccessSelections",
    "ReleaseSelectionAccess",
    "GetDepth",
    "SetDepth",
    "GetEndCondition",
    "SetEndCondition",
    "GetRevolutionAngle",
    "SetRevolutionAngle",
)


def end_condition_value(name: str) -> tuple[str, int]:
    """The SolidWorks name and value of an end condition.

    Raises:
        ValueError: For one ``edit_feature`` doesn't set.
    """
    key = "".join(c for c in name.lower() if c not in " _-")
    if key not in END_CONDITIONS:
        names = ", ".join(n for n, _ in END_CONDITIONS.values())
        raise ValueError(f"end_condition must be one of {names}, not {name!r}")
    return END_CONDITIONS[key]


def edge_query(rules: dict[str, Any]) -> dict[str, Any]:
    """An ``edge_rules`` query as ``select_edges`` takes it: ``edge_type`` lower case."""
    query = dict(rules)
    if query.get("edge_type") is not None:
        query["edge_type"] = str(query["edge_type"]).strip().lower()
    return query


def edge_rules_problem(rules: Any) -> str | None:
    """What is wrong with an ``edge_rules`` query, or None.

    It takes ``select_edges``' keys (:data:`EDGE_QUERY_KEYS`), checked by
    :func:`edge_rules.validate_edge_query`.

    Args:
        rules: The query as given.

    Returns:
        str | None: The problem, starting "edge_rules".
    """
    if not isinstance(rules, dict):
        return (
            "edge_rules must be a select_edges query: an object such as "
            "{'side': 'top', 'edge_type': 'linear'}."
        )
    unknown = sorted(set(rules) - set(EDGE_QUERY_KEYS))
    if unknown:
        return (
            f"edge_rules has unknown key(s) {', '.join(unknown)}; it takes "
            f"select_edges' keys: {', '.join(EDGE_QUERY_KEYS)}."
        )
    try:
        problem = validate_edge_query(edge_query(rules))
    except (TypeError, ValueError) as exc:  # a number where a list goes, ...
        problem = f"a value has the wrong type ({exc})."
    return f"edge_rules: {problem}" if problem else None


def check_changes(kind: str, changes: dict[str, Any], name: str) -> str | None:
    """What is wrong with editing ``changes`` on a ``kind`` feature, or None."""
    if not changes:
        return (
            "Give at least one change: depth, end_condition, reverse_direction, "
            "angle, radius, edges, edge_rules, offset or flip."
        )
    allowed = EDITABLE.get(kind)
    if allowed is None:
        return (
            f"{name!r} is a {kind or 'feature'}; edit_feature changes extrudes, "
            "cuts, revolves, fillets, chamfers and offset planes. Change other "
            "features' dimensions with set_dimension."
        )
    extra = sorted(set(changes) - allowed)
    if extra:
        return (
            f"{name!r} is a {kind}; it takes {', '.join(sorted(allowed))}, not "
            f"{', '.join(extra)}."
        )
    if "end_condition" in changes:
        try:
            end_condition_value(str(changes["end_condition"]))
        except ValueError as exc:
            return str(exc)
    return _edge_change_problem(changes)


def _edge_change_problem(changes: dict[str, Any]) -> str | None:
    """What is wrong with ``edges`` / ``edge_rules`` in ``changes``, or None."""
    if EDGE_CHANGES <= set(changes):
        return "Give edges or edge_rules, not both."
    if "edges" in changes and changes["edges"] != "selection":
        return (
            f"edges takes 'selection' (the edges selected now), not "
            f"{changes['edges']!r}; give a select_edges query as edge_rules instead."
        )
    if "edge_rules" in changes:
        return edge_rules_problem(changes["edge_rules"])
    return None


def no_edges_message(name: str, reason: str) -> str:
    """The error for a fillet or chamfer given no edges (``invalid_input``).

    ``tools/error_classifier`` gives the ``invalid_input`` rule its opening
    words, "No new edges for".

    Args:
        name: The feature's tree name.
        reason: Why: nothing selected, or what each rule kept.

    Returns:
        str: The message.
    """
    return f"No new edges for {name!r}, so it is unchanged: {reason}"


def selection_reason(selected: int) -> str:
    """Why ``edges: "selection"`` found no edge, with ``selected`` entities selected."""
    what = (
        "nothing is selected"
        if selected == 0
        else f"none of the {selected} selected entities is an edge"
    )
    return (
        f"{what}. Select its new edges first (select_edges or "
        "select_face_edges), or give edge_rules."
    )


def rules_reason(rules: list[dict[str, Any]], counts: list[dict[str, int]]) -> str:
    """Why ``edge_rules`` found no edge: each rule's counts."""
    text = no_match_message_rules(rules, counts)
    return text[:1].lower() + text[1:]


def selected_edges(adapter: Any, name: str) -> list[Any]:
    """The edges selected now, in selection order (``GetSelectedObject6``).

    Raises:
        ValueError: None of the selected entities is an edge.
    """
    model = adapter.currentModel
    manager = _attempt(adapter, lambda: model.SelectionManager)
    _flag(
        adapter,
        manager,
        "GetSelectedObjectCount2",
        "GetSelectedObjectType3",
        "GetSelectedObject6",
    )
    count = _attempt(adapter, lambda: int(manager.GetSelectedObjectCount2(-1)), 0)
    edges = []
    for index in range(1, count + 1):
        kind = _attempt(adapter, lambda i=index: manager.GetSelectedObjectType3(i, -1))
        if kind != SELECTED_EDGE:
            continue
        edge = _attempt(adapter, lambda i=index: manager.GetSelectedObject6(i, -1))
        if edge is not None:
            edges.append(edge)
    if not edges:
        raise ValueError(no_edges_message(name, selection_reason(count)))
    return edges


def matched_edges(adapter: Any, name: str, rules: dict[str, Any]) -> list[Any]:
    """The edges of the active part an ``edge_rules`` query matches.

    The handles come straight from :func:`edge_rules.match_rules`, so
    nothing is selected.

    Raises:
        ValueError: No rule matched an edge.
        LookupError: A rule's feature, face or plane isn't there.
    """
    expanded = expand_query(edge_query(rules))
    selector = EdgeRuleSelector(adapter)
    kept, counts = match_rules(
        expanded, selector.candidates, selector.convexity, selector.arc_middle
    )
    if not kept:
        raise ValueError(no_edges_message(name, rules_reason(expanded, counts)))
    return [edge for edge, _info in kept]


def new_edges(adapter: Any, name: str, changes: dict[str, Any]) -> list[Any] | None:
    """The edges ``changes`` gives a fillet or chamfer, or None when it gives none.

    Gathered before ``AccessSelections``, which may clear the selection.
    """
    if "edge_rules" in changes:
        return matched_edges(adapter, name, changes["edge_rules"])
    if "edges" in changes:
        return selected_edges(adapter, name)
    return None


def dispatch_array(items: list[Any]) -> Any:
    """``VARIANT(VT_ARRAY | VT_DISPATCH, items)``, as ``Edges`` takes a list.

    Probe 2 put a fillet's and a chamfer's ``Edges`` this way through late
    binding. Without pywin32 (mock and non-Windows runs) the list itself.
    """
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return list(items)
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(items)
    )


def edge_count(adapter: Any, data: Any) -> int | None:
    """How many edges a fillet's or chamfer's definition holds, or None.

    ``GetEdgeCount()``, both interfaces have it, else the length of
    ``Edges``: the order probe 2 read them in. ``GetEdgeCount`` is flagged
    here rather than with the other members, so an extrude's edit doesn't
    pay a failed name lookup for it.
    """
    _flag(adapter, data, "GetEdgeCount")
    count: int | None = _attempt(adapter, lambda: int(data.GetEdgeCount()))
    if count is not None:
        return count
    edges = _attempt(adapter, lambda: data.Edges)
    return None if edges is None else len(as_list(edges))


def _feature(adapter: Any, name: str) -> Any:
    model = adapter.currentModel
    feature = _attempt(adapter, lambda: model.FeatureByName(name))
    if feature is None:
        raise LookupError(f"Feature {name!r} not found; list_features names them.")
    _flag(
        adapter,
        feature,
        "GetTypeName2",
        "GetTypeName",
        "GetDefinition",
        "ModifyDefinition",
    )
    return feature


def _read(adapter: Any, kind: str, data: Any) -> dict[str, Any]:
    """The editable values a definition holds, in mm and degrees."""
    values: dict[str, Any] = {}
    if kind in _EXTRUDE_KINDS:
        depth = _attempt(adapter, lambda: data.GetDepth(True))
        end = _attempt(adapter, lambda: data.GetEndCondition(True))
        values["depth"] = None if depth is None else round(float(depth) * 1000, 6)
        values["end_condition"] = _END_CONDITION_NAMES.get(end, end)
    if kind in ("Revolution", "RevCut"):
        angle = _attempt(adapter, lambda: data.GetRevolutionAngle(True))
        values["angle"] = None if angle is None else round(math.degrees(angle), 6)
    if kind in (*_EXTRUDE_KINDS, "Revolution", "RevCut"):
        reverse = _attempt(adapter, lambda: data.ReverseDirection)
        values["reverse_direction"] = None if reverse is None else bool(reverse)
    if kind == "Fillet":
        radius = _attempt(adapter, lambda: data.DefaultRadius)
        values["radius"] = None if radius is None else round(float(radius) * 1000, 6)
    return values


def _write(data: Any, changes: dict[str, Any], edges: list[Any] | None) -> None:
    """Put ``changes``, and ``edges`` when given, into a definition whose selections are accessed."""
    if edges is not None:
        data.Edges = dispatch_array(edges)
    if "end_condition" in changes:
        data.SetEndCondition(
            True, end_condition_value(str(changes["end_condition"]))[1]
        )
    if "depth" in changes:
        data.SetDepth(True, float(changes["depth"]) / 1000.0)
    if "angle" in changes:
        data.SetRevolutionAngle(True, math.radians(float(changes["angle"])))
    if "reverse_direction" in changes:
        data.ReverseDirection = bool(changes["reverse_direction"])
    if "radius" in changes:
        data.DefaultRadius = float(changes["radius"]) / 1000.0


def edit_feature(
    adapter: Any, name: str, changes: dict[str, Any], null_component: Any
) -> dict[str, Any]:
    """Change an existing feature's parameters and read them back.

    A fillet's or chamfer's edges are replaced as a whole: the edges are
    gathered first (:func:`new_edges`), then put as its definition's
    ``Edges``. SolidWorks drops edges a fillet can't take (probe 2: a fillet
    given its own top face's four edges kept two), so the reply counts what
    was given and what it kept.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        name: The feature's tree name.
        changes: ``depth`` (mm), ``end_condition``, ``reverse_direction``,
            ``angle`` (degrees) or ``radius`` (mm); for a fillet or chamfer
            ``edges: "selection"`` or ``edge_rules`` (a ``select_edges``
            query).
        null_component: The null ``IComponent2`` the definition calls take
            for a part (a ``VT_DISPATCH`` null; ``None`` is a type mismatch).

    Returns:
        dict[str, Any]: ``feature``, ``type``, ``before`` and ``after``; with
        new edges also ``edges_before``, ``edges_given`` and ``edges_after``
        (``None`` when a count can't be read).

    Raises:
        LookupError: No such feature, or a rule's source isn't there.
        ValueError: A change this kind of feature doesn't take, or no edges
            were selected or matched.
        RuntimeError: SolidWorks gave no definition or refused the change.
    """
    model = adapter.currentModel
    feature = _feature(adapter, name)
    kind = feature_kind(adapter, feature)
    problem = check_changes(kind, changes, name)
    if problem:
        raise ValueError(problem)
    if kind == REF_PLANE:
        return edit_plane(adapter, name, feature, changes, null_component, _feature)
    edges = new_edges(adapter, name, changes)
    data = _attempt(adapter, lambda: feature.GetDefinition())
    if data is None:
        raise RuntimeError(
            f"SolidWorks gave no definition for {name!r}, so it can't be edited "
            "this way; change a dimension with set_dimension (e.g. "
            f"'D1@{name}') instead."
        )
    _flag(adapter, data, *_DATA_MEMBERS)
    before = _read(adapter, kind, data)
    edges_before = edge_count(adapter, data) if edges is not None else None
    if not _attempt(
        adapter, lambda: data.AccessSelections(model, null_component), False
    ):
        raise RuntimeError(
            f"SolidWorks would not open {name!r}'s definition for editing."
        )
    try:
        _write(data, changes, edges)
    except Exception:
        _attempt(
            adapter, lambda: adapter._get_attr_or_call(data, "ReleaseSelectionAccess")
        )
        raise
    if not feature.ModifyDefinition(data, model, null_component):
        _attempt(
            adapter, lambda: adapter._get_attr_or_call(data, "ReleaseSelectionAccess")
        )
        raise RuntimeError(
            f"SolidWorks refused the change to {name!r} (ModifyDefinition "
            "returned False); the feature is as it was. rebuild_model says why "
            "a value can't apply."
        )
    _attempt(adapter, lambda: model.EditRebuild3())
    after_data = _attempt(adapter, lambda: _feature(adapter, name).GetDefinition())
    if after_data is not None:
        _flag(adapter, after_data, *_DATA_MEMBERS)
    plain = {k: v for k, v in changes.items() if k not in EDGE_CHANGES}
    after = _read(adapter, kind, after_data) if after_data is not None else plain
    reply: dict[str, Any] = {
        "feature": name,
        "type": kind,
        "before": before,
        "after": after,
    }
    if edges is not None:
        reply["edges_before"] = edges_before
        reply["edges_given"] = len(edges)
        reply["edges_after"] = (
            edge_count(adapter, after_data) if after_data is not None else None
        )
    return reply


def rolled_back_features(adapter: Any) -> list[str]:
    """The names of the features below the rollback bar, in tree order."""
    model = adapter.currentModel
    _flag(adapter, model, "FirstFeature")
    names: list[str] = []
    feature = _attempt(adapter, lambda: model.FirstFeature())
    guard = 0
    while feature is not None and guard < 10000:
        guard += 1
        _flag(adapter, feature, "IsRolledBack", "GetNextFeature")
        if _attempt(adapter, lambda f=feature: f.IsRolledBack(), False):
            names.append(str(_attempt(adapter, lambda f=feature: f.Name, "")))
        feature = _attempt(adapter, lambda f=feature: f.GetNextFeature())
    return names


def set_rollback(adapter: Any, before: str | None, after: str | None) -> dict[str, Any]:
    """Move the rollback bar before or after a feature, or to the end.

    Args:
        adapter: A connected adapter with a ``currentModel``.
        before: Put the bar just above this feature.
        after: Put the bar just below this feature.

    Returns:
        dict[str, Any]: ``position`` in words, ``rolled_back``, the features
        now below the bar, and ``rebuild`` (:func:`rebuild_report`): rolling
        forward rebuilds the features the bar passes, and a fillet made with
        the bar up can lose edges then and only warn.

    Raises:
        LookupError: The named feature isn't in the tree.
        RuntimeError: SolidWorks didn't move the bar.
    """
    model = adapter.currentModel
    target = before or after
    if target:
        _feature(adapter, target)  # a clear not-found before EditRollback
    manager = model.FeatureManager
    _flag(adapter, manager, "EditRollback")
    location = (
        ROLLBACK_BEFORE if before else ROLLBACK_AFTER if after else ROLLBACK_TO_END
    )
    moved = manager.EditRollback(location, target or "")
    if not moved:
        raise RuntimeError(
            f"SolidWorks did not move the rollback bar "
            f"{'to the end' if not target else ('before ' if before else 'after ') + repr(target)}."
        )
    position = (
        f"before {target}" if before else f"after {target}" if after else "the end"
    )
    rolled = rolled_back_features(adapter)
    return {
        "position": position,
        "rolled_back": rolled,
        "rebuild": _attempt(adapter, lambda: rebuild_report(adapter, model, rolled)),
    }


def rebuild_report(adapter: Any, model: Any, rolled_back: list[str]) -> dict[str, Any]:
    """The rebuild errors and warnings SolidWorks holds now, as ``rebuild_model`` gives them.

    Read without a rebuild (``rebuild_inspector``); the features below the
    rollback bar, which aren't built, are left out.

    Returns:
        dict[str, Any]: ``errors`` (each from ``rebuild_diagnostics.explain``,
        warnings too), ``error_count``, ``warning_count`` and ``summary``.
    """
    from .rebuild_diagnostics import explain, summarize
    from .rebuild_inspector import collect_rebuild_issues

    below = set(rolled_back)
    entries = [
        explain(issue)
        for issue in collect_rebuild_issues(adapter, model)
        if issue.feature not in below
    ]
    error_count, warning_count, summary = summarize(entries)
    return {
        "errors": entries,
        "error_count": error_count,
        "warning_count": warning_count,
        "summary": summary,
    }


def _point_mm(adapter: Any, point: Any) -> list[float] | None:
    if point is None:
        return None
    coords = [_attempt(adapter, lambda a=axis: getattr(point, a)) for axis in "XY"]
    if any(c is None for c in coords):
        return None
    return [round(float(c) * 1000, 6) for c in coords]


#: ``swSketchSegments_e.swSketchTEXT``.
SEGMENT_TEXT = 4

#: ``swSketchSegments_e.swSketchARC``.
SEGMENT_ARC = 1


def describe_circle(adapter: Any, segment: Any) -> dict[str, Any] | None:
    """A full circle's ``center_mm`` and ``radius_mm``; None for an arc.

    ``ISketchArc.IsCircle()`` says whether the arc closes (the long 1, read
    by :func:`~.com_utils.reads_as_circle`), and
    ``GetRadius()`` gives the radius in metres. Three reads past the
    segment's type, against six for its start, end and centre.

    Returns:
        dict[str, Any] | None: ``type`` ``circle``, ``center_mm`` and
        ``radius_mm`` (each when it reads); None when the arc isn't a full
        circle or ``IsCircle`` doesn't read.
    """
    _flag(adapter, segment, "IsCircle", "GetRadius", "GetCenterPoint2")
    if not reads_as_circle(_attempt(adapter, lambda: segment.IsCircle())):
        return None
    info: dict[str, Any] = {"type": "circle"}
    center = _point_mm(adapter, _attempt(adapter, lambda: segment.GetCenterPoint2()))
    if center is not None:
        info["center_mm"] = center
    radius = _attempt(adapter, lambda: segment.GetRadius())
    if isinstance(radius, (int, float)) and not isinstance(radius, bool):
        info["radius_mm"] = round(float(radius) * 1000, 6)
    return info


def describe_text(adapter: Any, text: Any) -> dict[str, Any]:
    """A sketch text's string, height, font and position, as far as they read.

    ``ISketchText.Text`` (a property), ``GetTextFormat()`` (an
    ``ITextFormat``: ``CharHeight`` in metres, ``TypeFaceName``) and
    ``GetCoordinates()`` (metres).

    Returns:
        dict[str, Any]: ``text``, ``height_mm``, ``font`` and
        ``position_mm``; a value that doesn't read is left out.
    """
    _flag(adapter, text, "GetTextFormat", "GetCoordinates")
    info: dict[str, Any] = {}
    value = _attempt(adapter, lambda: text.Text)
    if isinstance(value, str):
        info["text"] = value
    text_format = _attempt(adapter, lambda: text.GetTextFormat())
    if text_format is not None:
        height = _attempt(adapter, lambda: text_format.CharHeight)
        if isinstance(height, int | float) and height > 0:
            info["height_mm"] = round(float(height) * 1000, 6)
        font = _attempt(adapter, lambda: text_format.TypeFaceName)
        if isinstance(font, str) and font:
            info["font"] = font
    coords = _attempt(adapter, lambda: list(text.GetCoordinates()))
    if coords and all(isinstance(c, int | float) for c in coords[:3]):
        info["position_mm"] = [round(float(c) * 1000, 6) for c in coords[:3]]
    return info


class _SketchTexts:
    """Find the ``ISketchText`` behind a text segment of a sketch.

    ``ISketch.GetSketchTextSegments`` gives the sketch's texts, read once;
    a text segment is matched to the one wrapping the same COM object, or,
    when none does, to the first one not matched yet, in the order they
    came. With none left the segment is read as itself.
    """

    def __init__(self, adapter: Any, sketch: Any) -> None:
        self._adapter, self._sketch = adapter, sketch
        self._texts: list[Any] | None = None
        self._used: set[int] = set()

    def __call__(self, segment: Any) -> Any:
        if self._texts is None:
            _flag(self._adapter, self._sketch, "GetSketchTextSegments")
            self._texts = as_list(
                _attempt(self._adapter, lambda: self._sketch.GetSketchTextSegments())
            )
        free = [i for i in range(len(self._texts)) if i not in self._used]
        same = [
            i for i in free if _sw_type_info.same_com_object(self._texts[i], segment)
        ]
        for index in same or free[:1]:
            self._used.add(index)
            return self._texts[index]
        return segment


def describe_segment(adapter: Any, segment: Any, text_of: Any = None) -> dict[str, Any]:
    """A sketch segment's kind and points in sketch mm, as far as they read.

    Args:
        adapter: The adapter.
        segment: An ``ISketchSegment``.
        text_of: For a text segment, gives its ``ISketchText`` (see
            :class:`_SketchTexts`); without it the segment itself is read.

    Returns:
        dict[str, Any]: ``type``, the points that read (``start_mm``,
        ``end_mm``, ``center_mm``), ``construction``; a text's
        :func:`describe_text` instead of points; a full circle (an arc whose
        ``ISketchArc.IsCircle`` reads 1) is ``type`` ``circle`` with
        ``center_mm`` and ``radius_mm`` (:func:`describe_circle`), since its
        start and end are the same point and said nothing.
    """
    _flag(
        adapter, segment, "GetType", "GetStartPoint2", "GetEndPoint2", "GetCenterPoint2"
    )
    kind = _attempt(adapter, lambda: segment.GetType())
    info: dict[str, Any] = {"type": SEGMENT_PREFIXES.get(kind, "Segment").lower()}
    circle = describe_circle(adapter, segment) if kind == SEGMENT_ARC else None
    if circle is not None:
        info = circle
    elif kind == SEGMENT_TEXT:
        # A text has no end points; "Text_5" alone said nothing of it.
        info.update(describe_text(adapter, text_of(segment) if text_of else segment))
    else:
        for key, getter in (
            ("start_mm", "GetStartPoint2"),
            ("end_mm", "GetEndPoint2"),
            ("center_mm", "GetCenterPoint2"),
        ):
            point = _point_mm(
                adapter, _attempt(adapter, lambda g=getter: getattr(segment, g)())
            )
            if point is not None:
                info[key] = point
    construction = _attempt(adapter, lambda: segment.ConstructionGeometry)
    if construction is not None:
        info["construction"] = bool(construction)
    return info


def describe_segments(adapter: Any, sketch: Any) -> list[tuple[Any, dict[str, Any]]]:
    """Every segment of ``sketch`` with :func:`describe_segment`'s description."""
    _flag(adapter, sketch, "GetSketchSegments")
    text_of = _SketchTexts(adapter, sketch)
    return [
        (segment, describe_segment(adapter, segment, text_of))
        for segment in as_list(_attempt(adapter, lambda: sketch.GetSketchSegments()))
    ]


def reference_point_mm(info: dict[str, Any]) -> tuple[float, float] | None:
    """A described segment's reference point: a circle's or arc's centre, a line's middle.

    ``open_sketch`` records it as ``add_circle`` and ``add_line`` do, so a
    dimension to an entity of a reopened sketch is placed without reading
    the entity back (``sketch._sketch_point_xyz``).
    """
    center = info.get("center_mm")
    if info.get("type") in ("circle", "arc") and isinstance(center, list):
        return (float(center[0]), float(center[1]))
    start, end = info.get("start_mm"), info.get("end_mm")
    if info.get("type") == "line" and isinstance(start, list) and isinstance(end, list):
        return ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    return None


def save_flag(adapter: Any, model: Any) -> bool | None:
    """``IModelDoc2.GetSaveFlag``: whether the document has unsaved changes."""
    _flag(adapter, model, "GetSaveFlag")
    value = _attempt(adapter, lambda: model.GetSaveFlag())
    return value if isinstance(value, bool) else None


def open_sketch(adapter: Any, name: str) -> dict[str, Any]:
    """Open an existing sketch for editing and give each of its segments an id.

    Records ``adapter._opened_sketch`` (the name, whether the document had
    unsaved changes before, and the segments as described) for
    :func:`unchanged_since_opened`: a sketch opened only to be read is left
    without a rebuild. It also holds the part's solid bodies, read before the
    sketch opens (``solid_bodies``), so ``exit_sketch``'s rebuild can say
    when an edit split a body off.

    Args:
        adapter: A connected ``PyWin32Adapter`` with a ``currentModel``.
        name: The sketch's tree name.

    Returns:
        dict[str, Any]: ``sketch`` and ``segments`` (each with ``id``,
        ``type`` and its points in sketch mm, or a text's string, height,
        font and position).

    Raises:
        LookupError: No such feature.
        ValueError: It isn't a sketch.
        RuntimeError: SolidWorks didn't open it.
    """
    from .bodies import solid_body_names
    from .visibility import SKETCH_TYPES

    model = adapter.currentModel
    feature = _feature(adapter, name)
    kind = str(_attempt(adapter, lambda: feature.GetTypeName2()) or "")
    if kind not in SKETCH_TYPES:
        raise ValueError(f"{name!r} is a {kind or 'feature'}, not a sketch.")
    was_dirty = save_flag(adapter, model)
    # Before EditSketch, which shows the part rolled back to the sketch.
    bodies = solid_body_names(adapter, model)
    _flag(adapter, feature, "Select2", "GetSpecificFeature2")
    _flag(adapter, model, "ClearSelection2", "EditSketch")
    _attempt(adapter, lambda: model.ClearSelection2(True))
    if not _attempt(adapter, lambda: feature.Select2(False, 0), False):
        raise RuntimeError(f"SolidWorks could not select sketch {name!r}.")
    # A void zero-argument method: runs once whether or not it was flagged.
    adapter._get_attr_or_call(model, "EditSketch")
    manager = model.SketchManager
    sketch = _attempt(adapter, lambda: manager.ActiveSketch)
    if sketch is None:
        raise RuntimeError(f"SolidWorks did not open sketch {name!r} for editing.")
    adapter.currentSketchManager = manager
    adapter.currentSketch = sketch
    adapter._last_sketch_name = name
    adapter._reset_sketch_entity_registry()
    manager.AddToDB = True  # as create_sketch: points land where asked
    segments = []
    described = describe_segments(adapter, sketch)
    for segment, info in described:
        entity_id = adapter._register_sketch_entity(info["type"].capitalize(), segment)
        segments.append({"id": entity_id, **info})
        reference = reference_point_mm(info)
        if reference is not None:
            adapter._sketch_entity_centers[entity_id] = reference
    adapter._opened_sketch = {
        "name": name,
        "was_dirty": was_dirty,
        "segments": [info for _segment, info in described],
        "solid_bodies": bodies,
    }
    return {"sketch": name, "segments": segments}


def unchanged_since_opened(adapter: Any, sketch: Any) -> bool:
    """Whether the open sketch is as :func:`open_sketch` found it, on a clean part.

    True only when the document had no unsaved changes when the sketch was
    opened and every segment still describes the same. ``exit_sketch`` then
    leaves without a rebuild: an ``edit_sketch`` + ``exit_sketch`` made only
    to read geometry marked the Lid part modified (SolidWorks 2026 SP4.1).
    Reads and forgets ``adapter._opened_sketch``.

    Args:
        adapter: The adapter.
        sketch: The sketch being exited (``ISketch``).

    Returns:
        bool: True for a read-only round trip.
    """
    opened = getattr(adapter, "_opened_sketch", None)
    adapter._opened_sketch = None
    if (
        not opened
        or opened.get("was_dirty") is not False
        or opened.get("name") != getattr(adapter, "_last_sketch_name", None)
        or sketch is None
    ):
        return False
    now = [info for _segment, info in describe_segments(adapter, sketch)]
    return bool(now == opened.get("segments"))


#: The most ids a not-found error lists.
MAX_LISTED_IDS = 30


def missing_ids_message(missing: list[str], known: list[str]) -> str:
    """The error for ids the open sketch doesn't have: which it has, and why.

    Args:
        missing: The ids asked for that aren't there.
        known: The ids the open sketch has, in the order to list them.

    Returns:
        str: The message, listing at most :data:`MAX_LISTED_IDS` known ids.
    """
    text = f"Sketch entity {', '.join(missing)} not found in the open sketch."
    shown = ", ".join(known[:MAX_LISTED_IDS])
    if not known:
        text += " The open sketch has no entity ids."
    elif len(known) == 1:
        text += f" The open sketch has one id: {shown}."
    elif len(known) <= MAX_LISTED_IDS:
        text += f" The open sketch has {len(known)} ids: {shown}."
    else:
        text += (
            f" The open sketch has {len(known)} ids; the first {MAX_LISTED_IDS}: "
            f"{shown}."
        )
    return text + (
        " Ids change each time a sketch is opened: use the ids in the latest "
        "edit_sketch reply (and the add_* replies since), or all: true to "
        "delete every entity."
    )


def delete_sketch_entities(adapter: Any, entity_ids: list[str]) -> dict[str, Any]:
    """Delete entities of the open sketch by id.

    Raises:
        LookupError: An id isn't in the open sketch.
        RuntimeError: SolidWorks didn't select or delete them.
    """
    missing = [i for i in entity_ids if i not in adapter._sketch_entities]
    if missing:
        raise LookupError(missing_ids_message(missing, list(adapter._sketch_entities)))
    model = adapter.currentModel
    _flag(adapter, model, "ClearSelection2", "EditDelete")
    _attempt(adapter, lambda: model.ClearSelection2(True))
    for index, entity_id in enumerate(entity_ids):
        if not adapter._select_sketch_entity(
            adapter._sketch_entities[entity_id], index > 0
        ):
            _attempt(adapter, lambda: model.ClearSelection2(True))
            raise RuntimeError(f"SolidWorks could not select {entity_id}.")
    adapter._get_attr_or_call(model, "EditDelete")
    for entity_id in entity_ids:
        adapter._sketch_entities.pop(entity_id, None)
        adapter._sketch_entity_centers.pop(entity_id, None)
    return {"deleted": list(entity_ids), "remaining": sorted(adapter._sketch_entities)}


def _among(entity: Any, segments: list[Any]) -> bool:
    """Whether an id's entity is (or, for a rectangle's lines, has) one of ``segments``."""
    members = list(entity) if isinstance(entity, (list, tuple)) else [entity]
    return any(
        _sw_type_info.same_com_object(member, segment)
        for member in members
        for segment in segments
    )


def delete_all_sketch_entities(adapter: Any) -> dict[str, Any]:
    """Delete every segment of the open sketch, whatever its id.

    Reads the segments from the sketch itself (``ISketch.GetSketchSegments``),
    so it also deletes what has no id. A segment SolidWorks won't select is
    left in the sketch and reported.

    Returns:
        dict[str, Any]: ``deleted`` (the ids of what went), ``deleted_count``
        (segments deleted, with an id or not), ``skipped`` (each segment
        left: its ``id`` when it has one, ``type`` and points) and
        ``remaining`` (the ids still in the sketch). Every other id is
        dropped: its geometry, relation or dimension is gone.

    Raises:
        RuntimeError: No sketch is open, or SolidWorks selected none of its
            segments.
    """
    manager = adapter.currentSketchManager
    sketch = _attempt(adapter, lambda: manager.ActiveSketch) or adapter.currentSketch
    if sketch is None:
        raise RuntimeError("No sketch is open in SolidWorks.")
    _flag(adapter, sketch, "GetSketchSegments")
    segments = as_list(_attempt(adapter, lambda: sketch.GetSketchSegments()))
    model = adapter.currentModel
    _flag(adapter, model, "ClearSelection2", "EditDelete")
    _attempt(adapter, lambda: model.ClearSelection2(True))
    selected: list[Any] = []
    left: list[Any] = []
    for segment in segments:
        if adapter._select_sketch_entity(segment, bool(selected)):
            selected.append(segment)
        else:
            left.append(segment)
    if left and not selected:
        raise RuntimeError(
            f"SolidWorks could not select any of the open sketch's {len(left)} "
            "segments, so none was deleted."
        )
    if selected:
        adapter._get_attr_or_call(model, "EditDelete")
    registry = adapter._sketch_entities
    remaining = [i for i, entity in registry.items() if _among(entity, left)]
    deleted = [
        i
        for i, entity in registry.items()
        if i not in remaining and _among(entity, selected)
    ]
    skipped = [
        {
            "id": next((i for i in remaining if _among(registry[i], [segment])), None),
            **describe_segment(adapter, segment),
        }
        for segment in left
    ]
    for entity_id in [i for i in registry if i not in remaining]:
        registry.pop(entity_id)
        adapter._sketch_entity_centers.pop(entity_id, None)
    return {
        "deleted": deleted,
        "deleted_count": len(selected),
        "skipped": skipped,
        "remaining": sorted(remaining),
    }
