"""Sketch frames, and axes and planes anywhere: the COM behind the Dipper's cages.

The Dipper build cut 21 faces of hexagonal truss cages, each on a vertical
plane at 30 or 60 degrees through a cell centre. ``create_axis`` took only
the model's x, y and z through the origin, so no plane could turn about a
vertical axis through a cell centre, and no pattern could go round one.

- :func:`open_sketch_frame`: the open sketch's frame, unrounded, for the
  sketch tools' ``coordinates: "model"`` (``ISketch.ModelToSketchTransform``,
  inverted, as ``create_sketch`` reads its ``axes``).
- :func:`read_plane_axes`: a reference plane's frame from
  ``IRefPlane.Transform``, read without opening a sketch. The API help says
  the transform takes a plane from the Front Plane's place to its own; that
  a sketch on it takes the same frame is what the probe checks.
- :func:`axis_through`: a reference axis along a line between two model
  points: a 3D sketch holding that line (``ISketchManager.Insert3DSketch``,
  ``CreateLine`` in model metres, ``AddToDB`` on), then
  ``IModelDoc2.InsertAxis2`` with the line selected.
- :func:`plane_through`: a reference plane through a point, square to a
  direction: the same kind of line from the point along the direction, then
  ``IFeatureManager.InsertRefPlane`` Perpendicular to the line (mark 0) and
  Coincident with its start point (mark 1).

The 3D sketch stays in the tree as the feature's parent. Measured on
SOLIDWORKS 2026 SP4.1 (``tests/scripts/probe_r2_sketching.py``, 29 of 29
checks): the axis lies on its line, the plane passes through its point,
and a plane turned 30 degrees about an axis reads its normal to 6e-6.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from ..base import AdapterResult, AdapterResultStatus
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .plane_frame import (
    OFF_PLANE_TOLERANCE_MM,
    SAME_POINT_MM,
    Frame,
    Vector,
    add,
    distance_to_line,
    dot,
    length,
    scale,
    sub,
    two_points,
    unit,
    vector,
)

#: ``swRefPlaneReferenceConstraints_e``.
_PERPENDICULAR = 2
_COINCIDENT = 4
#: ``IModelDocExtension.DeleteSelection2``: swDelete_Children.
_DELETE_CHILDREN = 1

#: How long the 3D sketch line is drawn along a direction (mm). Any length
#: gives the same axis or plane; this one is easy to see and pick.
LINE_MM = 10.0

#: An axis read back may be this far (mm) off the line asked for.
_READ_BACK_MM = 1e-3


def _model(adapter: Any) -> Any:
    model = adapter.currentModel
    if model is None:
        raise RuntimeError("No active model")
    return model


def _ref_frame(adapter: Any, holder: Any, getter: str) -> Frame | None:
    """The frame of ``holder.<getter>``'s ``IMathTransform`` (None when unread)."""
    transform = _attempt(adapter, lambda: getattr(holder, getter))
    if transform is None:
        return None
    return Frame.from_transform(_attempt(adapter, lambda: transform.ArrayData))


def sketch_frame(adapter: Any, sketch: Any) -> Frame | None:
    """A sketch's frame: ``ModelToSketchTransform`` inverted (None when unread)."""
    transform = _attempt(adapter, lambda: sketch.ModelToSketchTransform)
    if transform is None:
        return None
    # Inverse() takes no arguments: unflagged, late binding reads it as a
    # property (CLAUDE.md, "Method flagging via sw_type_info").
    _flag(adapter, transform, "Inverse")
    inverse = _attempt(adapter, lambda: transform.Inverse())
    if inverse is None:
        return None
    return Frame.from_transform(_attempt(adapter, lambda: inverse.ArrayData))


def open_sketch_frame(adapter: Any) -> dict[str, Any]:
    """The open sketch's frame, unrounded, with its summary.

    Returns:
        dict[str, Any]: ``x``, ``y``, ``normal`` (unit vectors in the model),
        ``origin_mm`` and ``summary``.

    Raises:
        LookupError: No sketch is open.
        RuntimeError: SolidWorks gave no transform for it.
    """
    model = _model(adapter)
    sketch = adapter.currentSketch
    if sketch is None:
        manager = _attempt(adapter, lambda: model.SketchManager)
        sketch = _attempt(adapter, lambda: manager.ActiveSketch) if manager else None
    if sketch is None:
        raise LookupError(
            "No sketch is open: open one with create_sketch or edit_sketch first."
        )
    frame = sketch_frame(adapter, sketch)
    if frame is None:
        raise RuntimeError(
            "SolidWorks gave no transform for the open sketch, so model points "
            "can't be placed on it; give sketch coordinates instead."
        )
    return {**frame.as_dict(), "summary": frame.describe()["summary"]}


def plane_feature(adapter: Any, plane: str) -> tuple[str, Any]:
    """The tree name and ``IFeature`` of a plane, by any name ``create_sketch`` takes.

    Raises:
        LookupError: No such feature.
        ValueError: The feature isn't a plane.
    """
    from .sketch import plane_candidates_for

    model = _model(adapter)
    _key, candidates = plane_candidates_for(plane)
    for name in candidates:
        feature = _attempt(adapter, lambda n=name: model.FeatureByName(n))
        if feature is None:
            continue
        _flag(adapter, feature, "GetTypeName2", "GetSpecificFeature2")
        kind = str(_attempt(adapter, lambda f=feature: f.GetTypeName2()) or "")
        if kind != "RefPlane":
            raise ValueError(
                f"{name!r} is a {kind or 'feature'}, not a plane; "
                "create_reference_plane makes one."
            )
        return name, feature
    raise LookupError(
        f"Plane '{plane}' not found in the part. Use Front, Top or Right, or a "
        "plane made by create_reference_plane (list_features names them)."
    )


def plane_frame(adapter: Any, feature: Any) -> Frame | None:
    """A plane feature's frame, from ``IRefPlane.Transform`` (None when unread)."""
    _flag(adapter, feature, "GetSpecificFeature2")
    ref_plane = _attempt(adapter, lambda: feature.GetSpecificFeature2())
    if ref_plane is None:
        return None
    return _ref_frame(adapter, ref_plane, "Transform")


def read_plane_axes(adapter: Any, plane: str) -> dict[str, Any]:
    """A plane's sketch axes and origin, read without opening a sketch.

    Returns:
        dict[str, Any]: ``plane`` (its tree name), ``axes`` (as
        ``create_sketch`` reports them), ``frame`` (the same, unrounded) and
        ``method`` (``"plane_transform"``).

    Raises:
        LookupError: No such plane.
        ValueError: Not a plane.
        RuntimeError: SolidWorks gave no transform.
    """
    name, feature = plane_feature(adapter, plane)
    frame = plane_frame(adapter, feature)
    if frame is None:
        raise RuntimeError(f"SolidWorks gave no transform for {name!r}.")
    return {
        "plane": name,
        "axes": frame.describe(),
        "frame": frame.as_dict(),
        "method": "plane_transform",
    }


def frame_of_named_plane(adapter: Any, name: str | None) -> Frame | None:
    """The frame of the plane called ``name`` (None when it can't be read)."""
    if not name:
        return None
    feature = _attempt(adapter, lambda: _model(adapter).FeatureByName(name))
    return plane_frame(adapter, feature) if feature is not None else None


@dataclass(frozen=True)
class Line3D:
    """A line drawn in a 3D sketch of its own.

    Attributes:
        sketch: The 3D sketch's tree name (None when it couldn't be read).
        segment: The ``ISketchSegment``.
        start_mm: Where it starts, model mm.
        end_mm: Where it ends, model mm.
    """

    sketch: str | None
    segment: Any
    start_mm: Vector
    end_mm: Vector

    def midpoint_m(self) -> Vector:
        return scale(add(self.start_mm, self.end_mm), 0.0005)

    def start_m(self) -> Vector:
        return scale(self.start_mm, 0.001)


def refuse_open_sketch(adapter: Any) -> None:
    """Refuse while a sketch is open: the 3D sketch would close it.

    Raises:
        RuntimeError: A sketch is open.
    """
    model = _model(adapter)
    _flag(adapter, model, "GetActiveSketch2")
    if (
        adapter.currentSketch is not None
        or _attempt(adapter, lambda: model.GetActiveSketch2()) is not None
    ):
        raise RuntimeError(
            "A sketch is open; exit_sketch first. The axis or plane is built on a "
            "3D sketch of its own, and opening it would close yours."
        )


def _newest_name(adapter: Any) -> str | None:
    """The name of the feature added last (``GetLastFeatureAdded``, else the tree's end)."""
    from .edge_feature_check import newest_feature

    model = _model(adapter)
    feature = newest_feature(adapter, None) or _attempt(
        adapter, lambda: model.FeatureByPositionReverse(0)
    )
    name = _attempt(adapter, lambda: feature.Name) if feature is not None else None
    return str(name) if name else None


def draw_line_3d(adapter: Any, start_mm: Vector, end_mm: Vector) -> Line3D:
    """Draw one line from ``start_mm`` to ``end_mm`` (model mm) in a new 3D sketch.

    ``AddToDB`` keeps the points where they are asked (no inference), and
    is put back as it was. The sketch is closed again before this returns;
    one closed with no line in it is deleted (pitfall #45).

    Raises:
        RuntimeError: SolidWorks opened no 3D sketch or drew no line.
    """
    model = _model(adapter)
    manager = model.SketchManager
    _flag(adapter, manager, "Insert3DSketch", "CreateLine")
    _attempt(adapter, lambda: model.ClearSelection2(True))
    manager.Insert3DSketch(True)
    sketch = _attempt(adapter, lambda: manager.ActiveSketch)
    if sketch is None:
        raise RuntimeError("SolidWorks opened no 3D sketch for the line.")
    name = _attempt(adapter, lambda: sketch.Name)
    added = _attempt(adapter, lambda: manager.AddToDB)
    segment = None
    try:
        _attempt(adapter, lambda: setattr(manager, "AddToDB", True))
        (x1, y1, z1), (x2, y2, z2) = scale(start_mm, 0.001), scale(end_mm, 0.001)
        segment = _attempt(adapter, lambda: manager.CreateLine(x1, y1, z1, x2, y2, z2))
    finally:
        _attempt(adapter, lambda: setattr(manager, "AddToDB", bool(added)))
        manager.Insert3DSketch(True)
    if segment is None:
        raise RuntimeError(
            f"SolidWorks drew no 3D sketch line from {list(start_mm)} to "
            f"{list(end_mm)} mm."
        )
    return Line3D(
        str(name) if name else _newest_name(adapter), segment, start_mm, end_mm
    )


def _select(
    adapter: Any, name: str, kind: str, at_m: Vector, append: bool, mark: int
) -> bool:
    from .features import _null_callout

    model = _model(adapter)
    x, y, z = at_m
    return bool(
        _attempt(
            adapter,
            lambda: model.Extension.SelectByID2(
                name, kind, x, y, z, append, mark, _null_callout(), 0
            ),
            False,
        )
    )


def select_line(adapter: Any, line: Line3D, mark: int, append: bool) -> bool:
    """Select the 3D sketch line: by ``Line1@3DSketch1``, else at its midpoint."""
    _flag(adapter, line.segment, "GetName")
    segment_name = _attempt(adapter, lambda: line.segment.GetName())
    origin: Vector = (0.0, 0.0, 0.0)
    if segment_name and line.sketch:
        full = f"{segment_name}@{line.sketch}"
        if _select(adapter, full, "EXTSKETCHSEGMENT", origin, append, mark):
            return True
    return _select(adapter, "", "EXTSKETCHSEGMENT", line.midpoint_m(), append, mark)


def _select_object(adapter: Any, entity: Any, mark: int, append: bool) -> bool:
    """``entity.Select4(append, data)``, the select data carrying ``mark``."""
    model = _model(adapter)
    manager = _attempt(adapter, lambda: model.SelectionManager)
    if manager is None:
        return False
    _flag(adapter, manager, "CreateSelectData")
    data = _attempt(adapter, lambda: manager.CreateSelectData())
    if data is None:
        return False
    _attempt(adapter, lambda: setattr(data, "Mark", mark))
    _flag(adapter, entity, "Select4")
    return bool(_attempt(adapter, lambda: entity.Select4(append, data), False))


def select_line_start(adapter: Any, line: Line3D, mark: int, append: bool) -> bool:
    """Select the 3D sketch line's start point: the point itself, else where it is.

    The point itself first (``ISketchSegment.GetStartPoint2``, then
    ``Select4`` with the mark). A pick by coordinates (``SelectByID2``) takes
    the first point along the view through them: on the Dish Rack truss
    build, a pick at X = -28.4 took an earlier plane's line start at
    X = +28.4, so the plane was made through +28.4 (SOLIDWORKS 2026).
    """
    _flag(adapter, line.segment, "GetStartPoint2")
    point = _attempt(adapter, lambda: line.segment.GetStartPoint2())
    if point is not None and _select_object(adapter, point, mark, append):
        return True
    return _select(adapter, "", "EXTSKETCHPOINT", line.start_m(), append, mark)


def remove_sketch(adapter: Any, line: Line3D) -> None:
    """Delete the 3D sketch a failed axis or plane left behind (best effort)."""
    if not line.sketch:
        return
    model = _model(adapter)
    feature = _attempt(adapter, lambda: model.FeatureByName(line.sketch))
    if feature is None:
        return
    _attempt(adapter, lambda: model.ClearSelection2(True))
    if _attempt(adapter, lambda: feature.Select2(False, 0), False):
        _attempt(adapter, lambda: model.Extension.DeleteSelection2(_DELETE_CHILDREN))


def _feature_count(adapter: Any) -> int | None:
    from .features import _feature_count as count

    return count(adapter)


def _added(before: int | None, after: int | None, what: str) -> None:
    """Refuse when the tree didn't grow by the sketch and the feature.

    Raises:
        RuntimeError: The count couldn't be read or didn't rise by two.
    """
    if before is None or after is None:
        raise RuntimeError(
            f"The feature count could not be read, so whether {what} was made "
            "is unknown."
        )
    if after < before + 2:
        raise RuntimeError(f"SolidWorks made no {what} from the 3D sketch line.")


def _axis_read_back(
    adapter: Any, name: str | None, start: Vector, end: Vector
) -> dict[str, Any] | None:
    """Where the axis SolidWorks made lies (``IRefAxis.GetRefAxisParams``, metres)."""
    if not name:
        return None
    feature = _attempt(adapter, lambda: _model(adapter).FeatureByName(name))
    if feature is None:
        return None
    _flag(adapter, feature, "GetSpecificFeature2")
    axis = _attempt(adapter, lambda: feature.GetSpecificFeature2())
    if axis is None:
        return None
    _flag(adapter, axis, "GetRefAxisParams")
    params = _attempt(adapter, lambda: axis.GetRefAxisParams())
    try:
        values = [float(v) * 1000.0 for v in params]
    except (TypeError, ValueError):
        return None
    if len(values) < 6:
        return None
    got_start, got_end = (
        (values[0], values[1], values[2]),
        (values[3], values[4], values[5]),
    )
    if length(sub(got_end, got_start)) < SAME_POINT_MM:
        return None
    asked = unit(sub(end, start))
    along = dot(unit(sub(got_end, got_start)), asked)
    off = max(
        distance_to_line(start, got_start, sub(got_end, got_start)),
        distance_to_line(end, got_start, sub(got_end, got_start)),
    )
    return {
        "start_mm": [round(v, 6) + 0.0 for v in got_start],
        "end_mm": [round(v, 6) + 0.0 for v in got_end],
        "on_line": off <= _READ_BACK_MM and abs(abs(along) - 1.0) <= 1e-6,
        "same_sense": along > 0,
    }


def axis_through(adapter: Any, start_mm: Any, end_mm: Any) -> dict[str, Any]:
    """Make a reference axis along the line from ``start_mm`` to ``end_mm``.

    Returns:
        dict[str, Any]: ``name``, ``sketch`` (the 3D sketch holding the
        line), ``point_mm``, ``second_point_mm``, ``direction`` (unit),
        ``read_back`` (where SolidWorks says the axis runs; None when
        unread) and the feature counts.

    Raises:
        ValueError: The points coincide.
        RuntimeError: A sketch is open, or no axis was made; the 3D sketch
            of a failed axis is deleted.
    """
    start, end = two_points(start_mm, end_mm)
    refuse_open_sketch(adapter)
    model = _model(adapter)
    before = _feature_count(adapter)
    line = draw_line_3d(adapter, start, end)
    try:
        _attempt(adapter, lambda: model.ClearSelection2(True))
        if not select_line(adapter, line, 0, False):
            raise RuntimeError(f"The axis line in {line.sketch} could not be selected.")
        _flag(adapter, model, "InsertAxis2")
        _attempt(adapter, lambda: model.InsertAxis2(True))
        _added(before, _feature_count(adapter), "axis")
    except RuntimeError:
        remove_sketch(adapter, line)
        raise
    name = _newest_name(adapter)
    return {
        "name": name,
        "sketch": line.sketch,
        "point_mm": list(start),
        "second_point_mm": list(end),
        "direction": [round(c, 9) + 0.0 for c in unit(sub(end, start))],
        "read_back": _axis_read_back(adapter, name, start, end),
        "features_before": before,
        "features_after": _feature_count(adapter),
    }


def plane_through(adapter: Any, point_mm: Any, normal: Any) -> dict[str, Any]:
    """Make a reference plane through ``point_mm`` square to ``normal``.

    Returns:
        dict[str, Any]: ``name``, ``point_mm``, ``normal`` (unit, as asked),
        ``sketch`` (the 3D sketch holding the line), ``axes`` (the new
        plane's, read back; None when unread), ``normal_matches`` (whether
        its normal points the way asked; None when unread), ``through_point``
        and the feature counts.

    Raises:
        ValueError: The point or normal isn't three numbers, or the normal
            has no length.
        RuntimeError: A sketch is open, no plane was made, or the plane made
            doesn't pass through the point; the 3D sketch of a failed plane
            is deleted with the plane.
    """
    point = vector(point_mm, "point")
    direction = unit(vector(normal, "normal"), "the normal")
    refuse_open_sketch(adapter)
    model = _model(adapter)
    before = _feature_count(adapter)
    line = draw_line_3d(adapter, point, add(point, scale(direction, LINE_MM)))
    try:
        _attempt(adapter, lambda: model.ClearSelection2(True))
        if not select_line(adapter, line, 0, False):
            raise RuntimeError(f"The line in {line.sketch} could not be selected.")
        if not select_line_start(adapter, line, 1, True):
            raise RuntimeError(
                f"The start of the line in {line.sketch} could not be selected."
            )
        from .. import sw_type_info

        manager = sw_type_info.flagged(model.FeatureManager, "IFeatureManager")
        plane = _attempt(
            adapter,
            lambda: manager.InsertRefPlane(
                _PERPENDICULAR, 0.0, _COINCIDENT, 0.0, 0, 0.0
            ),
        )
        _added(before, _feature_count(adapter), "plane")
    except RuntimeError:
        remove_sketch(adapter, line)
        raise
    name = _attempt(adapter, lambda: adapter._get_attr_or_call(plane, "Name"))
    name = str(name) if name else _newest_name(adapter)
    frame = frame_of_named_plane(adapter, name)
    if frame is not None:
        off = frame.to_sketch(point)[2]
        if abs(off) > OFF_PLANE_TOLERANCE_MM:
            # A plane made elsewhere was kept before, with only a warning.
            remove_sketch(adapter, line)
            raise RuntimeError(
                f"SolidWorks made {name} {abs(off):g} mm from point "
                f"{[round(c, 6) + 0.0 for c in point]}, not through it, so it was "
                "deleted. Make the plane from a reference plane with offset (and "
                "flip) instead."
            )
    return {
        "name": name,
        "point_mm": list(point),
        "normal": [round(c, 9) + 0.0 for c in direction],
        "sketch": line.sketch,
        "axes": frame.describe() if frame else None,
        "normal_matches": dot(frame.normal, direction) > 0 if frame else None,
        "through_point": (
            abs(frame.to_sketch(point)[2]) <= OFF_PLANE_TOLERANCE_MM if frame else None
        ),
        "features_before": before,
        "features_after": _feature_count(adapter),
    }


def _run(
    adapter: Any, operation_name: str, operation: Any
) -> AdapterResult[dict[str, Any]]:
    """Run ``operation`` as one COM operation; its errors read as plain sentences."""
    from .com_utils import strip_op_prefix

    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
    result = cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation(operation_name, operation),
    )
    if not result.is_success:
        result.error = strip_op_prefix(result.error, operation_name)
    return result


class SolidWorksReferenceGeometryMixin:
    """Sketch frames, plane axes, and axes and planes through any point."""

    async def get_open_sketch_axes(self) -> AdapterResult[dict[str, Any]]:
        """The open sketch's frame (:func:`open_sketch_frame`)."""
        return _run(self, "get_open_sketch_axes", lambda: open_sketch_frame(self))

    async def plane_axes(self, plane: str) -> AdapterResult[dict[str, Any]]:
        """A plane's sketch axes and origin (:func:`read_plane_axes`)."""
        return _run(self, "plane_axes", lambda: read_plane_axes(self, plane))

    async def create_axis_through(
        self, point_mm: list[float], second_point_mm: list[float]
    ) -> AdapterResult[dict[str, Any]]:
        """A reference axis through two model points (:func:`axis_through`)."""
        return _run(
            self,
            "create_axis_through",
            lambda: axis_through(self, point_mm, second_point_mm),
        )

    async def create_plane_through(
        self, point_mm: list[float], normal: list[float]
    ) -> AdapterResult[dict[str, Any]]:
        """A reference plane through a point, square to ``normal`` (:func:`plane_through`)."""
        return _run(
            self, "create_plane_through", lambda: plane_through(self, point_mm, normal)
        )
