"""Solid bodies of a multibody part: list, insert, combine, move, delete, split, measure.

A build on SOLIDWORKS 2026 SP4.1 ("Lid with inverted text features") had to
cut a holder's raised 1 mm text labels, with 0.15 mm clearance, into a lid's
underside, and had to drop to the raw API for every step. What it learned,
and what this module does with it:

* ``IFeatureManager.InsertCombineFeature(op, null, [a, b])`` returned null.
  The preselection form worked (23 bodies from a common): the bodies selected
  with marks, then ``null`` and an empty array. The API help gives the marks:
  the main body 1 and the tools 2 for a subtract; its example selects every
  body with mark 2 for an add, as the session did for the common.
* ``InsertIndent(0.001, 0.00015, False, True, True, False)``, with the lid at
  mark 1 and the 23 label bodies at mark 4, returned null when the tools lay
  fully inside the target. The API help's example selects a *face* of the
  tool at mark 4 (a "tool body region"), so both are tried.
* ``IBody2.MakeOffset`` is surface-only, so a tool can't simply be grown. What
  worked: per tool body, the union of temporary copies shifted by the
  clearance in 32 directions around the vertical axis, each at +/- the
  clearance along it, plus straight along it (``IBody2.Copy``,
  ``ApplyTransform``, ``Operations2(SWBODYADD)``), made a body of with
  ``IPartDoc.CreateFeatureFromBody3``, then a Combine subtract. 8 directions
  under-cut sharp letter tips (0.02 mm3 overlap); 32 brought it to 0.002 mm3.
  23 bodies x 66 copies took about 100 s.

``IBody2.Operations2`` works on temporary bodies and consumes both (API
help), so every boolean here runs on fresh ``Copy()`` results and the part's
own bodies are never touched by it.

Every zero-argument method (``GetFaceCount``, ``GetBodyBox``, ``Copy``,
``GetFaces``, ``PreSplitBody2``, ``EditRebuild3``) and the math utility's
``CreateTransform`` are flagged before they are called (pitfalls #5, #41,
#42). ``tests/scripts/probe_bodies.py`` runs every COM sequence below on
scratch parts, for someone to run with the user's OK.

The box arithmetic at the top is the mock adapter's geometry: each mock body
stands in for its box.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from ..base import AdapterResult, AdapterResultStatus
from . import limits
from .clearance import (
    TOUCHING_MM,
    InvalidRequest,
    box_distance,
    face_kind,
    nearest_pair,
)
from .com_utils import as_list, strip_op_prefix
from .com_utils import attempt as _attempt
from .com_utils import double_array as _double_array
from .com_utils import flag as _flag

#: ``swBodyType_e.swSolidBody``.
SOLID_BODY = 0
#: ``swBodyOperationType_e`` (swconst, SOLIDWORKS 2026).
BODY_INTERSECT = 15901
BODY_CUT = 15902
BODY_ADD = 15903
#: The tool's operation names, to ``swBodyOperationType_e``.
OPERATIONS = {"add": BODY_ADD, "subtract": BODY_CUT, "common": BODY_INTERSECT}
#: ``swBodyOperationError_e``: no error, no intersection, the same body twice.
NO_ERROR = 0
NO_INTERSECT = 1067
SAME_TOOL_AND_TARGET = 545
#: ``swInsertPartOptions_e``.
INSERT_PART_IMPORT_SOLIDS = 1
INSERT_PART_BREAK_LINK = 512
#: ``swFeatureTreeFolderType_e.swFeatureTreeFolder_Containing``.
FOLDER_CONTAINING = 2
#: ``swDeleteSelectionOptions_e.swDelete_Children``.
DELETE_CHILDREN = 1
#: ``swCreateFeatureBodyOpts_e.swCreateFeatureBodyCheck``.
CREATE_FEATURE_BODY_CHECK = 1
#: Selection marks (API help): a Combine's main body and tools; a Move/Copy
#: body; an Indent's target and tool regions.
MARK_MAIN = 1
MARK_TOOL = 2
MARK_MOVE = 1
MARK_INDENT_TARGET = 1
MARK_INDENT_TOOL = 4
#: ``VARENUM``: the element types of the arrays SolidWorks takes.
VT_BSTR = 8
VT_DISPATCH = 9
#: An Indent's wall thickness (m). It doesn't apply with Cut, but is required.
INDENT_THICKNESS_M = 0.001
#: An intersection smaller than this (mm3) is no overlap.
OVERLAP_MM3 = 1e-6
#: A residual overlap above this (mm3) after a mating negative is reported.
RESIDUAL_WARN_MM3 = 0.01
#: How far (mm) a moved body's box centre may be from the asked shift.
SHIFT_TOLERANCE_MM = 0.01
#: The model axes, by name.
AXES = {"x": 0, "y": 1, "z": 2}

Box = list[float]


# ---- pure pieces ---------------------------------------------------------------


def shift_offsets(
    clearance: float, directions: int, axis: str = "y"
) -> list[tuple[float, float, float]]:
    """Where the copies of a tool go to grow it by ``clearance``.

    The tool itself, then ``directions`` shifts by ``clearance`` evenly
    around ``axis``, each at ``+clearance`` and at ``-clearance`` along it,
    then straight along it both ways: ``2 * directions + 3`` offsets. Their
    union holds every point within ``clearance`` of the tool sideways and
    along the axis, as long as the tool is at least ``clearance`` thick.

    Args:
        clearance: The shift, in any length unit (the offsets come in it).
        directions: How many directions around the axis.
        axis: ``x``, ``y`` or ``z``: the axis the directions ring.

    Returns:
        list[tuple[float, float, float]]: The offsets, the zero offset first.
    """
    a = AXES[axis]
    u, v = (a + 1) % 3, (a + 2) % 3
    offsets = [(0.0, 0.0, 0.0)]
    for sign in (1.0, -1.0):
        for k in range(directions):
            turn = 2.0 * math.pi * k / directions
            offset = [0.0, 0.0, 0.0]
            offset[u] = round(clearance * math.cos(turn), 15) + 0.0
            offset[v] = round(clearance * math.sin(turn), 15) + 0.0
            offset[a] = sign * clearance
            offsets.append((offset[0], offset[1], offset[2]))
    for sign in (1.0, -1.0):
        offset = [0.0, 0.0, 0.0]
        offset[a] = sign * clearance
        offsets.append((offset[0], offset[1], offset[2]))
    return offsets


def translation_data(offset: Sequence[float]) -> list[float]:
    """A ``MathTransform``'s 16 values for a pure shift (metres)."""
    return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, *map(float, offset), 1.0] + [
        0.0
    ] * 3


def rotation_angles(axis: str, degrees: float) -> tuple[float, float, float]:
    """``InsertMoveCopyBody2``'s three angles (radians): a turn about one axis."""
    angles = [0.0, 0.0, 0.0]
    angles[AXES[axis]] = math.radians(degrees)
    return angles[0], angles[1], angles[2]


def missing_bodies_message(missing: Sequence[str], names: Sequence[str]) -> str:
    """The not-found error, naming what the part holds."""
    return (
        f"Body(ies) not found: {', '.join(missing)}. The part's solid bodies: "
        f"{', '.join(names) or 'none'}."
    )


def not_a_part_message(operation: str, kind: str) -> str:
    """The wrong-document error (``wrong_document_type``)."""
    return f"{operation} needs an active part document (active document is {kind})."


def folder_name(tools: Sequence[str]) -> str:
    """The folder a mating negative's features go in."""
    head = str(tools[0]).replace("@", "")
    more = f" and {len(tools) - 1} more" if len(tools) > 1 else ""
    return f"Negative of {head}{more}"


def _mm(values: Sequence[float], scale: float = 1000.0) -> list[float]:
    return [round(float(v) * scale, 4) + 0.0 for v in values]


def box_volume(box: Sequence[float]) -> float:
    """A box's volume; 0 for an empty one."""
    return math.prod(max(0.0, float(box[k + 3]) - float(box[k])) for k in range(3))


def box_overlap(a: Sequence[float], b: Sequence[float]) -> Box | None:
    """The box two boxes share, or None when they don't overlap."""
    lo = [max(float(a[k]), float(b[k])) for k in range(3)]
    hi = [min(float(a[k + 3]), float(b[k + 3])) for k in range(3)]
    if any(lo[k] >= hi[k] for k in range(3)):
        return None
    return lo + hi


def overlap_volume(a: Sequence[float], b: Sequence[float]) -> float:
    """The volume two boxes share."""
    shared = box_overlap(a, b)
    return box_volume(shared) if shared else 0.0


def grow_box(box: Sequence[float], by: float) -> Box:
    """The box grown by ``by`` on every side."""
    return [float(box[k]) - by for k in range(3)] + [
        float(box[k + 3]) + by for k in range(3)
    ]


def translate_box(box: Sequence[float], by: Sequence[float]) -> Box:
    """The box moved by ``by``."""
    return [float(box[k]) + float(by[k % 3]) for k in range(6)]


def union_box(a: Sequence[float], b: Sequence[float]) -> Box:
    """The box around two boxes."""
    return [min(float(a[k]), float(b[k])) for k in range(3)] + [
        max(float(a[k + 3]), float(b[k + 3])) for k in range(3)
    ]


def rotate_box(
    box: Sequence[float], axis: str, degrees: float, about: Sequence[float]
) -> Box:
    """The box around ``box`` turned about an axis through ``about``.

    The turn follows the right-hand rule about the model axis.
    """
    from .clearance import transform_box
    from .placement import rotation_rows_from_deg

    turn = [0.0, 0.0, 0.0]
    turn[AXES[axis]] = degrees
    rows = rotation_rows_from_deg(*turn)
    centred = translate_box(box, [-float(c) for c in about])
    return transform_box(centred, rows, about)


def split_box(box: Sequence[float], axis: int, at: float) -> tuple[Box, Box] | None:
    """The two halves of a box cut at ``at`` along ``axis``, or None if it misses."""
    if not float(box[axis]) < at < float(box[axis + 3]):
        return None
    low, high = list(map(float, box)), list(map(float, box))
    low[axis + 3] = at
    high[axis] = at
    return low, high


def box_inside(inner: Sequence[float], outer: Sequence[float], pad: float) -> bool:
    """Whether ``inner`` lies within ``outer`` grown by ``pad``."""
    return all(float(inner[k]) >= float(outer[k]) - pad for k in range(3)) and all(
        float(inner[k + 3]) <= float(outer[k + 3]) + pad for k in range(3)
    )


def box_center(box: Sequence[float]) -> list[float]:
    """A box's centre."""
    return [(float(box[k]) + float(box[k + 3])) / 2.0 for k in range(3)]


def box_area(box: Sequence[float]) -> float:
    """A box's surface area."""
    x, y, z = (float(box[k + 3]) - float(box[k]) for k in range(3))
    return 2.0 * (x * y + y * z + z * x)


def nearest_points(
    a: Sequence[float], b: Sequence[float]
) -> tuple[list[float], list[float]]:
    """The nearest points of two boxes; where they overlap on an axis, its middle."""
    point_a: list[float] = []
    point_b: list[float] = []
    for k in range(3):
        if float(a[k + 3]) < float(b[k]):
            point_a.append(float(a[k + 3]))
            point_b.append(float(b[k]))
        elif float(b[k + 3]) < float(a[k]):
            point_a.append(float(a[k]))
            point_b.append(float(b[k + 3]))
        else:
            middle = (max(float(a[k]), float(b[k])) + min(a[k + 3], b[k + 3])) / 2
            point_a.append(middle)
            point_b.append(middle)
    return point_a, point_b


# ---- COM: reading bodies --------------------------------------------------------


def _wrapped(adapter: Any, obj: Any, *names: str) -> Any:
    """A dispatch from an array, wrapped for late binding when raw; ``names`` flagged.

    SolidWorks hands some objects back inside arrays as raw ``PyIDispatch``
    (``_as_com`` in ``io.py``); those can't be flagged until wrapped.
    """
    if obj is not None and not hasattr(obj, "_FlagAsMethod"):
        from .io import _dynamic

        obj = _attempt(adapter, lambda: _dynamic.Dispatch(obj), obj)
    _flag(adapter, obj, *names)
    return obj


def solid_bodies(
    adapter: Any, model: Any, visible_only: bool = False
) -> list[tuple[str, Any]]:
    """The part's solid bodies as ``(name, IBody2)``, in SolidWorks' order."""
    _flag(adapter, model, "GetBodies2")
    raw = _attempt(adapter, lambda: model.GetBodies2(SOLID_BODY, visible_only))
    found: list[tuple[str, Any]] = []
    for item in as_list(raw):
        body = _wrapped(adapter, item)
        name = _attempt(adapter, lambda b=body: b.Name)
        if name:
            found.append((str(name), body))
    return found


def body_names(adapter: Any, model: Any) -> list[str]:
    """The names of the part's solid bodies."""
    return [name for name, _body in solid_bodies(adapter, model)]


def find_bodies(adapter: Any, model: Any, names: Sequence[str]) -> dict[str, Any]:
    """The named solid bodies of the part.

    Raises:
        LookupError: A name isn't there; the message lists what is.
    """
    present = solid_bodies(adapter, model)
    by_name = dict(present)
    missing = [n for n in names if n not in by_name]
    if missing:
        raise LookupError(missing_bodies_message(missing, [n for n, _b in present]))
    return {n: by_name[n] for n in names}


def _box_m(adapter: Any, body: Any) -> Box | None:
    """``IBody2.GetBodyBox`` (metres), or None."""
    _flag(adapter, body, "GetBodyBox")
    box = as_list(_attempt(adapter, lambda: body.GetBodyBox()))
    return [float(v) for v in box[:6]] if len(box) >= 6 else None


def _mass_m(adapter: Any, body: Any) -> list[float]:
    """``IBody2.GetMassProperties(1)``: centre, volume, area... (metres)."""
    _flag(adapter, body, "GetMassProperties")
    props = as_list(_attempt(adapter, lambda: body.GetMassProperties(1.0)))
    return [float(v) for v in props] if len(props) >= 5 else []


def volume_mm3(adapter: Any, body: Any) -> float | None:
    """A body's volume in mm3, or None when it can't be read."""
    props = _mass_m(adapter, body)
    return round(props[3] * 1e9, 6) + 0.0 if props else None


def body_facts(adapter: Any, name: str, body: Any) -> dict[str, Any]:
    """What ``list_bodies`` reports for one body.

    Returns:
        dict[str, Any]: ``name``, ``volume_mm3``, ``surface_area_mm2``,
        ``center_of_mass_mm``, ``box_mm`` and ``size_mm`` (``GetBodyBox``,
        approximate), ``face_count`` and ``visible``; a value SolidWorks
        didn't give is None.
    """
    props = _mass_m(adapter, body)
    box = _box_m(adapter, body)
    _flag(adapter, body, "GetFaceCount")
    faces = _attempt(adapter, lambda: body.GetFaceCount())
    visible = _attempt(adapter, lambda: body.Visible)
    return {
        "name": name,
        "volume_mm3": round(props[3] * 1e9, 4) + 0.0 if props else None,
        "surface_area_mm2": round(props[4] * 1e6, 4) + 0.0 if props else None,
        "center_of_mass_mm": _mm(props[:3]) if props else None,
        "box_mm": _mm(box) if box else None,
        "size_mm": (
            [round((box[k + 3] - box[k]) * 1000.0, 4) + 0.0 for k in range(3)]
            if box
            else None
        ),
        "face_count": int(faces) if isinstance(faces, (int, float)) else None,
        "visible": bool(visible) if visible is not None else None,
    }


# ---- COM: selecting, features ---------------------------------------------------


def _clear(adapter: Any, model: Any) -> None:
    _flag(adapter, model, "ClearSelection2")
    _attempt(adapter, lambda: model.ClearSelection2(True))


def _rebuild(adapter: Any, model: Any) -> None:
    # EditRebuild3 takes no arguments: unflagged, late binding reads it as a
    # property and the call does nothing (pitfall #5).
    _flag(adapter, model, "EditRebuild3")
    _attempt(adapter, lambda: model.EditRebuild3())


def _select_by_id(adapter: Any, model: Any, name: str, mark: int) -> bool:
    """``SelectByID2(name, "SOLIDBODY", ...)``: the API help's way to pick a body."""
    from .features import _null_callout

    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "SelectByID2")
    return bool(
        _attempt(
            adapter,
            lambda: extension.SelectByID2(
                name, "SOLIDBODY", 0.0, 0.0, 0.0, True, mark, _null_callout(), 0
            ),
            False,
        )
    )


#: One pick: a label for messages (a body's name, or None for a face), the
#: entity, its mark, and ``body`` or ``face``.
Pick = tuple[str | None, Any, int, str]


def select_marked(adapter: Any, model: Any, picks: Sequence[Pick]) -> None:
    """Select each entity with its mark, after clearing the selection.

    A body is selected with ``IBody2.Select2(True, data)`` (the API help's
    Move Bodies example), falling back to ``SelectByID2`` by name; a face
    with ``IEntity.Select4(True, data)``. ``data`` is a fresh
    ``ISelectData`` with its ``Mark`` set (pitfall #24: ``None`` is a type
    mismatch).

    Raises:
        RuntimeError: Something couldn't be selected; the selection is
            cleared and nothing was changed.
    """
    _clear(adapter, model)
    selection = _attempt(adapter, lambda: model.SelectionManager)
    _flag(adapter, selection, "CreateSelectData")
    missed: list[str] = []
    for label, entity, mark, kind in picks:
        data = _attempt(adapter, lambda: selection.CreateSelectData())
        if data is not None:
            _attempt(adapter, lambda d=data, m=mark: setattr(d, "Mark", m))
        if kind == "face":
            _flag(adapter, entity, "Select4")
            ok = _attempt(adapter, lambda e=entity, d=data: e.Select4(True, d), False)
        else:
            _flag(adapter, entity, "Select2")
            ok = _attempt(adapter, lambda e=entity, d=data: e.Select2(True, d), False)
            if not ok and label:
                ok = _select_by_id(adapter, model, label, mark)
        if not ok:
            missed.append(label or "a tool face")
    if missed:
        _clear(adapter, model)
        raise RuntimeError(
            f"SolidWorks did not select {', '.join(missed)}; nothing was changed."
        )


def feature_manager(adapter: Any, model: Any, *names: str) -> Any:
    """``IModelDoc2.FeatureManager`` with ``names`` flagged.

    Raises:
        RuntimeError: The part has none.
    """
    manager = _attempt(adapter, lambda: model.FeatureManager)
    if manager is None:
        raise RuntimeError("The part has no FeatureManager.")
    _flag(adapter, manager, *names)
    return manager


def feature_name(adapter: Any, feature: Any) -> str | None:
    """``IFeature.Name``, or None."""
    name = _attempt(adapter, lambda: feature.Name) if feature is not None else None
    return str(name) if name else None


def delete_features(adapter: Any, model: Any, features: Sequence[Any]) -> None:
    """Delete features (last first) and what depends on them; then rebuild."""
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "DeleteSelection2")
    for feature in reversed(list(features)):
        _clear(adapter, model)
        _flag(adapter, feature, "Select2")
        if _attempt(adapter, lambda f=feature: f.Select2(False, 0), False):
            _attempt(adapter, lambda: extension.DeleteSelection2(DELETE_CHILDREN))
    _clear(adapter, model)
    _rebuild(adapter, model)


def make_folder(
    adapter: Any, model: Any, features: Sequence[Any], name: str
) -> str | None:
    """Put consecutive features in a new FeatureManager folder named ``name``.

    ``IFeatureManager.InsertFeatureTreeFolder2(swFeatureTreeFolder_Containing)``
    moves the selected features into a new folder (API help: they must be
    consecutive). Returns the folder's name as read back, or None.
    """
    _clear(adapter, model)
    for feature in features:
        _flag(adapter, feature, "Select2")
        _attempt(adapter, lambda f=feature: f.Select2(True, 0))
    manager = feature_manager(adapter, model, "InsertFeatureTreeFolder2")
    folder = _attempt(
        adapter, lambda: manager.InsertFeatureTreeFolder2(FOLDER_CONTAINING)
    )
    _clear(adapter, model)
    if folder is None:
        return None
    _attempt(adapter, lambda: setattr(folder, "Name", name))
    return feature_name(adapter, folder)


# ---- COM: temporary bodies --------------------------------------------------------

#: What a temporary body is asked to do here.
_TEMP_METHODS = ("Copy", "ApplyTransform", "Operations2", "GetMassProperties")


def temp_copy(adapter: Any, body: Any) -> Any:
    """``IBody2.Copy()``: a temporary body, flagged for what is done with it."""
    _flag(adapter, body, "Copy")
    copy = _attempt(adapter, lambda: body.Copy())
    return _wrapped(adapter, copy, *_TEMP_METHODS) if copy is not None else None


def boolean(
    adapter: Any, target: Any, tool: Any, operation: int
) -> tuple[list[Any], int | None, Exception | None]:
    """``target.Operations2(operation, tool, error)`` on two temporary bodies.

    Both are consumed (API help), so pass copies.

    Returns:
        tuple: The resulting bodies (flagged), the ``swBodyOperationError_e``
        code, and the exception when the call raised.
    """
    from .io import _byref_int

    code = _byref_int()
    result, error = adapter._attempt_with_error(
        lambda: target.Operations2(operation, tool, code)
    )
    value = getattr(code, "value", None)
    pieces = [_wrapped(adapter, p, *_TEMP_METHODS) for p in as_list(result) if p]
    return pieces, int(value) if isinstance(value, (int, float)) else None, error


def intersect_mm3(adapter: Any, a: Any, b: Any) -> float:
    """The volume two bodies share (mm3), from copies of both.

    Raises:
        RuntimeError: The bodies couldn't be copied or intersected.
    """
    first, second = temp_copy(adapter, a), temp_copy(adapter, b)
    if first is None or second is None:
        raise RuntimeError("IBody2.Copy gave nothing, so the overlap can't be read.")
    pieces, code, error = boolean(adapter, first, second, BODY_INTERSECT)
    if error is not None:
        raise RuntimeError(f"IBody2.Operations2 (common) failed: {error}")
    if not pieces and code == SAME_TOOL_AND_TARGET:
        return volume_mm3(adapter, a) or 0.0
    return round(sum(volume_mm3(adapter, p) or 0.0 for p in pieces), 6) + 0.0


def grow_tool(
    adapter: Any,
    name: str,
    body: Any,
    offsets: Sequence[Sequence[float]],
    utility: Any,
) -> list[Any]:
    """The union of copies of ``body`` shifted by each offset (metres).

    Each copy is moved with ``ApplyTransform`` (an ``IMathUtility``
    transform) and joined to the union with ``Operations2(SWBODYADD)``.
    ``limits.check`` runs before each copy.

    Returns:
        list[Any]: The temporary bodies of the union (one, for a solid tool).

    Raises:
        RuntimeError: A copy couldn't be made, moved or joined.
    """
    grown: list[Any] = []
    for k, offset in enumerate(offsets):
        limits.check(f"growing {name} by the clearance (copy {k + 1}/{len(offsets)})")
        piece = temp_copy(adapter, body)
        if piece is None:
            raise RuntimeError(f"IBody2.Copy gave nothing for {name}.")
        if any(offset):
            xform = _attempt(
                adapter,
                lambda o=offset: utility.CreateTransform(
                    _double_array(translation_data(o))
                ),
            )
            moved = xform is not None and _attempt(
                adapter, lambda p=piece, x=xform: p.ApplyTransform(x), False
            )
            if not moved:
                raise RuntimeError(
                    f"A copy of {name} could not be shifted "
                    "(IMathUtility.CreateTransform / IBody2.ApplyTransform)."
                )
        if not grown:
            grown = [piece]
            continue
        pieces, code, error = boolean(adapter, grown[0], piece, BODY_ADD)
        if error is not None or not pieces:
            raise RuntimeError(
                f"IBody2.Operations2 (add) could not join copy {k + 1} of {name} "
                f"({error or f'error code {code}'})."
            )
        grown = pieces + grown[1:]
    return grown


def _math_utility(adapter: Any) -> Any:
    """``ISldWorks.GetMathUtility()`` with ``CreateTransform`` flagged (pitfall #41)."""
    utility = _attempt(adapter, lambda: adapter.swApp.GetMathUtility())
    if utility is None:
        raise RuntimeError("ISldWorks.GetMathUtility gave nothing.")
    _flag(adapter, utility, "CreateTransform")
    return utility


# ---- operations -------------------------------------------------------------------


def list_bodies(adapter: Any, visible_only: bool = False) -> dict[str, Any]:
    """The active part's solid bodies, with ``body_facts`` for each."""
    model = adapter.currentModel
    bodies = [
        body_facts(adapter, name, body)
        for name, body in solid_bodies(adapter, model, visible_only)
    ]
    return {"bodies": bodies, "count": len(bodies)}


def _insert_combine(
    adapter: Any,
    model: Any,
    operation: str,
    main: tuple[str, Any],
    tools: Sequence[tuple[str, Any]],
) -> tuple[Any, str]:
    """``InsertCombineFeature`` in the forms that can work, first that does.

    1. preselected: a subtract's main body at mark 1 and tools at mark 2; for
       an add or common every body at mark 2 (the API help's example, and
       the session's common); then ``null`` and an empty array;
    2. for an add or common, the main body at mark 1 instead;
    3. the bodies passed directly (the session's common returned null so).

    Returns:
        tuple[Any, str]: The feature (None when every form gave none) and the
        form that made it.
    """
    from .features import _null_callout
    from .io import _variant_array

    op = OPERATIONS[operation]
    manager = feature_manager(adapter, model, "InsertCombineFeature")
    marks = [(MARK_MAIN, MARK_TOOL)]
    if operation != "subtract":
        marks = [(MARK_TOOL, MARK_TOOL), (MARK_MAIN, MARK_TOOL)]
    for main_mark, tool_mark in marks:
        limits.check("combining bodies")
        select_marked(
            adapter,
            model,
            [(main[0], main[1], main_mark, "body")]
            + [(n, b, tool_mark, "body") for n, b in tools],
        )
        feature = _attempt(
            adapter,
            lambda: manager.InsertCombineFeature(
                op, _null_callout(), _variant_array(VT_DISPATCH, [])
            ),
        )
        _clear(adapter, model)
        if feature is not None:
            return feature, f"preselected (main body at mark {main_mark})"
    if operation == "subtract":
        main_arg, tool_list = main[1], [b for _n, b in tools]
    else:
        main_arg, tool_list = _null_callout(), [main[1], *(b for _n, b in tools)]
    feature = _attempt(
        adapter,
        lambda: manager.InsertCombineFeature(
            op, main_arg, _variant_array(VT_DISPATCH, tool_list)
        ),
    )
    return feature, "bodies passed directly"


def combine_bodies(
    adapter: Any, operation: str, main: str, tools: Sequence[str]
) -> dict[str, Any]:
    """Combine bodies: add, subtract (tools from main) or common.

    Raises:
        InvalidRequest: ``main`` among the tools, or a tool twice.
        LookupError: A body isn't in the part.
        RuntimeError: SolidWorks made no Combine feature.
    """
    if main in tools or len(set(tools)) != len(tools):
        raise InvalidRequest(
            "give the main body once and each tool once, the main body not "
            "among the tools."
        )
    model = adapter.currentModel
    found = find_bodies(adapter, model, [main, *tools])
    before = body_names(adapter, model)
    main_volume = volume_mm3(adapter, found[main])
    feature, form = _insert_combine(
        adapter, model, operation, (main, found[main]), [(t, found[t]) for t in tools]
    )
    if feature is None:
        raise RuntimeError(
            f"SolidWorks made no Combine ({operation}) feature from {main} and "
            f"{', '.join(tools)}, preselected or passed directly. For common the "
            "bodies must overlap; for subtract the tools must cut the main body."
        )
    _rebuild(adapter, model)
    after = solid_bodies(adapter, model)
    inputs = {main, *tools}
    result = [
        body_facts(adapter, n, b) for n, b in after if n not in before or n in inputs
    ]
    return {
        "feature": feature_name(adapter, feature),
        "operation": operation,
        "main": main,
        "tools": list(tools),
        "form": form,
        "bodies_before": before,
        "bodies_after": [n for n, _b in after],
        "result": result,
        "main_volume_before_mm3": main_volume,
        "result_volume_mm3": round(sum(r["volume_mm3"] or 0.0 for r in result), 4)
        + 0.0,
    }


def _move_feature(
    adapter: Any,
    model: Any,
    bodies: Sequence[tuple[str, Any]],
    translate_m: Sequence[float] | None,
    turn: tuple[Sequence[float], tuple[float, float, float]] | None,
    copy: bool,
    copies: int,
) -> str | None:
    """One ``InsertMoveCopyBody2`` on the bodies, selected at mark 1.

    The API help: translation and rotation can't share a feature (the
    rotation wins), so a caller wanting both makes two.

    Raises:
        RuntimeError: SolidWorks made no Move/Copy Body feature.
    """
    select_marked(adapter, model, [(n, b, MARK_MOVE, "body") for n, b in bodies])
    tx, ty, tz = (float(v) for v in translate_m) if translate_m else (0.0, 0.0, 0.0)
    (px, py, pz), (ax, ay, az) = turn if turn else ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    manager = feature_manager(adapter, model, "InsertMoveCopyBody2")
    feature = _attempt(
        adapter,
        lambda: manager.InsertMoveCopyBody2(
            tx, ty, tz, 0.0, px, py, pz, ax, ay, az, bool(copy), int(copies)
        ),
    )
    _clear(adapter, model)
    if feature is None:
        what = "copy" if copy else "move"
        raise RuntimeError(
            f"SolidWorks made no Move/Copy Body feature to {what} "
            f"{', '.join(n for n, _b in bodies)}."
        )
    return feature_name(adapter, feature)


def match_renamed(
    expected: Mapping[str, Box | None], fresh: Mapping[str, Box | None]
) -> dict[str, str]:
    """Pair bodies a feature renamed with their new names.

    Each body in ``expected`` (its box where the feature should have put it)
    takes the name in ``fresh`` whose box centre is nearest, each name once.
    """
    out: dict[str, str] = {}
    free = dict(fresh)
    for old, box in expected.items():
        if not free:
            break
        if box is None:
            name = next(iter(free))
        else:
            centre = box_center(box)
            name = min(
                free,
                key=lambda n: (
                    math.dist(centre, box_center(free[n])) if free[n] else math.inf
                ),
            )
        out[old] = name
        del free[name]
    return out


def _names_now(
    adapter: Any,
    model: Any,
    current: Mapping[str, str],
    expected: Mapping[str, Box | None],
    step_before: Sequence[str],
) -> dict[str, str]:
    """Each moved body's name after a move feature (original name -> name now).

    SolidWorks 2026 renames a body a feature changes after the feature: a
    moved ``Boss-Extrude2[1]`` comes back as ``Body-Move/Copy1`` (probe,
    2026-11-16), so a name that is gone is matched to a new one by where the
    body should be now.
    """
    after = solid_bodies(adapter, model)
    present = {n for n, _b in after}
    out = {o: n for o, n in current.items() if n in present}
    lost = {o: expected.get(o) for o in current if o not in out}
    if lost:
        fresh = {n: _box_m(adapter, b) for n, b in after if n not in step_before}
        out.update(match_renamed(lost, fresh))
    return out


def move_bodies(
    adapter: Any,
    model: Any,
    names: Sequence[str],
    translate_mm: Sequence[float] | None,
    rotate_deg: float,
    axis: str,
    rotate_about_mm: Sequence[float] | None,
    copy: bool,
    copies: int,
) -> dict[str, Any]:
    """Move or copy bodies: a translation, then a turn about an axis.

    With both, the translation (and copy) is one feature and the turn a
    second, applied to what the first made (the copies, for a copy). A moved
    body is renamed after the feature that moved it; ``moved`` gives each
    one's ``name_now``.

    Returns:
        dict[str, Any]: ``features``, ``copy``, ``bodies``, ``new_bodies``
        (the copies' facts), ``moved`` (each moved body's name now and box
        centre before and after, and the shift) and ``bodies_after``.
    """
    translate_m = [float(v) / 1000.0 for v in translate_mm] if translate_mm else None
    if translate_m is not None and not any(translate_m):
        translate_m = None
    if translate_m is None and not rotate_deg:
        raise InvalidRequest("give translate_mm or rotate_deg; nothing would move.")
    found = find_bodies(adapter, model, names)
    before = body_names(adapter, model)
    boxes = {n: _box_m(adapter, found[n]) for n in names}
    expected: dict[str, Box | None] = dict(boxes)
    current = {n: n for n in names}
    features: list[str | None] = []
    targets = list(names)
    if translate_m is not None:
        features.append(
            _move_feature(
                adapter,
                model,
                [(n, found[n]) for n in targets],
                translate_m,
                None,
                copy,
                copies,
            )
        )
        _rebuild(adapter, model)
        if copy:
            targets = [n for n in body_names(adapter, model) if n not in before]
        else:
            expected = {
                n: translate_box(b, translate_m) if b else None
                for n, b in expected.items()
            }
            current = _names_now(adapter, model, current, expected, before)
            targets = [current[n] for n in names if n in current]
    if rotate_deg:
        about = [float(v) / 1000.0 for v in (rotate_about_mm or (0.0, 0.0, 0.0))]
        step_before = body_names(adapter, model)
        now = find_bodies(adapter, model, targets)
        features.append(
            _move_feature(
                adapter,
                model,
                [(n, now[n]) for n in targets],
                None,
                (about, rotation_angles(axis, rotate_deg)),
                copy and translate_m is None,
                copies if translate_m is None else 1,
            )
        )
        _rebuild(adapter, model)
        if not copy:
            expected = {
                n: rotate_box(b, axis, rotate_deg, about) if b else None
                for n, b in expected.items()
            }
            current = _names_now(adapter, model, current, expected, step_before)
    after = solid_bodies(adapter, model)
    by_name = dict(after)
    new = [n for n, _b in after if n not in before]
    moved = []
    if not copy:
        new = [n for n in new if n not in current.values()]
        for n in names:
            was = boxes.get(n)
            name_now = current.get(n)
            body_now = by_name.get(name_now) if name_now else None
            box_now = _box_m(adapter, body_now) if body_now is not None else None
            entry: dict[str, Any] = {"name": n}
            if name_now:
                entry["name_now"] = name_now
            if was and box_now:
                c0, c1 = box_center(was), box_center(box_now)
                entry["center_before_mm"] = _mm(c0)
                entry["center_after_mm"] = _mm(c1)
                entry["center_shift_mm"] = _mm([c1[k] - c0[k] for k in range(3)])
            moved.append(entry)
    return {
        "features": [f for f in features if f],
        "copy": bool(copy),
        "bodies": list(names),
        "new_bodies": [body_facts(adapter, n, by_name[n]) for n in new],
        "moved": moved,
        "bodies_after": [n for n, _b in after],
    }


def insert_part(
    adapter: Any,
    file_path: str,
    configuration: str | None = None,
    translate_mm: Sequence[float] | None = None,
    rotate_deg: float = 0.0,
    axis: str = "z",
    rotate_about_mm: Sequence[float] | None = None,
    break_link: bool = False,
) -> dict[str, Any]:
    """Insert another part's solid bodies into the active part.

    ``IPartDoc.InsertPart3(path, swInsertPartImportSolids, configuration)``
    puts the part at this part's origin (API help); a translation or turn is
    then a Move/Copy Body feature on the new bodies, as the help suggests.

    Raises:
        LookupError: The file doesn't exist.
        InvalidRequest: It isn't a part file, or it is the active part.
        RuntimeError: SolidWorks inserted nothing, or no solid body.
    """
    path = os.path.abspath(str(file_path))
    if not path.lower().endswith(".sldprt"):
        raise InvalidRequest(f"{path} is not a part file (.sldprt).")
    if not os.path.isfile(path):
        raise LookupError(f"Part file not found: {path}")
    model = adapter.currentModel
    own = _attempt(adapter, lambda: adapter._get_attr_or_call(model, "GetPathName"))
    if own and os.path.normcase(os.path.abspath(str(own))) == os.path.normcase(path):
        raise InvalidRequest("a part can't be inserted into itself.")
    before = body_names(adapter, model)
    options = INSERT_PART_IMPORT_SOLIDS | (INSERT_PART_BREAK_LINK if break_link else 0)
    _flag(adapter, model, "InsertPart3")
    feature = _attempt(
        adapter, lambda: model.InsertPart3(path, options, configuration or "")
    )
    if feature is None:
        which = f" (configuration {configuration!r})" if configuration else ""
        raise RuntimeError(
            f"InsertPart3 inserted nothing from {path}{which}; check that the "
            "file opens in SolidWorks and the configuration exists."
        )
    _rebuild(adapter, model)
    added = [n for n in body_names(adapter, model) if n not in before]
    name = feature_name(adapter, feature)
    if not added:
        raise RuntimeError(
            f"InsertPart3 made {name or 'a feature'} but added no solid body: "
            f"{os.path.basename(path)} may hold none."
        )
    move_features: list[str] = []
    if (translate_mm and any(translate_mm)) or rotate_deg:
        moved = move_bodies(
            adapter,
            model,
            added,
            translate_mm,
            rotate_deg,
            axis,
            rotate_about_mm,
            False,
            1,
        )
        move_features = moved["features"]
    found = find_bodies(adapter, model, added)
    return {
        "feature": name,
        "file_path": path,
        "configuration": configuration,
        "bodies_added": added,
        "bodies": [body_facts(adapter, n, found[n]) for n in added],
        "move_features": move_features,
    }


def delete_keep_body(adapter: Any, names: Sequence[str], keep: bool) -> dict[str, Any]:
    """A Body-Delete/Keep feature: delete the bodies, or keep only them.

    The bodies are selected (mark 0, as the API help's example), then
    ``IFeatureManager.InsertDeleteBody2(keep)``.

    Raises:
        InvalidRequest: Keeping every body would delete nothing.
        LookupError: A body isn't in the part.
        RuntimeError: SolidWorks made no feature.
    """
    model = adapter.currentModel
    found = find_bodies(adapter, model, names)
    before = body_names(adapter, model)
    if keep and set(names) >= set(before):
        raise InvalidRequest(
            "keeping every solid body deletes nothing; name the bodies to keep."
        )
    select_marked(adapter, model, [(n, found[n], 0, "body") for n in names])
    manager = feature_manager(adapter, model, "InsertDeleteBody2")
    feature = _attempt(adapter, lambda: manager.InsertDeleteBody2(bool(keep)))
    _clear(adapter, model)
    if feature is None:
        raise RuntimeError(
            f"SolidWorks made no Body-Delete/Keep feature for {', '.join(names)}."
        )
    _rebuild(adapter, model)
    after = body_names(adapter, model)
    expected = [n for n in before if (n not in names) == keep]
    return {
        "feature": feature_name(adapter, feature),
        "keep": bool(keep),
        "bodies": list(names),
        "removed": [n for n in before if n not in after],
        "expected_removed": expected,
        "bodies_after": after,
    }


def _select_split_tool(adapter: Any, plane: str | None, sketch: str | None) -> str:
    """Select the split's tool by name; return the name that resolved.

    Raises:
        LookupError: No plane or sketch of that name.
    """
    from .features import _select_feature_by_name
    from .sketch import plane_candidates_for

    candidates = plane_candidates_for(plane)[1] if plane else [str(sketch)]
    for name in candidates:
        if _select_feature_by_name(adapter, name) is not None:
            return name
    kind = "Plane" if plane else "Sketch"
    raise LookupError(f"{kind} not found: {plane or sketch}")


def split_body(
    adapter: Any, plane: str | None, sketch: str | None, body: str | None
) -> dict[str, Any]:
    """A Split feature: cut bodies along a plane or a sketch, keeping every piece.

    The plane or sketch is selected, ``IFeatureManager.PreSplitBody2()``
    gives the pieces, and ``PostSplitBody2(pieces, False, origins, paths,
    "")`` marks them all, saves none (empty paths) and consumes none (API
    help's Create Split Feature example). With ``body``, only the pieces
    inside that body's box are marked.

    Raises:
        LookupError: The plane, sketch or body isn't there.
        RuntimeError: Nothing was split.
    """
    from .io import _variant_array

    model = adapter.currentModel
    before = body_names(adapter, model)
    outer = None
    if body:
        outer = _box_m(adapter, find_bodies(adapter, model, [body])[body])
    tool = _select_split_tool(adapter, plane, sketch)
    manager = feature_manager(adapter, model, "PreSplitBody2", "PostSplitBody2")
    pieces = [
        _wrapped(adapter, p, "GetBodyBox")
        for p in as_list(_attempt(adapter, lambda: manager.PreSplitBody2()))
        if p
    ]
    _clear(adapter, model)
    if not pieces:
        raise RuntimeError(
            f"{tool} splits no body (PreSplitBody2 gave none); it must cut "
            "through a solid body."
        )
    if outer is not None:
        pad = 1e-5
        pieces = [
            p
            for p in pieces
            if (box := _box_m(adapter, p)) is not None and box_inside(box, outer, pad)
        ]
        if not pieces:
            raise RuntimeError(f"{tool} doesn't split body {body}.")
    count = len(pieces)
    feature = _attempt(
        adapter,
        lambda: manager.PostSplitBody2(
            _variant_array(VT_DISPATCH, pieces),
            False,
            _variant_array(VT_DISPATCH, [None] * count),
            _variant_array(VT_BSTR, [""] * count),
            "",
        ),
    )
    if feature is None:
        raise RuntimeError(f"PostSplitBody2 made no Split feature along {tool}.")
    _rebuild(adapter, model)
    after = solid_bodies(adapter, model)
    return {
        "feature": feature_name(adapter, feature),
        "tool": tool,
        "body": body,
        "pieces": [body_facts(adapter, n, b) for n, b in after if n not in before],
        "bodies_before": before,
        "bodies_after": [n for n, _b in after],
    }


def _faces(adapter: Any, name: str, body: Any) -> list[tuple[Any, Box]]:
    """Each face of the body with its ``IFace2.GetBox`` (metres, padded).

    Raises:
        RuntimeError: No face could be read.
    """
    _flag(adapter, body, "GetFaces")
    faces: list[tuple[Any, Box]] = []
    for face in as_list(_attempt(adapter, lambda: body.GetFaces())):
        limits.check("reading faces for the clearance")
        _flag(adapter, face, "GetBox")
        box = as_list(_attempt(adapter, lambda f=face: f.GetBox()))
        if len(box) >= 6:
            faces.append((face, [float(v) for v in box[:6]]))
    if not faces:
        raise RuntimeError(
            f"No face of body {name!r} could be read (GetFaces or GetBox gave nothing)."
        )
    return faces


def body_clearance(adapter: Any, body_a: str, body_b: str) -> dict[str, Any]:
    """The minimum distance between two solid bodies of the active part.

    Bodies whose boxes meet are first intersected (copies, ``Operations2``):
    a shared volume means they overlap, which reads as touching with
    ``overlap_mm3``. Otherwise the face pairs are measured nearest box first
    with ``IModelDoc2.ClosestDistance`` until none can be nearer
    (``clearance.nearest_pair``; pitfall #46).

    Raises:
        InvalidRequest: The same body twice.
        LookupError: A body isn't in the part.
        RuntimeError: Nothing could be measured.
    """
    from .clearance import _closest

    if body_a == body_b:
        raise InvalidRequest(
            f"body_a and body_b are both {body_a!r}; give two different bodies."
        )
    model = adapter.currentModel
    found = find_bodies(adapter, model, [body_a, body_b])
    a, b = found[body_a], found[body_b]
    box_a, box_b = _box_m(adapter, a), _box_m(adapter, b)
    reply: dict[str, Any] = {"body_a": body_a, "body_b": body_b}
    if box_a is None or box_b is None or box_distance(box_a, box_b) == 0.0:
        overlap = intersect_mm3(adapter, a, b)
        if overlap > OVERLAP_MM3:
            return {
                **reply,
                "clearance_mm": 0.0,
                "touching": True,
                "overlap_mm3": overlap,
                "point_a_mm": None,
                "point_b_mm": None,
                "face_a": None,
                "face_b": None,
                "pairs_checked": 0,
                "pairs_total": 0,
            }
    faces_a, faces_b = _faces(adapter, body_a, a), _faces(adapter, body_b, b)
    _flag(adapter, model, "ClosestDistance")
    nearest = nearest_pair(
        [box for _f, box in faces_a],
        [box for _f, box in faces_b],
        lambda i, j: _closest(adapter, model, faces_a[i][0], faces_b[j][0]),
        touching=TOUCHING_MM / 1000.0,
        check=lambda: limits.check("measuring the clearance between bodies"),
    )
    total = len(faces_a) * len(faces_b)
    if nearest is None:
        raise RuntimeError(
            f"ClosestDistance measured none of the {total} face pairs of "
            f"{body_a} and {body_b}."
        )
    clearance_mm = nearest.distance * 1000.0
    touching = clearance_mm <= TOUCHING_MM
    i, j = nearest.pair
    return {
        **reply,
        "clearance_mm": 0.0 if touching else round(clearance_mm, 6),
        "touching": touching,
        "overlap_mm3": 0.0,
        "point_a_mm": _mm(nearest.point_a),
        "point_b_mm": _mm(nearest.point_b),
        "face_a": {
            "kind": face_kind(adapter, faces_a[i][0]),
            "box_mm": _mm(faces_a[i][1]),
        },
        "face_b": {
            "kind": face_kind(adapter, faces_b[j][0]),
            "box_mm": _mm(faces_b[j][1]),
        },
        "pairs_checked": nearest.pairs_checked,
        "pairs_total": total,
    }


def _residual_mm3(adapter: Any, model: Any, target: str, tools: Sequence[str]) -> float:
    """How much of the tools still lies in the target (mm3)."""
    found = find_bodies(adapter, model, [target, *tools])
    total = 0.0
    for tool in tools:
        limits.check("measuring what the tools still overlap")
        total += intersect_mm3(adapter, found[tool], found[target])
    return round(total, 6) + 0.0


def _try_indent(
    adapter: Any,
    model: Any,
    target: str,
    tools: Sequence[str],
    clearance_m: float,
    volume_before: float | None,
    attempts: list[str],
) -> list[str] | None:
    """An Indent feature cutting the tools with clearance, if SolidWorks makes one.

    ``InsertIndent(thickness, clearance, False, True, True, False)`` with the
    target at mark 1 and the tools' regions at mark 4: first a face of each
    tool (the API help's example), then the tool bodies (the session's form,
    null for tools fully inside the target). A feature that removes nothing
    or leaves the tools overlapping is deleted.

    Returns:
        list[str] | None: The feature's name, or None (``attempts`` says why).
    """
    manager = feature_manager(adapter, model, "InsertIndent")
    for form in ("a face of each tool", "the tool bodies"):
        limits.check("trying an Indent feature")
        picks = _indent_picks(adapter, model, target, tools, form == "the tool bodies")
        if picks is None:
            attempts.append(f"InsertIndent with {form}: a tool has no face to select")
            continue
        try:
            select_marked(adapter, model, picks)
        except RuntimeError as exc:
            attempts.append(f"InsertIndent with {form}: {exc}")
            continue
        feature = _attempt(
            adapter,
            lambda: manager.InsertIndent(
                INDENT_THICKNESS_M, clearance_m, False, True, True, False
            ),
        )
        _clear(adapter, model)
        if feature is None:
            attempts.append(f"InsertIndent with {form} at mark 4 gave no feature")
            continue
        _rebuild(adapter, model)
        name = feature_name(adapter, feature)
        after = volume_mm3(adapter, find_bodies(adapter, model, [target])[target])
        residual = _residual_mm3(adapter, model, target, tools)
        removed = (volume_before or 0.0) - (after or 0.0)
        if removed > OVERLAP_MM3 and residual <= RESIDUAL_WARN_MM3:
            return [name] if name else []
        attempts.append(
            f"InsertIndent with {form} made {name} but it removed "
            f"{round(removed, 6)} mm3 and left {residual} mm3 of overlap; "
            "it was deleted"
        )
        delete_features(adapter, model, [feature])
    return None


def _indent_picks(
    adapter: Any, model: Any, target: str, tools: Sequence[str], whole_bodies: bool
) -> list[Pick] | None:
    """The target at mark 1, and each tool (or its first face) at mark 4.

    Returns:
        list[Pick] | None: The picks; None when a tool has no face.
    """
    found = find_bodies(adapter, model, [target, *tools])
    picks: list[Pick] = [(target, found[target], MARK_INDENT_TARGET, "body")]
    for tool in tools:
        if whole_bodies:
            picks.append((tool, found[tool], MARK_INDENT_TOOL, "body"))
            continue
        _flag(adapter, found[tool], "GetFaces")
        faces = as_list(_attempt(adapter, lambda t=found[tool]: t.GetFaces()))
        if not faces:
            return None
        picks.append((None, faces[0], MARK_INDENT_TOOL, "face"))
    return picks


def _shifted_copies(
    adapter: Any,
    model: Any,
    target: str,
    tools: Sequence[str],
    offsets: Sequence[Sequence[float]],
    before: Sequence[str],
    attempts: list[str],
) -> tuple[list[str], str | None]:
    """The session's workaround: grow each tool, make bodies of it, subtract them.

    Each tool's grown union becomes a body (``CreateFeatureFromBody3``, with
    its check, then without), and one Combine subtract cuts them all from the
    target. The features go in a folder. Anything made before a failure is
    deleted again.

    Returns:
        tuple[list[str], str | None]: The features' names and the folder's.

    Raises:
        RuntimeError: A step failed; nothing of it is left in the part.
    """
    utility = _math_utility(adapter)
    _flag(adapter, model, "CreateFeatureFromBody3")
    made: list[Any] = []
    try:
        found = find_bodies(adapter, model, tools)
        for tool in tools:
            for piece in grow_tool(adapter, tool, found[tool], offsets, utility):
                limits.check(f"making a body of {tool} grown by the clearance")
                feature = _attempt(
                    adapter,
                    lambda p=piece: model.CreateFeatureFromBody3(
                        p, False, CREATE_FEATURE_BODY_CHECK
                    ),
                ) or _attempt(
                    adapter, lambda p=piece: model.CreateFeatureFromBody3(p, False, 0)
                )
                if feature is None:
                    raise RuntimeError(
                        f"CreateFeatureFromBody3 made no body of {tool} grown by "
                        "the clearance."
                    )
                made.append(feature)
        grown = [n for n in body_names(adapter, model) if n not in before]
        if not grown:
            raise RuntimeError(
                "The grown tools made features but no solid body to subtract."
            )
        bodies = find_bodies(adapter, model, [target, *grown])
        combine, form = _insert_combine(
            adapter,
            model,
            "subtract",
            (target, bodies[target]),
            [(n, bodies[n]) for n in grown],
        )
        if combine is None:
            raise RuntimeError(
                f"SolidWorks made no Combine (subtract) of the grown tools from "
                f"{target}."
            )
        made.append(combine)
        attempts.append(f"Combine subtract {form}")
    except BaseException:
        delete_features(adapter, model, made)
        raise
    _rebuild(adapter, model)
    names = [n for n in (feature_name(adapter, f) for f in made) if n]
    return names, make_folder(adapter, model, made, folder_name(tools))


def make_mating_negative(
    adapter: Any,
    target: str,
    tools: Sequence[str],
    clearance_mm: float,
    directions: int = 32,
    axis: str = "y",
    method: str = "auto",
) -> dict[str, Any]:
    """Cut the tools, grown by ``clearance_mm``, out of the target body.

    ``auto`` tries an Indent feature first (``_try_indent``) and falls back
    to shifted copies (``_shifted_copies``); ``indent`` and
    ``shifted_copies`` use one. The tool bodies stay in the part.

    Returns:
        dict[str, Any]: ``method``, ``target``, ``result_body``, ``tools``,
        ``clearance_mm``, ``directions``, ``axis``, ``copies_per_tool``,
        ``volume_before_mm3``, ``volume_after_mm3``, ``volume_removed_mm3``,
        ``residual_overlap_mm3`` (what of the original tools still lies in
        the result; ~0), ``features``, ``folder`` and ``attempts``.

    Raises:
        InvalidRequest: The target among the tools, or a tool twice.
        LookupError: A body isn't in the part.
        RuntimeError: No method cut the negative.
    """
    if target in tools or len(set(tools)) != len(tools):
        raise InvalidRequest(
            "give the target once and each tool once, the target not among the tools."
        )
    model = adapter.currentModel
    found = find_bodies(adapter, model, [target, *tools])
    before = body_names(adapter, model)
    volume_before = volume_mm3(adapter, found[target])
    clearance_m = float(clearance_mm) / 1000.0
    offsets = shift_offsets(clearance_m, int(directions), axis)
    attempts: list[str] = []
    features: list[str] | None = None
    folder: str | None = None
    used = None
    if method in ("auto", "indent"):
        features = _try_indent(
            adapter, model, target, list(tools), clearance_m, volume_before, attempts
        )
        used = "indent" if features is not None else None
    if features is None and method in ("auto", "shifted_copies"):
        features, folder = _shifted_copies(
            adapter, model, target, list(tools), offsets, before, attempts
        )
        used = "shifted_copies"
    if features is None:
        raise RuntimeError(
            "The Indent feature cut nothing: "
            + "; ".join(attempts)
            + ". Call again with method 'shifted_copies' (or 'auto')."
        )
    after_names = body_names(adapter, model)
    result_body = (
        target
        if target in after_names
        else next((n for n in after_names if n not in before), target)
    )
    volume_after = volume_mm3(
        adapter, find_bodies(adapter, model, [result_body])[result_body]
    )
    residual = _residual_mm3(adapter, model, result_body, tools)
    removed = (
        round((volume_before or 0.0) - (volume_after or 0.0), 6) + 0.0
        if volume_before is not None and volume_after is not None
        else None
    )
    return {
        "method": used,
        "target": target,
        "result_body": result_body,
        "tools": list(tools),
        "clearance_mm": float(clearance_mm),
        "directions": int(directions),
        "axis": axis,
        "copies_per_tool": len(offsets) if used == "shifted_copies" else 0,
        "volume_before_mm3": volume_before,
        "volume_after_mm3": volume_after,
        "volume_removed_mm3": removed,
        "residual_overlap_mm3": residual,
        "features": features,
        "folder": folder,
        "attempts": attempts,
    }


# ---- the PyWin32 mixin ----------------------------------------------------------


class SolidWorksBodiesMixin:
    """The multibody-part methods of ``PyWin32Adapter``.

    Each checks that a part is active, then runs its operation through
    ``_handle_com_operation`` (one STA thread, the settle pause, the time
    limit), and drops the ``Error in <op>:`` prefix from expected failures.
    """

    if TYPE_CHECKING:
        currentModel: Any
        swApp: Any

        def _handle_com_operation(
            self, operation_name: str, operation: Callable[..., Any], *args: Any
        ) -> AdapterResult[Any]: ...

    def _run_on_part(
        self, operation: str, work: Callable[[], dict[str, Any]]
    ) -> AdapterResult[dict[str, Any]]:
        """Run ``work`` on the active part, or say why not."""
        from .io import _doc_type

        if not self.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        doc_type = _doc_type(self)
        if doc_type != 1:
            kind = {2: "assembly", 3: "drawing"}.get(doc_type or 0, "unknown")
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=not_a_part_message(operation, kind),
            )
        result = cast(
            "AdapterResult[dict[str, Any]]",
            self._handle_com_operation(operation, work),
        )
        if not result.is_success:
            result.error = strip_op_prefix(result.error, operation)
        return result

    async def list_bodies(
        self, visible_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """The active part's solid bodies (see :func:`list_bodies`)."""
        return self._run_on_part("list_bodies", lambda: list_bodies(self, visible_only))

    async def insert_part(
        self,
        file_path: str,
        configuration: str | None = None,
        translate_mm: list[float] | None = None,
        rotate_deg: float = 0.0,
        axis: str = "z",
        rotate_about_mm: list[float] | None = None,
        break_link: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Insert a part's bodies into the active part (see :func:`insert_part`)."""
        return self._run_on_part(
            "insert_part",
            lambda: insert_part(
                self,
                file_path,
                configuration,
                translate_mm,
                rotate_deg,
                axis,
                rotate_about_mm,
                break_link,
            ),
        )

    async def combine_bodies(
        self, operation: str, main: str, tools: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Combine bodies (see :func:`combine_bodies`)."""
        return self._run_on_part(
            "combine_bodies", lambda: combine_bodies(self, operation, main, tools)
        )

    async def move_copy_body(
        self,
        bodies: list[str],
        translate_mm: list[float] | None = None,
        rotate_deg: float = 0.0,
        axis: str = "z",
        rotate_about_mm: list[float] | None = None,
        copy: bool = False,
        copies: int = 1,
    ) -> AdapterResult[dict[str, Any]]:
        """Move or copy bodies (see :func:`move_bodies`)."""
        return self._run_on_part(
            "move_copy_body",
            lambda: move_bodies(
                self,
                self.currentModel,
                bodies,
                translate_mm,
                rotate_deg,
                axis,
                rotate_about_mm,
                copy,
                copies,
            ),
        )

    async def delete_keep_body(
        self, bodies: list[str], keep: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Delete bodies, or keep only them (see :func:`delete_keep_body`)."""
        return self._run_on_part(
            "delete_keep_body", lambda: delete_keep_body(self, bodies, keep)
        )

    async def split_body(
        self,
        plane: str | None = None,
        sketch: str | None = None,
        body: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Split bodies along a plane or sketch (see :func:`split_body`)."""
        return self._run_on_part(
            "split_body", lambda: split_body(self, plane, sketch, body)
        )

    async def body_clearance(
        self, body_a: str, body_b: str
    ) -> AdapterResult[dict[str, Any]]:
        """The gap between two bodies (see :func:`body_clearance`)."""
        return self._run_on_part(
            "body_clearance", lambda: body_clearance(self, body_a, body_b)
        )

    async def make_mating_negative(
        self,
        target: str,
        tools: list[str],
        clearance_mm: float,
        directions: int = 32,
        axis: str = "y",
        method: str = "auto",
    ) -> AdapterResult[dict[str, Any]]:
        """Cut the tools grown by a clearance (see :func:`make_mating_negative`)."""
        return self._run_on_part(
            "make_mating_negative",
            lambda: make_mating_negative(
                self, target, tools, clearance_mm, directions, axis, method
            ),
        )
