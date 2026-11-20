"""ISO holes from SolidWorks' Hole Wizard (``tools/hole-wizard``, design D2 to D10).

:func:`make_hole` makes a clearance, counterbore, countersink or tapped ISO
metric hole on a flat face, from a standard size, in this order:

1. check the arguments (:func:`check_hole_input`), before SolidWorks is
   touched;
2. find the face at ``face_point`` (``FaceEdgeSelector.face_at_point``): it
   must be flat, and point along ``side`` when that is given (D2);
3. for a modeled thread, read the ``Metric Tap`` library first: the coarse
   pitch gives the ISO basic minor diameter the hole is drilled at (D7);
4. clear the selection and pick the face with ``SelectByRay`` from 0.01 mm
   above the point along its normal, straight into it, and check that one
   thing is selected (D2, pitfall #59);
5. ``IFeatureManager.HoleWizard5`` with the D4 enums and value vector; no
   feature means SolidWorks has no such size;
6. read the sizes SolidWorks used back from ``IWizardHoleFeatureData2`` (D5);
7. dimension the hole's position sketch from the origin (D3). A failure
   there keeps the hole, with ``fully_defined`` false and a ``warning``;
8. for ``thread: "modeled"``, a modeled internal thread from the hole's mouth
   (``threads.make_thread``);
9. ``ForceRebuild3`` and the failing features.

A failure after step 5 deletes the hole (its sketches and thread with it),
rebuilds, and checks that the feature count and the volume are as they were
(D10).

The sizes come from SolidWorks' tables: this module holds the enum values
and the value vectors, and no size table (D5).

Unprobed (tasks section 1, probes 1.1 to 1.11): every ``HoleWizard5`` value
vector below is hjbaard's, and agrees with the API help's Remarks for
``HoleWizard5`` (clearance: fit in ``Value1``; counterbore and countersink:
fit in ``Value4``; straight tap: cosmetic thread in ``Value7``, thread end in
``Value8``). Where the Hole Wizard puts the point, whether its position
sketch can be dimensioned this way, which ``IWizardHoleFeatureData2``
properties hold which sizes, how a through thread's length is read and
whether the cosmetic thread stays beside a modeled one are all for the
probes.

Credit: hjbaard's ``solidworks-mcp`` (MIT, (c) 2026 Henk Jan Baard),
``session.py`` ``add_hole_wizard``, is the reference for the call order and
the value vectors. This code is written fresh.
"""

from __future__ import annotations

import math
import re
from typing import Any

from . import limits, threads
from .com_utils import as_list, selection_count
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: kind -> (``swWzdGeneralHoleTypes_e``, ``swWzdHoleStandardFastenerTypes_e``).
KINDS: dict[str, tuple[int, int]] = {
    "clearance": (2, 144),  # swWzdHole, swStandardISOScrewClearances
    "counterbore": (0, 139),  # swWzdCounterBore, swStandardISOSocketHeadCap
    "countersink": (1, 140),  # swWzdCounterSink, swStandardISOSocketCTSKFlatHead
    "tapped": (4, 147),  # swWzdTap, swStandardISOTappedHole
}
#: ``swWzdHoleStandards_e.swStandardISO``.
STANDARD_ISO = 8
#: ``swWzdHoleScrewClearanceTypes_e``.
FITS = {"close": 0, "normal": 1, "loose": 2}
#: ``swEndConditions_e``.
END_BLIND, END_THROUGH_ALL = 0, 1
#: ``swWzdHoleCosmeticThreadTypes_e.swCosmeticThreadWithCallout``.
COSMETIC_WITH_CALLOUT = 1
#: ``swWzdHoleThreadEndCondition_e``.
THREAD_END_BLIND, THREAD_END_THROUGH_ALL = 0, 1
#: ``swSelectType_e.swSelFACES``.
SEL_FACES = 2
#: ``swConstraintType_e``.
COINCIDENT, HORIZONTAL_POINTS, VERTICAL_POINTS = 9, 25, 26
#: ``swDeleteSelectionOptions_e``: children and absorbed features.
DELETE_CHILDREN_AND_ABSORBED = 1 | 2
#: ``swSetValueInConfiguration_e.swSetValue_InThisConfiguration``.
THIS_CONFIGURATION = 1
#: How far above the face the pick's ray starts (m), and its radius (m).
RAY_START_M = 1e-5
RAY_RADIUS_M = 1e-4
#: A sketch coordinate this close to the origin's (mm) is pinned by a relation.
ON_AXIS_MM = 1e-4
#: The volume the cleanup may leave off (mm3).
VOLUME_SLACK_MM3 = 1e-3
#: ``side`` must agree with the face normal this well (D2).
SIDE_DOT = 0.999

THREAD_CHOICES = ("cosmetic", "modeled")
_SIZE = re.compile(r"^\s*M(\d+(?:\.\d+)?)\s*$")


# ---- pure helpers ---------------------------------------------------------


def _invalid(text: str) -> ValueError:
    return ValueError(f"Invalid request: {text}")


def check_hole_input(
    kind: str,
    size: str,
    depth_mm: float | None = None,
    fit: str | None = None,
    thread: str | None = "cosmetic",
    side: str | None = None,
) -> dict[str, Any]:
    """The arguments, checked and with their defaults (D4, D7).

    Returns:
        dict[str, Any]: ``kind``, ``size``, ``diameter`` (the nominal mm),
        ``depth_mm`` (None for through all), ``fit`` (None for tapped),
        ``thread`` (None unless tapped) and ``side``.

    Raises:
        ValueError: ``Invalid request`` naming what is wrong.
    """
    from .face_selection import side_vector

    if kind not in KINDS:
        raise _invalid(f"kind must be one of {', '.join(KINDS)} (got {kind!r}).")
    match = _SIZE.match(str(size or ""))
    if match is None:
        raise _invalid(
            f"hole size {size!r} is not an ISO metric size; give a bare "
            "M<diameter>, e.g. 'M3' or 'M5' (no pitch)."
        )
    diameter = float(match.group(1))
    if diameter <= 0:
        raise _invalid(f"hole size {size!r} needs a diameter above 0.")
    if depth_mm is not None and (
        isinstance(depth_mm, bool)
        or not isinstance(depth_mm, int | float)
        or depth_mm <= 0
    ):
        raise _invalid(
            f"depth_mm must be above 0 mm (got {depth_mm!r}); leave it out for "
            "a hole through all."
        )
    tapped = kind == "tapped"
    if fit is not None and tapped:
        raise _invalid(
            "fit is for clearance, counterbore and countersink holes, not tapped ones."
        )
    if fit is not None and fit not in FITS:
        raise _invalid(f"fit must be one of {', '.join(FITS)} (got {fit!r}).")
    thread = thread or "cosmetic"
    if thread not in THREAD_CHOICES:
        raise _invalid(f"thread must be 'cosmetic' or 'modeled' (got {thread!r}).")
    if thread == "modeled" and not tapped:
        raise _invalid(
            f"thread 'modeled' is for tapped holes; a {kind} hole has no thread."
        )
    if side is not None:
        try:
            side_vector(side)
        except ValueError as exc:
            raise _invalid(str(exc)) from exc
    return {
        "kind": kind,
        "size": f"M{diameter:g}",
        "diameter": diameter,
        "depth_mm": float(depth_mm) if depth_mm is not None else None,
        "fit": None if tapped else (fit or "normal"),
        "thread": thread if tapped else None,
        "side": side,
    }


def wizard_values(kind: str, fit: str | None, through: bool) -> list[float]:
    """``HoleWizard5``'s ``Value1`` to ``Value12`` (D4; SolidWorks ignores -1).

    - clearance: the fit in ``Value1``; 0 elsewhere through all, -1 blind;
    - counterbore and countersink: the fit in ``Value4``, -1 elsewhere;
    - tapped: the cosmetic thread type in ``Value7`` and the thread end in
      ``Value8``, -1 elsewhere (zeros there cut garbage; without the cosmetic
      thread SolidWorks mills the threaded length at the major diameter).

    Probe 1.1 re-proves each vector with a volume test.
    """
    screw_fit = float(FITS[fit or "normal"]) if kind != "tapped" else -1.0
    if kind == "clearance":
        rest = 0.0 if through else -1.0
        return [screw_fit] + [rest] * 11
    values = [-1.0] * 12
    if kind in ("counterbore", "countersink"):
        values[3] = screw_fit
    elif kind == "tapped":
        values[6] = float(COSMETIC_WITH_CALLOUT)
        values[7] = float(THREAD_END_THROUGH_ALL if through else THREAD_END_BLIND)
    else:
        raise _invalid(f"kind must be one of {', '.join(KINDS)} (got {kind!r}).")
    return values


def _dot(a: Any, b: Any) -> float:
    return float(sum(float(x) * float(y) for x, y in zip(a, b, strict=True)))


def _unit(vector: Any) -> tuple[float, float, float]:
    x, y, z = (float(c) for c in vector)
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0:
        return (0.0, 0.0, 0.0)
    return (x / length, y / length, z / length)


def direction_name(normal: Any) -> str:
    """``(0, 0, 1)`` -> ``+Z``; another direction as its rounded vector."""
    unit = _unit(normal)
    for axis, name in enumerate("XYZ"):
        if abs(abs(unit[axis]) - 1.0) < 1e-6:
            return f"{'+' if unit[axis] > 0 else '-'}{name}"
    return str([round(c, 4) + 0.0 for c in unit])


def check_side(side: str | None, normal: Any) -> None:
    """``side`` must be the way the face points (D2): a check, not a search.

    Raises:
        ValueError: ``Invalid request`` naming the face's direction.
    """
    from .face_selection import side_vector

    if side is None:
        return
    if _dot(side_vector(side), _unit(normal)) >= SIDE_DOT:
        return
    raise _invalid(
        f"the face at the point points {direction_name(normal)}, not {side!r}. "
        "Check face_point, or leave side out."
    )


def in_plane(normal: Any) -> tuple[float, float, float]:
    """A unit vector at right angles to ``normal``."""
    n = _unit(normal)
    helper = (1.0, 0.0, 0.0) if abs(n[0]) < 0.9 else (0.0, 1.0, 0.0)
    cross = (
        n[1] * helper[2] - n[2] * helper[1],
        n[2] * helper[0] - n[0] * helper[2],
        n[0] * helper[1] - n[1] * helper[0],
    )
    return _unit(cross)


def sketch_offsets(axes: dict[str, Any], point_mm: list[float]) -> tuple[float, float]:
    """How far ``point_mm`` is from the origin along the sketch's x and y (mm).

    The origin, seen in a sketch, is its projection on the sketch plane, so the
    distance along sketch x is the point's dot product with sketch x.
    """
    return _dot(point_mm, axes["x"]), _dot(point_mm, axes["y"])


def _mm(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return round(float(value) * 1000.0, 4)


def _deg(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return round(math.degrees(float(value)), 4)


def hole_block(request: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    """The reply's ``hole`` from ``IWizardHoleFeatureData2``'s values (D11).

    Args:
        request: :func:`check_hole_input`'s result.
        raw: The properties as read (metres and radians; None unread).
    """
    block: dict[str, Any] = {
        "standard": "ISO",
        "kind": request["kind"],
        "size": request["size"],
    }
    kind = request["kind"]
    if kind == "tapped":
        cosmetic = raw.get("CosmeticThreadType")
        block.update(
            {
                "tap_drill_diameter_mm": _mm(raw.get("TapDrillDiameter")),
                "thread_diameter_mm": _mm(raw.get("ThreadDiameter")),
                "thread_depth_mm": _mm(raw.get("ThreadDepth")),
                "cosmetic_thread": bool(cosmetic) if cosmetic is not None else None,
                "thread": request["thread"],
            }
        )
        return block
    diameter = next(
        (
            _mm(raw.get(name))
            for name in ("ThruHoleDiameter", "HoleDiameter", "Diameter")
            if _mm(raw.get(name)) is not None
        ),
        None,
    )
    block.update({"fit": request["fit"], "diameter_mm": diameter})
    if kind == "counterbore":
        block["cbore_diameter_mm"] = _mm(raw.get("CounterBoreDiameter"))
        block["cbore_depth_mm"] = _mm(raw.get("CounterBoreDepth"))
    if kind == "countersink":
        block["csink_diameter_mm"] = _mm(raw.get("CounterSinkDiameter"))
        block["csink_angle_deg"] = _deg(raw.get("CounterSinkAngle"))
    return block


#: The sizes each kind must read back, or the hole is removed (D10).
REQUIRED_SIZES = {
    "clearance": ("diameter_mm",),
    "counterbore": ("diameter_mm", "cbore_diameter_mm", "cbore_depth_mm"),
    "countersink": ("diameter_mm", "csink_diameter_mm"),
    "tapped": ("tap_drill_diameter_mm",),
}


def missing_sizes(block: dict[str, Any]) -> list[str]:
    """The sizes of ``block`` its kind needs and SolidWorks didn't give."""
    return [key for key in REQUIRED_SIZES[block["kind"]] if block.get(key) is None]


# ---- COM ------------------------------------------------------------------

_WIZARD_PROPERTIES = (
    "ThruHoleDiameter",
    "HoleDiameter",
    "Diameter",
    "CounterBoreDiameter",
    "CounterBoreDepth",
    "CounterSinkDiameter",
    "CounterSinkAngle",
    "TapDrillDiameter",
    "ThreadDiameter",
    "ThreadDepth",
    "CosmeticThreadType",
)


def has_member(adapter: Any, obj: Any, name: str) -> bool:
    """Whether a dispatch has ``name`` (``GetIDsOfNames``; ``hasattr`` on fakes)."""
    ole = getattr(obj, "_oleobj_", None)
    if ole is None or not hasattr(ole, "GetIDsOfNames"):
        return obj is not None and hasattr(obj, name)
    _found, error = adapter._attempt_with_error(lambda: ole.GetIDsOfNames(name))
    return error is None


def surface_kind(adapter: Any, face: Any) -> str:
    """``a cylinder``, ``a cone``, ... for a face that isn't flat."""
    _flag(adapter, face, "GetSurface")
    surface = _attempt(adapter, lambda: face.GetSurface())
    tests = ("IsCylinder", "IsCone", "IsSphere", "IsTorus")
    _flag(adapter, surface, *tests)
    for test in tests:
        if _attempt(adapter, lambda t=test: getattr(surface, t)(), False):
            return "a " + test[2:].lower()
    return "a curved surface"


def find_face(adapter: Any, point_mm: list[float], side: str | None) -> Any:
    """The flat face at ``point_mm``, checked against ``side`` (D2).

    Raises:
        LookupError: No face there.
        ValueError: ``Invalid request``: not flat, or it points another way.
    """
    from .face_selection import FaceEdgeSelector

    selector = FaceEdgeSelector(adapter)
    face = selector.face_at_point(selector.faces(), list(point_mm))
    if not face.is_planar:
        raise _invalid(
            f"the face at {list(point_mm)} mm is not flat (it is "
            f"{surface_kind(adapter, face.handle)}, e.g. inside an earlier "
            "hole's countersink or bore); a Hole Wizard hole starts on a flat face."
        )
    check_side(side, face.normal)
    return face


def part_volume(adapter: Any, model: Any) -> float | None:
    """The solid bodies' volume, mm3, or None when one doesn't read."""
    from .bodies import solid_bodies, volume_mm3

    total = 0.0
    for _name, body in solid_bodies(adapter, model):
        volume = volume_mm3(adapter, body)
        if volume is None:
            return None
        total += volume
    return total


def pick_face(adapter: Any, model: Any, point_mm: list[float], normal: Any) -> None:
    """Select the face with ``SelectByRay`` from just above the point, into it.

    Raises:
        RuntimeError: Anything but one entity selected; the selection is cleared.
    """
    from .com_utils import clear_selection

    n = _unit(normal)
    start = [
        float(c) / 1000.0 + RAY_START_M * d for c, d in zip(point_mm, n, strict=True)
    ]
    clear_selection(adapter, model)
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "SelectByRay")
    _attempt(
        adapter,
        lambda: extension.SelectByRay(
            start[0], start[1], start[2], -n[0], -n[1], -n[2],
            RAY_RADIUS_M, SEL_FACES, False, 0, 0,
        ),
        False,
    )  # fmt: skip
    count = selection_count(adapter, model)
    if count != 1:
        clear_selection(adapter, model)
        raise RuntimeError(
            f"SolidWorks' ray pick at {list(point_mm)} mm selected {count} "
            "entities, not the one face; nothing was changed."
        )


def wizard_hole(
    adapter: Any, model: Any, request: dict[str, Any], drill_mm: float | None
) -> Any:
    """``HoleWizard5`` on the picked face; the new feature, or None."""
    from .bodies import feature_manager

    manager = feature_manager(adapter, model, "HoleWizard5")
    generic, fastener = KINDS[request["kind"]]
    through = request["depth_mm"] is None
    values = wizard_values(request["kind"], request["fit"], through)
    depth = 0.0 if through else request["depth_mm"] / 1000.0
    diameter = drill_mm / 1000.0 if drill_mm is not None else -1.0
    end = END_THROUGH_ALL if through else END_BLIND
    return _attempt(
        adapter,
        lambda: manager.HoleWizard5(
            generic, STANDARD_ISO, fastener, request["size"], end,
            diameter, depth, -1.0, *values,
            "", False, False, True, False, True, False,
        ),
    )  # fmt: skip


def read_sizes(adapter: Any, feature: Any) -> dict[str, Any]:
    """``IWizardHoleFeatureData2``'s size properties (metres, radians), as read."""
    _flag(adapter, feature, "GetDefinition")
    data = _attempt(adapter, lambda: feature.GetDefinition())
    if data is None:
        return {}
    return {
        name: _attempt(adapter, lambda n=name: getattr(data, n))
        for name in _WIZARD_PROPERTIES
    }


def _sub_features(adapter: Any, feature: Any) -> list[Any]:
    found = []
    _flag(adapter, feature, "GetFirstSubFeature")
    sub = _attempt(adapter, lambda: feature.GetFirstSubFeature())
    while sub is not None and len(found) < 20:
        _flag(adapter, sub, "GetNextSubFeature", "GetTypeName2", "GetSpecificFeature2")
        found.append(sub)
        sub = _attempt(adapter, lambda s=sub: s.GetNextSubFeature())
    return found


def position_sketch(adapter: Any, feature: Any) -> tuple[Any, Any]:
    """The hole's position sketch: a sub-feature sketch of points and no segments.

    Raises:
        RuntimeError: None found.
    """
    for sub in _sub_features(adapter, feature):
        if _attempt(adapter, lambda s=sub: s.GetTypeName2()) != "ProfileFeature":
            continue
        sketch = _attempt(adapter, lambda s=sub: s.GetSpecificFeature2())
        _flag(adapter, sketch, "GetSketchPoints2", "GetSketchSegments")
        points = as_list(_attempt(adapter, lambda k=sketch: k.GetSketchPoints2()))
        segments = as_list(_attempt(adapter, lambda k=sketch: k.GetSketchSegments()))
        if points and not segments:
            return sub, sketch
    raise RuntimeError("the hole has no position sketch of points (probe 1.3)")


def _select(adapter: Any, model: Any, entities: list[Any]) -> None:
    """Select sketch points with ``Select4`` and a real ``SelectData`` (pitfall #24)."""
    from .com_utils import clear_selection

    clear_selection(adapter, model)
    manager = _attempt(adapter, lambda: model.SelectionManager)
    _flag(adapter, manager, "CreateSelectData")
    for entity in entities:
        data = _attempt(adapter, lambda: manager.CreateSelectData())
        _flag(adapter, entity, "Select4")
        if not _attempt(adapter, lambda e=entity, d=data: e.Select4(True, d), False):
            raise RuntimeError("SolidWorks did not select a sketch point")


def _dimension(
    adapter: Any, model: Any, entities: list[Any], method: str, value_mm: float, at: Any
) -> str:
    """A horizontal or vertical dimension between two points, set to ``value_mm``."""
    _select(adapter, model, entities)
    _flag(adapter, model, method)
    display = _attempt(adapter, lambda: getattr(model, method)(*at))
    if display is None:
        raise RuntimeError(f"IModelDoc2.{method} made no dimension")
    _flag(adapter, display, "GetDimension2")
    dimension = _attempt(adapter, lambda: display.GetDimension2(0))
    _flag(adapter, dimension, "SetSystemValue3")
    _attempt(
        adapter,
        lambda: dimension.SetSystemValue3(
            abs(value_mm) / 1000.0, THIS_CONFIGURATION, None
        ),
    )
    name = _attempt(adapter, lambda: dimension.FullName)
    if not name:
        raise RuntimeError(f"the {method} dimension has no name")
    return str(name)


def _relate(adapter: Any, sketch: Any, entities: list[Any], relation: int) -> None:
    from .sketch_relations import relation_manager

    manager = relation_manager(adapter, sketch)
    if _attempt(adapter, lambda: manager.AddRelation(entities, relation)) is None:
        raise RuntimeError("SolidWorks added no sketch relation")


def pin_position(
    adapter: Any, model: Any, feature: Any, point_mm: list[float]
) -> dict[str, Any]:
    """Dimension the hole's point from the origin in its sketch (D3).

    A coordinate on the origin's axis can't take a dimension of 0, so it gets
    a relation (points vertical or horizontal, or coincident for both) and
    no dimension name.

    Raises:
        RuntimeError: Any step failed; the sketch is closed again.
    """
    from .inspection import sketch_axes
    from .sketch_points import origin_point, point_xy
    from .sketch_relations import constrained_status

    sub, sketch = position_sketch(adapter, feature)
    axes = sketch_axes(adapter, sketch)
    if axes is None:
        raise RuntimeError("the position sketch's axes don't read")
    dx, dy = sketch_offsets(axes, point_mm)
    origin_mm = axes["origin_mm"]
    local = (
        _dot([p - o for p, o in zip(point_mm, origin_mm, strict=True)], axes["x"]),
        _dot([p - o for p, o in zip(point_mm, origin_mm, strict=True)], axes["y"]),
    )
    _flag(adapter, sub, "Select2")
    from .com_utils import clear_selection

    clear_selection(adapter, model)
    if not _attempt(adapter, lambda: sub.Select2(False, 0), False):
        raise RuntimeError("SolidWorks did not select the position sketch")
    adapter._get_attr_or_call(model, "EditSketch")
    try:
        points = as_list(_attempt(adapter, lambda: sketch.GetSketchPoints2()))

        def off(point: Any) -> float:
            xy = point_xy(adapter, point) or (math.inf, math.inf)
            return math.hypot(xy[0] * 1000.0 - local[0], xy[1] * 1000.0 - local[1])

        point = min(points, key=off)
        origin = origin_point(adapter)
        if origin is None:
            raise RuntimeError("SolidWorks gave no origin point")
        pair = [origin, point]
        text = [(float(c) + 2.0) / 1000.0 for c in point_mm]
        names: dict[str, str | None] = {"x": None, "y": None}
        x_zero, y_zero = abs(dx) < ON_AXIS_MM, abs(dy) < ON_AXIS_MM
        if x_zero and y_zero:
            _relate(adapter, sketch, pair, COINCIDENT)
        else:
            if x_zero:
                _relate(adapter, sketch, pair, VERTICAL_POINTS)
            else:
                names["x"] = _dimension(
                    adapter, model, pair, "AddHorizontalDimension2", dx, text
                )
            if y_zero:
                _relate(adapter, sketch, pair, HORIZONTAL_POINTS)
            else:
                names["y"] = _dimension(
                    adapter, model, pair, "AddVerticalDimension2", dy, text
                )
    finally:
        manager = _attempt(adapter, lambda: model.SketchManager)
        _flag(adapter, manager, "InsertSketch")
        _attempt(adapter, lambda: manager.InsertSketch(True))
        clear_selection(adapter, model)
    _flag(adapter, model, "ForceRebuild3")
    _attempt(adapter, lambda: model.ForceRebuild3(False))
    status = constrained_status(adapter, sketch)
    return {
        "sketch": str(_attempt(adapter, lambda: sub.Name) or ""),
        "dimensions": names,
        "fully_defined": bool(status.get("is_fully_defined")),
    }


def _circles_near_axis(
    adapter: Any, centre_mm: list[float], normal: Any, diameter: float
) -> list[tuple[float, list[float]]]:
    """Circular edges of ``diameter`` on the axis through ``centre_mm``.

    Returns:
        list: ``(depth below the face (mm), centre)`` for each.
    """
    from .edge_index import index_for

    index = index_for(adapter)
    n = _unit(normal)
    found = []
    for body in index.bodies():
        for edge in index.body_edges(body):
            limits.check("finding the hole's edges")
            info = index.describe(edge)
            if info.get("curve") != "circular" or not info.get("radius_mm"):
                continue
            if abs(2.0 * float(info["radius_mm"]) - diameter) > 0.02:
                continue
            centre = [float(c) for c in info["center_mm"]]
            offset = [c - p for c, p in zip(centre, centre_mm, strict=True)]
            along = _dot(offset, n)
            sideways = [o - along * d for o, d in zip(offset, n, strict=True)]
            if math.sqrt(_dot(sideways, sideways)) <= 0.1:
                found.append((-along, centre))
    return found


def mouth_and_length(
    adapter: Any,
    point_mm: list[float],
    normal: Any,
    diameter: float,
    depth: float | None,
) -> tuple[list[float], float | None]:
    """A point on the hole's mouth edge, and (through all) the bore's length.

    The mouth is the circle of ``diameter`` nearest the face on the hole's
    axis; a through hole's length runs to the farthest such circle
    (probe 1.9 checks this reading).
    """
    circles = _circles_near_axis(adapter, point_mm, normal, diameter)
    mouth = min(circles, key=lambda c: abs(c[0]), default=None)
    centre = mouth[1] if mouth is not None else list(point_mm)
    u = in_plane(normal)
    edge_point = [c + d * diameter / 2.0 for c, d in zip(centre, u, strict=True)]
    if depth is not None:
        return edge_point, depth
    far = max((c[0] for c in circles), default=None)
    return edge_point, (round(far - (mouth[0] if mouth else 0.0), 4) if far else None)


def feature_count(adapter: Any, model: Any) -> int | None:
    """``IFeatureManager.GetFeatureCount(True)``, or None."""
    return threads.feature_count(adapter, model)


def remove_hole(
    adapter: Any, model: Any, feature: Any, count_before: Any, volume_before: Any
) -> str | None:
    """Delete the hole with its sketches and thread, rebuild; what is left, or None (D10)."""
    from .com_utils import clear_selection

    clear_selection(adapter, model)
    _flag(adapter, feature, "Select2")
    extension = _attempt(adapter, lambda: model.Extension)
    _flag(adapter, extension, "DeleteSelection2")
    if _attempt(adapter, lambda: feature.Select2(False, 0), False):
        _attempt(
            adapter, lambda: extension.DeleteSelection2(DELETE_CHILDREN_AND_ABSORBED)
        )
    clear_selection(adapter, model)
    threads.remove_extra(adapter, model, count_before)
    _flag(adapter, model, "ForceRebuild3")
    _attempt(adapter, lambda: model.ForceRebuild3(False))
    left = []
    count = feature_count(adapter, model)
    if count_before is not None and count is not None and count != count_before:
        left.append(f"the tree has {count} features, not {count_before}")
    volume = part_volume(adapter, model)
    if (
        volume_before is not None
        and volume is not None
        and abs(volume - volume_before) > VOLUME_SLACK_MM3
    ):
        left.append(f"the volume is {volume:.3f} mm3, not {volume_before:.3f}")
    return "; ".join(left) or None


def make_hole(
    adapter: Any,
    kind: str,
    size: str,
    face_point: list[float],
    side: str | None = None,
    depth_mm: float | None = None,
    fit: str | None = None,
    thread: str = "cosmetic",
) -> dict[str, Any]:
    """Make one Hole Wizard hole, all or nothing (D2 to D10).

    Returns:
        dict[str, Any]: ``feature``, ``hole``, ``position``, ``face``,
        ``thread`` (modeled only), ``failing_features`` and, when the
        position couldn't be pinned, ``warning``.

    Raises:
        ValueError: ``Invalid request``.
        LookupError: No face at ``face_point``.
        RuntimeError: ``does not support`` (D8), or a step failed after
            the hole was made (it was removed).
    """
    from .equations import failing_features

    request = check_hole_input(kind, size, depth_mm, fit, thread, side)
    model = adapter.currentModel
    manager = _attempt(adapter, lambda: model.FeatureManager)
    if not has_member(adapter, manager, "HoleWizard5"):
        raise RuntimeError(
            f"{threads.revision_text(threads.revision(adapter))} does not support "
            "the Hole Wizard call this tool uses: IFeatureManager has no "
            "HoleWizard5 (SOLIDWORKS 2014 or later)."
        )
    face = find_face(adapter, face_point, side)
    modeled = request["thread"] == "modeled"
    thread_size = drill = None
    if modeled:
        sizes = threads.library_sizes(adapter, model, internal=True)
        thread_size = threads.coarse_size(request["diameter"], sizes)
        if thread_size is None:
            raise _invalid(
                f"SolidWorks' {threads.INTERNAL_LIBRARY!r} thread library has no "
                f"{request['size']} size, so a modeled {request['size']} thread "
                "can't be made."
            )
        drill = threads.minor_diameter(*threads.parse_thread_size(thread_size))
    count_before = feature_count(adapter, model)
    volume_before = part_volume(adapter, model)
    limits.check("making the hole")
    pick_face(adapter, model, face_point, face.normal)
    feature = wizard_hole(adapter, model, request, drill)
    if feature is None:
        from .com_utils import clear_selection

        clear_selection(adapter, model)
        threads.remove_extra(adapter, model, count_before)
        raise _invalid(
            f"SolidWorks made no {request['kind']} hole of size "
            f"{request['size']!r}: the size isn't in its ISO table for that kind."
        )
    try:
        limits.check("reading the hole's sizes")
        block = hole_block(request, read_sizes(adapter, feature))
        missing = missing_sizes(block)
        if missing:
            raise RuntimeError(
                f"SolidWorks gave no {', '.join(missing)} for the hole "
                "(IWizardHoleFeatureData2)"
            )
        warning = None
        limits.check("fixing the hole's position")
        pinned, error = adapter._attempt_with_error(
            lambda: pin_position(adapter, model, feature, face_point)
        )
        if error is not None:
            pinned = {
                "sketch": None,
                "dimensions": {"x": None, "y": None},
                "fully_defined": False,
            }
            warning = (
                "The hole was made, but its position could not be dimensioned "
                f"from the origin ({error}); it sits where the Hole Wizard put "
                "it, within about 0.05 mm of face_point."
            )
        made_thread = None
        if modeled and thread_size is not None and drill is not None:
            limits.check("making the hole's thread")
            depth = block.get("thread_depth_mm") if request["depth_mm"] else None
            edge_point, length = mouth_and_length(
                adapter, face_point, face.normal, drill, depth
            )
            if not length:
                raise RuntimeError("the length of the hole for its thread doesn't read")
            made_thread = threads.make_thread(
                adapter, thread_size, edge_point, length, internal=True
            )
    except Exception as exc:
        left = remove_hole(adapter, model, feature, count_before, volume_before)
        after = (
            f" Cleaning up left something behind: {left}."
            if left
            else " The hole was removed; the model is as it was."
        )
        kind_of = (
            type(exc)
            if isinstance(exc, ValueError | LookupError | limits.OperationStopped)
            else RuntimeError
        )
        raise kind_of(f"{str(exc).rstrip('.')}.{after}") from exc
    _flag(adapter, model, "ForceRebuild3")
    _attempt(adapter, lambda: model.ForceRebuild3(False))
    reply: dict[str, Any] = {
        "feature": str(_attempt(adapter, lambda: feature.Name) or ""),
        "hole": block,
        "position": pinned,
        "face": {
            "center_mm": [round(c * 1000.0, 4) + 0.0 for c in face.center],
            "normal": [round(float(c), 6) + 0.0 for c in face.normal],
        },
        "failing_features": failing_features(adapter, model),
    }
    if made_thread is not None:
        reply["thread"] = made_thread["thread"]
        reply["thread_feature"] = made_thread["feature"]
    if warning:
        reply["warning"] = warning
    return reply


class SolidWorksHolesMixin:
    """``create_hole_wizard_hole`` on ``PyWin32Adapter`` (D12)."""

    async def create_hole_wizard_hole(
        self,
        kind: str,
        size: str,
        face_point: list[float],
        side: str | None = None,
        depth_mm: float | None = None,
        fit: str | None = None,
        thread: str = "cosmetic",
    ) -> Any:
        """Make an ISO Hole Wizard hole on the flat face at ``face_point``.

        Returns:
            AdapterResult[dict[str, Any]]: ``feature``, ``hole``,
            ``position``, ``face``, ``thread`` (modeled), ``failing_features``.
        """
        from ..base import AdapterResult, AdapterResultStatus

        try:
            check_hole_input(kind, size, depth_mm, fit, thread, side)
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        refused = threads.part_guard(self, "create_hole_wizard_hole")
        if refused is not None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=refused)
        return self._handle_com_operation(  # type: ignore[attr-defined]
            "create_hole_wizard_hole",
            lambda: make_hole(
                self, kind, size, face_point, side, depth_mm, fit, thread
            ),
        )
