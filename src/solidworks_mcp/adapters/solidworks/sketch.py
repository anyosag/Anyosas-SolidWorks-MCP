"""Sketch-domain mixin for PyWin32 SolidWorks operations."""

from __future__ import annotations

import math
import sys
import time
from typing import Any, cast

from loguru import logger

from .. import sw_type_info as _sw_type_info
from ..base import AdapterResult, AdapterResultStatus
from . import limits, rectangle_check, sketch_points, sketch_relations
from . import sketch_pattern_dimensions as _pattern_dims
from .com_utils import flag as _flag
from .com_utils import strip_op_prefix

# swConstraintType_e values per the official SolidWorks API enum docs
# (SolidWorks.Interop.swconst). The legacy IModelDoc2.SketchAddConstraints
# API takes string identifiers but on SW 2026/3DEXPERIENCE silently no-ops
# without adding the relation. ISketchRelationManager.AddRelation takes
# these integer enum values and works reliably.
RELATION_NAME_MAP: dict[str, int] = {
    "horizontal": 4,
    "vertical": 5,
    "tangent": 6,
    "parallel": 7,
    "perpendicular": 8,
    "coincident": 9,
    "concentric": 10,
    "symmetric": 11,  # swConstraintType_SYMMETRIC — requires entity3 (centerline)
    "equal": 14,  # swConstraintType_SAMELENGTH
    "fix": 17,  # swConstraintType_FIXED
    "collinear": 27,  # swConstraintType_COLINEAR (single-l spelling)
}

# Relations that take a third entity (the centerline of symmetry for now).
# All other relations reject a non-null ``entity3``.
_THREE_ENTITY_RELATIONS: frozenset[str] = frozenset({"symmetric"})

#: ``ISketchManager.CreateArc``'s ``Direction`` argument: +1 draws the arc
#: counter-clockwise from its start point to its end point, -1 clockwise.
ARC_DIRECTION_FLAGS: dict[str, int] = {"ccw": 1, "cw": -1}

#: The most segments one ``add_sketch_outlines`` call draws. A 7 x 7
#: honeycomb is 294 segments; the cap keeps one call from holding SolidWorks
#: for minutes. ``add_sketch_entities`` has the same cap, a rectangle
#: counting as its four lines.
MAX_OUTLINE_SEGMENTS = 2000

#: What ``add_sketch_entities`` draws: each single-entity tool, with the
#: fields an item gives for it, the parameters of that tool's adapter
#: method (sketch mm).
SKETCH_ENTITY_FIELDS: dict[str, tuple[str, ...]] = {
    "add_line": ("x1", "y1", "x2", "y2"),
    "add_circle": ("center_x", "center_y", "radius"),
    "add_rectangle": ("x1", "y1", "x2", "y2"),
    "add_arc": ("center_x", "center_y", "start_x", "start_y", "end_x", "end_y"),
    "add_centerline": ("x1", "y1", "x2", "y2"),
}

#: The prefix of the ids each of those tools registers (``Circle_3``).
SKETCH_ENTITY_PREFIXES: dict[str, str] = {
    "add_line": "Line",
    "add_circle": "Circle",
    "add_rectangle": "Rectangle",
    "add_arc": "Arc",
    "add_centerline": "Centerline",
}


class SolidWorksSketchMixin:
    """Expose sketch creation and editing methods via mixin-local implementation."""

    @staticmethod
    def _adapter(obj: Any) -> Any:
        """Return the runtime adapter object for dynamic attribute access."""
        return cast(Any, obj)

    def _point_xyz(self, point_obj: Any) -> tuple[float, float, float] | None:
        adapter = self._adapter(self)
        return cast(
            tuple[float, float, float] | None,
            adapter._sketch_geometry.point_xyz(point_obj),
        )

    def _set_point_xyz(self, point_obj: Any, x: float, y: float, z: float) -> bool:
        adapter = self._adapter(self)
        return cast(bool, adapter._sketch_geometry.set_point_xyz(point_obj, x, y, z))

    def _read_segment_endpoints(
        self, entity: Any
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        adapter = self._adapter(self)
        return cast(
            tuple[tuple[float, float, float], tuple[float, float, float]] | None,
            adapter._sketch_geometry.read_segment_endpoints(entity),
        )

    def _segment_point_objects(self, entity: Any) -> tuple[Any | None, Any | None]:
        adapter = self._adapter(self)
        return cast(
            tuple[Any | None, Any | None],
            adapter._sketch_geometry.segment_point_objects(entity),
        )

    def _shared_segment_vertex(
        self, entity1: Any, entity2: Any
    ) -> tuple[Any, Any, Any] | None:
        adapter = self._adapter(self)
        return cast(
            tuple[Any, Any, Any] | None,
            adapter._sketch_geometry.shared_segment_vertex(entity1, entity2),
        )

    def _smart_dimension_direction(self, dx: float, dy: float) -> int:
        adapter = self._adapter(self)
        return cast(int, adapter._sketch_geometry.smart_dimension_direction(dx, dy))

    def _single_line_dimension_placement(
        self, entity: Any
    ) -> tuple[float, float, float, int] | None:
        adapter = self._adapter(self)
        return cast(
            tuple[float, float, float, int] | None,
            adapter._sketch_geometry.single_line_dimension_placement(entity),
        )

    def _angular_dimension_placement(
        self, entity1: Any, entity2: Any
    ) -> tuple[float, float, float, int] | None:
        adapter = self._adapter(self)
        return cast(
            tuple[float, float, float, int] | None,
            adapter._sketch_geometry.angular_dimension_placement(entity1, entity2),
        )

    async def create_sketch(self, plane: str) -> AdapterResult[str]:
        return _create_sketch_impl(self, plane)

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return _add_line_impl(self, x1, y1, x2, y2)

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        return _add_circle_impl(self, center_x, center_y, radius)

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return _add_rectangle_impl(self, x1, y1, x2, y2)

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        direction: str = "ccw",
    ) -> AdapterResult[str]:
        return _add_arc_impl(
            self, center_x, center_y, start_x, start_y, end_x, end_y, direction
        )

    async def add_sketch_outlines(
        self, outlines: list[dict[str, Any]]
    ) -> AdapterResult[dict[str, Any]]:
        return _add_sketch_outlines_impl(self, outlines)

    async def add_sketch_entities(
        self, items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return _add_sketch_entities_impl(self, items, rollback_on_error)

    async def add_sketch_text(
        self,
        text: str,
        x: float = 0.0,
        y: float = 0.0,
        height: float | None = None,
        font: str | None = None,
        angle: float = 0.0,
        bold: bool = False,
        italic: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Write text in the open sketch (``sketch_text.add_sketch_text``)."""
        from .sketch_text import add_sketch_text

        if not self._adapter(self).currentSketchManager:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        return _sketch_edit_operation(
            self,
            "add_sketch_text",
            lambda: add_sketch_text(
                self, text, x, y, height, font, angle, bold, italic
            ),
        )

    async def set_sketch_construction(
        self, entity_ids: list[str], construction: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return _set_sketch_construction_impl(self, entity_ids, construction)

    async def edit_sketch(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Open an existing sketch for editing (``feature_editing.open_sketch``)."""
        from .feature_editing import open_sketch

        return _sketch_edit_operation(
            self, "edit_sketch", lambda: open_sketch(self, name)
        )

    async def delete_sketch_entities(
        self, entity_ids: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Delete entities of the open sketch by id."""
        from .feature_editing import delete_sketch_entities

        if not self._adapter(self).currentSketchManager:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        return _sketch_edit_operation(
            self,
            "delete_sketch_entities",
            lambda: delete_sketch_entities(self, entity_ids),
        )

    async def delete_all_sketch_entities(self) -> AdapterResult[dict[str, Any]]:
        """Delete every segment of the open sketch."""
        from .feature_editing import delete_all_sketch_entities

        if not self._adapter(self).currentSketchManager:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        return _sketch_edit_operation(
            self,
            "delete_all_sketch_entities",
            lambda: delete_all_sketch_entities(self),
        )

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        return _add_spline_impl(self, points)

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return _add_centerline_impl(self, x1, y1, x2, y2)

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        return _add_polygon_impl(self, center_x, center_y, radius, sides)

    async def add_ellipse(
        self, center_x: float, center_y: float, major_axis: float, minor_axis: float
    ) -> AdapterResult[str]:
        return _add_ellipse_impl(self, center_x, center_y, major_axis, minor_axis)

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[Any]:
        return _add_sketch_constraint_impl(
            self, entity1, entity2, relation_type, entity3
        )

    async def add_sketch_dimension(
        self, entity1: str, entity2: str | None, dimension_type: str, value: float
    ) -> AdapterResult[Any]:
        return _add_sketch_dimension_impl(self, entity1, entity2, dimension_type, value)

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
        add_dimensions: bool = True,
    ) -> AdapterResult[str]:
        return _sketch_linear_pattern_impl(
            self, entities, direction_x, direction_y, spacing, count, add_dimensions
        )

    async def sketch_circular_pattern(
        self,
        entities: list[str],
        angle: float,
        count: int,
        center_x: float = 0.0,
        center_y: float = 0.0,
        add_dimensions: bool = True,
    ) -> AdapterResult[str]:
        return _sketch_circular_pattern_impl(
            self, entities, angle, count, center_x, center_y, add_dimensions
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        return _sketch_mirror_impl(self, entities, mirror_line)

    async def sketch_offset(
        self, entities: list[str], offset_distance: float, reverse_direction: bool
    ) -> AdapterResult[str]:
        return _sketch_offset_impl(self, entities, offset_distance, reverse_direction)

    async def exit_sketch(self) -> AdapterResult[None]:
        return _exit_sketch_impl(self)

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        return _check_sketch_fully_defined_impl(self, sketch_name)


def _create_sketch_impl(adapter: Any, plane: str) -> AdapterResult[str]:
    """Open a new sketch on a named plane.

    Short names (``"Front"``, ``"Top"``, ``"Right"``, ``"XY"``, ``"XZ"``,
    ``"YZ"``) and the English tree names resolve to the three default planes,
    whose localized names (``"Alzado"``, ``"Planta"``, ...) are tried too. Any
    other name must be a plane in the part. If no candidate can be selected
    by name, ``Extension.SelectByID2`` with entity type ``"PLANE"`` is tried;
    when that fails too, the call fails. It never falls back to another plane.

    When the sketch is successfully opened, ``adapter.currentSketchManager``,
    ``adapter.currentSketch``, ``adapter._sketch_count``, and
    ``adapter._last_sketch_name`` are updated, and ``ISketchManager.AddToDB``
    is set so entities land at exactly the requested coordinates
    (``exit_sketch`` clears it again).

    Args:
        adapter: A fully connected ``PyWin32Adapter`` with a non-``None``
            ``currentModel``.
        plane: A default plane's short or tree name, or the exact name of
            another plane in the FeatureManager tree.

    Returns:
        AdapterResult[str]: On success, ``data`` is the sketch name, and
        ``metadata`` has ``plane`` (the tree name of the plane used) and
        ``axes`` (where sketch x and y point in the model; None when it can't
        be read for a plane other than the default three).

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when no plane
            candidate can be selected.

    Example::

        result = _create_sketch_impl(adapter, "Top")
        print(result.metadata["axes"]["summary"])
        # sketch x = model +X, sketch y = model -Z, normal = model +Y
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _sketch_operation() -> str:
        """Inner COM closure that selects the plane and opens the sketch.

        Tries the plane's names, then ``SelectByID2``. Updates adapter sketch
        state on success, and records the plane used and the axes in
        ``found``.

        Returns:
            str: The sketch name.

        Raises:
            Exception: If no plane candidate could be selected.
        """
        default_key, plane_candidates = plane_candidates_for(plane)
        selected = False
        selection_error = None
        plane_used = plane_candidates[0]

        # Only the names that mean this plane are tried. A plane that isn't
        # in the part is an error: falling back to another plane would put
        # the sketch somewhere the caller didn't ask for.
        for candidate in plane_candidates:
            plane_feature, selection_error_candidate = adapter._attempt_with_error(
                lambda c=candidate: adapter.currentModel.FeatureByName(c)
            )
            if selection_error_candidate:
                selection_error = selection_error_candidate
                continue
            selected = bool(
                plane_feature
                and adapter._attempt(
                    lambda pf=plane_feature: pf.Select2(False, 0), default=False
                )
            )
            if selected:
                plane_used = candidate
                break

        if not selected:
            # SW 2022: callout must be None (empty string causes type mismatch)
            selected, selection_error_candidate = adapter._attempt_with_error(
                lambda: adapter.currentModel.Extension.SelectByID2(
                    plane_candidates[0],
                    "PLANE",
                    0,
                    0,
                    0,
                    False,
                    0,
                    None,
                    0,
                )
            )
            if selection_error_candidate:
                selection_error = selection_error_candidate

        if not selected:
            # The last select's error is the SW 2022 form's "Type mismatch"
            # on SW 2026, not anything about the plane; in the message its
            # HRESULT made the error com_type_mismatch, not not_found.
            if selection_error:
                logger.debug("Selecting plane {!r} failed: {}", plane, selection_error)
            raise Exception(
                f"Plane '{plane}' not found in the part. Use Front, Top "
                "or Right, or a plane made by create_reference_plane "
                "(list_features names them)"
            )

        adapter.currentSketchManager = adapter.currentModel.SketchManager
        adapter._attempt(
            lambda: _sw_type_info.flag_methods(
                adapter.currentSketchManager, "ISketchManager"
            ),
            default=0,
        )
        adapter._reset_sketch_entity_registry()
        try:
            adapter.currentSketch = adapter.currentSketchManager.InsertSketch(True)
        except Exception:
            adapter.currentSketch = adapter.currentSketchManager.InsertSketch()
        # Without AddToDB every Create* point snaps to nearby inferred
        # alignments: an end at x=5.2915 landed on x=5 (vertical with an
        # earlier point at x=5) and a 22 mm ring came out 23 mm. Writing
        # straight to the sketch database keeps the requested coordinates.
        # DisplayWhenAdded stays on: it only defers drawing, which would hide
        # entities from a user watching SolidWorks until the next redraw.
        adapter.currentSketchManager.AddToDB = True

        if not (adapter.currentSketch and hasattr(adapter.currentSketch, "Name")):
            # ISketchManager.ActiveSketch is the documented property for
            # "whatever sketch is open right now" — more reliable than
            # trusting InsertSketch's return value, which some SW versions
            # return as an empty Sub result. Try it before the GetActiveSketch2
            # method fallbacks: a bare ``swApp.ActiveDoc`` is not flagged, so
            # its ``GetActiveSketch2`` resolves to a non-callable and silently
            # yields ``None`` — which forces the synthetic ``Sketch_N``
            # fallback name and makes the sketch unselectable by later
            # features (create_cut_extrude included).
            adapter.currentSketch = (
                adapter._attempt(lambda: adapter.currentSketchManager.ActiveSketch)
                or adapter._attempt(lambda: adapter.currentModel.GetActiveSketch2())
                or adapter._attempt(lambda: adapter.swApp.ActiveDoc.GetActiveSketch2())
            )

        adapter._sketch_count += 1

        if adapter.currentSketch and hasattr(adapter.currentSketch, "Name"):
            sketch_name = str(adapter.currentSketch.Name)
        else:
            sketch_name = f"Sketch_{adapter._sketch_count}"
        adapter._last_sketch_name = sketch_name
        found["plane"] = plane_used
        found["axes"] = _read_sketch_axes(adapter, default_key)
        return sketch_name

    found: dict[str, Any] = {}
    result = cast(
        AdapterResult[str],
        adapter._handle_com_operation("create_sketch", _sketch_operation),
    )
    if result.is_success:
        result.metadata = {**(result.metadata or {}), **found}
    return result


#: The three planes every part starts with, by key: the English tree name
#: first, then the names other language installs give them.
_DEFAULT_PLANE_NAMES: dict[str, tuple[str, ...]] = {
    "front": ("Front Plane", "Alzado", "前视基准面", "前視基準面"),
    "top": ("Top Plane", "Planta", "上视基准面", "上視基準面"),
    "right": ("Right Plane", "Vista lateral", "右视基准面", "右視基準面"),
}

#: What callers may call each default plane. XY, XZ and YZ name the plane
#: that holds those two axes: in SolidWorks the Front Plane is XY, the Top
#: Plane XZ and the Right Plane YZ.
_DEFAULT_PLANE_KEYS: dict[str, str] = {
    "front": "front",
    "front plane": "front",
    "xy": "front",
    "top": "top",
    "top plane": "top",
    "xz": "top",
    "right": "right",
    "right plane": "right",
    "yz": "right",
}

#: Sketch x, sketch y and the plane's normal in model coordinates, for the
#: default planes. Used when the sketch's own transform can't be read.
_DEFAULT_PLANE_AXES: dict[str, tuple[tuple[float, float, float], ...]] = {
    "front": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
    "right": ((0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
}


def plane_candidates_for(plane: str) -> tuple[str | None, list[str]]:
    """The tree names to try for ``plane``, and its default-plane key.

    Args:
        plane: What the caller asked for: ``Front``, ``Top``, ``Right``,
            ``XY``, ``XZ``, ``YZ``, a default plane's English tree name, or
            the name of any other plane in the part.

    Returns:
        tuple[str | None, list[str]]: ``("top", ["Top Plane", "Planta",
        ...])`` for a default plane; ``(None, [plane])`` for any other.
    """
    name = plane.strip()
    key = _DEFAULT_PLANE_KEYS.get(name.lower())
    if key is None:
        return None, [name]
    return key, list(_DEFAULT_PLANE_NAMES[key])


def _axis_label(vector: list[float]) -> str:
    """``+X``, ``-Z`` and so on for an axis-aligned vector, else the numbers."""
    for index, letter in enumerate("XYZ"):
        if abs(abs(vector[index]) - 1.0) < 1e-6:
            return ("+" if vector[index] > 0 else "-") + letter
    return "(" + ", ".join(f"{value:g}" for value in vector) + ")"


def describe_sketch_axes(
    x_axis: tuple[float, ...] | list[float],
    y_axis: tuple[float, ...] | list[float],
    normal: tuple[float, ...] | list[float],
    origin_mm: tuple[float, ...] | list[float],
) -> dict[str, Any]:
    """The reply's ``axes``: where the sketch's x and y point in the model.

    Args:
        x_axis: Sketch x as a unit vector in model coordinates.
        y_axis: Sketch y as a unit vector in model coordinates.
        normal: The sketch plane's normal in model coordinates.
        origin_mm: The sketch origin in model coordinates, mm.

    Returns:
        dict[str, Any]: ``x``, ``y``, ``normal``, ``origin_mm`` (rounded) and
        ``summary``, e.g. ``"sketch x = model +X, sketch y = model -Z"``.
    """

    def _rounded(values: tuple[float, ...] | list[float]) -> list[float]:
        return [round(float(v), 6) + 0.0 for v in values]

    x, y, n = _rounded(x_axis), _rounded(y_axis), _rounded(normal)
    return {
        "x": x,
        "y": y,
        "normal": n,
        "origin_mm": _rounded(origin_mm),
        "summary": (
            f"sketch x = model {_axis_label(x)}, sketch y = model "
            f"{_axis_label(y)}, normal = model {_axis_label(n)}"
        ),
    }


def axes_from_transform(data: Any) -> dict[str, Any] | None:
    """Read the sketch axes from a sketch-to-model ``IMathTransform.ArrayData``.

    SolidWorks transforms apply to row vectors, so the first three rows of
    the rotation block are where the sketch's x, y and z axes land in the
    model; elements 9 to 11 are the translation in metres.

    Args:
        data: ``ArrayData`` of the inverse of ``ISketch.ModelToSketchTransform``
            (16 numbers), or anything else when it couldn't be read.

    Returns:
        dict[str, Any] | None: The axes, or None when ``data`` isn't a
        rotation plus translation.
    """
    try:
        values = [float(v) for v in data]
    except (TypeError, ValueError):
        return None
    if len(values) < 12:
        return None
    x_axis, y_axis, normal = values[0:3], values[3:6], values[6:9]
    for axis in (x_axis, y_axis, normal):
        if abs(math.sqrt(sum(c * c for c in axis)) - 1.0) > 1e-6:
            return None
    origin_mm = [v * 1000.0 for v in values[9:12]]
    return describe_sketch_axes(x_axis, y_axis, normal, origin_mm)


def default_plane_axes(key: str) -> dict[str, Any]:
    """The axes of a default plane, with its origin at the model origin."""
    x_axis, y_axis, normal = _DEFAULT_PLANE_AXES[key]
    return describe_sketch_axes(x_axis, y_axis, normal, (0.0, 0.0, 0.0))


def _read_sketch_axes(adapter: Any, default_key: str | None) -> dict[str, Any] | None:
    """The open sketch's axes in model coordinates.

    Read from the sketch itself (``ModelToSketchTransform``, inverted), so a
    sketch on any plane gets its true axes. A default plane falls back to
    the known table when the transform can't be read; any other plane then
    has none (None), rather than a guess.
    """
    sketch = adapter.currentSketch
    data = None
    if sketch is not None:
        transform = adapter._attempt(lambda: sketch.ModelToSketchTransform)
        if transform is not None:
            # Inverse() takes no arguments: unflagged, late binding reads it
            # as a property (CLAUDE.md, "Method flagging via sw_type_info").
            adapter._attempt(lambda: _sw_type_info.flagged(transform, "IMathTransform"))
            inverse = adapter._attempt(lambda: transform.Inverse())
            if inverse is not None:
                data = adapter._attempt(lambda: inverse.ArrayData)
    axes = axes_from_transform(data)
    if axes is None and default_key is not None:
        axes = default_plane_axes(default_key)
    return axes


def _add_line_impl(
    adapter: Any, x1: float, y1: float, x2: float, y2: float
) -> AdapterResult[str]:
    """Add a straight line segment to the active sketch.

    Calls ``SketchManager.CreateLine`` and registers the resulting entity in
    the adapter\'s internal sketch-entity registry so it can be referenced by
    subsequent dimension and constraint calls.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch
            (``currentSketchManager`` must be non-``None``).
        x1: Start X coordinate in **millimetres**.
        y1: Start Y coordinate in **millimetres**.
        x2: End X coordinate in **millimetres**.
        y2: End Y coordinate in **millimetres**.

    Returns:
        AdapterResult[str]: On success, ``data`` is the registered entity ID
        string (e.g. ``"Line_1"``).  On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreateLine`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_line(adapter, 0, 0, 50, 0)
        print(result.data)  # "Line_1"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    return cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "add_line", lambda: _draw_line(adapter, x1, y1, x2, y2)
        ),
    )


def _draw_line(adapter: Any, x1: float, y1: float, x2: float, y2: float) -> str:
    """Draw a line with ``CreateLine`` and register it, inside a COM operation.

    Shared by ``add_line`` and ``add_sketch_entities``, so both draw and
    register a line the same way.

    Returns:
        str: Registered entity ID for the new line.

    Raises:
        Exception: If ``CreateLine`` returns ``None``.
    """
    line = adapter.currentSketchManager.CreateLine(
        x1 / 1000.0, y1 / 1000.0, 0, x2 / 1000.0, y2 / 1000.0, 0
    )
    if not line:
        raise Exception("Failed to create line")
    entity_id: str = adapter._register_sketch_entity("Line", line)
    # The midpoint, in mm: where a dimension to this line is placed.
    adapter._sketch_entity_centers[entity_id] = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    return entity_id


def _add_circle_impl(
    adapter: Any, center_x: float, center_y: float, radius: float
) -> AdapterResult[str]:
    """Add a circle to the active sketch defined by centre and radius.

    Calls ``SketchManager.CreateCircleByRadius`` and registers the entity.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        center_x: Circle centre X coordinate in **millimetres**.
        center_y: Circle centre Y coordinate in **millimetres**.
        radius: Circle radius in **millimetres**.

    Returns:
        AdapterResult[str]: On success, ``data`` is the registered entity ID
        (e.g. ``"Circle_2"``).  On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreateCircleByRadius`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_circle(adapter, 25.0, 0.0, 10.0)
        print(result.data)  # "Circle_2"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    return cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "add_circle", lambda: _draw_circle(adapter, center_x, center_y, radius)
        ),
    )


def _draw_circle(adapter: Any, center_x: float, center_y: float, radius: float) -> str:
    """Draw a circle with ``CreateCircleByRadius`` and register it.

    Shared by ``add_circle`` and ``add_sketch_entities``; runs inside their
    COM operation.

    Returns:
        str: Registered entity ID for the new circle.

    Raises:
        Exception: If ``CreateCircleByRadius`` returns ``None``.
    """
    circle = adapter.currentSketchManager.CreateCircleByRadius(
        center_x / 1000.0, center_y / 1000.0, 0, radius / 1000.0
    )
    if not circle:
        raise Exception("Failed to create circle")
    entity_id: str = adapter._register_sketch_entity("Circle", circle)
    # The centre, in mm: where a dimension to this circle is placed.
    adapter._sketch_entity_centers[entity_id] = (center_x, center_y)
    return entity_id


def _add_rectangle_impl(
    adapter: Any, x1: float, y1: float, x2: float, y2: float
) -> AdapterResult[str]:
    """Add a corner-defined rectangle to the active sketch.

    Calls ``SketchManager.CreateCornerRectangle`` which returns an array of
    four line entities.  The array is registered as a single composite entity.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        x1: First corner X coordinate in **millimetres**.
        y1: First corner Y coordinate in **millimetres**.
        x2: Opposite corner X coordinate in **millimetres**.
        y2: Opposite corner Y coordinate in **millimetres**.

    Returns:
        AdapterResult[str]: On success, ``data`` is the composite entity ID
        (e.g. ``"Rectangle_3"``).  On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreateCornerRectangle`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_rectangle(adapter, 0, 0, 40, 20)
        print(result.data)  # "Rectangle_3"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    return cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "add_rectangle", lambda: _draw_rectangle(adapter, x1, y1, x2, y2)
        ),
    )


def _draw_rectangle(adapter: Any, x1: float, y1: float, x2: float, y2: float) -> str:
    """Draw a corner rectangle, read it back and register it.

    Shared by ``add_rectangle`` and ``add_sketch_entities``; runs inside
    their COM operation. Sketch inference is off while it is drawn, and the
    four lines are read back against the corners asked (``rectangle_check``).

    Returns:
        str: Registered entity ID for the new rectangle.

    Raises:
        Exception: If ``CreateCornerRectangle`` returns ``None``, or the
            lines it drew, read back, are not the rectangle asked (they are
            deleted again).
    """
    with rectangle_check.inference_off(adapter):
        lines = adapter.currentSketchManager.CreateCornerRectangle(
            x1 / 1000.0, y1 / 1000.0, 0, x2 / 1000.0, y2 / 1000.0, 0
        )
    if not lines:
        raise Exception("Failed to create rectangle")
    drawn = rectangle_check.drawn_extent(adapter, lines)
    asked = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
    wrong = rectangle_check.mismatch(asked, drawn) if drawn else None
    if wrong is not None:
        removed = (
            "it was deleted again"
            if rectangle_check.delete_segments(adapter, lines)
            else "it is still in the sketch; delete it (delete_sketch_entities)"
        )
        raise Exception(
            f"SolidWorks drew the rectangle from {wrong}; {removed}. "
            "add_outlines draws the same corners as four lines."
        )
    entity_id = cast(str, adapter._register_sketch_entity("Rectangle", lines))
    # Like polygons, rectangles register as a SAFEARRAY tuple of segment
    # handles — no single dispatch ``GetCenterPoint`` to recover the
    # geometric centre from later. Stash it now so the rectangle ID is a
    # valid seed for ``sketch_circular_pattern``.
    adapter._sketch_entity_centers[entity_id] = (
        (x1 + x2) / 2.0,
        (y1 + y2) / 2.0,
    )
    return entity_id


def _add_arc_impl(
    adapter: Any,
    center_x: float,
    center_y: float,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    direction: str = "ccw",
) -> AdapterResult[str]:
    """Add a circular arc to the active sketch.

    Calls ``SketchManager.CreateArc`` with its direction flag: ``1`` draws
    counter-clockwise from start to end, ``-1`` clockwise (as seen looking
    at the sketch, x right and y up). All coordinates must lie in the sketch
    plane; the Z component is always forced to ``0``.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        center_x: Arc centre X in **millimetres**.
        center_y: Arc centre Y in **millimetres**.
        start_x: Arc start point X in **millimetres**.
        start_y: Arc start point Y in **millimetres**.
        end_x: Arc end point X in **millimetres**.
        end_y: Arc end point Y in **millimetres**.
        direction: ``"ccw"`` (the default) or ``"cw"``.

    Returns:
        AdapterResult[str]: On success, ``data`` is the registered entity ID
        (e.g. ``"Arc_4"``).  On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreateArc`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_arc(
            adapter,
            center_x=0, center_y=0,
            start_x=10, start_y=0,
            end_x=0, end_y=10,
        )
        print(result.data)  # "Arc_4"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    flag = ARC_DIRECTION_FLAGS.get(direction)
    if flag is None:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=f"direction must be 'ccw' or 'cw', not {direction!r}",
        )
    return cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "add_arc",
            lambda: _draw_arc(
                adapter, center_x, center_y, start_x, start_y, end_x, end_y, flag
            ),
        ),
    )


def _draw_arc(
    adapter: Any,
    center_x: float,
    center_y: float,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    flag: int,
) -> str:
    """Draw an arc with ``CreateArc`` and register it.

    Shared by ``add_arc`` and ``add_sketch_entities``; runs inside their COM
    operation.

    Args:
        adapter: The adapter, with a sketch open.
        center_x: Arc centre X in mm.
        center_y: Arc centre Y in mm.
        start_x: Start point X in mm.
        start_y: Start point Y in mm.
        end_x: End point X in mm.
        end_y: End point Y in mm.
        flag: ``CreateArc``'s direction (``ARC_DIRECTION_FLAGS``).

    Returns:
        str: Registered entity ID for the new arc.

    Raises:
        Exception: If ``CreateArc`` returns ``None``.
    """
    arc = adapter.currentSketchManager.CreateArc(
        center_x / 1000.0,
        center_y / 1000.0,
        0,
        start_x / 1000.0,
        start_y / 1000.0,
        0,
        end_x / 1000.0,
        end_y / 1000.0,
        0,
        flag,
    )
    if not arc:
        raise Exception("Failed to create arc")
    entity_id: str = adapter._register_sketch_entity("Arc", arc)
    adapter._sketch_entity_centers[entity_id] = (center_x, center_y)
    return entity_id


def _sketch_edit_operation(
    adapter: Any, operation_name: str, operation: Any
) -> AdapterResult[dict[str, Any]]:
    """Run a sketch-editing closure; its errors read as plain sentences."""
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")
    result = cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation(operation_name, operation),
    )
    if not result.is_success:
        result.error = strip_op_prefix(result.error, operation_name)
    return result


def _make_construction(adapter: Any, segment: Any, construction: bool) -> bool:
    """Set ``ISketchSegment.ConstructionGeometry`` and read it back.

    Returns:
        bool: True when the segment now has the requested state.
    """
    adapter._attempt(lambda: setattr(segment, "ConstructionGeometry", construction))
    return bool(adapter._attempt(lambda: segment.ConstructionGeometry)) == construction


def _segments_of(entity: Any) -> list[Any]:
    """The segment handles a registered entity holds.

    A rectangle or polygon registers the tuple ``CreateCornerRectangle`` /
    ``CreatePolygon`` returned; every other entity is one segment.
    """
    if isinstance(entity, (list, tuple)):
        return [part for part in entity if part is not None]
    return [entity]


def _remove_drawn(adapter: Any, drawn: list[tuple[str, Any]]) -> bool:
    """Delete what one call drew again: select its segments, then ``EditDelete``.

    Args:
        adapter: The adapter, with the sketch still open.
        drawn: ``(entity id, handle)`` for each entity the call drew; a
            rectangle's handle is its four lines, and an empty id is a
            segment that was never registered.

    Returns:
        bool: True when SolidWorks deleted them (or there was nothing to
        delete); their ids are then dropped from the registry.
    """
    segments = [segment for _, handle in drawn for segment in _segments_of(handle)]
    if not segments:
        return True  # nothing drawn, and nothing selected to delete
    selected = all(
        adapter._select_sketch_entity(segment, index > 0)
        for index, segment in enumerate(segments)
    )
    if not selected:
        return False
    # EditDelete returns nothing; only an exception means it failed.
    if (
        adapter._attempt(lambda: adapter.currentModel.EditDelete(), default=False)
        is False
    ):
        return False
    for entity_id, _ in drawn:
        adapter._sketch_entities.pop(entity_id, None)
        adapter._sketch_entity_centers.pop(entity_id, None)
    return True


def _add_sketch_outlines_impl(
    adapter: Any, outlines: list[dict[str, Any]]
) -> AdapterResult[dict[str, Any]]:
    """Draw whole outlines of lines and arcs in one COM operation.

    Each outline is a chain of segments the tool already resolved: a line
    ``{"type": "line", "start": [x, y], "end": [x, y]}`` or an arc ``{"type":
    "arc", "center": [x, y], "start": [x, y], "end": [x, y], "direction":
    "ccw"}``, in mm, each starting where the last ended. One call is one
    ``_handle_com_operation``, so it pays the settle pause once instead of
    once per segment.

    If SolidWorks refuses a segment, the segments this call drew are deleted
    again (selected, then ``EditDelete``), so a failed call leaves the sketch
    as it was; the error says whether that worked.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        outlines: The outlines, each ``{"segments": [...], "construction":
            bool}``.

    Returns:
        AdapterResult[dict[str, Any]]: ``outlines`` (each with its segment
        ``ids``), ``segment_count``, ``line_count`` and ``arc_count``.
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    total = sum(len(outline.get("segments") or []) for outline in outlines)
    if total == 0:
        return AdapterResult(
            status=AdapterResultStatus.ERROR, error="outlines contain no segments"
        )
    if total > MAX_OUTLINE_SEGMENTS:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=(
                f"{total} segments in one call; the most is "
                f"{MAX_OUTLINE_SEGMENTS}. Split the outlines over several calls"
            ),
        )

    def _create(segment: dict[str, Any]) -> Any:
        start, end = segment["start"], segment["end"]
        if segment["type"] == "arc":
            center = segment["center"]
            return adapter.currentSketchManager.CreateArc(
                center[0] / 1000.0,
                center[1] / 1000.0,
                0,
                start[0] / 1000.0,
                start[1] / 1000.0,
                0,
                end[0] / 1000.0,
                end[1] / 1000.0,
                0,
                ARC_DIRECTION_FLAGS[segment.get("direction", "ccw")],
            )
        return adapter.currentSketchManager.CreateLine(
            start[0] / 1000.0, start[1] / 1000.0, 0, end[0] / 1000.0, end[1] / 1000.0, 0
        )

    def _cleanup(drawn: list[tuple[str, Any]]) -> str:
        if _remove_drawn(adapter, drawn):
            return "the segments this call drew were removed again"
        return (
            f"{len(drawn)} segments this call drew stay in the sketch; undo or "
            "delete them"
        )

    def _outlines_operation() -> dict[str, Any]:
        drawn: list[tuple[str, Any]] = []
        replies: list[dict[str, Any]] = []
        counts = {"line": 0, "arc": 0}
        for outline_index, outline in enumerate(outlines, start=1):
            construction = bool(outline.get("construction", False))
            ids: list[str] = []
            for segment_index, segment in enumerate(outline["segments"], start=1):
                try:
                    limits.check(
                        f"drawing outline {outline_index}, segment {segment_index}"
                    )
                except limits.OperationStopped as stopped:
                    raise Exception(f"{stopped}; {_cleanup(drawn)}") from stopped
                handle, create_error = adapter._attempt_with_error(
                    lambda s=segment: _create(s)
                )
                made = bool(handle) and (
                    not construction or _make_construction(adapter, handle, True)
                )
                if not made:
                    if handle:
                        what = "was drawn but could not be made construction geometry"
                    elif create_error is not None:
                        what = f"was not created ({create_error})"
                    else:
                        what = "was not created"
                    if handle:
                        drawn.append(("", handle))
                    raise Exception(
                        f"Outline {outline_index}, segment {segment_index} "
                        f"({segment['type']} from {segment['start']} to "
                        f"{segment['end']}) {what}; {_cleanup(drawn)}"
                    )
                prefix = "Arc" if segment["type"] == "arc" else "Line"
                entity_id: str = adapter._register_sketch_entity(prefix, handle)
                if segment["type"] == "arc":
                    center = segment["center"]
                    adapter._sketch_entity_centers[entity_id] = (center[0], center[1])
                else:
                    start, end = segment["start"], segment["end"]
                    adapter._sketch_entity_centers[entity_id] = (
                        (start[0] + end[0]) / 2.0,
                        (start[1] + end[1]) / 2.0,
                    )
                drawn.append((entity_id, handle))
                ids.append(entity_id)
                counts[segment["type"]] += 1
            replies.append({"ids": ids, "construction": construction})
        return {
            "outlines": replies,
            "segment_count": counts["line"] + counts["arc"],
            "line_count": counts["line"],
            "arc_count": counts["arc"],
        }

    return cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation("add_sketch_outlines", _outlines_operation),
    )


def sketch_entities_problem(items: Any) -> str | None:
    """Why ``add_sketch_entities`` can't draw ``items``, or None when it can.

    Checked before anything is drawn, by the live adapter and the mock alike,
    so a malformed item never stops a call half-way.

    Args:
        items: The items, each ``{"tool": ..., <fields>}`` as
            ``SKETCH_ENTITY_FIELDS`` lists them.

    Returns:
        str | None: An ``Invalid request: ...`` sentence naming the item, or
        None.
    """
    if not isinstance(items, list | tuple) or not items:
        return "Invalid request: no sketch entities to draw"
    segments = 0
    for index, item in enumerate(items):
        problem = _item_problem(index, item)
        if problem is not None:
            return f"Invalid request: {problem}"
        segments += 4 if item["tool"] == "add_rectangle" else 1
    if segments > MAX_OUTLINE_SEGMENTS:
        return (
            f"Invalid request: {segments} segments in one call (a rectangle is "
            f"four); the most is {MAX_OUTLINE_SEGMENTS}. Split them over several "
            "calls"
        )
    return None


def _item_problem(index: int, item: Any) -> str | None:
    """What is wrong with one ``add_sketch_entities`` item, or None."""
    if not isinstance(item, dict):
        return f"item {index} is not an object"
    tool = item.get("tool")
    fields = SKETCH_ENTITY_FIELDS.get(tool) if isinstance(tool, str) else None
    if fields is None:
        return (
            f"item {index}: tool {tool!r} is not one add_sketch_entities draws; "
            f"give one of {', '.join(SKETCH_ENTITY_FIELDS)}"
        )
    known = {"tool", "label", "construction", *fields}
    if tool == "add_arc":
        known.add("direction")
    unknown = sorted(map(str, set(item) - known))
    if unknown:
        return f"item {index} ({tool}) has no field {', '.join(unknown)}"
    missing = [name for name in fields if name not in item]
    if missing:
        return f"item {index} ({tool}) needs {', '.join(missing)}"
    for name in fields:
        value = item[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            return f"item {index} ({tool}): {name} must be a number, not {value!r}"
    if not isinstance(item.get("construction", False), bool):
        return f"item {index} ({tool}): construction must be true or false"
    if not isinstance(item.get("label", ""), str):
        return f"item {index} ({tool}): label must be text"
    direction = item.get("direction", "ccw")
    if not isinstance(direction, str) or direction not in ARC_DIRECTION_FLAGS:
        return (
            f"item {index} (add_arc): direction must be 'ccw' or 'cw', not "
            f"{direction!r}"
        )
    return None


def _item_name(index: int, item: dict[str, Any]) -> str:
    """How messages name an item: its ``label`` (``circles[2]``), else ``item 2``."""
    label = item.get("label")
    return label if isinstance(label, str) and label else f"item {index}"


def _describe_item(item: dict[str, Any]) -> str:
    """An item's geometry for a message, e.g. ``circle at (10, 5), radius 2``."""
    tool = item["tool"]
    if tool == "add_circle":
        return (
            f"circle at ({item['center_x']:g}, {item['center_y']:g}), "
            f"radius {item['radius']:g}"
        )
    if tool == "add_arc":
        return (
            f"{item.get('direction', 'ccw')} arc about ({item['center_x']:g}, "
            f"{item['center_y']:g}) from ({item['start_x']:g}, "
            f"{item['start_y']:g}) to ({item['end_x']:g}, {item['end_y']:g})"
        )
    return (
        f"{SKETCH_ENTITY_PREFIXES[tool].lower()} from ({item['x1']:g}, "
        f"{item['y1']:g}) to ({item['x2']:g}, {item['y2']:g})"
    )


def _draw_item(adapter: Any, item: dict[str, Any]) -> str:
    """Draw one item as its single tool draws it; the id it registered."""
    tool = item["tool"]
    values = [float(item[name]) for name in SKETCH_ENTITY_FIELDS[tool]]
    if tool == "add_circle":
        return _draw_circle(adapter, *values)
    if tool == "add_rectangle":
        return _draw_rectangle(adapter, *values)
    if tool == "add_arc":
        cx, cy, sx, sy, ex, ey = values
        flag = ARC_DIRECTION_FLAGS[item.get("direction", "ccw")]
        return _draw_arc(adapter, cx, cy, sx, sy, ex, ey, flag)
    if tool == "add_centerline":
        return _draw_centerline(adapter, *values)
    return _draw_line(adapter, *values)


def _entities(count: int) -> str:
    """``1 entity``, ``3 entities``."""
    return f"{count} {'entity' if count == 1 else 'entities'}"


def _id_span(ids: list[str]) -> str:
    """Ids for a message: all of a few, else the first and last."""
    return ", ".join(ids) if len(ids) <= 6 else f"{ids[0]} ... {ids[-1]}"


def _sentences(*parts: str | None) -> str:
    """The parts given, as sentences each ending in one full stop."""
    return " ".join(part.rstrip(" .;") + "." for part in parts if part)


def _add_sketch_entities_impl(
    adapter: Any, items: list[dict[str, Any]], rollback_on_error: bool = True
) -> AdapterResult[dict[str, Any]]:
    """Draw lines, circles, rectangles, arcs and centrelines in one COM operation.

    Each item is ``{"tool": <tool>, <fields>}``: one of the single-entity
    tools with the parameters of its adapter method (``SKETCH_ENTITY_FIELDS``,
    sketch mm), plus ``construction`` (ignored for a centreline, which
    already is), ``direction`` for an arc, and ``label``, how messages name
    it. The items are drawn in order, each by the ``_draw_*`` function its
    single tool uses (the same ids, registry entries and rectangle
    read-back), inside one ``_handle_com_operation``: one settle gap for the
    lot instead of one per entity. Pitfall #26 protects the gap between
    operations, not the calls inside one, as ``add_sketch_outlines`` does.

    When an item fails (SolidWorks refuses it, it won't become construction
    geometry, or the call runs out of time), nothing after it is drawn. With
    ``rollback_on_error`` (the default, as ``add_sketch_outlines``) what this
    call drew is deleted again, so a failed call leaves the sketch as it was.
    Without it, the items before the failure stay, as separate calls would
    have left them, and an entity drawn but not made construction stays as
    normal geometry, as ``add_line`` with ``construction`` leaves it.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        items: The entities to draw, in order.
        rollback_on_error: Delete what this call drew when an item fails.

    Returns:
        AdapterResult[dict[str, Any]]: ``items``, one ``{"index", "tool",
        "id", "execution_time"}`` per item (``id`` as the item's single tool
        returns it), and ``count``. On a failure the error names the item,
        and ``data`` has ``items`` (those still in the sketch),
        ``failed_index`` (None when no item failed, e.g. a dialog),
        ``failed`` (its name), ``error`` (what went wrong with it),
        ``removed`` and, when the failed item's entity is in the sketch,
        ``failed_id``.
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    problem = sketch_entities_problem(items)
    if problem is not None:
        return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)

    drawn: list[dict[str, Any]] = []  # each drawn item's result, in order
    handles: list[tuple[str, Any]] = []  # every entity this call drew
    failure: dict[str, Any] = {}

    def _stop(
        index: int, said: str, error: str, failed_id: str | None = None
    ) -> Exception:
        """Record a failed item, remove what this call drew if asked; the error."""
        failure.update(index=index, failed=_item_name(index, items[index]))
        failure["error"] = error
        removed = (
            rollback_on_error and bool(handles) and _remove_drawn(adapter, handles)
        )
        failure["removed"] = removed
        if failed_id is not None and not removed:
            failure["failed_id"] = failed_id
        notes: list[str | None] = []
        if removed:
            notes.append(
                f"The {_entities(len(handles))} this call drew "
                f"{'was' if len(handles) == 1 else 'were'} removed again"
            )
        elif rollback_on_error and handles:
            ids = [entity_id for entity_id, _ in handles]
            notes.append(
                f"The {_entities(len(ids))} this call drew "
                f"{'stays' if len(ids) == 1 else 'stay'} in the sketch "
                f"({_id_span(ids)}); delete them (delete_sketch_entities) or undo"
            )
        else:
            if failed_id is not None:
                notes.append(f"{failed_id} stays in the sketch as normal geometry")
            if drawn:
                notes.append(
                    f"The {_entities(len(drawn))} drawn before it "
                    f"{'stays' if len(drawn) == 1 else 'stay'} in the sketch"
                )
        return Exception(_sentences(said, *notes))

    def _entities_operation() -> dict[str, Any]:
        for index, item in enumerate(items):
            name = _item_name(index, item)
            try:
                limits.check(f"drawing {name}")
            except limits.OperationStopped as stopped:
                raise _stop(index, str(stopped), str(stopped)) from stopped
            started = time.perf_counter()
            entity_id, error = adapter._attempt_with_error(
                lambda i=item: _draw_item(adapter, i)
            )
            if entity_id is None:  # each _draw_* raises rather than return None
                cause = str(error)
                said = f"{name} ({_describe_item(item)}) was not created: {cause}"
                raise _stop(index, said, cause)
            handle = adapter._sketch_entities.get(entity_id)
            handles.append((entity_id, handle))
            wanted = bool(item.get("construction")) and item["tool"] != "add_centerline"
            if wanted and not all(
                _make_construction(adapter, segment, True)
                for segment in _segments_of(handle)
            ):
                raise _stop(
                    index,
                    f"{name} ({_describe_item(item)}) was drawn as {entity_id} but "
                    "could not be made construction geometry",
                    f"SolidWorks did not make {entity_id} construction geometry",
                    failed_id=entity_id,
                )
            drawn.append(
                {
                    "index": index,
                    "tool": item["tool"],
                    "id": entity_id,
                    "execution_time": time.perf_counter() - started,
                }
            )
        return {"items": list(drawn), "count": len(drawn)}

    result = cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation("add_sketch_entities", _entities_operation),
    )
    if result.is_success:
        return result
    result.error = strip_op_prefix(result.error, "add_sketch_entities")
    if failure or drawn:
        removed = bool(failure.get("removed"))
        report: dict[str, Any] = {
            "items": [] if removed else list(drawn),
            "failed_index": failure.get("index"),
            **{
                key: failure[key]
                for key in ("failed", "error", "failed_id")
                if key in failure
            },
            "removed": removed,
        }
        # A dialog's details (``data.dialog``) stay beside the items.
        earlier = result.data if isinstance(result.data, dict) else {}
        result.data = {**earlier, **report}
    return result


def _set_sketch_construction_impl(
    adapter: Any, entity_ids: list[str], construction: bool = True
) -> AdapterResult[dict[str, Any]]:
    """Make sketch entities drawn in the open sketch construction geometry.

    ``add_line``, ``add_circle`` and ``add_rectangle`` draw with ``CreateLine``
    and friends, which have no construction flag; this sets
    ``ISketchSegment.ConstructionGeometry`` on each segment afterwards (all
    four lines of a rectangle) and reads it back.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        entity_ids: Ids the ``add_*`` calls returned, e.g. ``"Line_3"``.
        construction: True for construction geometry, False for normal.

    Returns:
        AdapterResult[dict[str, Any]]: ``entity_ids``, ``segment_count`` and
        ``construction``.
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    missing = [i for i in entity_ids if i not in adapter._sketch_entities]
    if missing:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=(f"Sketch entity {', '.join(missing)} not found in the open sketch"),
        )

    def _construction_operation() -> dict[str, Any]:
        segment_count = 0
        for entity_id in entity_ids:
            for segment in _segments_of(adapter._sketch_entities[entity_id]):
                if not _make_construction(adapter, segment, construction):
                    raise Exception(
                        f"SolidWorks did not make {entity_id} "
                        f"{'construction' if construction else 'normal'} geometry"
                    )
                segment_count += 1
        return {
            "entity_ids": list(entity_ids),
            "segment_count": segment_count,
            "construction": construction,
        }

    return cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation(
            "set_sketch_construction", _construction_operation
        ),
    )


def _add_spline_impl(
    adapter: Any, points: list[dict[str, float]]
) -> AdapterResult[str]:
    """Add a NURBS spline through the supplied control points.

    Calls ``SketchManager.CreateSpline2(points, simulateNaturalEnds=False)``
    with a flattened XYZ coordinate list. Each point dict must contain
    ``"x"`` and ``"y"`` keys; the Z component is forced to ``0``.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        points: Ordered list of control-point dicts with keys ``"x"`` and
            ``"y"`` (in **millimetres**). Minimum 2 points required by
            SolidWorks. Example::

                [{"x": 0, "y": 0}, {"x": 25, "y": 10}, {"x": 50, "y": 0}]

    Returns:
        AdapterResult[str]: On success, ``data`` is the registered entity
        ID string (e.g. ``"Spline_5"``). On failure, ``status`` is
        ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when fewer
            than two points are supplied or ``CreateSpline2`` returns
            ``None``.

    Example::

        pts = [{"x": 0, "y": 0}, {"x": 20, "y": 15}, {"x": 40, "y": 0}]
        result = pywin32_sketch_ops.add_spline(adapter, pts)
        print(result.data)  # "Spline_1"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _spline_operation() -> str:
        if len(points) < 2:
            raise Exception("add_spline requires at least 2 points")

        spline_points: list[float] = []
        for point in points:
            spline_points.extend([point["x"] / 1000.0, point["y"] / 1000.0, 0.0])

        # pywin32 late binding unpacks a bare list into N positional
        # VARIANTs, so SolidWorks sees 3*N+1 arguments instead of 2 and
        # rejects the call with DISP_E_BADPARAMCOUNT. Wrap the doubles in a
        # SAFEARRAY VARIANT (VT_ARRAY|VT_R8) — the same lazy-import dance as
        # add_sketch_constraint so non-Windows CI still exercises this path.
        try:
            import pythoncom as _pythoncom
            from win32com.client import VARIANT as _VARIANT
        except ImportError:
            _pythoncom = None
            _VARIANT = None

        points_arg: Any
        if _pythoncom is not None and _VARIANT is not None:
            points_arg = _VARIANT(_pythoncom.VT_ARRAY | _pythoncom.VT_R8, spline_points)
        elif sys.platform == "win32":
            raise Exception("pywin32 is required for add_spline on Windows")
        else:
            points_arg = spline_points

        spline = adapter.currentSketchManager.CreateSpline2(points_arg, False)
        if not spline:
            raise Exception("Failed to create spline")

        # ISketchSpline dispatch does not expose Select4 via IDispatch —
        # GetIDsOfNames returns Member not found. Retrieve the entity as
        # ISketchSegment from the active sketch instead; that interface
        # exposes Select4, which mirror/offset require.
        if adapter.currentSketch is not None:
            adapter._attempt(
                lambda: _sw_type_info.flag_methods(adapter.currentSketch, "ISketch"),
                default=0,
            )
            segments = adapter._attempt(
                lambda: adapter.currentSketch.GetSketchSegments(), default=None
            )
            if segments is not None and len(segments) > 0:
                return cast(
                    str, adapter._register_sketch_entity("Spline", segments[-1])
                )

        return cast(str, adapter._register_sketch_entity("Spline", spline))

    return cast(
        AdapterResult[str],
        adapter._handle_com_operation("add_spline", _spline_operation),
    )


def _add_centerline_impl(
    adapter: Any, x1: float, y1: float, x2: float, y2: float
) -> AdapterResult[str]:
    """Add a construction centre-line to the active sketch.

    Centre-lines are used as rotation axes for revolve features and as
    mirror lines for symmetric sketches.  Calls
    ``SketchManager.CreateCenterLine``; the resulting entity is not
    registered in the entity registry because it cannot be dimensioned
    independently.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        x1: Start X in **millimetres**.
        y1: Start Y in **millimetres**.
        x2: End X in **millimetres**.
        y2: End Y in **millimetres**.

    Returns:
        AdapterResult[str]: On success, ``data`` is a timestamped ID string
        (e.g. ``"Centerline_9341"``).  On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreateCenterLine`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_centerline(adapter, 0, -20, 0, 20)
        print(result.data)  # "Centerline_9341"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")
    return cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "add_centerline", lambda: _draw_centerline(adapter, x1, y1, x2, y2)
        ),
    )


def _draw_centerline(adapter: Any, x1: float, y1: float, x2: float, y2: float) -> str:
    """Draw a centreline with ``CreateCenterLine`` and register it.

    Registered so later dimension and constraint calls can name it (e.g. as
    the centreline of a ``symmetric`` relation). Shared by ``add_centerline``
    and ``add_sketch_entities``; runs inside their COM operation.

    Returns:
        str: Registered entity ID (e.g. ``"Centerline_3"``).

    Raises:
        Exception: If ``CreateCenterLine`` returns ``None``.
    """
    centerline = adapter.currentSketchManager.CreateCenterLine(
        x1 / 1000.0, y1 / 1000.0, 0, x2 / 1000.0, y2 / 1000.0, 0
    )
    if not centerline:
        raise Exception("Failed to create centerline")
    return cast(str, adapter._register_sketch_entity("Centerline", centerline))


def _add_polygon_impl(
    adapter: Any,
    center_x: float,
    center_y: float,
    radius: float,
    sides: int,
) -> AdapterResult[str]:
    """Add a regular polygon inscribed in a circle to the active sketch.

    Calls ``SketchManager.CreatePolygon(XC, YC, Zc, Xp, Yp, Zp, Sides,
    Inscribed)``.  All eight arguments are required by the COM API — passing
    fewer arguments surfaces a pywin32 ``"Parameter not optional."`` error at
    the SOLIDWORKS boundary.  The vertex point ``(Xp, Yp, Zp)`` is placed on
    the positive X axis at ``radius`` from centre, which fixes the polygon's
    rotation reproducibly.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        center_x: Polygon centre X in **millimetres**.
        center_y: Polygon centre Y in **millimetres**.
        radius: Circumradius in **millimetres** (distance from centre to each
            vertex). Corresponds to ``CreatePolygon(..., Inscribed=True)``,
            i.e. the polygon is inscribed in a circle of this radius.
        sides: Number of polygon sides.  SolidWorks accepts 3–40.

    Returns:
        AdapterResult[str]: On success, ``data`` is the registered entity ID
        (e.g. ``"Polygon_3"``).  The ID is stored in
        ``adapter._sketch_entities`` so it can be passed back to
        ``sketch_linear_pattern`` / ``sketch_circular_pattern`` /
        ``sketch_mirror`` / ``sketch_offset``.  On failure, ``status`` is
        ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreatePolygon`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_polygon(
            adapter, center_x=0, center_y=0, radius=15.0, sides=6
        )
        print(result.data)  # "Polygon_3"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _polygon_operation() -> str:
        """Inner COM closure that calls CreatePolygon.

        Returns:
            str: Registered entity ID for the polygon (e.g. ``"Polygon_3"``).

        Raises:
            Exception: If ``CreatePolygon`` returns ``None``.
        """
        polygon = adapter.currentSketchManager.CreatePolygon(
            center_x / 1000.0,
            center_y / 1000.0,
            0,
            (center_x + radius) / 1000.0,
            center_y / 1000.0,
            0,
            sides,
            True,
        )
        if not polygon:
            raise Exception("Failed to create polygon")
        # Register so the returned ID is usable by sketch_linear_pattern,
        # sketch_circular_pattern, sketch_mirror, and sketch_offset — without
        # this the polygon string is opaque and every downstream op fails
        # with "Unknown sketch entity 'Polygon_*'".
        entity_id = cast(str, adapter._register_sketch_entity("Polygon", polygon))
        # Polygons register as a SAFEARRAY tuple of segments — there's no
        # single dispatch ``GetCenterPoint`` to recover the center from later.
        # Stash the known center so ``sketch_circular_pattern`` can derive the
        # seed-to-axis offset for polygon seeds.
        adapter._sketch_entity_centers[entity_id] = (center_x, center_y)
        return entity_id

    return cast(
        AdapterResult[str],
        adapter._handle_com_operation("add_polygon", _polygon_operation),
    )


def _add_ellipse_impl(
    adapter: Any,
    center_x: float,
    center_y: float,
    major_axis: float,
    minor_axis: float,
) -> AdapterResult[str]:
    """Add an axis-aligned ellipse to the active sketch.

    Calls ``SketchManager.CreateEllipse`` with the major-axis endpoint on the
    positive X direction from the centre and the minor-axis endpoint on the
    positive Y direction.  The ellipse is therefore axis-aligned and cannot
    be rotated via this function.

    The created ellipse is registered in the adapter's sketch-entity
    registry so subsequent constraint and dimension calls can reference it
    by ID, matching the behaviour of ``add_line`` / ``add_circle`` /
    ``add_arc`` and the mock adapter.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        center_x: Ellipse centre X in **millimetres**.
        center_y: Ellipse centre Y in **millimetres**.
        major_axis: Full major-axis length in **millimetres** (half is used
            as the offset from centre).
        minor_axis: Full minor-axis length in **millimetres** (half is used
            as the offset from centre).

    Returns:
        AdapterResult[str]: On success, ``data`` is the registered entity ID
        (e.g. ``"Ellipse_4"``).  On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            ``CreateEllipse`` returns ``None``.

    Example::

        result = pywin32_sketch_ops.add_ellipse(
            adapter, center_x=0, center_y=0, major_axis=30.0, minor_axis=15.0
        )
        print(result.data)  # "Ellipse_4"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _ellipse_operation() -> str:
        """Inner COM closure that calls CreateEllipse and registers the entity.

        Returns:
            str: Registered entity ID for the new ellipse.

        Raises:
            Exception: If ``CreateEllipse`` returns ``None``.
        """
        ellipse = adapter.currentSketchManager.CreateEllipse(
            center_x / 1000.0,
            center_y / 1000.0,
            0,
            (center_x + major_axis / 2) / 1000.0,
            center_y / 1000.0,
            0,
            center_x / 1000.0,
            (center_y + minor_axis / 2) / 1000.0,
            0,
        )
        if not ellipse:
            raise Exception("Failed to create ellipse")
        return cast(str, adapter._register_sketch_entity("Ellipse", ellipse))

    return cast(
        AdapterResult[str],
        adapter._handle_com_operation("add_ellipse", _ellipse_operation),
    )


def _add_sketch_constraint_impl(
    adapter: Any,
    entity1: str,
    entity2: str | None,
    relation_type: str,
    entity3: str | None = None,
) -> AdapterResult[dict[str, Any]]:
    """Add a geometric relation (constraint) between sketch entities.

    Resolves ``entity1`` (and ``entity2``/``entity3`` if provided) against the
    adapter's sketch-entity registry, ``"Origin"`` or a point of an entity
    (``"<id>.center"``, ``.start``, ``.end``; see :func:`relation_targets`
    and ``sketch_points``), then calls
    ``ISketchRelationManager.AddRelation(entities, relation_type_enum)`` on
    the active sketch. Entity handles are passed as a
    ``VARIANT(VT_ARRAY | VT_DISPATCH, [...])`` — pywin32 will not marshal a
    plain Python list of CDispatch objects to a SAFEARRAY by itself.

    The legacy ``IModelDoc2.SketchAddConstraints`` API silently no-ops on
    SW 2026/3DEXPERIENCE despite accepting the call, so this implementation
    uses the modern ``ISketchRelationManager.AddRelation`` per the official
    SolidWorks API docs.

    Supported ``relation_type`` strings (case-insensitive): ``"horizontal"``,
    ``"vertical"``, ``"parallel"``, ``"perpendicular"``, ``"tangent"``,
    ``"coincident"``, ``"concentric"``, ``"equal"``, ``"symmetric"``,
    ``"collinear"``, ``"fix"``.

    ``"symmetric"`` is the only relation that takes a third entity
    (``entity3`` — the centerline of symmetry). All other relations reject
    a non-null ``entity3``.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch and a valid
            ``currentModel``.
        entity1: The primary entity: a registered id (from ``add_line`` /
            ``add_circle`` / ``edit_sketch``), ``"Origin"`` or a point of one.
        entity2: The secondary entity, named the same way, or ``None`` for
            single-entity relations (horizontal, vertical, fix).
        relation_type: Constraint type string (see above).
        entity3: Registered ID of a third entity. Only meaningful for
            ``"symmetric"`` — pass the centerline ID (from ``add_centerline``)
            as the line of symmetry. Must be ``None`` for every other
            relation type.

    The relation is checked after: the sketch's relation count
    (``GetRelationsCount``) must rise or an entity must move. When the count
    reads and neither happened, SolidWorks took the call and did nothing (a
    circle coincident with a centreline did that), which is an error rather
    than a success that looks defined.

    Returns:
        AdapterResult[dict[str, Any]]: On success, ``data`` has ``id`` (the
        registered relation, e.g. ``"Constraint_3"``), ``entities`` (the
        references tied, after mapping a circle to its centre) and
        ``verified_by`` (``relation_count``, ``geometry`` or None when
        neither reads). On failure, ``status`` is ``ERROR``.
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _constraint_operation() -> dict[str, Any]:
        if not adapter.currentModel:
            raise Exception("No active model")

        rt_norm = (relation_type or "").strip().lower()
        relation_type_enum = RELATION_NAME_MAP.get(rt_norm)
        if relation_type_enum is None:
            supported = ", ".join(sorted(RELATION_NAME_MAP))
            raise Exception(
                f"Unsupported relation type '{relation_type}'. Supported: {supported}"
            )

        # Arity validation per relation type
        if rt_norm in _THREE_ENTITY_RELATIONS:
            if entity2 is None or entity3 is None:
                raise Exception(
                    f"Relation '{relation_type}' requires entity1, entity2, "
                    "and entity3 (the centerline of symmetry)"
                )
        elif entity3 is not None:
            raise Exception(
                f"Relation '{relation_type}' does not accept entity3 — only "
                "'symmetric' takes a third entity (the centerline)"
            )

        refs = [r for r in (entity1, entity2, entity3) if r]
        targets = relation_targets(adapter, rt_norm, refs)
        entities = [t.obj for t in targets]

        # Flag IModelDoc2 + ISketch + ISketchRelationManager so late-binding
        # resolves GetActiveSketch2, RelationManager, and AddRelation as
        # methods/properties correctly.
        adapter._attempt(
            lambda: _sw_type_info.flag_methods(adapter.currentModel, "IModelDoc2"),
            default=0,
        )

        active_sketch = adapter._attempt(
            lambda: adapter.currentModel.GetActiveSketch2(), default=None
        )
        if active_sketch is None:
            raise Exception(
                "No active sketch on the model — create_sketch first or "
                "open the existing sketch for edit."
            )
        adapter._attempt(
            lambda: _sw_type_info.flag_methods(active_sketch, "ISketch"),
            default=0,
        )

        relmgr = adapter._attempt(lambda: active_sketch.RelationManager, default=None)
        if relmgr is None:
            raise Exception("Active sketch has no RelationManager")
        adapter._attempt(
            lambda: _sw_type_info.flag_methods(relmgr, "ISketchRelationManager"),
            default=0,
        )

        # pywin32 won't auto-marshal a Python list of CDispatch entities to a
        # SAFEARRAY — the VT_ARRAY|VT_DISPATCH variant is the shape SolidWorks
        # accepts. Mirror the lazy-import dance used for sw_type_info above so
        # non-Windows CI (where pywin32 isn't installed) still exercises this
        # call path; fake adapters used in unit tests accept any sequence.
        try:
            import pythoncom as _pythoncom
            from win32com.client import VARIANT as _VARIANT
        except ImportError:
            _pythoncom = None
            _VARIANT = None

        ents_variant: Any
        if _pythoncom is not None and _VARIANT is not None:
            ents_variant = _VARIANT(
                _pythoncom.VT_ARRAY | _pythoncom.VT_DISPATCH, entities
            )
        elif sys.platform == "win32":
            # On Windows the real adapter feeds entities to a live COM method
            # that requires the SAFEARRAY shape — fail clearly rather than let
            # AddRelation surface a low-level "server threw an exception" COM
            # error from an unwrappable list argument.
            raise Exception("pywin32 is required for add_sketch_constraint on Windows")
        else:
            # Non-Windows: this branch is exercised only by mocked unit tests
            # whose fake AddRelation accepts any sequence.
            ents_variant = entities
        count_before = sketch_relations.relation_count(adapter, relmgr)
        where_before = [sketch_points.signature(adapter, t) for t in targets]
        sketch_relation, add_err = adapter._attempt_with_error(
            lambda: relmgr.AddRelation(ents_variant, relation_type_enum)
        )
        named = " and ".join(f"'{t.ref}'" for t in targets)
        if add_err is not None or sketch_relation is None:
            detail = f": {add_err}" if add_err else ""
            raise Exception(
                f"SolidWorks rejected '{relation_type}' relation on {named}{detail}"
            )
        verified_by = relation_took(
            count_before,
            sketch_relations.relation_count(adapter, relmgr),
            where_before,
            [sketch_points.signature(adapter, t) for t in targets],
        )
        if verified_by == "nothing":
            raise Exception(
                f"SolidWorks accepted the '{rt_norm}' relation on {named} but "
                "added no relation and moved nothing, so it did not take (it may "
                "already hold, or not apply to these entities). Name a point "
                "('<id>.center', '<id>.start', '<id>.end', 'Origin') to tie "
                "points, and check with get_sketch detail 'summary'."
            )
        return {
            "id": adapter._register_sketch_entity("Constraint", sketch_relation),
            "entities": [t.ref for t in targets],
            "verified_by": verified_by,
        }

    return cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation("add_sketch_constraint", _constraint_operation),
    )


def relation_targets(
    adapter: Any, relation: str, refs: list[str]
) -> list[sketch_points.Target]:
    """What a relation ties, each reference resolved (``sketch_points.resolve``).

    A coincident between an arc or circle and a line ties the arc's centre to
    the line; SolidWorks accepted the curve itself and did nothing (a circle
    and a centreline on SW 2026 SP4.1). A full circle coincident with a point
    (the origin, say) ties its centre to the point too; an arc and a point
    keep SolidWorks' meaning, the point on the arc.

    Raises:
        Exception: A reference names nothing (``sketch_points.resolve``).
    """
    targets = [sketch_points.resolve(adapter, ref) for ref in refs]
    if relation != "coincident" or len(targets) != 2:
        return targets

    def _to_centre(curve: sketch_points.Target, other: sketch_points.Target) -> bool:
        if curve.kind not in ("arc", "circle"):
            return False
        return other.kind == "line" or (other.is_point and curve.kind == "circle")

    first, second = targets
    if _to_centre(first, second):
        return [sketch_points.as_centre(adapter, first), second]
    if _to_centre(second, first):
        return [first, sketch_points.as_centre(adapter, second)]
    return targets


def relation_took(
    count_before: int | None,
    count_after: int | None,
    where_before: list[tuple[float, ...] | None],
    where_after: list[tuple[float, ...] | None],
) -> str | None:
    """How a new relation shows it took, or ``"nothing"`` when it shows it didn't.

    Returns:
        str | None: ``"relation_count"`` (the sketch has one more),
        ``"geometry"`` (an entity moved), ``"nothing"`` (the count read and
        didn't rise, and nothing that read moved), or None when the count
        can't be read and nothing moved.
    """
    if count_before is not None and count_after is not None:
        if count_after > count_before:
            return "relation_count"
    moved = any(
        before is not None and after is not None and before != after
        for before, after in zip(where_before, where_after, strict=True)
    )
    if moved:
        return "geometry"
    counted = count_before is not None and count_after is not None
    return "nothing" if counted else None


def _add_sketch_dimension_impl(
    adapter: Any,
    entity1: str,
    entity2: str | None,
    dimension_type: str,
    value: float,
) -> AdapterResult[dict[str, Any]]:
    """Add a driving dimension to one or two sketch entities or points.

    Supports linear, horizontal, vertical, angular, radial, and diameter
    dimensions:

    * **linear** — a line's length on a single entity (text placed by
      ``adapter._single_line_dimension_placement``); between a line and
      another entity, :func:`_two_entity_linear_dimension`; between points,
      circles (their centres) and lines otherwise, :func:`_distance_dimension`.
    * **horizontal** / **vertical** — the distance along sketch x or y
      between two points or lines, or a line's run or rise
      (:func:`_distance_dimension`).
    * **angular** — places an angular dimension between two connected line
      segments sharing a common vertex.  The vertex is found via
      ``adapter._shared_segment_vertex`` and multiple direction/segment
      combinations are tried until one succeeds.
    * **radial** / **diameter** — places a radius or diameter dimension on a
      selected sketch arc or circle using ``IModelDoc2.AddRadialDimension2`` or
      ``IModelDoc2.AddDiameterDimension2``.

    SolidWorks can otherwise enter the interactive ``Modify`` approval flow
    during sketch dimension creation. The adapter keeps the relevant
    sketch-input preferences disabled for the full automation session in
    ``_ComSessionCoordinator.set_automation_preferences`` and uses the dedicated
    radial/diameter APIs here because that path is more reliable in unattended
    COM sessions.

    Dimensional values for **linear** dimensions are in **millimetres**;
    for **angular** dimensions they are in **degrees**. The value is set, then
    read back (``IDimension.SystemValue``): a dimension that doesn't take it
    (a driven one, where the sketch already fixes that distance) is an error.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch and a valid
            ``currentModel`` and ``swApp``.
        entity1: The primary entity: a registered id, ``"Origin"`` or a point
            of an entity (``"<id>.center"``, ``.start``, ``.end``).
        entity2: The secondary entity, named the same way (required for
            angular dimensions and distances), or ``None``.
        dimension_type: ``"linear"``, ``"horizontal"``, ``"vertical"``,
            ``"angular"``, ``"radial"``, or ``"diameter"`` (case-insensitive).
        value: Dimension value. Millimetres for linear, radial, and diameter;
            degrees for angular.

    Returns:
        AdapterResult[dict[str, Any]]: On success, ``data`` has ``id`` (the
        registered dimension), ``entities`` (what it measures), and when they
        read ``name`` (as ``set_dimension`` takes it) and ``measured`` (mm or
        degrees). On failure, ``status`` is ``ERROR``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when the
            entity IDs cannot be resolved, the placement cannot be computed,
            or the SolidWorks ``AddDimension`` call fails.

    Example::

        # Dimension a line to 50 mm
        result = pywin32_sketch_ops.add_sketch_dimension(
            adapter, "Line_1", None, "linear", 50.0
        )
        print(result.data["id"])  # "Dimension_7"

        # Angular dimension between two intersecting lines
        result = pywin32_sketch_ops.add_sketch_dimension(
            adapter, "Line_1", "Line_2", "angular", 45.0
        )
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _dimension_operation() -> dict[str, Any]:
        """Inner COM closure that resolves entities, places and values the dimension.

        Resolves entity objects from the adapter registry, computes the
        placement point, suppresses the interactive value dialog, calls
        ``AddDimension``, then sets the dimensional value via
        ``SetSystemValue3`` / ``SetSystemValue2`` / ``SystemValue`` fallbacks.

        Returns:
            dict[str, Any]: The new dimension (see the outer docstring).

        Raises:
            Exception: If entity IDs are unknown, placement cannot be
                determined, or the COM call fails.
        """
        # Re-assert per-call: SolidWorks resets swInputDimValOnCreate (10)
        # when entering a new sketch, so the global connect-time suppression
        # is not sufficient. Setting it to False here prevents the Modify
        # dialog from blocking unattended automation.
        _sw = getattr(adapter, "swApp", None)
        if _sw:
            adapter._attempt(lambda: _sw.SetUserPreferenceToggle(10, False))
            adapter._attempt(lambda: _sw.SetUserPreferenceToggle(372, False))
            adapter._attempt(lambda: _sw.SetUserPreferenceToggle(520, False))

        def _radial_dimension_placement() -> tuple[float, float, float, int]:
            """Compute a deterministic placement for radial/diameter dimensions.

            Avoids additional COM geometry reads that can block on some live sessions.
            """
            offset = max(0.01, min(0.05, abs(value) / 1000.0 + 0.01))
            return (
                offset,
                offset,
                0.0,
                adapter.constants["swSmartDimensionDirectionUp"],
            )

        if not adapter.currentModel:
            return {"id": f"Dimension_{int(time.time() * 1000) % 10000}"}

        dim_type = (dimension_type or "linear").strip().lower()
        refs = [entity1] + ([entity2] if entity2 else [])
        if dim_type in ("horizontal", "vertical") or (
            dim_type == "linear" and any(_is_point_reference(r) for r in refs)
        ):
            return _distance_dimension(adapter, refs, dim_type, value)

        entity1_obj = adapter._sketch_entities.get(entity1)
        if entity1_obj is None:
            raise Exception(
                f"Unknown sketch entity '{entity1}'. Use IDs returned by add_line/add_arc/add_circle/add_spline/add_centerline."
            )

        entity2_obj = None
        if entity2:
            entity2_obj = adapter._sketch_entities.get(entity2)
            if entity2_obj is None:
                raise Exception(
                    f"Unknown sketch entity '{entity2}'. Use IDs returned by add_line/add_arc/add_circle/add_spline/add_centerline."
                )

        if (
            dim_type == "linear"
            and entity2_obj is not None
            and sketch_points.segment_kind(adapter, entity1_obj) in ("arc", "circle")
        ):
            # Centre to centre, or centre to line: the line-first path below
            # needs a line as entity1 (it failed for these on SW 2026 SP4.1).
            return _distance_dimension(adapter, refs, dim_type, value)

        placement = None
        if dim_type == "angular" and entity2_obj is not None:
            placement = adapter._angular_dimension_placement(entity1_obj, entity2_obj)
        elif dim_type == "linear":
            placement = adapter._single_line_dimension_placement(entity1_obj)
        elif dim_type in {"radial", "diameter"}:
            placement = _radial_dimension_placement()

        if placement is None:
            raise Exception(
                f"Unsupported or ambiguous dimension placement for type '{dim_type}'"
            )

        text_x, text_y, text_z, direction = placement

        def _try_create_angular_dimension() -> Any:
            """Attempt to create an angular dimension between two line segments.

            Finds the shared vertex between ``entity1_obj`` and
            ``entity2_obj``, then iterates through every (segment, vertex,
            direction) combination until SolidWorks accepts the placement.

            Returns:
                Any: The SolidWorks display-dimension COM object on success,
                or ``None`` if all attempts fail.
            """
            shared_vertex = adapter._shared_segment_vertex(entity1_obj, entity2_obj)
            if shared_vertex is None:
                return None

            _, vertex1, vertex2 = shared_vertex
            segment_attempts = ((entity1_obj, vertex1), (entity2_obj, vertex2))
            direction_attempts = [direction] + [
                candidate
                for candidate in (
                    adapter.constants["swSmartDimensionDirectionRight"],
                    adapter.constants["swSmartDimensionDirectionUp"],
                    adapter.constants["swSmartDimensionDirectionLeft"],
                    adapter.constants["swSmartDimensionDirectionDown"],
                )
                if candidate != direction
            ]

            for segment_obj, vertex_obj in segment_attempts:
                for candidate_direction in direction_attempts:
                    adapter.currentModel.ClearSelection2(True)
                    if not adapter._select_sketch_entity(segment_obj, append=False):
                        continue
                    if not adapter._attempt(
                        lambda vo=vertex_obj: bool(vo.Select2(True, 0)), default=False
                    ):
                        continue
                    display_dim = adapter._attempt(
                        lambda d=candidate_direction: (
                            adapter.currentModel.Extension.AddDimension(
                                text_x, text_y, text_z, d
                            )
                        ),
                        default=None,
                    )
                    if display_dim:
                        return display_dim
            return None

        if dim_type == "angular":
            display_dim = _try_create_angular_dimension()
        elif dim_type == "linear" and entity2_obj is not None:
            adapter.currentModel.ClearSelection2(True)
            if not adapter._select_sketch_entity(entity1_obj, append=False):
                raise Exception(f"Failed to select primary entity '{entity1}'")
            if not adapter._select_sketch_entity(entity2_obj, append=True):
                raise Exception(f"Failed to select secondary entity '{entity2}'")
            display_dim = _two_entity_linear_dimension(
                adapter, (entity1, entity2), entity1_obj, entity2_obj, direction
            )
        else:
            adapter.currentModel.ClearSelection2(True)
            if not adapter._select_sketch_entity(entity1_obj, append=False):
                raise Exception(f"Failed to select primary entity '{entity1}'")
            if entity2_obj is not None and not adapter._select_sketch_entity(
                entity2_obj, append=True
            ):
                raise Exception(f"Failed to select secondary entity '{entity2}'")

            if dim_type == "radial":
                display_dim = adapter._attempt(
                    lambda: adapter.currentModel.AddRadialDimension2(
                        text_x, text_y, text_z
                    ),
                    default=None,
                )
            elif dim_type == "diameter":
                display_dim = adapter._attempt(
                    lambda: adapter.currentModel.AddDiameterDimension2(
                        text_x, text_y, text_z
                    ),
                    default=None,
                )
            else:
                # Use a single deterministic AddDimension call for non-angular
                # dimensions that require extension-line direction.
                display_dim = adapter._attempt(
                    lambda: adapter.currentModel.Extension.AddDimension(
                        text_x, text_y, text_z, direction
                    ),
                    default=None,
                )

        if not display_dim:
            raise Exception("SolidWorks failed to create sketch dimension")
        return _finish_dimension(adapter, display_dim, dim_type, value, refs)

    return cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation("add_sketch_dimension", _dimension_operation),
    )


#: Dimension types that measure a distance between two things.
_DISTANCE_TYPES = ("linear", "horizontal", "vertical")


def _is_point_reference(ref: str) -> bool:
    """Whether ``ref`` names a point (``"Origin"``, ``"<id>.center"``, ...)."""
    text = (ref or "").strip()
    return text.lower() == sketch_points.ORIGIN or "." in text


def _dimension_units(dim_type: str) -> tuple[float, str]:
    """System units per the value's unit, and the unit: radians per degree or m per mm."""
    if dim_type == "angular":
        return math.pi / 180.0, "deg"
    return 0.001, "mm"


def _finish_dimension(
    adapter: Any, display_dim: Any, dim_type: str, value: float, refs: list[str]
) -> dict[str, Any]:
    """Set a new dimension's value, read it back, and register it.

    The value goes through ``SetSystemValue3`` (this configuration),
    ``SetSystemValue2`` or the ``SystemValue`` property, whichever takes it.
    ``SystemValue`` is then read: a dimension that reads something else
    didn't take the value (it is driven: the sketch already fixes that
    distance) and is an error naming it, so a sketch that looks dimensioned
    isn't left at another size.

    Returns:
        dict[str, Any]: ``id``, ``entities`` and, when they read, ``name``
        and ``measured``.

    Raises:
        Exception: The value read back is not the value asked.
    """
    from .documentation import _strip_document_suffix

    factor, unit = _dimension_units(dim_type)
    value_si = value * factor
    dim_obj = (
        adapter._attempt(lambda: display_dim.GetDimension2(0), default=None)
        or adapter._attempt(lambda: display_dim.GetDimension(), default=None)
        or display_dim
    )
    if (
        adapter._attempt(
            lambda: dim_obj.SetSystemValue3(value_si, 1, None), default=None
        )
        is None
    ):
        if (
            adapter._attempt(lambda: dim_obj.SetSystemValue2(value_si, 1), default=None)
            is None
        ):
            if hasattr(dim_obj, "SystemValue"):
                dim_obj.SystemValue = value_si

    dimension_id = adapter._register_sketch_entity("Dimension", display_dim)
    reply: dict[str, Any] = {"id": dimension_id, "entities": list(refs)}
    name = _strip_document_suffix(
        adapter._attempt(lambda: dim_obj.FullName, default=None)
    )
    if name:
        reply["name"] = name
    read = adapter._attempt(lambda: dim_obj.SystemValue, default=None)
    if isinstance(read, (int, float)) and not isinstance(read, bool):
        measured = round(float(read) / factor, 6)
        reply["measured"] = measured
        if abs(float(read) - value_si) > max(1e-8, abs(value_si) * 1e-6):
            raise Exception(
                f"The {dim_type} dimension {name or dimension_id} was added but "
                f"reads {measured:g} {unit}, not {value:g} {unit}: SolidWorks "
                "didn't drive the sketch to it (it is a driven dimension when "
                "the sketch already fixes that size). undo removes it."
            )
    return reply


def _distance_dimension(
    adapter: Any, refs: list[str], dim_type: str, value: float
) -> dict[str, Any]:
    """A distance between points, circles and lines, or a line's run or rise.

    Each reference is resolved (``sketch_points.resolve``); a circle or arc
    stands for its centre, so two circles are dimensioned centre to centre
    and a circle and a line centre to line. Both are selected and
    ``IModelDoc2.AddDimension2`` (``linear``: the shortest distance),
    ``AddHorizontalDimension2`` or ``AddVerticalDimension2`` places the
    dimension (what the macro recorder writes for a smart, horizontal or
    vertical dimension). One line alone gives its length, run or rise.
    When a centre can't be dimensioned the circle itself is tried, which
    SolidWorks measures at its centre too.

    Raises:
        Exception: A reference names nothing, is neither a point nor a line,
            a position can't be read, or SolidWorks made no dimension.
    """
    model = adapter.currentModel
    targets = [sketch_points.resolve(adapter, ref) for ref in refs]
    if len(targets) == 1:
        only = targets[0]
        if only.kind not in ("line", "segment"):
            raise Exception(
                f"A {dim_type} dimension of '{only.ref}' needs entity2: the point "
                "or line to measure from, e.g. 'Origin'."
            )
    curves = [t for t in targets if t.kind in ("arc", "circle")]
    curve_ids = {t.entity_id for t in curves}
    if len(targets) == 2:
        targets = [sketch_points.as_centre(adapter, t) for t in targets]
        for target in targets:
            if not (target.is_point or target.kind in ("line", "segment")):
                raise Exception(
                    f"'{target.ref}' is a {target.kind}: a distance is measured "
                    "between points (a circle's centre, 'Origin', '<id>.start') "
                    "and lines."
                )
    targets = [sketch_points.with_position(adapter, t) for t in targets]
    text = _distance_text(adapter, targets, dim_type)

    _flag(
        adapter,
        model,
        "ClearSelection2",
        "AddDimension2",
        "AddHorizontalDimension2",
        "AddVerticalDimension2",
    )
    create = {
        "linear": lambda: model.AddDimension2(*text),
        "horizontal": lambda: model.AddHorizontalDimension2(*text),
        "vertical": lambda: model.AddVerticalDimension2(*text),
    }[dim_type]
    choices = [[t.obj for t in targets]]
    if curves and len(targets) == 2:
        # The circles themselves, in place of the centres they stood for.
        choices.append(
            [t.owner if t.entity_id in curve_ids else t.obj for t in targets]
        )
    display_dim = None
    for objects in choices:
        adapter._attempt(lambda: model.ClearSelection2(True), default=None)
        if not all(
            adapter._select_sketch_entity(obj, append=i > 0)
            for i, obj in enumerate(objects)
        ):
            continue
        display_dim = adapter._attempt(create, default=None)
        if display_dim:
            break
    if not display_dim:
        named = " and ".join(f"'{t.ref}'" for t in targets)
        raise Exception(
            f"SolidWorks made no {dim_type} dimension on {named} (with both "
            "selected, nothing came back)."
        )
    return _finish_dimension(
        adapter, display_dim, dim_type, value, [t.ref for t in targets]
    )


def _distance_text(
    adapter: Any, targets: list[sketch_points.Target], dim_type: str
) -> tuple[float, float, float]:
    """Where a distance dimension's text goes, in sketch metres.

    A point and a line, measured square to the line: beside the line, past
    its nearer end (:func:`_text_beside_line`). Otherwise half way between
    the two, moved :data:`_DIMENSION_CLEARANCE_M` off the measurement: above
    for a horizontal one, right for a vertical one, square to it for a
    linear one. One line alone: its single-line placement.

    Raises:
        Exception: A position can't be read.
    """
    clearance = _DIMENSION_CLEARANCE_M
    if len(targets) == 1:
        placement = adapter._attempt(
            lambda: adapter._single_line_dimension_placement(targets[0].obj),
            default=None,
        )
        if placement is not None:
            return (placement[0], placement[1], placement[2])
        if targets[0].xy is None:
            raise Exception(f"Cannot read where '{targets[0].ref}' is to place it")
        return (targets[0].xy[0], targets[0].xy[1] + clearance, 0.0)
    first, second = targets
    if first.xy is None or second.xy is None:
        unread = first if first.xy is None else second
        raise Exception(f"Cannot read where '{unread.ref}' is to place the dimension")
    if dim_type == "linear":
        line = next((t for t in targets if t.kind == "line"), None)
        point = next((t for t in targets if t.is_point), None)
        if line is not None and point is not None and point.xy is not None:
            beside = _text_beside_line(adapter, line.obj, (*point.xy, 0.0))
            if beside is not None:
                return beside
    (x1, y1), (x2, y2) = first.xy, second.xy
    mid_x, mid_y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    if dim_type == "horizontal":
        return (mid_x, max(y1, y2) + clearance, 0.0)
    if dim_type == "vertical":
        return (max(x1, x2) + clearance, mid_y, 0.0)
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 1e-12:
        return (mid_x, mid_y + clearance, 0.0)
    return (
        mid_x - (y2 - y1) / length * clearance,
        mid_y + (x2 - x1) / length * clearance,
        0.0,
    )


def _sketch_point_xyz(
    adapter: Any, entity_id: str, entity: Any
) -> tuple[float, float, float] | None:
    """A sketch entity's reference point in metres: a circle's centre, a line's midpoint.

    Lines and circles record that point (in mm) when they are created, so it
    is read from ``_sketch_entity_centers`` without a COM call. Reading it
    back from SolidWorks returned nothing on SW 2026; that read stays only as
    the fallback for entities that record no point.

    Args:
        adapter: A ``PyWin32Adapter``.
        entity_id: The entity's registered ID.
        entity: The registered ``ISketchArc`` or ``ISketchLine`` dispatch.

    Returns:
        tuple[float, float, float] | None: The point, or ``None`` when it
        cannot be read.
    """
    cached = adapter._sketch_entity_centers.get(entity_id)
    if cached is not None:
        return (cached[0] / 1000.0, cached[1] / 1000.0, 0.0)
    adapter._attempt(
        lambda: _sw_type_info.flag_methods(entity, "ISketchArc"), default=0
    )
    centre = adapter._attempt(lambda: entity.GetCenterPoint(), default=None)
    if isinstance(centre, (list, tuple)) and len(centre) >= 3:
        return (float(centre[0]), float(centre[1]), float(centre[2]))
    ends = adapter._attempt(
        lambda: adapter._sketch_geometry.read_segment_endpoints(entity), default=None
    )
    if ends is None:
        return None
    (x1, y1, z1), (x2, y2, z2) = ends
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0, (z1 + z2) / 2.0)


#: How far past a line's end a dimension to it is placed, in metres.
_DIMENSION_CLEARANCE_M = 0.008


def _text_beside_line(
    adapter: Any, line: Any, point: tuple[float, float, float]
) -> tuple[float, float, float] | None:
    """Where to put a line-to-point dimension so it sits outside the outline.

    Midway between the line and the point along the measurement, then moved
    along the line past its end nearest the point, so the dimensions to the
    holes of a plate stand clear of the plate instead of piling up inside it.

    Args:
        adapter: A ``PyWin32Adapter``.
        line: The ``ISketchLine`` dispatch.
        point: The other entity's point, in metres.

    Returns:
        tuple[float, float, float] | None: The text position, or ``None``
        when the line's ends cannot be read.
    """
    ends = adapter._attempt(
        lambda: adapter._sketch_geometry.read_segment_endpoints(line), default=None
    )
    if ends is None:
        return None
    (ax, ay, _az), (bx, by, _bz) = ends
    length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5
    if length <= 1e-9:
        return None
    ux, uy = (bx - ax) / length, (by - ay) / length
    along = (point[0] - ax) * ux + (point[1] - ay) * uy
    foot = (ax + along * ux, ay + along * uy)
    mid = ((foot[0] + point[0]) / 2.0, (foot[1] + point[1]) / 2.0)
    shift = (
        -along - _DIMENSION_CLEARANCE_M
        if along < length / 2.0
        else length - along + _DIMENSION_CLEARANCE_M
    )
    return (mid[0] + shift * ux, mid[1] + shift * uy, point[2])


def _two_entity_linear_dimension(
    adapter: Any, ids: tuple[str, str], entity1: Any, entity2: Any, direction: int
) -> Any:
    """A linear dimension between two sketch entities, e.g. a line and a circle.

    The text goes beside a line, clear of the outline (see
    :func:`_text_beside_line`), or midway between the two entities when the
    first is not a line. ``Extension.AddDimension`` with the
    first entity's single-line placement returned nothing for a line and a
    circle on SW 2026, so the calls are tried in turn until one returns a
    display dimension: ``IModelDoc2::AddDimension2`` with both entities
    selected (what the macro recorder writes for a smart dimension), the same
    with the circle's centre point selected, then ``Extension.AddDimension``.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        ids: The two entities' registered IDs.
        entity1: The first entity, usually a line.
        entity2: The second entity, usually a circle.
        direction: ``swSmartDimensionDirection_e`` for the last attempt.

    Returns:
        Any: The display dimension, or ``None`` when every attempt failed
        (an attempt whose entities cannot be selected is skipped). The caller
        has already checked that both entities can be selected.

    Raises:
        Exception: When the entities' positions cannot be read.
    """
    model = adapter.currentModel
    points = [
        _sketch_point_xyz(adapter, i, e)
        for i, e in zip(ids, (entity1, entity2), strict=True)
    ]
    if points[0] is None or points[1] is None:
        raise Exception("Cannot read where the two entities are to place the dimension")
    text = _text_beside_line(adapter, entity1, points[1]) or tuple(
        (a + b) / 2.0 for a, b in zip(points[0], points[1], strict=True)
    )
    centre = adapter._attempt(
        lambda: adapter._get_attr_or_call(entity2, "GetCenterPoint2"), default=None
    )

    def _selected(second: Any) -> bool:
        adapter._attempt(lambda: model.ClearSelection2(True), default=None)
        return bool(
            adapter._select_sketch_entity(entity1, append=False)
            and adapter._select_sketch_entity(second, append=True)
        )

    attempts = [
        (entity2, lambda: model.AddDimension2(*text)),
        *([(centre, lambda: model.AddDimension2(*text))] if centre is not None else []),
        (entity2, lambda: model.Extension.AddDimension(*text, direction)),
    ]
    for second, create in attempts:
        if not _selected(second):
            continue
        display_dim = adapter._attempt(create, default=None)
        if display_dim:
            return display_dim
    return None


def _select_sketch_entities(adapter: Any, entity_ids: list[str], mark: int) -> None:
    """Select sketch entities from the registry under a specific mark.

    Resolves each ID against ``adapter._sketch_entities`` and calls
    ``ISketchSegment.Select4(Append=True, Data)`` on each — with ``Data``
    being a configured ``ISelectData`` carrying the requested ``mark`` so
    SolidWorks knows how to interpret the selection (e.g. mark=1 sketch
    segments + mark=2 centerline for ``SketchMirror``).

    The ``ISelectionMgr`` dispatch needs ``sw_type_info.flag_methods``
    flagging or pywin32 late binding cannot resolve ``CreateSelectData``
    and surfaces ``"Member not found."`` from the COM boundary.  This
    matches the lazy-import dance used by ``add_sketch_constraint``;
    when ``sw_type_info`` cannot be imported the flagging step is skipped
    but ``ISelectionMgr.CreateSelectData`` and ``ISketchSegment.Select4``
    are still invoked.  Callers must therefore not invoke this helper
    without a live ``ISelectionMgr`` on ``adapter.currentModel``.

    Args:
        adapter: A ``PyWin32Adapter``.  ``adapter.currentModel`` must be a
            live ``IModelDoc2`` dispatch.
        entity_ids: Registry IDs returned by ``add_line`` / ``add_arc`` /
            etc.  Must be non-empty; resolution failure raises.
        mark: ``ISelectData.Mark`` value applied to every selection.

    Raises:
        Exception: If an entity ID is not in the registry or a Select4
            call returns ``False``.
    """
    sel_mgr = adapter.currentModel.SelectionManager
    adapter._attempt(
        lambda: _sw_type_info.flag_methods(sel_mgr, "ISelectionMgr"),
        default=0,
    )
    select_data = sel_mgr.CreateSelectData()
    select_data.Mark = mark
    for ent_id in entity_ids:
        entity = adapter._sketch_entities.get(ent_id)
        if entity is None:
            raise Exception(
                f"Unknown sketch entity '{ent_id}'. Use IDs returned by "
                "add_line/add_arc/add_circle/add_spline/add_centerline."
            )
        # ``ISketchManager::CreatePolygon`` returns the polygon's edges as
        # a SAFEARRAY, which pywin32 unmarshals to a tuple of
        # ``ISketchSegment`` handles — there is no single COM object to
        # ``Select4`` on.  Treat any iterable (tuple/list) as a group of
        # segments and select each, so a polygon ID can flow into
        # sketch_linear_pattern / sketch_circular_pattern / sketch_mirror /
        # sketch_offset the same as any other entity.  Single-segment
        # entities (lines, arcs, splines, ellipses, centerlines) keep the
        # original Select4 path.
        if isinstance(entity, (list, tuple)):
            for segment in entity:
                adapter._attempt(
                    lambda s=segment: _sw_type_info.flag_methods(s, "ISketchSegment"),
                    default=0,
                )
                ok = segment.Select4(True, select_data)
                if not ok:
                    raise Exception(
                        f"Failed to select segment of sketch entity '{ent_id}'"
                    )
        else:
            adapter._attempt(
                lambda e=entity: _sw_type_info.flag_methods(e, "ISketchSegment"),
                default=0,
            )
            ok = entity.Select4(True, select_data)
            if not ok:
                raise Exception(f"Failed to select sketch entity '{ent_id}'")


def _sketch_linear_pattern_impl(
    adapter: Any,
    entities: list[str],
    direction_x: float,
    direction_y: float,
    spacing: float,
    count: int,
    add_dimensions: bool = True,
) -> AdapterResult[str]:
    """Create a linear sketch pattern from the registered seed entities.

    Selects ``entities`` then calls
    ``ISketchManager::CreateLinearSketchStepAndRepeat(NumX, NumY, SpacingX,
    SpacingY, AngleX, AngleY, DeleteInstances, XSpacingDim, YSpacingDim,
    AngleDim, CreateNumOfInstancesDimInXDir, CreateNumOfInstancesDimInYDir)``.
    The COM API expects spacing in metres and angles in radians; this
    function does both conversions internally.

    The ``(direction_x, direction_y)`` vector defines pattern direction 1
    (``AngleX``).  Direction 2 (``AngleY``) is set perpendicular so the SW
    UI shows a clean axis frame, but ``NumY`` stays at 1 so no second-axis
    instances are produced.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        entities: Registered entity IDs to pattern.  Must be non-empty.
        direction_x: Pattern direction X component (unit-less, any non-zero
            vector is normalised internally via ``atan2``).
        direction_y: Pattern direction Y component.
        spacing: Distance between instances in **millimetres**.
        count: Total number of instances (including the seed).  Must be at
            least 2.
        add_dimensions: Show the spacing and instance-count dimensions
            (``XSpacingDim``, ``CreateNumOfInstancesDimInXDir``), which make
            the pattern parametric: ``set_dimension`` on them changes it.

    Returns:
        AdapterResult[str]: On success, ``data`` is a synthesised
        ``"LinearPattern_<count>x<spacing>_<rand>"`` ID — the COM method
        only returns a boolean, so no usable SW handle exists. ``metadata``
        has ``dimensions`` (see
        :func:`sketch_pattern_dimensions.new_dimensions`) and ``parametric``.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            inputs are invalid, an entity isn't registered, or
            ``CreateLinearSketchStepAndRepeat`` returns ``False``.

    Example::

        # 5 copies of Line_1 along +X, 15 mm apart
        result = pywin32_sketch_ops.sketch_linear_pattern(
            adapter, ["Line_1"], direction_x=1.0, direction_y=0.0,
            spacing=15.0, count=5
        )
        print(result.data)  # "LinearPattern_5x15.0_8765"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _linear_pattern_operation() -> str:
        if not entities:
            raise Exception("sketch_linear_pattern requires at least one entity")
        if count < 2:
            raise Exception("sketch_linear_pattern requires count >= 2")
        if spacing <= 0:
            raise Exception("sketch_linear_pattern requires spacing > 0")
        if math.hypot(direction_x, direction_y) < 1e-9:
            raise Exception(
                "sketch_linear_pattern requires a non-zero direction vector"
            )

        # Validate every entity ID exists in the registry before mutating
        # selection state, so an unknown ID doesn't leave SW with a
        # half-built selection.
        for ent_id in entities:
            if ent_id not in adapter._sketch_entities:
                raise Exception(
                    f"Unknown sketch entity '{ent_id}'. Use IDs returned by "
                    "add_line/add_arc/add_circle/add_spline/add_centerline."
                )

        before = _pattern_dims.sketch_dimensions(adapter) if add_dimensions else {}
        # Clear any pre-existing selection so SW only sees the seed entities.
        adapter.currentModel.ClearSelection2(True)
        try:
            _select_sketch_entities(adapter, entities, mark=0)

            angle_x = math.atan2(direction_y, direction_x)
            # Direction 2 (Y) goes 90° from direction 1; NumY=1 keeps it
            # single-row so the second-axis spacing/angle aren't actually
            # consumed, but SW still wants well-formed values.
            angle_y = angle_x + math.pi / 2.0

            ok = adapter.currentSketchManager.CreateLinearSketchStepAndRepeat(
                count,  # NumX
                1,  # NumY
                spacing / 1000.0,  # SpacingX (metres)
                0.0,  # SpacingY
                angle_x,  # AngleX (radians)
                angle_y,  # AngleY (radians)
                "",  # DeleteInstances
                add_dimensions,  # XSpacingDim
                False,  # YSpacingDim
                False,  # AngleDim (between the two directions)
                add_dimensions,  # CreateNumOfInstancesDimInXDir
                False,  # CreateNumOfInstancesDimInYDir
            )
            if not ok:
                raise Exception("Failed to create linear sketch pattern")
        finally:
            adapter.currentModel.ClearSelection2(True)

        if add_dimensions:
            found["dimensions"] = _pattern_dims.new_dimensions(
                adapter, before, _pattern_dims.LINEAR_ROLES
            )
        return f"LinearPattern_{count}x{spacing}_{int(time.time() * 1000) % 10000}"

    found: dict[str, Any] = {"parametric": add_dimensions, "dimensions": []}
    result = cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "sketch_linear_pattern", _linear_pattern_operation
        ),
    )
    if result.is_success:
        result.metadata = {**(result.metadata or {}), **found}
    return result


def _sketch_circular_pattern_impl(
    adapter: Any,
    entities: list[str],
    angle: float,
    count: int,
    center_x: float = 0.0,
    center_y: float = 0.0,
    add_dimensions: bool = True,
) -> AdapterResult[str]:
    """Create a circular sketch pattern from the registered seed entities.

    Selects ``entities`` then calls
    ``ISketchManager::CreateCircularSketchStepAndRepeat(ArcRadius, ArcAngle,
    PatternNum, PatternSpacing, PatternRotate, DeleteInstances, RadiusDim,
    AngleDim, CreateNumOfInstancesDim)``.

    ``ArcRadius`` is the radius at which SW places the pattern instances
    — getting it wrong puts every copy on a tight cluster around the
    seed rather than the intended ring.  We derive it from the first
    registered entity's centre (via ``ISketchArc.GetCenterPoint`` once
    the dispatch is flagged) relative to the pattern centre
    ``(center_x, center_y)``.  The COM API has no pattern-centre
    parameter — the rotation axis is implied by ``(ArcRadius, ArcAngle)``
    relative to the seed — so the centre is given that way: the distance
    from the seed to it, and the direction from the seed toward it.

    ``PatternSpacing`` is the per-instance angle in radians.  For a
    full 360° pattern we use ``angle / count`` so the last instance
    lands one slot before the seed (tiles cleanly).  For partial sweeps
    (< 360°) we use ``angle / (count - 1)`` so the last instance lands
    at the full requested angle.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        entities: Registered entity IDs to pattern.  Must be non-empty.
        angle: Total swept angle in **degrees** (e.g. ``360`` for a full
            ring or ``180`` for a half-circle).  Must be > 0.
        count: Total number of instances (including the seed).  Must be
            at least 2.
        center_x: The pattern centre's x in sketch millimetres.
        center_y: The pattern centre's y in sketch millimetres.
        add_dimensions: Show the radius, angle and instance-count
            dimensions (``RadiusDim``, ``AngleDim``,
            ``CreateNumOfInstancesDim``), which make the pattern parametric.

    Returns:
        AdapterResult[str]: On success, ``data`` is a synthesised
        ``"CircularPattern_<count>x<angle>deg_<rand>"`` ID — the COM
        method only returns a boolean. ``metadata`` has ``dimensions``,
        ``parametric``, ``center`` (mm) and ``radius`` (mm, seed to centre).

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            inputs are invalid, an entity isn't registered, or the COM
            call returns ``False``.

    Example::

        # 6 evenly-spaced copies of Circle_1 around the origin
        result = pywin32_sketch_ops.sketch_circular_pattern(
            adapter, ["Circle_1"], 360.0, 6
        )
        print(result.data)  # "CircularPattern_6x360.0deg_4321"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _circular_pattern_operation() -> str:
        if not entities:
            raise Exception("sketch_circular_pattern requires at least one entity")
        if count < 2:
            raise Exception("sketch_circular_pattern requires count >= 2")
        if angle <= 0:
            raise Exception("sketch_circular_pattern requires angle > 0")

        # Validate every entity ID exists before mutating selection state.
        for ent_id in entities:
            if ent_id not in adapter._sketch_entities:
                raise Exception(
                    f"Unknown sketch entity '{ent_id}'. Use IDs returned by "
                    "add_line/add_arc/add_circle/add_spline/add_centerline."
                )

        before = _pattern_dims.sketch_dimensions(adapter) if add_dimensions else {}
        adapter.currentModel.ClearSelection2(True)
        try:
            _select_sketch_entities(adapter, entities, mark=0)

            # The COM API doesn't take pattern-centre coordinates directly.
            # Instead:
            #   ``ArcRadius`` = distance from the seed to the rotation axis.
            #   ``ArcAngle``  = angle (radians) **from the seed toward the
            #                   rotation axis**, NOT a starting angle. With
            #                   ArcAngle=0 SW puts the axis at +X relative
            #                   to the seed.
            # We recover both by reading the seed's centre via
            # ``ISketchArc.GetCenterPoint`` (after flagging the dispatch
            # with sw_type_info — pywin32 late binding otherwise resolves
            # the method as a tuple-valued property) and computing the
            # offset to the requested ``(center_x, center_y)``.
            #
            # Without this fix, ArcAngle=0 + ArcRadius=1 mm puts every
            # instance on a tiny ring beside the seed instead of the
            # intended pattern (caught by the #17 live screenshot). Falls
            # back to placing the axis at angle π from the seed if
            # GetCenterPoint isn't available; that still works when the
            # user positions the seed on the +X side of the origin.
            first_entity = adapter._sketch_entities.get(entities[0])

            seed_xy: tuple[float, float] | None = None

            # Group entities (polygons via ``CreatePolygon``, rectangles via
            # ``CreateCornerRectangle``) register as a SAFEARRAY of segments
            # — a tuple, not a single dispatch — so the ``GetCenterPoint``
            # path below would silently fall back to a 1 mm placeholder
            # radius. ``_add_polygon_impl`` / ``_add_rectangle_impl`` stash
            # the seed centre in ``_sketch_entity_centers`` at register time;
            # use that. Any future tuple-registering primitive that forgets
            # to populate the cache falls through to the clear-error branch
            # below.
            if isinstance(first_entity, (list, tuple)):
                seed_xy = adapter._sketch_entity_centers.get(entities[0])
                if seed_xy is None:
                    # Polygons and rectangles always reach the cached branch
                    # above, so naming them here would be misleading — the
                    # caller is hitting this with a group seed whose
                    # ``add_*`` writer never stashed a centre. List only the
                    # always-works primitive types.
                    raise Exception(
                        f"sketch_circular_pattern can't derive the seed centre "
                        f"for '{entities[0]}' — this entity type registers as "
                        f"a group (tuple of segments) with no cached centre. "
                        f"Use a circle, arc, or ellipse seed."
                    )

            if seed_xy is None and first_entity is not None:
                # GetCenterPoint lives on multiple sketch-entity interfaces
                # (ISketchArc for arcs/circles, ISketchEllipse for ellipses),
                # all with the same zero-arg signature. Flag every interface
                # we might encounter so the lookup works regardless of seed
                # type — without this, an ellipse seed silently resolves
                # GetCenterPoint as a property and the pattern is laid out
                # at a bogus 1 mm radius.
                adapter._attempt(
                    lambda: _sw_type_info.flag_methods(
                        first_entity,
                        "ISketchArc",
                        "ISketchEllipse",
                    ),
                    default=0,
                )
                point = adapter._attempt(lambda: first_entity.GetCenterPoint())
                if point is not None and hasattr(point, "__len__") and len(point) >= 2:
                    seed_xy = (float(point[0]) * 1000.0, float(point[1]) * 1000.0)

            # Single-dispatch seeds without ``GetCenterPoint`` (line, spline,
            # centerline) used to fall through here with ``seed_xy is None``
            # and silently produce a 1 mm placeholder pattern at the wrong
            # radius — same bug class as the polygon/rectangle tuple case,
            # but quieter because no exception fires. Surface a clear error
            # naming the offending seed instead.
            if seed_xy is None:
                raise Exception(
                    f"sketch_circular_pattern can't derive the seed centre "
                    f"for '{entities[0]}' — this seed type has no "
                    f"GetCenterPoint dispatch on ISketchArc/ISketchEllipse. "
                    f"Use a circle, arc, ellipse, or polygon seed."
                )

            # SW's ``CreateCircularSketchStepAndRepeat`` has no
            # pattern-centre parameter: the centre is the point at
            # ``ArcRadius`` from the seed in the ``ArcAngle`` direction, so
            # dx/dy run from the seed to the requested centre.
            dx_mm = center_x - seed_xy[0]
            dy_mm = center_y - seed_xy[1]
            arc_radius_mm = math.hypot(dx_mm, dy_mm)
            found["radius"] = round(arc_radius_mm, 6)
            arc_angle_rad = math.atan2(dy_mm, dx_mm) if arc_radius_mm > 0 else 0.0

            # ``CreateCircularSketchStepAndRepeat`` silently returns False on
            # negative ``ArcAngle`` values — the bundled VBA/C# examples all
            # pass positive radians (e.g. ``4.732863934409`` ≈ 271°).  Python's
            # ``atan2`` produces ``-π`` for a seed on the +X axis (because
            # ``-seed_xy[1]`` is ``-0.0``), which is geometrically equivalent
            # to ``+π`` but fails the COM call.  Normalise to ``[0, 2π)``.
            if arc_angle_rad < 0:
                arc_angle_rad += 2.0 * math.pi

            # 1 mm minimum keeps SW from silently rejecting the call when
            # the seed sits right on the pattern centre.
            arc_radius_m = max(arc_radius_mm / 1000.0, 0.001)
            # For a full 360° pattern, ``angle / count`` keeps adjacent
            # instances evenly spaced (instance ``count`` would coincide
            # with the seed). For partial sweeps the last instance should
            # land at the full requested angle, so divide by ``count - 1``
            # instead — otherwise ``angle=180, count=3`` would reach only
            # 120°.
            angle_rad = math.radians(angle)
            if abs(angle - 360.0) < 1e-9:
                pattern_spacing = angle_rad / count
            else:
                pattern_spacing = angle_rad / (count - 1)

            ok = adapter.currentSketchManager.CreateCircularSketchStepAndRepeat(
                arc_radius_m,  # ArcRadius — seed-to-axis distance (metres)
                arc_angle_rad,  # ArcAngle — direction from seed to axis (radians)
                count,  # PatternNum
                pattern_spacing,  # PatternSpacing (radians)
                True,  # PatternRotate
                "",  # DeleteInstances
                add_dimensions,  # RadiusDim
                add_dimensions,  # AngleDim
                add_dimensions,  # CreateNumOfInstancesDim
            )
            if not ok:
                raise Exception("Failed to create circular sketch pattern")
        finally:
            adapter.currentModel.ClearSelection2(True)

        if add_dimensions:
            found["dimensions"] = _pattern_dims.new_dimensions(
                adapter, before, _pattern_dims.CIRCULAR_ROLES
            )
        return f"CircularPattern_{count}x{angle}deg_{int(time.time() * 1000) % 10000}"

    found: dict[str, Any] = {
        "parametric": add_dimensions,
        "dimensions": [],
        "center": {"x": center_x, "y": center_y},
    }
    result = cast(
        AdapterResult[str],
        adapter._handle_com_operation(
            "sketch_circular_pattern", _circular_pattern_operation
        ),
    )
    if result.is_success:
        result.metadata = {**(result.metadata or {}), **found}
    return result


def _sketch_mirror_impl(
    adapter: Any, entities: list[str], mirror_line: str
) -> AdapterResult[str]:
    """Mirror sketch entities about a registered centreline.

    Selects the ``entities`` under mark **1** and the ``mirror_line``
    centreline under mark **2** — those are the marks SOLIDWORKS expects
    per the ``IModelDoc2::SketchMirror`` documentation — then invokes the
    method with no arguments.  ``SketchMirror`` returns ``void``, so
    success is reported by the absence of a COM error and the resulting
    ID is a synthesised ``"Mirror_<mirror_line_id>_<rand>"`` string.

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        entities: Registered entity IDs to mirror.  Must be non-empty.
        mirror_line: Registered entity ID of the centreline.  Must be a
            value previously returned by ``add_centerline``.

    Returns:
        AdapterResult[str]: On success, ``data`` is a synthesised mirror
        ID.  On failure, ``status`` is ``ERROR`` with the COM error or a
        validation message describing the problem.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when the
            inputs are invalid, an entity isn't in the registry, or the
            COM call raises.

    Example::

        result = pywin32_sketch_ops.sketch_mirror(
            adapter, ["Line_1", "Line_2"], "Centerline_1"
        )
        print(result.data)  # "Mirror_Centerline_1_3456"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _mirror_operation() -> str:
        if not entities:
            raise Exception("sketch_mirror requires at least one entity")
        if not mirror_line:
            raise Exception(
                "sketch_mirror requires a mirror_line entity ID (add_centerline)"
            )
        if mirror_line not in adapter._sketch_entities:
            raise Exception(
                f"Unknown mirror_line entity '{mirror_line}'. Use the ID "
                "returned by add_centerline."
            )
        # IModelDoc2::SketchMirror specifies that the mirror axis must be
        # a centreline; selecting any other segment under mark=2 silently
        # no-ops on SW. ``add_centerline`` returns IDs prefixed with
        # ``Centerline_``, so reject anything else up front.
        # TODO(#24-followup): replace the prefix-string parse with a
        # proper introspection of the entity's ``ConstructionGeometry``
        # property on the real adapter (still keep the prefix check on
        # the mock, where there's no real dispatch to inspect). The
        # prefix parse is brittle if either side renames its ID scheme;
        # the SW invariant we're really enforcing is "this segment has
        # construction-geometry flagged".
        if not mirror_line.startswith("Centerline_"):
            raise Exception(
                f"mirror_line must be a centerline (from add_centerline), "
                f"got '{mirror_line}'"
            )

        # Validate every source entity ID up front so an unknown ID does
        # not leave SW with a half-built selection state.
        for ent_id in entities:
            if ent_id not in adapter._sketch_entities:
                raise Exception(
                    f"Unknown sketch entity '{ent_id}'. Use IDs returned by "
                    "add_line/add_arc/add_circle/add_spline/add_centerline."
                )

        adapter.currentModel.ClearSelection2(True)
        try:
            # Mark 1 for the source segments per IModelDoc2::SketchMirror docs.
            _select_sketch_entities(adapter, entities, mark=1)
            # Mark 2 for the centreline.
            _select_sketch_entities(adapter, [mirror_line], mark=2)

            # IModelDoc2::SketchMirror is VT_VOID — no return value, so a
            # successful invocation is its own success signal.
            adapter.currentModel.SketchMirror()
        finally:
            adapter.currentModel.ClearSelection2(True)

        return f"Mirror_{mirror_line}_{int(time.time() * 1000) % 10000}"

    return cast(
        AdapterResult[str],
        adapter._handle_com_operation("sketch_mirror", _mirror_operation),
    )


def _sketch_offset_impl(
    adapter: Any,
    entities: list[str],
    offset_distance: float,
    reverse_direction: bool,
) -> AdapterResult[str]:
    """Offset selected sketch entities by a fixed distance.

    Selects ``entities`` then calls
    ``ISketchManager::SketchOffset2(Offset, BothDirections, Chain, CapEnds,
    MakeConstruction, AddDimensions)``.

    ``Offset`` is in metres; a negative value flips the offset direction
    per the COM docs, so ``reverse_direction=True`` is implemented by
    negating the value rather than relying on ``BothDirections``.

    The remaining flags are pinned to predictable defaults so the call is
    deterministic for automation: no caps, no auto-conversion to
    construction geometry, no on-canvas dimension, and ``Chain=False`` so
    only the supplied entities are offset (rather than the entire
    contour they belong to).

    Args:
        adapter: A ``PyWin32Adapter`` with an open sketch.
        entities: Registered entity IDs to offset.  Must be non-empty.
        offset_distance: Offset distance in **millimetres**.  Must be > 0
            — the direction is controlled by ``reverse_direction``, not
            the sign.
        reverse_direction: When ``True``, offset in the opposite of SW's
            default direction (effectively a negated ``Offset`` argument).

    Returns:
        AdapterResult[str]: On success, ``data`` is a synthesised
        ``"Offset_<distance>_<direction>_<rand>"`` ID.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when
            inputs are invalid, an entity isn't registered, or the COM
            call returns ``False``.

    Example::

        result = pywin32_sketch_ops.sketch_offset(
            adapter, ["Line_1"], 5.0, reverse_direction=False
        )
        print(result.data)  # "Offset_5.0_outward_9876"
    """
    if not adapter.currentSketchManager:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active sketch")

    def _offset_operation() -> str:
        if not entities:
            raise Exception("sketch_offset requires at least one entity")
        if offset_distance <= 0:
            raise Exception(
                "sketch_offset requires offset_distance > 0 — use "
                "reverse_direction to flip the side"
            )

        # Validate every entity ID up front so an unknown ID does not
        # leave SW with a half-built selection.
        for ent_id in entities:
            if ent_id not in adapter._sketch_entities:
                raise Exception(
                    f"Unknown sketch entity '{ent_id}'. Use IDs returned by "
                    "add_line/add_arc/add_circle/add_spline/add_centerline."
                )

        adapter.currentModel.ClearSelection2(True)
        try:
            _select_sketch_entities(adapter, entities, mark=0)

            # Negative Offset flips the side per SketchOffset2 docs.
            offset_m = (
                -offset_distance / 1000.0
                if reverse_direction
                else offset_distance / 1000.0
            )

            adapter._attempt(
                lambda: _sw_type_info.flag_methods(
                    adapter.currentSketchManager, "ISketchManager"
                ),
                default=0,
            )
            ok = adapter.currentSketchManager.SketchOffset2(
                offset_m,  # Offset (metres)
                False,  # BothDirections
                False,  # Chain
                0,  # CapEnds (swSkOffsetCapEndType_e: 0 = no caps)
                0,  # MakeConstruction (swSkOffsetMakeConstructionType_e: 0 = none)
                False,  # AddDimensions
            )
            if not ok:
                raise Exception("Failed to offset sketch entities")
        finally:
            adapter.currentModel.ClearSelection2(True)

        direction = "inward" if reverse_direction else "outward"
        return f"Offset_{offset_distance}_{direction}_{int(time.time() * 1000) % 10000}"

    return cast(
        AdapterResult[str],
        adapter._handle_com_operation("sketch_offset", _offset_operation),
    )


#: What a COM read gives when it raised.
_NOT_READ = object()


def sketch_is_empty(adapter: Any, sketch: Any) -> bool:
    """Whether an open sketch has nothing in it: no segment, point or text.

    True only when ``ISketch.GetSketchSegments``, ``GetSketchPoints2`` and
    ``GetSketchTextSegments`` all read and give nothing (each answers null
    on an empty sketch); a read that fails is not taken for empty.

    Args:
        adapter: The adapter (for ``_attempt``).
        sketch: The open ``ISketch``, or None.

    Returns:
        bool: True for an empty sketch.
    """
    if sketch is None:
        return False
    from .com_utils import flag

    readers = ("GetSketchSegments", "GetSketchPoints2", "GetSketchTextSegments")
    flag(adapter, sketch, *readers)
    for reader in readers:
        found = adapter._attempt(lambda r=reader: getattr(sketch, r)(), _NOT_READ)
        if found is _NOT_READ or found:
            return False
    return True


def _exit_sketch_impl(adapter: Any) -> AdapterResult[None]:
    """Exit any sketch-edit mode the active model is in and reset adapter state.

    The previous implementation trusted ``adapter.currentSketchManager`` —
    a Python-side handle populated only by ``create_sketch`` on **this**
    adapter instance.  A fresh adapter pointing at a SolidWorks process
    that already has a sketch open (from a crashed prior run, an
    aborted automation, or a manual user edit) would report
    ``WARNING: "No active sketch to exit"`` while SW was still sitting
    in sketch-edit mode — and every subsequent ``create_sketch`` then
    failed with ``Failed to select plane: Front Plane`` because SW
    can't open a new sketch while one is already active.

    Now queries ``IModelDoc2.GetActiveSketch2`` to find out what SW
    actually has open, and toggles ``SketchManager.InsertSketch(True)``
    when either SW or the adapter thinks a sketch is in edit mode.
    Adapter-side state is always cleared on success.

    A sketch ``edit_sketch`` opened on a part with no unsaved changes, and
    left as it was (``feature_editing.unchanged_since_opened``), is exited
    with ``InsertSketch(False)``: nothing changed, so nothing is rebuilt.
    Such a round trip made only to read the geometry marked the Lid part
    modified. The result's ``metadata`` then says ``unchanged`` and the
    document's ``is_dirty`` afterwards: ``IModelDoc2.SetSaveFlag`` only
    sets the flag, so a flag SolidWorks set can't be cleared.

    A sketch exited with nothing in it is deleted by SolidWorks (pitfall
    #45). When the sketch's segments, points and texts all read and there
    are none, its name is looked up after the exit; when it is gone,
    ``metadata`` says ``discarded`` (the name) and ``_last_sketch_name`` is
    cleared, so no later feature reaches for it. The Dipper made 19 empty
    sketches to read their axes, and a later ``edit_sketch`` found none.

    A sketch ``edit_sketch`` opened and changed is rebuilt on exit; the
    part's solid bodies, counted before it opened and after the rebuild,
    are in ``metadata`` (``rebuild_diagnostics.body_counts``). An outline
    left 2.5 mm short of the strip became a second body that way, unseen.

    Args:
        adapter: A ``PyWin32Adapter`` with a non-``None`` ``currentModel``.

    Returns:
        AdapterResult[None]: On success, ``status`` is ``SUCCESS``, with
        ``metadata`` ``{"unchanged": True, "rebuilt": False, "is_dirty": ...}``
        after a read-only round trip, ``discarded`` for an empty sketch
        SolidWorks deleted, and the body counts after an edit.
        When neither SW nor the adapter has an active sketch,
        ``status`` is ``WARNING`` (already-exited is not a failure).
        When ``currentModel`` is ``None``, also returns ``WARNING`` so
        defensive cleanup callers don't see spurious errors.

    Raises:
        Exception: Propagated through ``_handle_com_operation`` when the
            ``InsertSketch`` call itself raises.

    Example::

        pywin32_sketch_ops.add_line(adapter, 0, 0, 50, 0)
        pywin32_sketch_ops.exit_sketch(adapter)
        # adapter.currentSketch is now None and SW is out of sketch-edit mode
    """
    if adapter.currentModel is None:
        # No document means nothing can be in sketch-edit mode either.
        # Match the legacy "already exited" semantics so cleanup callers
        # that fire exit_sketch defensively don't see spurious errors.
        return AdapterResult(
            status=AdapterResultStatus.WARNING,
            error="No active sketch to exit",
        )

    round_trip: dict[str, Any] = {}

    def _exit_operation() -> str:
        from .bodies import solid_body_names
        from .feature_editing import save_flag, unchanged_since_opened
        from .rebuild_diagnostics import body_counts

        # ``GetActiveSketch2`` is a real zero-arg method on IModelDoc2.
        # Without flagging, pywin32 late binding resolves it as a property
        # and SW returns ``Member not found`` — the same root cause as
        # the cross-thread bugs in runbook #5.
        adapter._attempt(
            lambda: _sw_type_info.flag_methods(adapter.currentModel, "IModelDoc2"),
            default=0,
        )

        sw_active = adapter._attempt(lambda: adapter.currentModel.GetActiveSketch2())
        adapter_active = adapter.currentSketchManager

        # Already out of sketch-edit mode — clean up adapter state so a
        # future create_sketch starts from a known-good baseline, then
        # warn.  Using ``data`` to signal "no_op" lets callers tell the
        # difference between "I exited a sketch" and "nothing was open".
        if sw_active is None and adapter_active is None:
            adapter._opened_sketch = None
            return "no_active_sketch"

        name = adapter._last_sketch_name
        opened = getattr(adapter, "_opened_sketch", None) or {}
        # The bodies edit_sketch counted, for the sketch it opened.
        bodies_before = (
            opened.get("solid_bodies") if opened.get("name") == name else None
        )
        open_sketch = adapter.currentSketch or sw_active
        empty = sketch_is_empty(adapter, open_sketch)
        # Read before leaving: still in edit mode, as edit_sketch found it?
        unchanged = unchanged_since_opened(adapter, open_sketch)

        # Prefer the adapter's SketchManager handle when available (it was
        # captured at create_sketch time on the executor thread, so it's
        # apartment-safe); fall back to a fresh ``currentModel.SketchManager``
        # for the SW-only state case.
        sketch_manager = adapter_active or adapter.currentModel.SketchManager
        adapter._attempt(
            lambda: _sw_type_info.flag_methods(sketch_manager, "ISketchManager"),
            default=0,
        )
        # Hand the document back with SolidWorks' default (inference on) for
        # interactive sketching; create_sketch turned AddToDB on.
        adapter._attempt(lambda: setattr(sketch_manager, "AddToDB", False))
        # InsertSketch(UpdateEditRebuild): rebuild only when something changed.
        sketch_manager.InsertSketch(not unchanged)
        adapter.currentSketch = None
        adapter.currentSketchManager = None
        adapter._reset_sketch_entity_registry()
        if unchanged:
            round_trip.update(
                unchanged=True,
                rebuilt=False,
                is_dirty=save_flag(adapter, adapter.currentModel),
            )
        elif bodies_before is not None:
            round_trip.update(
                body_counts(
                    bodies_before, solid_body_names(adapter, adapter.currentModel)
                )
            )
        if empty and name:
            left = adapter._attempt(
                lambda: adapter.currentModel.FeatureByName(name), _NOT_READ
            )
            if left is None:  # looked up, and gone
                round_trip["discarded"] = name
                adapter._last_sketch_name = None
        return "exited"

    result = cast(
        AdapterResult[str],
        adapter._handle_com_operation("exit_sketch", _exit_operation),
    )
    # Translate "no sketch was open" into a WARNING so callers that branch
    # on ``is_error`` still treat already-exited as benign.  ``data`` is
    # the operation tag; ``error`` carries the human message.
    if result.is_success and result.data == "no_active_sketch":
        return cast(
            AdapterResult[None],
            AdapterResult(
                status=AdapterResultStatus.WARNING,
                error="No active sketch to exit",
            ),
        )
    if result.is_success:
        return cast(
            AdapterResult[None],
            AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data=None,
                execution_time=result.execution_time,
                metadata=dict(round_trip) or None,
            ),
        )
    return cast(AdapterResult[None], result)


def _check_sketch_fully_defined_impl(
    adapter: Any,
    sketch_name: str | None = None,
) -> AdapterResult[dict[str, Any]]:
    """Check whether a sketch is fully defined (``ISketch.GetConstrainedStatus``).

    The earlier version probed members such as ``IsFullyConstrained``,
    ``GetStatus`` and ``FullyDefined``, none of which ``ISketch`` or
    ``IFeature`` has in SolidWorks' type library, so every sketch read
    ``unknown`` / ``unavailable``. ``GetConstrainedStatus`` is the member
    (``swConstrainedStatus_e``); see ``sketch_relations.constrained_status``,
    which also counts the over-defining and dangling relations.

    Args:
        adapter: A ``PyWin32Adapter`` with a non-``None`` ``currentModel``.
        sketch_name: Name of the sketch feature as it appears in the
            FeatureManager tree (e.g. ``"Sketch1"``).  When ``None``, the
            open sketch is read, then the adapter's ``_last_sketch_name``.

    Returns:
        AdapterResult[dict[str, Any]]: On success, ``data`` has
        ``sketch_name``, ``is_fully_defined`` (None when SolidWorks can't
        tell), ``definition_state`` (``fully_defined``, ``under_defined``,
        ``over_defined``, ``no_solution``, ``invalid_solution``,
        ``autosolve_off`` or ``unknown``), ``source``, ``raw_status`` and,
        when they read, ``over_defining_relations`` and
        ``dangling_relations``. On failure, ``status`` is ``ERROR``.
    """
    if not adapter.currentModel:
        return AdapterResult(status=AdapterResultStatus.ERROR, error="No active model")

    def _sketch_of(name: str) -> Any:
        feature = adapter._attempt(
            lambda: adapter.currentModel.FeatureByName(name), default=None
        )
        if not feature:
            raise Exception(f"Sketch not found: {name}")
        _flag(adapter, feature, "GetSpecificFeature2")
        found = adapter._attempt(lambda: feature.GetSpecificFeature2(), default=None)
        if found is None:
            raise Exception(f"SolidWorks gave no sketch for {name!r}")
        return found

    def _get_sketch_payload() -> dict[str, Any]:
        resolved_name = sketch_name
        if sketch_name:
            sketch_obj = _sketch_of(sketch_name)
        else:
            _flag(adapter, adapter.currentModel, "GetActiveSketch2")
            sketch_obj = adapter.currentSketch or adapter._attempt(
                lambda: adapter.currentModel.GetActiveSketch2(), default=None
            )
            if sketch_obj is not None:
                resolved_name = adapter._last_sketch_name
            elif adapter._last_sketch_name:
                resolved_name = adapter._last_sketch_name
                sketch_obj = _sketch_of(resolved_name)
            else:
                raise Exception("No active sketch found")
        return {
            "sketch_name": resolved_name,
            **sketch_relations.constrained_status(adapter, sketch_obj),
        }

    return cast(
        AdapterResult[dict[str, Any]],
        adapter._handle_com_operation(
            "check_sketch_fully_defined", _get_sketch_payload
        ),
    )
