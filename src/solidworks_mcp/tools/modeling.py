"""Modeling tools for SolidWorks MCP Server.

Provides tools for creating and manipulating SolidWorks models, including parts,
assemblies, drawings, and features like extrusions, revolves, etc.
"""

from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from ..adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    LoftParameters,
    RevolveParameters,
    SolidWorksAdapter,
    SweepParameters,
)
from ..adapters.solidworks.cut_check import shortfall_warning
from ..adapters.solidworks.edge_feature_check import warnings as edge_feature_warnings
from ..adapters.solidworks.plane_frame import add, scale, two_points, unit, vector
from ..adapters.solidworks.rebuild_diagnostics import issues_warning
from ..adapters.solidworks.reference_geometry import LINE_MM
from .input_compat import CompatInput
from .sketching import AddSketchTextInput, text_warnings
from .tool_boundary import (
    ToolOperationError,
    normalize_input,
    require_capability,
    require_success,
    with_tool_boundary,
)

# Input schemas using Python 3.14 built-in types


def _with_units(value: Any, units: str) -> str:
    """``12 mm``, ``180 deg`` or ``2 (count)`` for a reply message."""
    shown = f"{value:g}" if isinstance(value, int | float) else str(value)
    return f"{shown} (count)" if units == "count" else f"{shown} {units}"


def _result_value(data: Any, *keys: str, default: Any = None) -> Any:
    """Build internal result value.

    Args:
        data (Any): The data value.
        *keys (str): Additional positional arguments forwarded to the call.
        default (Any): Fallback value returned when the operation fails. Defaults to None.

    Returns:
        Any: The result produced by the operation.
    """
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return default

    for key in keys:
        if hasattr(data, key):
            value = getattr(data, key)
            if value is not None:
                return value
    return default


async def _read_summary(adapter: Any) -> AdapterResult[dict[str, Any]] | None:
    """Read the geometry summary, or ``None`` if the adapter has no such read.

    Args:
        adapter: The active adapter.

    Returns:
        AdapterResult[dict[str, Any]] | None: The summary result.
    """
    reader = getattr(adapter, "get_model_geometry_summary", None)
    if reader is None:
        return None
    result: AdapterResult[dict[str, Any]] = await reader()
    return result


def _box_size(summary: dict[str, Any]) -> Any:
    """Return a summary's bounding-box size ``[x, y, z]``, or ``None``."""
    box = summary.get("bounding_box_mm")
    return box.get("size") if isinstance(box, dict) else None


def _attach_measured_change(
    reply: dict[str, Any],
    before: AdapterResult[dict[str, Any]] | None,
    after: AdapterResult[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Add the feature's measured volume and size change to its reply.

    Best-effort: when the "before" read failed (or the adapter has none) the
    reply is returned unchanged; anything else unmeasurable is ``None`` with a
    ``note``. A feature that succeeded without changing the volume gets a
    ``warning``, since that is how SolidWorks reports a silent no-op.

    Args:
        reply: The feature tool's success reply.
        before: Summary read before the feature.
        after: Summary read after the feature.

    Returns:
        dict[str, Any]: ``reply``, with ``measured_change`` when measurable.
    """
    if before is None or not before.is_success:
        return reply
    old = before.data or {}
    new = (after.data or {}) if after is not None and after.is_success else None
    volume_before = old.get("volume_mm3")
    volume_after = new.get("volume_mm3") if new is not None else None
    numbers = (int, float)
    delta = (
        round(volume_after - volume_before, 4)
        if isinstance(volume_before, numbers) and isinstance(volume_after, numbers)
        else None
    )
    change: dict[str, Any] = {
        "volume_before_mm3": volume_before,
        "volume_after_mm3": volume_after,
        "volume_delta_mm3": delta,
        "bounding_box_before_mm": _box_size(old),
        "bounding_box_after_mm": _box_size(new) if new is not None else None,
        "solid_body_count_before": old.get("solid_body_count"),
        "solid_body_count_after": (
            new.get("solid_body_count") if new is not None else None
        ),
    }
    if new is None:
        error = after.error if after is not None else None
        change["note"] = f"Could not read the model after the feature: {error}"
    elif delta is None:
        change["note"] = "Volume is not determinable with the active adapter"
    reply["measured_change"] = change
    if delta == 0:
        reply["warning"] = (
            "The feature reported success but changed no volume; SolidWorks "
            "may have accepted it without doing anything. Check the sketch, "
            "the direction and the depth."
        )
    return reply


def _added_nothing(
    before: AdapterResult[dict[str, Any]] | None,
    after: AdapterResult[dict[str, Any]] | None,
) -> bool:
    """Whether a boss left the volume as it was (it grew into the part).

    Returns:
        bool: True only when both volumes were read and they are the same;
        False when either can't be read.
    """
    if before is None or after is None or not (before.is_success and after.is_success):
        return False
    old = (before.data or {}).get("volume_mm3")
    new = (after.data or {}).get("volume_mm3")
    if not isinstance(old, int | float) or not isinstance(new, int | float):
        return False
    return float(new) - float(old) <= 1e-6 * max(abs(float(old)), 1.0)


def sketch_point(axes: Any, point_mm: list[float]) -> tuple[float, float] | None:
    """A model point in a sketch's own x and y, from the sketch's ``axes``.

    Args:
        axes: ``create_sketch``'s ``axes`` (``x``, ``y`` unit vectors and
            ``origin_mm`` in model coordinates), or None.
        point_mm: The point in model mm.

    Returns:
        tuple[float, float] | None: Sketch (x, y) in mm, or None without axes.
    """
    if not isinstance(axes, dict):
        return None
    origin, x_axis, y_axis = (_vector3(axes.get(k)) for k in ("origin_mm", "x", "y"))
    if origin is None or x_axis is None or y_axis is None:
        return None
    offset = [float(p) - o for p, o in zip(point_mm, origin, strict=True)]
    return (
        round(sum(d * a for d, a in zip(offset, x_axis, strict=True)), 9),
        round(sum(d * a for d, a in zip(offset, y_axis, strict=True)), 9),
    )


def _vector3(value: Any) -> list[float] | None:
    """Three numbers as floats, or None when ``value`` isn't three numbers."""
    if not isinstance(value, list | tuple) or len(value) != 3:
        return None
    if not all(isinstance(c, int | float) for c in value):
        return None
    return [float(c) for c in value]


def _flag_a_shallow_cut(
    reply: dict[str, Any], applied: Any, input_data: Any
) -> dict[str, Any]:
    """Warn when a blind cut removed far less than its floor area x depth.

    Args:
        reply: The cut's reply, with ``measured_change`` when measured.
        applied: The cut's ``parameters`` from the adapter.
        input_data: The cut's input (``end_condition``, ``depth``).

    Returns:
        dict[str, Any]: ``reply``, with ``cut_extrude.floor_area_mm2`` and a
        ``warning`` when the depth ran outside the part.
    """
    floor = applied.get("floor_area_mm2") if isinstance(applied, dict) else None
    if not isinstance(floor, int | float):
        return reply
    reply["cut_extrude"]["floor_area_mm2"] = round(float(floor), 4)
    delta = (reply.get("measured_change") or {}).get("volume_delta_mm3")
    if str(input_data.end_condition).lower() != "blind" or not isinstance(
        delta, int | float
    ):
        return reply
    warning = shortfall_warning(-float(delta), float(floor), float(input_data.depth))
    if warning is not None:
        own = reply.get("warning")
        reply["warning"] = f"{own} {warning}" if own else warning
    return reply


def _add_warnings(reply: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    """Append ``warnings`` to the reply's ``warning``, after any it has."""
    texts = [reply["warning"]] if reply.get("warning") else []
    texts += [w for w in warnings if w]
    if texts:
        reply["warning"] = " ".join(texts)
    return reply


def _edge_feature_reply(
    kind: str, reply: dict[str, Any], feature: Any
) -> dict[str, Any]:
    """Add what a new fillet or chamfer took to its reply.

    The adapter read the part before and after (``geometry_before`` /
    ``geometry_after``), the edges selected and the edges the feature holds,
    and the features below the rollback bar (``edge_feature_check``).

    Args:
        kind: ``"fillet"`` or ``"chamfer"``, the reply's key.
        reply: The success reply.
        feature: The adapter's feature.

    Returns:
        dict[str, Any]: ``reply``, with ``edges_selected`` and ``edges_held``
        in its ``kind`` entry, ``measured_change`` when the part read, and a
        ``warning`` when fewer edges were kept or the rollback bar is up.
    """
    properties = _result_value(feature, "properties", default={})
    if not isinstance(properties, dict):
        properties = {}
    entry = reply[kind]
    entry["edges_selected"] = properties.get("edges_selected")
    entry["edges_held"] = properties.get("edges_held")
    before = properties.get("geometry_before")
    if isinstance(before, dict):
        after = properties.get("geometry_after")
        reply = _attach_measured_change(
            reply,
            AdapterResult(status=AdapterResultStatus.SUCCESS, data=before),
            AdapterResult(status=AdapterResultStatus.SUCCESS, data=after)
            if isinstance(after, dict)
            else AdapterResult(
                status=AdapterResultStatus.ERROR, error="the summary didn't read"
            ),
        )
    return _add_warnings(
        reply, edge_feature_warnings(kind, str(entry["name"]), properties)
    )


def _cut_direction_note(applied: Any) -> str:
    """Say in a cut's message which side it cut, when it was not the one wanted.

    Args:
        applied: The cut's ``parameters`` from the adapter.

    Returns:
        str: A note in parentheses, or ``""`` when the cut went the way asked
        (or, with no direction given, along the sketch normal as compared).
    """
    if not isinstance(applied, dict):
        return ""
    flipped = bool(applied.get("direction_flipped"))
    choice = applied.get("direction_choice")
    removed = applied.get("volume_removed_mm3")
    if choice == "not_compared" and applied.get("direction_note"):
        return f" (the two sides were not compared: {applied['direction_note']})"
    if flipped and isinstance(removed, dict):
        return (
            " (it cut against the sketch normal, the side with more material: "
            f"{removed.get('against_normal', 0.0):.4g} mm3 against "
            f"{removed.get('along_normal', 0.0):.4g} mm3 along it)"
        )
    if flipped and choice == "only_side_with_material":
        return (
            " (the side along the sketch normal had no material, so it cut the "
            "other way)"
        )
    if flipped:
        return " (the requested direction found no material, so it cut the other way)"
    return ""


_START_OFFSET = (
    "Start the feature this many mm from the sketch plane instead of on it: "
    "positive along the sketch normal (the side create_extrusion grows into), "
    "negative the other way. Saves making a reference plane just to start "
    "at a depth; 0 starts on the plane"
)
_DRAFT_OUTWARD_BOSS = (
    "With draft_angle: false (the default) tapers the boss inward, narrower "
    "at its far end; true flares it outward, wider at its far end"
)
_DRAFT_OUTWARD_CUT = (
    "With draft_angle: false (the default) tapers the cut inward, narrower "
    "as it goes deeper; true flares it outward, wider as it goes deeper"
)


_REGIONS = (
    "'all' builds it from every region the sketch's contours enclose, which "
    "SolidWorks unions into one: for circles or polygons that cross or touch. "
    "A contour wholly inside another is filled too. Left out, a failure because "
    "contours cross is retried this way when no contour lies inside another, "
    "with a warning"
)


def _regions_reply(block: dict[str, Any], applied: Any) -> list[str]:
    """Put ``regions`` in a feature's reply block; the automatic union's note."""
    if not isinstance(applied, dict) or applied.get("regions") != "all":
        return []
    block["regions"] = "all"
    note = applied.get("regions_note")
    return [str(note)] if note else []


def _check_draft(draft_angle: float, draft_outward: bool) -> None:
    """Refuse a draft direction given without a draft angle.

    Raises:
        ValueError: ``draft_outward`` without a ``draft_angle``.
    """
    if draft_outward and not draft_angle:
        raise ValueError("draft_outward needs a draft_angle (degrees)")


def _offset_and_draft(input_data: Any) -> dict[str, Any]:
    """The start offset and draft direction a reply shows, when they were set."""
    shown: dict[str, Any] = {}
    if input_data.start_offset_mm:
        shown["start_offset_mm"] = input_data.start_offset_mm
    if input_data.draft_angle:
        shown["draft_angle"] = input_data.draft_angle
        shown["draft_outward"] = input_data.draft_outward
    return shown


class OpenModelInput(BaseModel):
    """Input schema for opening a SolidWorks model.

    Attributes:
        file_path (str): The file path value.
        read_only (bool): Open it so nothing can be saved over the file.
    """

    file_path: str = Field(
        description="Full path to the SolidWorks file (.sldprt, .sldasm, .slddrw)"
    )
    read_only: bool = Field(
        default=False,
        description=(
            "Open it read-only, to look at the file without risking it: "
            "SolidWorks then saves nothing over it. A document already open "
            "for editing stays as it is"
        ),
    )


class CreatePartInput(CompatInput):
    """Input schema for creating a new SolidWorks part.

    Attributes:
        material (str | None): The material value.
        name (str): The name value.
        template (str | None): The template value.
        units (str | None): The units value.
    """

    name: str = Field(
        description=(
            "A label for your own reference; SolidWorks names the new part "
            "itself (Part1, ...) and the file takes the name of the path "
            "you save it to with save_part"
        )
    )
    template: str | None = Field(
        default=None,
        description=(
            "Part template (.prtdot) path; omit to use the SolidWorks default"
        ),
    )
    units: str | None = Field(default=None, description="Document units")
    material: str | None = Field(default=None, description="Material name")

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the create part input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Name is required.
        """
        if not self.name.strip():
            raise ValueError("name is required")


class CreateExtrusionInput(CompatInput):
    """Input schema for creating an extrusion feature.

    Attributes:
        both_directions (bool): The both directions value.
        depth (float): The depth value.
        draft_angle (float): The draft angle value.
        draft_outward (bool): Draft outward instead of inward.
        end_condition (str): The end condition value.
        merge_result (bool): The merge result value.
        reverse (bool | None): The reverse value.
        reverse_direction (bool): The reverse direction value.
        sketch_name (str | None): The sketch to extrude; None for the last closed.
        start_offset_mm (float): Start this far off the sketch plane.
        thin_feature (bool): The thin feature value.
        thin_thickness (float | None): The thin thickness value.
        thin_side (str): The side a thin wall grows on.
    """

    sketch_name: str | None = Field(
        default=None,
        description=(
            'Sketch to use, by its name in the feature tree (e.g. "Sketch2"); omit to use the sketch `exit_sketch` closed last'
        ),
    )
    depth: float = Field(description="Extrusion depth in millimeters")
    reverse: bool | None = Field(default=None, description="Reverse direction alias")
    draft_angle: float = Field(default=0.0, description="Draft angle in degrees")
    draft_outward: bool = Field(default=False, description=_DRAFT_OUTWARD_BOSS)
    start_offset_mm: float = Field(default=0.0, description=_START_OFFSET)
    reverse_direction: bool = Field(
        default=False, description="Reverse extrusion direction"
    )
    both_directions: bool = Field(
        default=False,
        description=(
            "Extrude symmetrically about the sketch plane (mid-plane): `depth` is "
            "the total thickness, half on each side"
        ),
    )
    thin_feature: bool = Field(
        default=False,
        description=(
            "Make a thin wall of `thin_thickness` from the sketch's outline; the "
            "sketch may be an open chain (a bent plate's centreline)"
        ),
    )
    thin_thickness: float | None = Field(
        default=None, description="Wall thickness in mm; required with thin_feature"
    )
    thin_side: Literal["one_side", "other_side", "mid_plane"] = Field(
        default="one_side",
        description=(
            "Which side of the sketch line the thin wall grows on: one_side, "
            "other_side, or mid_plane (half each side)"
        ),
    )
    end_condition: str = Field(default="Blind", description="End condition type")
    merge_result: bool = Field(default=True, description="Merge with existing geometry")
    regions: Literal["all"] | None = Field(default=None, description=_REGIONS)

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the create extrusion input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: A non-positive depth, or a thin wall without thickness.
        """
        if self.depth <= 0:
            raise ValueError("depth must be positive")
        if self.reverse is not None:
            self.reverse_direction = self.reverse
        if self.thin_feature and not (self.thin_thickness and self.thin_thickness > 0):
            raise ValueError("thin_feature needs a positive thin_thickness (mm)")
        _check_draft(self.draft_angle, self.draft_outward)


class CreateRevolveInput(CompatInput):
    """Input schema for creating a revolve feature.

    Attributes:
        angle (float): The angle value.
        axis_entity (str | None): A reference axis to revolve about.
        both_directions (bool): The both directions value.
        merge_result (bool): The merge result value.
        reverse_direction (bool): The reverse direction value.
        sketch_name (str | None): The sketch to revolve; None for the last closed.
        thin_feature (bool): The thin feature value.
        thin_thickness (float | None): The thin thickness value.
    """

    sketch_name: str | None = Field(
        default=None,
        description=(
            'Sketch to use, by its name in the feature tree (e.g. "Sketch2"); omit to use the sketch `exit_sketch` closed last'
        ),
    )
    axis_entity: str | None = Field(
        default=None,
        description=(
            "A reference axis to revolve about, by its name in the feature tree "
            '(e.g. "Axis1" from `create_axis`); omit to use the centreline '
            "drawn in the sketch"
        ),
    )
    angle: float = Field(description="Revolve angle in degrees")
    reverse_direction: bool = Field(
        default=False, description="Reverse revolve direction"
    )
    both_directions: bool = Field(
        default=False, description="Revolve in both directions"
    )
    thin_feature: bool = Field(default=False, description="Create as thin wall feature")
    thin_thickness: float | None = Field(
        default=None, description="Thickness for thin wall feature in mm"
    )
    merge_result: bool = Field(default=True, description="Merge with existing geometry")

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the create revolve input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Angle must be positive.
        """
        if self.angle <= 0:
            raise ValueError("angle must be positive")


class CreateCutRevolveInput(CompatInput):
    """Input schema for cutting a revolved profile out of the part.

    Attributes:
        angle (float): Degrees to revolve; 360 for a full turn.
        axis_entity (str | None): A reference axis to revolve about.
        both_directions (bool): Revolve half the angle each way.
        reverse_direction (bool): Revolve the other way round.
        sketch_name (str | None): The sketch to revolve; None for the last closed.
    """

    sketch_name: str | None = Field(
        default=None,
        description=(
            'Sketch to use, by its name in the feature tree (e.g. "Sketch2"); '
            "omit to use the sketch `exit_sketch` closed last"
        ),
    )
    axis_entity: str | None = Field(
        default=None,
        description=(
            "A reference axis to revolve about, by its name in the feature tree "
            '(e.g. "Axis1" from `create_axis`); omit to use the centreline '
            "drawn in the sketch"
        ),
    )
    angle: float = Field(
        default=360.0,
        gt=0,
        le=360,
        description="Degrees to revolve the profile; 360 (the default) cuts all round",
    )
    reverse_direction: bool = Field(
        default=False, description="Revolve the other way round the axis"
    )
    both_directions: bool = Field(
        default=False,
        description="Revolve `angle` degrees each way from the sketch (180 at most)",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a two-way revolve of more than a full turn.

        Raises:
            ValueError: ``both_directions`` with an angle over 180 degrees.
        """
        if self.both_directions and self.angle > 180:
            raise ValueError(
                f"both_directions revolves {self.angle:g} degrees each way, "
                f"{2 * self.angle:g} in all; give 180 or less, or leave "
                "both_directions off for a full turn"
            )


class CreateSweepInput(BaseModel):
    """Input schema for creating a sweep feature.

    Attributes:
        merge_result (bool): The merge result value.
        path (str): The path value.
        twist_along_path (bool): The twist along path value.
        twist_angle (float): The twist angle value.
    """

    path: str = Field(description="Name or ID of the sweep path")
    twist_along_path: bool = Field(default=False, description="Twist along path")
    twist_angle: float = Field(default=0.0, description="Twist angle in degrees")
    merge_result: bool = Field(default=True, description="Merge with existing geometry")


class CreateLoftInput(BaseModel):
    """Input schema for creating a loft feature.

    Attributes:
        end_tangent (str | None): The end tangent value.
        guide_curves (list[str] | None): The guide curves value.
        merge_result (bool): The merge result value.
        profiles (list[str]): The profiles value.
        start_tangent (str | None): The start tangent value.
    """

    profiles: list[str] = Field(description="List of profile names or IDs")
    guide_curves: list[str] | None = Field(
        default=None, description="List of guide curve names or IDs"
    )
    start_tangent: str | None = Field(
        default=None, description="Start tangent condition"
    )
    end_tangent: str | None = Field(default=None, description="End tangent condition")
    merge_result: bool = Field(default=True, description="Merge with existing geometry")


class GetDimensionInput(CompatInput):
    """Input schema for getting a dimension value.

    Attributes:
        dimension_name (str | None): The dimension name value.
        name (str | None): The name value.
    """

    name: str | None = Field(
        default=None,
        description="Dimension name (e.g., 'D1@Sketch1', 'D1@Boss-Extrude1')",
    )
    dimension_name: str | None = Field(default=None, description="Dimension name alias")

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the get dimension input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Name is required.
        """
        if self.name is None:
            self.name = self.dimension_name
        if not self.name:
            raise ValueError("name is required")


class SetDimensionInput(CompatInput):
    """Input schema for setting a dimension value.

    Attributes:
        dimension_name (str | None): The dimension name value.
        name (str | None): The name value.
        units (str | None): The units value.
        value (float): The value value.
    """

    name: str | None = Field(
        default=None,
        description="Dimension name (e.g., 'D1@Sketch1', 'D1@Boss-Extrude1')",
    )
    dimension_name: str | None = Field(default=None, description="Dimension name alias")
    value: float = Field(
        description=(
            "New value in the dimension's own unit: millimetres for a length, "
            "degrees for an angle, a whole number for a count (a pattern's "
            "instances). list_model_dimensions gives each one's `units`."
        )
    )
    units: str | None = Field(
        default=None, description="Not used; kept for old callers"
    )

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the set dimension input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Name is required.
        """
        if self.name is None:
            self.name = self.dimension_name
        if not self.name:
            raise ValueError("name is required")


class SetUnitsInput(CompatInput):
    """Input schema for setting the active document's unit system.

    Attributes:
        unit_system (str): Target linear unit - ``mm``, ``cm``, ``m``, ``in``
            or ``ft`` (aliases such as ``inch`` / ``millimeters`` accepted).
    """

    unit_system: str = Field(description="Linear unit system: mm, cm, m, in, or ft")

    def model_post_init(self, __context: Any) -> None:
        if not self.unit_system.strip():
            raise ValueError("unit_system is required")


class CloseModelInput(BaseModel):
    """Input schema for closing a model.

    Attributes:
        save (bool): The save value.
    """

    save: bool = Field(default=False, description="Save the model before closing")
    overwrite_modified_references: bool = Field(
        default=False,
        description=(
            "Set true only after the user agreed: lets saving an assembly also "
            "overwrite the user's own modified part files that a "
            "`confirmation_required` error listed."
        ),
    )
    close_leftovers: bool = Field(
        default=False,
        description=(
            "Also close documents SolidWorks still has loaded with no window that "
            "no open document uses, such as parts left by an assembly that was "
            "closed. Ones with unsaved changes stay open and are listed"
        ),
    )
    keep_active: bool = Field(
        default=False,
        description="With close_leftovers: leave the active document open",
    )

    def model_post_init(self, __context: Any) -> None:
        if self.keep_active and not self.close_leftovers:
            raise ValueError("keep_active needs close_leftovers")
        if self.keep_active and self.save:
            raise ValueError(
                "keep_active leaves the document open, so save does nothing"
            )


class CreateCutExtrudeInput(CompatInput):
    """Input schema for creating a cut-extrude feature.

    Attributes:
        depth (float): Cut depth in millimeters.
        draft_angle (float): Draft angle in degrees.
        end_condition (str): End condition type.
        reverse_direction (bool | None): True cuts along the sketch normal,
            False against it; None lets a one-way cut take the side with more
            material.
        both_directions (bool): With ``"ThroughAll"``, cut both ways.
        sketch_name (str | None): Sketch to cut from.
        draft_outward (bool): Draft outward instead of inward.
        start_offset_mm (float): Start this far off the sketch plane.
    """

    depth: float = Field(description="Cut depth in millimeters")
    draft_angle: float = Field(default=0.0, description="Draft angle in degrees")
    draft_outward: bool = Field(default=False, description=_DRAFT_OUTWARD_CUT)
    start_offset_mm: float = Field(default=0.0, description=_START_OFFSET)
    reverse_direction: bool | None = Field(
        default=None,
        description=(
            "Usually leave unset: a one-way cut then tries both sides of the "
            "sketch and keeps the one that removes more material (on a tie, "
            "the side along the sketch normal, where create_extrusion grows). "
            "true cuts along the sketch normal, the side create_extrusion grows "
            "into by default; false cuts against it (from a sketch on a face, "
            "into the part). A set side that has no material is cut the other "
            "way instead, and the reply sets direction_flipped"
        ),
    )
    end_condition: str = Field(
        default="Blind",
        description=(
            "'Blind' (cut 'depth' mm), 'MidPlane' ('depth' mm in total, half "
            "each side of the sketch plane), 'ThroughAll' (through the body, "
            "one way) or 'ThroughAllBoth' (through the body both ways, for a "
            "sketch inside the body)"
        ),
    )
    both_directions: bool = Field(
        default=False,
        description=(
            "With end_condition 'ThroughAll', cut through the body both ways "
            "(same as 'ThroughAllBoth')"
        ),
    )
    sketch_name: str | None = Field(
        default=None,
        description=(
            "Sketch to cut from, e.g. 'Sketch3'. If omitted, the sketch most "
            "recently created/exited in this session is used, but only if it "
            "still exists in the model — there is no other fallback. Pass "
            "this explicitly whenever more than one unconsumed sketch could "
            "exist, or after any operation that might have changed the "
            "active sketch."
        ),
    )
    regions: Literal["all"] | None = Field(default=None, description=_REGIONS)

    def model_post_init(self, __context: Any) -> None:
        if self.depth <= 0:
            raise ValueError("depth must be positive")
        _check_draft(self.draft_angle, self.draft_outward)


class EmbossTextInput(AddSketchTextInput):
    """Input schema for raising or sinking a label in one call.

    Attributes:
        plane (str | None): The plane to write on.
        face_point (list[float] | None): A point on a flat face to write on.
        depth (float): How far the text stands up or goes in, mm.
        mode (str): ``emboss`` (raised) or ``engrave`` (cut in).
        reverse_direction (bool | None): Which side to extrude or cut.
    """

    plane: str | None = Field(
        default=None,
        description=(
            "Plane to write on: 'Front', 'Top', 'Right' or a plane in the part "
            "(e.g. 'Plane1'). Give this or face_point"
        ),
    )
    face_point: list[float] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "Instead of plane: [x, y, z] mm on a flat face; a reference plane "
            "is made on the face and the text written on it"
        ),
    )
    depth: float = Field(
        gt=0, description="How far the text stands up (emboss) or goes in (engrave), mm"
    )
    mode: Literal["emboss", "engrave"] = Field(
        default="emboss",
        description="'emboss' raises the text off the part; 'engrave' cuts it in",
    )
    reverse_direction: bool | None = Field(
        default=None,
        description=(
            "Usually leave unset: an emboss that adds nothing (it grew into the "
            "part) is made again the other way, and an engrave cuts the side "
            "with more material. Set it to choose, as create_extrusion's and "
            "create_cut_extrude's reverse_direction"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        """Need exactly one place to write.

        Raises:
            ValueError: Both or neither of ``plane`` and ``face_point``.
        """
        super().model_post_init(__context)
        if (self.plane is None) == (self.face_point is None):
            raise ValueError("give plane or face_point, one of them")
        if self.plane is not None and not self.plane.strip():
            raise ValueError("plane is blank")


def _check_edge_ids(edge_names: list[str], edge_ids: list[str]) -> None:
    """Refuse ``edge_ids`` given with ``edge_names``, or ids that aren't ids.

    Raises:
        ValueError: With what is wrong.
    """
    from ..adapters.solidworks.persist_ids import ids_problem

    if not edge_ids:
        return
    if edge_names:
        raise ValueError("give edge_names or edge_ids, not both")
    problem = ids_problem(edge_ids)
    if problem:
        raise ValueError(problem)


class AddFilletInput(CompatInput):
    """Input schema for adding a fillet feature.

    Attributes:
        edge_names (list[str]): Edge names to fillet.
        radius (float): Fillet radius in millimeters.
    """

    radius: float = Field(description="Fillet radius in millimeters")
    edge_names: list[str] = Field(
        default_factory=list,
        description=(
            "Edges (or faces) to fillet. Accepts a topology name (e.g. "
            "'Edge<1>'), a coordinate hint in metres ('x,y,z') to select the "
            "nearest edge, or 'face:x,y,z' to select a whole face — which "
            "fillets every edge bounding it in one call. Prefer 'face:' over "
            "a coordinate on an edge that sits exactly on a shared boundary "
            "between two faces (e.g. a hole rim flush with a flat face): the "
            "nearest-edge match there can silently resolve to a different, "
            "unrelated edge. Leave empty to use whatever is *currently "
            "selected* in the live SolidWorks UI (e.g. entities read back "
            "via get_selected_entities and left selected) without touching "
            "the selection — this is the most reliable option when the user "
            "has hand-picked specific edges, since it never round-trips "
            "through coordinates. Falls back to filleting all edges only if "
            "nothing at all is selected."
        ),
    )
    edge_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Edges by the ids select_edges gave with with_ids (they last across "
            "rebuilds); they are selected first. Not with edge_names"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        if self.radius <= 0:
            raise ValueError("radius must be positive")
        _check_edge_ids(self.edge_names, self.edge_ids)


class AddChamferInput(CompatInput):
    """Input schema for adding a chamfer feature.

    Attributes:
        distance (float): Chamfer distance in millimeters.
        edge_names (list[str]): Edge names to chamfer.
        angle (float): Chamfer angle in degrees.
        flip (bool): Measure the distance along the other face.
    """

    distance: float = Field(
        description="Chamfer distance in millimeters, along one of the edge's faces"
    )
    angle: float = Field(
        default=45.0,
        description=(
            "Chamfer angle in degrees, between 0 and 90; 45 makes both legs "
            "equal to distance"
        ),
    )
    flip: bool = Field(
        default=False,
        description=(
            "Measure distance along the edge's other face (swaps the long and "
            "short legs of an angled chamfer)"
        ),
    )
    edge_names: list[str] = Field(
        default_factory=list,
        description=(
            "Edges (or faces) to chamfer. Accepts a topology name (e.g. "
            "'Edge<1>'), a coordinate hint in metres ('x,y,z') to select the "
            "nearest edge, or 'face:x,y,z' to select a whole face — which "
            "chamfers every edge bounding it in one call. Prefer 'face:' over "
            "a coordinate on an edge that sits exactly on a shared boundary "
            "between two faces (e.g. a hole rim flush with a flat face): the "
            "nearest-edge match there can silently resolve to a different, "
            "unrelated edge. Leave empty to use whatever is *currently "
            "selected* in the live SolidWorks UI (e.g. entities read back "
            "via get_selected_entities and left selected) without touching "
            "the selection — this is the most reliable option when the user "
            "has hand-picked specific edges, since it never round-trips "
            "through coordinates. Falls back to chamfering all edges only if "
            "nothing at all is selected."
        ),
    )
    edge_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Edges by the ids select_edges gave with with_ids (they last across "
            "rebuilds); they are selected first. Not with edge_names"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        if self.distance <= 0:
            raise ValueError("distance must be positive")
        if not 0 < self.angle < 90:
            raise ValueError("angle must be between 0 and 90 degrees")
        _check_edge_ids(self.edge_names, self.edge_ids)


class CreateAssemblyInput(CompatInput):
    """Input schema for creating a new assembly.

    Attributes:
        name (str): The name value.
        template (str | None): The template value.
    """

    name: str = Field(
        description=(
            "A label for your own reference; SolidWorks names the new assembly "
            "itself (Assem1, ...) and the file takes the name of the path "
            "you save it to with save_assembly"
        )
    )
    template: str | None = Field(
        default=None,
        description=(
            "Assembly template (.asmdot) path; omit to use the SolidWorks default"
        ),
    )


class InsertComponentInput(CompatInput):
    """Input schema for inserting a component into an assembly.

    Attributes:
        file_path (str): Path to the part or sub-assembly.
        x (float): X position in millimetres.
        y (float): Y position in millimetres.
        z (float): Z position in millimetres.
    """

    file_path: str = Field(
        description="Absolute path to the .sldprt or .sldasm to insert"
    )
    x: float = Field(default=0.0, description="X position in millimetres")
    y: float = Field(default=0.0, description="Y position in millimetres")
    z: float = Field(default=0.0, description="Z position in millimetres")
    fixed: bool | None = Field(
        default=None,
        description=(
            "true fixes the component, false floats it (also the first one, which "
            "SolidWorks fixes by itself); leave it out for SolidWorks' choice"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.file_path.strip():
            raise ValueError("file_path is required")


class DeleteFeatureInput(CompatInput):
    """Input schema for deleting a feature.

    Attributes:
        name (str): Feature or sketch name to delete.
        delete_absorbed (bool): Delete the feature's own sketch with it.
    """

    name: str = Field(
        description="Feature or sketch name, e.g. 'Boss-Extrude1' or 'Sketch1'"
    )
    delete_absorbed: bool = Field(
        default=False,
        description=(
            "Delete the feature's own sketch too (a cut's profile); off leaves "
            "it in the tree and the reply names it"
        ),
    )


class SuppressFeatureInput(CompatInput):
    """Input schema for suppressing or unsuppressing a feature.

    Attributes:
        name (str): Feature name to toggle.
        suppress (bool): True to suppress, False to unsuppress.
    """

    name: str = Field(description="Feature name, e.g. 'Fillet1'")
    suppress: bool = Field(
        default=True, description="True to suppress, False to unsuppress"
    )


class RenameFeatureInput(CompatInput):
    """Input schema for renaming a feature.

    Attributes:
        old_name (str): Current feature name.
        new_name (str): New feature name.
    """

    old_name: str = Field(description="Current feature name, e.g. 'Fillet1'")
    new_name: str = Field(description="New feature name, e.g. 'MountingEdgeFillet'")

    def model_post_init(self, __context: Any) -> None:
        if not self.old_name.strip():
            raise ValueError("old_name is required")
        if not self.new_name.strip():
            raise ValueError("new_name is required")


class SelectFeatureInput(CompatInput):
    """Input schema for selecting a named feature.

    Attributes:
        feature_name (str): Feature, sketch, plane or component name.
        append (bool): Add to the current selection instead of replacing it.
    """

    feature_name: str = Field(
        description="Feature, sketch, plane or component name, e.g. "
        "'Boss-Extrude1', 'Sketch1', 'Front Plane' (an '@Part1' suffix is ok)"
    )
    append: bool = Field(
        default=False,
        description="Add to the current selection instead of replacing it",
    )

    def model_post_init(self, __context: Any) -> None:
        self.feature_name = self.feature_name.strip()
        if not self.feature_name:
            raise ValueError("feature_name is required")


class SelectFaceEdgesInput(CompatInput):
    """Input schema for multi-selecting every edge of one face.

    Attributes:
        point (list[float] | None): A point on the face, [x, y, z] in mm.
        side (str | None): Which way the face points.
        plane (str | None): Reference plane the face lies on.
        edge_type (str): Edge filter: all, linear or circular.
        append (bool): Add to the current selection instead of replacing it.
    """

    point: list[float] | None = Field(
        default=None, description="A point on the face, [x, y, z] in mm"
    )
    side: str | None = Field(
        default=None,
        description="Outward direction of the face: +X/-X/+Y/-Y/+Z/-Z or "
        "right/left/top/bottom/front/back (top=+Y, front=+Z, right=+X, as in "
        "SolidWorks' default views); picks the furthest flat face that way",
    )
    plane: str | None = Field(
        default=None,
        description="Reference plane name, e.g. 'Front Plane'; picks the body "
        "face(s) lying on it",
    )
    edge_type: str = Field(
        default="all", description="'all', 'linear' or 'circular' (e.g. hole rims)"
    )
    append: bool = Field(
        default=False,
        description="Add to the current selection instead of replacing it",
    )

    def model_post_init(self, __context: Any) -> None:
        from ..adapters.solidworks.face_selection import validate_face_query

        self.edge_type = str(self.edge_type or "all").strip().lower()
        problem = validate_face_query(self.point, self.side, self.plane, self.edge_type)
        if problem:
            raise ValueError(problem)


class SelectEdgesInput(CompatInput):
    """Input schema for selecting edges by rule.

    Attributes:
        feature (str | None): Take the edges of the faces this feature made.
        face_point (list[float] | None): Take the edges of the face at this point.
        side (str | None): Take the edges of the furthest face facing this way.
        plane (str | None): Take the edges of the face(s) on this plane.
        edge_type (str): Keep all, linear or circular edges.
        parallel_to (str | list[float] | None): Keep straight edges along this.
        parallel_tolerance_deg (float): How far off parallel still counts.
        inside_box (list[list[float]] | None): Keep edges inside this box, mm.
        min_length (float | None): Keep edges at least this long, mm.
        max_length (float | None): Keep edges at most this long, mm.
        between_faces (list[list[float]] | None): Take the edges two faces share.
        edge_ids (list[str] | None): Take the edges with these ids.
        body (str | None): Keep only this body's edges.
        convexity (str | None): Keep convex, concave or tangent edges.
        with_ids (bool): Also return an id for each selected edge.
        append (bool): Add to the current selection instead of replacing it.
    """

    feature: str | None = Field(
        default=None,
        description=(
            "Take the edges of the faces this feature made, e.g. 'Cut-Extrude2' "
            "(the cut's walls and rims). Give at most one of feature, "
            "face_point, side, plane, between_faces and edge_ids; none takes "
            "every edge of the part"
        ),
    )
    face_point: list[float] | None = Field(
        default=None,
        description="Take the edges of the face at this point, [x, y, z] mm",
    )
    side: str | None = Field(
        default=None,
        description=(
            "Take the edges of the furthest flat face facing this way: top, "
            "bottom, front, back, right, left or +X..-Z"
        ),
    )
    plane: str | None = Field(
        default=None,
        description="Take the edges of the face(s) lying on this plane, e.g. 'Top Plane'",
    )
    between_faces: list[list[float]] | None = Field(
        default=None,
        description=(
            "Take the edges two faces share: a point on each, [[x, y, z], [x, y, "
            "z]] mm, e.g. where a wall meets a floor"
        ),
    )
    edge_ids: list[str] | None = Field(
        default=None,
        description=(
            "Take the edges with these ids, as an earlier call with with_ids "
            "gave them. They last across rebuilds while the feature that made "
            "the edge stays"
        ),
    )
    body: str | None = Field(
        default=None,
        description=(
            "Keep only this solid body's edges, by its name now (list_bodies: a "
            "feature renames the bodies it changes). With feature: that "
            "feature's edges on this body"
        ),
    )
    edge_type: str = Field(
        default="all",
        description="Keep 'all' (the default), 'linear' or 'circular' edges",
    )
    parallel_to: str | list[float] | None = Field(
        default=None,
        description=(
            "Keep only straight edges along this direction: 'x', 'y', 'z' or [x, y, z]"
        ),
    )
    parallel_tolerance_deg: float = Field(
        default=1.0,
        gt=0,
        le=45,
        description="How many degrees off parallel_to an edge may be (default 1)",
    )
    inside_box: list[list[float]] | None = Field(
        default=None,
        description=(
            "Keep only edges wholly inside this box: [[xmin, ymin, zmin], "
            "[xmax, ymax, zmax]] in model mm"
        ),
    )
    min_length: float | None = Field(
        default=None, ge=0, description="Keep only edges at least this long, mm"
    )
    max_length: float | None = Field(
        default=None, ge=0, description="Keep only edges at most this long, mm"
    )
    convexity: Literal["convex", "concave", "tangent"] | None = Field(
        default=None,
        description=(
            "Keep only 'convex' edges (outer edges, hole rims), 'concave' ones "
            "(inside corners, a pocket's floor edges) or 'tangent' ones (where a "
            "fillet meets a face). Read after every other filter, about ten "
            "calls an edge; one whose faces don't read is left out"
        ),
    )
    with_ids: bool = Field(
        default=False,
        description=(
            "Also return edge_ids, one per selected edge (up to 100): ids that "
            "name the same edges after rebuilds, for edge_ids here or in "
            "add_fillet and add_chamfer"
        ),
    )
    any_of: list[dict[str, Any]] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description=(
            "Several rules in one call: select the edges any of them matches. Each "
            "rule takes the keys above (a source and filters) and is laid over the "
            "ones given outside any_of, e.g. {'feature': 'Boss-Extrude1', 'any_of': "
            "[{'inside_box': [[0,0,0],[5,5,5]]}, {'inside_box': [[20,0,0],[25,5,5]]}]}. "
            "Each source's edges are read once, so this is much faster than one call "
            "per rule"
        ),
    )
    append: bool = Field(
        default=False,
        description="Add to the current selection instead of replacing it",
    )

    def model_post_init(self, __context: Any) -> None:
        from ..adapters.solidworks.edge_rules import validate_edge_query

        self.edge_type = str(self.edge_type or "all").strip().lower()
        problem = validate_edge_query(self.query())
        if problem:
            raise ValueError(problem)

    def query(self) -> dict[str, Any]:
        """The rules as the adapter's ``select_edges`` takes them (``with_ids`` when on)."""
        query = self.model_dump(exclude={"append", "with_ids"}, exclude_none=True)
        if self.with_ids:
            query["with_ids"] = True
        return query


DisplayType = Literal[
    "planes",
    "axes",
    "temporary_axes",
    "origins",
    "coordinate_systems",
    "points",
    "sketches",
]


class SetVisibilityInput(CompatInput):
    """Input schema for showing or hiding reference geometry and sketches.

    Attributes:
        visible (bool): Show (true) or hide (false).
        names (list[str]): Planes, axes, points, coordinate systems or sketches.
        types (list[str]): Every entity of these types in the document.
    """

    visible: bool = Field(description="true shows them, false hides them")
    names: list[str] = Field(
        default_factory=list,
        description=(
            "Planes, axes, points, coordinate systems or sketches by their tree "
            "names, e.g. ['Plane1', 'Sketch3']"
        ),
    )
    types: list[DisplayType] = Field(
        default_factory=list,
        description=(
            "Every entity of these types in the document, as View > Hide/Show "
            "does: planes, axes, temporary_axes, origins, coordinate_systems, "
            "points, sketches"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        self.names = [n.strip() for n in self.names if n and n.strip()]
        if not self.names and not self.types:
            raise ValueError("give names, types or both to show or hide")


class EditFeatureInput(CompatInput):
    """Input schema for changing an existing feature's parameters.

    Attributes:
        name (str): The feature to change.
        depth (float | None): New depth, mm (extrude, cut).
        end_condition (str | None): New end condition (extrude, cut).
        reverse_direction (bool | None): New direction (extrude, cut, revolve).
        angle (float | None): New angle, degrees (revolve).
        radius (float | None): New radius, mm (fillet).
        edges (str | None): "selection": new edges, those selected now (fillet, chamfer).
        edge_rules (dict | None): New edges by a select_edges query (fillet, chamfer).
        offset (float | None): New distance from its reference, mm (offset plane).
        flip (bool | None): Put it on the other side of its reference (offset plane).
    """

    name: str = Field(description="The feature to change, e.g. 'Cut-Extrude2'")
    depth: float | None = Field(
        default=None, gt=0, description="Extrude or cut: the new depth, mm"
    )
    end_condition: str | None = Field(
        default=None,
        description=(
            "Extrude or cut: Blind, ThroughAll, ThroughAllBoth, MidPlane or UpToNext"
        ),
    )
    reverse_direction: bool | None = Field(
        default=None,
        description="Extrude, cut or revolve: true to go the other way",
    )
    angle: float | None = Field(
        default=None, gt=0, le=360, description="Revolve: the new angle, degrees"
    )
    radius: float | None = Field(
        default=None, gt=0, description="Fillet: the new radius, mm"
    )
    edges: Literal["selection"] | None = Field(
        default=None,
        description=(
            "Fillet or chamfer: 'selection' replaces all its edges with the edges "
            "selected now (select_edges or select_face_edges first)"
        ),
    )
    edge_rules: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Fillet or chamfer: replace all its edges with those this select_edges "
            "query matches (it selects nothing). It takes select_edges' keys except "
            "append, any_of included, e.g. {'side': 'top', 'edge_type': 'linear'}. "
            "Not with edges"
        ),
    )
    offset: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Offset plane (create_reference_plane's offset): the new distance "
            "from its reference, mm. Moves the plane and what is built on it"
        ),
    )
    flip: bool | None = Field(
        default=None,
        description=(
            "Offset plane: true puts it on the other side of its reference, "
            "false on the first side (as create_reference_plane's flip)"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        from ..adapters.solidworks.feature_editing import (
            edge_query,
            edge_rules_problem,
        )

        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name is required")
        if not self.changes():
            raise ValueError(
                "give at least one change: depth, end_condition, "
                "reverse_direction, angle, radius, edges, edge_rules, offset or "
                "flip"
            )
        if self.edges is not None and self.edge_rules is not None:
            raise ValueError("give edges or edge_rules, not both")
        if self.edge_rules is not None:
            problem = edge_rules_problem(self.edge_rules)
            if problem:
                raise ValueError(problem)
            self.edge_rules = edge_query(self.edge_rules)

    def changes(self) -> dict[str, Any]:
        """The changes given, by name."""
        return self.model_dump(
            include={
                "depth",
                "end_condition",
                "reverse_direction",
                "angle",
                "radius",
                "edges",
                "edge_rules",
                "offset",
                "flip",
            },
            exclude_none=True,
        )


class FeatureDefinitionInput(CompatInput):
    """Input schema for reading a feature's parameters.

    Attributes:
        name (str): The feature to read.
    """

    name: str = Field(description="The feature to read, e.g. 'Boss-Extrude1'")

    def model_post_init(self, __context: Any) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name is required")


class SetRollbackInput(CompatInput):
    """Input schema for moving the rollback bar.

    Attributes:
        before (str | None): Put the bar just above this feature.
        after (str | None): Put the bar just below this feature.
    """

    before: str | None = Field(
        default=None,
        description="Put the bar just above this feature, e.g. 'Fillet1'",
    )
    after: str | None = Field(
        default=None,
        description="Put the bar just below this feature; neither moves it to the end",
    )

    def model_post_init(self, __context: Any) -> None:
        self.before = (self.before or "").strip() or None
        self.after = (self.after or "").strip() or None
        if self.before and self.after:
            raise ValueError("give before or after, not both")


class RebuildModelInput(CompatInput):
    """Input schema for forcing a model rebuild.

    Attributes:
        top_level_only (bool): Rebuild only the top level of an assembly.
    """

    top_level_only: bool = Field(
        default=False,
        description="Rebuild only the top level of an assembly (default: full "
        "rebuild including sub-assemblies)",
    )


class UndoInput(CompatInput):
    """Input schema for undoing model operations.

    Attributes:
        count (int): Number of operations to undo.
    """

    count: int = Field(default=1, ge=1, description="Number of operations to undo")


class AddMateInput(CompatInput):
    """Input schema for mating two assembly components.

    Attributes:
        component_a (str): First component instance name.
        component_b (str): Second component instance name.
        entity_a (str): Named feature on the first component.
        entity_b (str): Named feature on the second component.
        mate_type (str): Mate type.
        alignment (str): Mate alignment.
        distance (float): Distance in millimetres for a distance mate.
        angle (float): Angle in degrees for an angle mate.
    """

    component_a: str = Field(
        description="First component instance name, as reported by list_components"
    )
    component_b: str = Field(description="Second component instance name")
    entity_a: str | None = Field(
        default=None,
        description=(
            "Named reference plane or axis on the first component, e.g. 'Front "
            "Plane'; defaults to 'Front Plane' when face_a is not given either"
        ),
    )
    entity_b: str | None = Field(
        default=None,
        description=(
            "Named reference plane or axis on the second component; defaults "
            "to 'Front Plane' when face_b is not given either"
        ),
    )
    face_a: list[float] | None = Field(
        default=None,
        description=(
            "[x, y, z] in mm on a face of the first component, in that part's "
            "own coordinates (as it was modelled), instead of entity_a: e.g. a "
            "point on a pin's round face for a concentric mate"
        ),
    )
    face_b: list[float] | None = Field(
        default=None,
        description=(
            "[x, y, z] in mm on a face of the second component, in its own "
            "part coordinates, instead of entity_b"
        ),
    )
    mate_type: str = Field(
        default="coincident",
        description=(
            "coincident, concentric, perpendicular, parallel, tangent, "
            "distance or angle"
        ),
    )
    alignment: str = Field(
        default="aligned", description="aligned, anti_aligned or closest"
    )
    distance: float = Field(
        default=0.0, description="Distance in millimetres, for a distance mate"
    )
    angle: float = Field(default=0.0, description="Angle in degrees, for an angle mate")
    flip: bool = Field(
        default=False,
        description=(
            "For a distance or angle mate: put component_b on the other side of "
            "the distance or angle (SolidWorks picks a side, not always the one "
            "you meant)"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.component_a.strip() or not self.component_b.strip():
            raise ValueError("component_a and component_b are required")
        if self.flip and self.mate_type.strip().lower() not in ("distance", "angle"):
            raise ValueError("flip is for distance and angle mates only")
        for side in ("a", "b"):
            entity, face = (
                getattr(self, f"entity_{side}"),
                getattr(self, f"face_{side}"),
            )
            if entity and face is not None:
                raise ValueError(f"give entity_{side} or face_{side}, not both")
            if face is not None and len(face) != 3:
                raise ValueError(f"face_{side} must be [x, y, z] in millimetres")


class SetComponentStateInput(CompatInput):
    """Input schema for fixing, floating and flexing a component.

    Attributes:
        component (str): Component instance name.
        fixed (bool | None): True fixes it, False floats it.
        flexible (bool | None): True flexible, False rigid.
    """

    component: str = Field(
        description="Component instance name, as reported by list_components"
    )
    fixed: bool | None = Field(
        default=None,
        description=(
            "true fixes the component in place; false floats it so mates can "
            "move it; leave it out to keep it as it is"
        ),
    )
    flexible: bool | None = Field(
        default=None,
        description=(
            "For a sub-assembly: true lets its own mates move inside this "
            "assembly (a crank that turns); false makes it rigid; leave it out "
            "to keep it as it is"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.component.strip():
            raise ValueError("component is required")
        if self.fixed is None and self.flexible is None:
            raise ValueError("give fixed, flexible or both")


class SetComponentTransformInput(CompatInput):
    """Input schema for placing a component: origin, rotation and fixed state.

    Attributes:
        component (str): Component instance name.
        origin_mm (list[float] | None): Where its origin goes, mm.
        rotation_deg (list[float] | None): Turns about X, Y, Z, degrees.
        matrix (list[list[float]] | None): Its axes as rows.
        fixed (bool | None): Fix or float it afterwards.
    """

    component: str = Field(
        description="Component instance name, as reported by list_components"
    )
    origin_mm: list[float] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "Where the component's own origin goes, [x, y, z] mm in assembly "
            "coordinates; leave it out to keep it where it is"
        ),
    )
    rotation_deg: list[float] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "Turns [rx, ry, rz] in degrees about the assembly's X, then Y, then Z "
            "axis (right-hand rule), from unturned: [0, 90, 0] points the "
            "component's X axis along -Z. Leave it and matrix out to keep its turn"
        ),
    )
    matrix: list[list[float]] | None = Field(
        default=None,
        description=(
            "The component's X, Y and Z axes in assembly coordinates, three rows of "
            "three numbers, orthonormal; instead of rotation_deg"
        ),
    )
    fixed: bool | None = Field(
        default=None,
        description=(
            "true leaves it fixed, false floating; leave it out to keep its state "
            "(a fixed component is floated for the move and fixed again)"
        ),
    )
    turn_deg: float = Field(
        default=0.0,
        ge=-360.0,
        le=360.0,
        description=(
            "Then turn it this many degrees (right-hand rule about turn_axis) "
            "from where it is, about an axis through turn_pivot_mm: a lid "
            "opened 30 degrees about its hinge. Applied after origin_mm and "
            "rotation_deg or matrix"
        ),
    )
    turn_axis: str | list[float] | None = Field(
        default=None,
        description=(
            "The direction of the turn's axis in assembly coordinates: 'x', "
            "'y', 'z', '-x', '-y', '-z' or [x, y, z]; needed with turn_deg"
        ),
    )
    turn_pivot_mm: list[float] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description=(
            "A point on the turn's axis, [x, y, z] mm in assembly coordinates "
            "(a point on the hinge pin's axis); the origin when omitted"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        from ..adapters.solidworks.placement import axis_vector, rotation_problem

        if not self.component.strip():
            raise ValueError("component is required")
        if self.rotation_deg is not None and self.matrix is not None:
            raise ValueError("give rotation_deg or matrix, not both")
        if self.matrix is not None:
            problem = rotation_problem(self.matrix)
            if problem:
                raise ValueError(problem)
        if self.turn_deg and self.turn_axis is None:
            raise ValueError("turn_deg needs turn_axis, the direction to turn about")
        if self.turn_axis is not None:
            axis_vector(self.turn_axis)

    def turn_axis_vector(self) -> list[float] | None:
        """The turn's axis as a unit vector, or None without a turn."""
        from ..adapters.solidworks.placement import axis_vector

        if self.turn_axis is None or not self.turn_deg:
            return None
        return axis_vector(self.turn_axis)

    def rotation_rows(self) -> list[list[float]] | None:
        """The rotation as rows (the component's axes), or None to keep it."""
        from ..adapters.solidworks.placement import rotation_rows_from_deg

        if self.matrix is not None:
            return [[float(x) for x in row] for row in self.matrix]
        if self.rotation_deg is not None:
            return rotation_rows_from_deg(*self.rotation_deg)
        return None


class CreateReferencePlaneInput(CompatInput):
    """Input schema for creating a reference plane.

    Attributes:
        reference (str): Name of the reference plane.
        face_point (list[float] | None): A point on a flat face to use instead.
        offset (float): Offset distance in millimetres.
        angle (float): Angle in degrees.
        axis (str | None): Axis to rotate about, for an angle.
        flip (bool): Reverse the offset or angle direction.
    """

    reference: str = Field(
        default="",
        description=(
            "Name of the reference plane, e.g. 'Front Plane' or 'Plane1'; leave "
            "it out when giving face_point"
        ),
    )
    face_point: list[float] | None = Field(
        default=None,
        description=(
            "[x, y, z] in mm on a flat face of the part (sloped or not), to use "
            "that face as the reference instead of a named plane"
        ),
    )
    offset: float = Field(
        default=0.0,
        description=(
            "Offset distance in millimetres. Non-zero for a named plane; 0 with "
            "face_point puts the plane on the face"
        ),
    )
    angle: float = Field(
        default=0.0,
        description="Angle in degrees between the new plane and the reference, about axis",
    )
    axis: str | None = Field(
        default=None,
        description=(
            "Name of the axis the plane rotates about, e.g. 'Axis1' from "
            "create_axis; required with angle"
        ),
    )
    flip: bool = Field(
        default=False, description="Reverse the offset side or the angle direction"
    )
    point: list[float] | None = Field(
        default=None,
        description=(
            "[x, y, z] in mm the plane passes through, with normal, instead of "
            "reference or face_point"
        ),
    )
    normal: list[float] | None = Field(
        default=None,
        description=(
            "With point: the plane's normal [x, y, z], any length; [0.866, 0, 0.5] "
            "is a vertical plane at 30 degrees to the Right Plane"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        given = [
            bool(self.reference.strip()),
            self.face_point is not None,
            self.point is not None,
        ]
        if sum(given) != 1:
            raise ValueError("give exactly one of reference, face_point or point")
        if self.face_point is not None and len(self.face_point) != 3:
            raise ValueError("face_point must be [x, y, z] in millimetres")
        if self.point is not None:
            if self.normal is None:
                raise ValueError("a plane through point needs normal")
            if self.offset or self.angle or self.axis or self.flip:
                raise ValueError(
                    "point and normal set the plane alone: no offset, angle, axis "
                    "or flip (give the normal the other way to turn it)"
                )
            vector(self.point, "point")
            unit(vector(self.normal, "normal"), "normal")
        elif self.normal is not None:
            raise ValueError("normal goes with point")
        if self.angle and not self.axis:
            raise ValueError("an angled plane needs axis, the axis to rotate about")


class CreateAxisInput(CompatInput):
    """Input schema for creating a reference axis.

    Attributes:
        reference (str): Principal axis direction.
    """

    reference: str = Field(
        default="",
        description="Principal axis direction through the origin: 'x', 'y' or 'z' "
        "(case-insensitive; a leading +/- is stripped); leave it out with point",
    )
    point: list[float] | None = Field(
        default=None,
        description=(
            "Instead of reference: [x, y, z] in mm the axis passes through, with "
            "direction or second_point"
        ),
    )
    direction: list[float] | None = Field(
        default=None,
        description="With point: the axis direction [x, y, z], any length; [0, 1, 0] is vertical",
    )
    second_point: list[float] | None = Field(
        default=None,
        description="With point, instead of direction: another point [x, y, z] in mm on the axis",
    )

    def model_post_init(self, __context: Any) -> None:
        if self.point is None:
            if not self.reference.strip():
                raise ValueError(
                    "reference is required, or point with direction or second_point"
                )
            if self.direction is not None or self.second_point is not None:
                raise ValueError("direction and second_point go with point")
            return
        if self.reference.strip():
            raise ValueError("give reference or point, not both")
        if (self.direction is None) == (self.second_point is None):
            raise ValueError("with point, give direction or second_point")
        self.line()  # refuses bad or coincident points now

    def line(self) -> tuple[list[float], list[float]]:
        """Two points on the axis, mm: ``point``, and ``second_point`` or one along ``direction``.

        Raises:
            ValueError: A point or the direction isn't three numbers, the
                direction has no length, or the points coincide.
        """
        start = vector(self.point, "point")
        if self.second_point is not None:
            end = vector(self.second_point, "second_point")
        else:
            end = add(start, scale(unit(vector(self.direction, "direction")), LINE_MM))
        first, second = two_points(start, end)
        return list(first), list(second)


class CreateReferencePointInput(CompatInput):
    """Input schema for creating a reference point.

    Attributes:
        mode (str): "along_curve" or "face_center".
        x (float): X of a point on the target edge/face, in millimetres.
        y (float): Y of a point on the target edge/face, in millimetres.
        z (float): Z of a point on the target edge/face, in millimetres.
        distance (float | None): For "along_curve", offset from the edge
            start in millimetres.
        percent (float | None): For "along_curve", position as 0-100 of the
            edge length.
    """

    mode: str = Field(
        description="'along_curve' (a point on an edge) or 'face_center' "
        "(the centroid of a face)"
    )
    x: float = Field(description="X of a point on the target edge/face, in mm")
    y: float = Field(description="Y of a point on the target edge/face, in mm")
    z: float = Field(description="Z of a point on the target edge/face, in mm")
    distance: float | None = Field(
        default=None,
        description="along_curve only: offset from the edge start in mm "
        "(give exactly one of distance or percent)",
    )
    percent: float | None = Field(
        default=None,
        description="along_curve only: position as 0-100 of the edge length",
    )

    def model_post_init(self, __context: Any) -> None:
        if str(self.mode or "").strip().lower() not in (
            "along_curve",
            "face_center",
        ):
            raise ValueError("mode must be 'along_curve' or 'face_center'")


class MirrorFeatureInput(CompatInput):
    """Input schema for mirroring solid bodies or features.

    Attributes:
        features (list[str]): Body or feature names to mirror.
        mirror_plane (str): Plane name to mirror about.
        merge (bool): Merge the mirrored result with the original.
        mirror_bodies (bool): Mirror whole bodies rather than features.
    """

    features: list[str] = Field(
        default_factory=list,
        description="Body or feature names to mirror. At least one required. "
        "For a body mirror these are the names as they appear under Solid "
        "Bodies, which for a lofted wing is the loft's own name",
    )
    mirror_plane: str = Field(
        description="Plane name to mirror about, e.g. 'Right Plane'"
    )
    merge: bool = Field(
        default=True,
        description="Merge the mirrored result with the original body",
    )
    mirror_bodies: bool = Field(
        default=True,
        description="Mirror whole solid bodies rather than individual "
        "features. Defaults to True, which is what works for anything "
        "built from a loft or sweep - a feature mirror only resolves when "
        "the feature's own sketch sits on the mirror plane",
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.features:
            raise ValueError("features must contain at least one name")
        if not self.mirror_plane.strip():
            raise ValueError("mirror_plane is required")


class PatternCircularInput(CompatInput):
    """Input schema for patterning features around an axis.

    Attributes:
        features (list[str]): Names of the features to pattern.
        axis (str): Name of the axis to rotate about.
        count (int): Total number of instances including the original.
        angle (float | None): Total angle in degrees, spread evenly.
        step_angle (float | None): Angle in degrees between neighbours.
        reverse_direction (bool): Pattern the other way round the axis.
    """

    features: list[str] = Field(
        default_factory=list,
        description="Names of the features to pattern. At least one required.",
    )
    axis: str = Field(
        default="",
        description="Name of the axis to rotate about, e.g. 'Axis1' as "
        "created by create_axis; leave it out with axis_point",
    )
    axis_point: list[float] | None = Field(
        default=None,
        description=(
            "Instead of axis: [x, y, z] in mm the axis passes through, with "
            "axis_direction; the axis is made first (as create_axis with point)"
        ),
    )
    axis_direction: list[float] | None = Field(
        default=None,
        description="With axis_point: the axis direction [x, y, z], any length",
    )
    count: int = Field(
        description="Total number of instances including the original. "
        "Must be at least 2 - a pattern of one is just the original feature"
    )
    angle: float | None = Field(
        default=None,
        gt=0,
        le=360,
        description=(
            "Total angle in degrees the instances are spread over evenly; a "
            "full 360 when neither angle nor step_angle is given"
        ),
    )
    step_angle: float | None = Field(
        default=None,
        gt=0,
        lt=360,
        description=(
            "Instead of angle: degrees between neighbouring instances, e.g. "
            "60 for instances every 60 degrees"
        ),
    )
    reverse_direction: bool = Field(
        default=False, description="Pattern the other way round the axis"
    )

    def model_post_init(self, __context: Any) -> None:
        if not self.features:
            raise ValueError("features must contain at least one name")
        if self.axis_point is None:
            if not self.axis.strip():
                raise ValueError("axis is required, or axis_point with axis_direction")
            if self.axis_direction is not None:
                raise ValueError("axis_direction goes with axis_point")
        else:
            if self.axis.strip():
                raise ValueError("give axis or axis_point, not both")
            if self.axis_direction is None:
                raise ValueError("axis_point needs axis_direction")
            self.axis_line()
        if self.count < 2:
            raise ValueError("count must be at least 2")
        if self.angle is not None and self.step_angle is not None:
            raise ValueError(
                "give angle (the total) or step_angle (between instances), not both"
            )
        if self.step_angle is not None:
            turned = self.step_angle * (self.count - 1)
            if turned >= 360 - 1e-6:
                raise ValueError(
                    f"{self.count - 1} steps of {self.step_angle:g} degrees turn "
                    f"{turned:g} degrees, a full turn or more: the last instance "
                    "would land on an earlier one"
                )

    def axis_line(self) -> tuple[list[float], list[float]]:
        """Two points (mm) on the axis ``axis_point`` and ``axis_direction`` give.

        Raises:
            ValueError: Not three numbers each, or a direction of no length.
        """
        start = vector(self.axis_point, "axis_point")
        direction = unit(vector(self.axis_direction, "axis_direction"))
        return list(start), list(add(start, scale(direction, LINE_MM)))

    def spacing(self) -> dict[str, Any]:
        """How the pattern is spaced, for the adapter and the reply.

        Returns:
            dict[str, Any]: ``angle`` and ``equal_spacing`` as the adapter
            takes them (SolidWorks reads ``angle`` as the total when spacing
            is equal, else as the step), plus ``step_angle`` and
            ``total_angle`` in degrees.
        """
        if self.step_angle is not None:
            return {
                "angle": self.step_angle,
                "equal_spacing": False,
                "step_angle": self.step_angle,
                "total_angle": self.step_angle * (self.count - 1),
            }
        total = self.angle if self.angle is not None else 360.0
        # A full turn's last step closes on the original; a partial one ends
        # at the far end of the angle.
        steps = self.count if abs(total - 360.0) < 1e-9 else self.count - 1
        return {
            "angle": total,
            "equal_spacing": True,
            "step_angle": total / steps,
            "total_angle": total,
        }


_DIRECTION = (
    "'x', 'y' or 'z' for a model axis ('-x' the negative way), a reference "
    "axis or plane by name (along the axis, or the plane's normal), or "
    "'x,y,z' in mm on a straight edge (along the edge)"
)


class PatternLinearInput(CompatInput):
    """Input schema for repeating features or bodies in rows.

    Attributes:
        features (list[str]): Features (or bodies) to pattern.
        direction (str): Direction 1.
        count (int): Instances along it, the original included.
        spacing (float): Millimetres between neighbours along it.
        direction2 (str | None): Direction 2, for a grid.
        count2 (int | None): Instances along Direction 2.
        spacing2 (float | None): Millimetres between neighbours along it.
        reverse_direction (bool): Go the other way along Direction 1.
        reverse_direction2 (bool): Go the other way along Direction 2.
        bodies (bool): Pattern solid bodies instead of features.
    """

    features: list[str] = Field(
        default_factory=list,
        description=(
            "Names of the features to repeat (e.g. 'Cut-Extrude3'), or with "
            "`bodies` the solid bodies `list_bodies` names. At least one"
        ),
    )
    direction: str = Field(description="Direction 1: " + _DIRECTION)
    count: int = Field(
        description="Instances along direction, the original included (2 or more)"
    )
    spacing: float = Field(
        gt=0, description="Millimetres between neighbouring instances along direction"
    )
    direction2: str | None = Field(
        default=None,
        description="A second direction, for a grid, given the same way; omit for one row",
    )
    count2: int | None = Field(
        default=None,
        description="Instances along direction2, the first row included (2 or more)",
    )
    spacing2: float | None = Field(
        default=None,
        gt=0,
        description="Millimetres between neighbouring rows along direction2",
    )
    reverse_direction: bool = Field(
        default=False, description="Repeat the other way along direction"
    )
    reverse_direction2: bool = Field(
        default=False, description="Repeat the other way along direction2"
    )
    bodies: bool = Field(
        default=False,
        description="Repeat whole solid bodies instead of features",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a pattern SolidWorks couldn't make.

        Raises:
            ValueError: No features, a count under 2, a direction that can't
                be read, or a half-given second direction.
        """
        from ..adapters.solidworks.patterns import parse_direction

        self.features = [name.strip() for name in self.features if name.strip()]
        if not self.features:
            raise ValueError("features must contain at least one name")
        if self.count < 2:
            raise ValueError("count must be at least 2 (the original included)")
        parse_direction(self.direction)
        given = (self.count2 is not None, self.spacing2 is not None)
        if self.direction2 is None:
            if any(given):
                raise ValueError("count2 and spacing2 need a direction2")
            return
        parse_direction(self.direction2)
        if not all(given):
            raise ValueError("direction2 needs count2 and spacing2")
        if (self.count2 or 0) < 2:
            raise ValueError("count2 must be at least 2 (the first row included)")


class MeasureClearanceInput(CompatInput):
    """Input schema for measuring the gap between two components.

    Attributes:
        component_a (str): One component's instance name.
        component_b (str): The other's.
    """

    component_a: str = Field(
        description="One component's instance name, as list_components reports it"
    )
    component_b: str = Field(
        description="The other component's instance name, e.g. 'latch-1'"
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a blank name, or the same component twice."""
        self.component_a, self.component_b = (
            self.component_a.strip(),
            self.component_b.strip(),
        )
        if not self.component_a or not self.component_b:
            raise ValueError("component_a and component_b are both required")
        if self.component_a == self.component_b:
            raise ValueError(
                f"component_a and component_b are both {self.component_a!r}; "
                "give two different components"
            )


class CreateDrawingInput(CompatInput):
    """Input schema for creating a new drawing.

    Attributes:
        name (str): The name value.
        template (str | None): The template value.
    """

    name: str = Field(
        description=(
            "A label for your own reference; SolidWorks names the new drawing "
            "itself (Draw1, ...) and the file takes the name of the path "
            "you save it to with save_file"
        )
    )
    template: str | None = Field(
        default=None,
        description=(
            "Drawing template (.drwdot) path; omit to use the SolidWorks default"
        ),
    )


async def register_modeling_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register modeling tools with FastMCP.

    Registers comprehensive modeling tools for SolidWorks automation including model
    creation, feature creation, and model management operations.

    Args:
        mcp (FastMCP): The mcp value.
        adapter (SolidWorksAdapter): Adapter instance used for the operation.
        config (dict[str, Any]): Configuration values for the operation.

    Returns:
        int: The computed numeric result.

    Example:
                        ```python
                        from solidworks_mcp.tools.modeling import register_modeling_tools

                        tool_count = await register_modeling_tools(mcp, adapter, config)
                        print(f"Registered {tool_count} modeling tools")
                        ```
    """
    mcp = with_tool_boundary(mcp, adapter)
    tool_count = 0

    async def _select_edge_ids(edge_ids: list[str]) -> None:
        """Select the edges ``edge_ids`` name, for a fillet or chamfer of the selection."""
        if edge_ids:
            picked = await adapter.select_edges({"edge_ids": list(edge_ids)})
            require_success(picked, context="Failed to select the edges by id")

    @mcp.tool()
    async def open_model(input_data: OpenModelInput) -> dict[str, Any]:
        """Open a part, assembly or drawing from disk and make it the active document.

        Use it to start work on an existing file. To switch to a document that is
        already open, use `activate_document`. With `read_only: true` it opens the
        file so SolidWorks saves nothing over it, to inspect someone's file safely
        (`get_feature_definition`, `get_sketch`). Needs: the file on disk (a vault
        file must be checked out). Changes: opens the document; nothing is written.
        Returns: `model` with `title`, `type`, `path`, `configuration` and
        `read_only` (read back from SolidWorks), and a `warning` quoting a dialog
        SolidWorks showed (and the server cancelled) when the file opened anyway, or
        when a read-only open found the document already open for editing.

        Args:
            input_data: The file. `file_path` (.sldprt, .sldasm or .slddrw) is
                required; `read_only` is off.

        Returns:
            dict[str, Any]: The opened document.
        """
        input_data = normalize_input(input_data, OpenModelInput)
        if input_data.read_only:
            require_capability(
                adapter,
                "open_model_read_only",
                "The active adapter cannot open a file read-only; nothing was "
                "opened. Open it without read_only, and don't save it.",
            )
            result = await adapter.open_model_read_only(input_data.file_path)
        else:
            result = await adapter.open_model(input_data.file_path)

        require_success(result, context="Failed to open model")
        model = result.data
        title = _result_value(model, "title", "name", default=input_data.file_path)
        model_type = _result_value(model, "type", default="Part")
        path = _result_value(model, "path", "file_path", default=input_data.file_path)
        configuration = _result_value(model, "configuration", default="Default")
        properties = _result_value(model, "properties", default=None)
        read_only = (
            properties.get("read_only") if isinstance(properties, dict) else None
        )
        if not isinstance(read_only, bool):
            read_only = input_data.read_only
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"Opened {model_type}: {title}" + (" (read-only)" if read_only else "")
            ),
            "model": {
                "title": title,
                "name": title,
                "type": model_type,
                "path": path,
                "configuration": configuration,
                "read_only": bool(read_only),
            },
            "execution_time": result.execution_time,
        }
        warnings: list[str] = []
        # A dialog SolidWorks showed while opening, cancelled; the file opened.
        metadata = getattr(result, "metadata", None)
        dialog_warning = metadata.get("warning") if isinstance(metadata, dict) else None
        if isinstance(dialog_warning, str) and dialog_warning:
            warnings.append(dialog_warning)
        if input_data.read_only and not read_only:
            warnings.append(
                f"{title} was already open in SolidWorks for editing, so it is "
                "not read-only: saving would write the file. Close it first "
                "(close_model) to open it read-only."
            )
        if warnings:
            reply["warning"] = " ".join(warnings)
        return reply

    @mcp.tool()
    async def create_part(input_data: CreatePartInput) -> dict[str, Any]:
        """Create a new, empty part document and make it active, ready for `create_sketch`.

        Uses `template` (a .prtdot) or the default part template, then sets `units` if
        given, and assigns `material` if given. The part is not saved until
        `save_part`. Needs: SolidWorks running. Changes: opens a new part document.
        Returns: `part` with `name`, `units`, `template` and `material` (the one
        assigned, or null), plus a `warning` when the material couldn't be assigned;
        the part is created either way.

        Args:
            input_data: The part. `name` is required but only a label: the reply
                gives SolidWorks' own name (Part1, ...); `template`, `units` (mm, cm, m, in, ft) and
                `material` (a name from `list_available_materials`) are optional.

        Returns:
            dict[str, Any]: The new part.
        """
        input_data = normalize_input(input_data, CreatePartInput)
        result = await adapter.create_part(
            input_data.name, input_data.units, input_data.template
        )

        require_success(result, context="Failed to create part")
        model = result.data
        part_name = _result_value(model, "name", default=input_data.name)
        units = _result_value(model, "units", default=input_data.units or "mm")
        material = None
        warning = None
        if input_data.material:
            assigned = await adapter.set_material(input_data.material)
            if assigned.is_success:
                material = input_data.material
            else:
                warning = (
                    f"The part was created without a material: "
                    f"'{input_data.material}' could not be assigned "
                    f"({assigned.error}). Use list_available_materials, then "
                    "set_material."
                )
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Created new part: {part_name}",
            "part": {
                "name": part_name,
                "units": units,
                "material": material,
                "template": input_data.template,
            },
            "execution_time": result.execution_time,
        }
        if warning:
            reply["warning"] = warning
        return reply

    @mcp.tool()
    async def create_assembly(input_data: CreateAssemblyInput) -> dict[str, Any]:
        """Create a new, empty assembly document and make it active, ready for `insert_component`.

        Uses `template` (an .asmdot) or the default assembly template. It is not saved
        until `save_assembly`. Needs: SolidWorks running. Changes: opens a new assembly
        document. Returns: `assembly` with `name` and `template`. Then call
        `insert_component` for each part.

        Args:
            input_data: The assembly. `name` is required but only a label: the
                reply gives SolidWorks' own name (Assem1, ...); `template` has a
                default.

        Returns:
            dict[str, Any]: The new assembly.
        """
        input_data = normalize_input(input_data, CreateAssemblyInput)
        result = await adapter.create_assembly(input_data.name, input_data.template)

        require_success(result, context="Failed to create assembly")
        model = result.data
        assembly_name = _result_value(model, "name", default=input_data.name)
        return {
            "status": "success",
            "message": f"Created new assembly: {assembly_name}",
            "assembly": {
                "name": assembly_name,
                "template": input_data.template,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def create_drawing(input_data: CreateDrawingInput) -> dict[str, Any]:
        """Create a new, empty drawing document and make it active.

        Uses `template` (a .drwdot) or the default drawing template. Then place views
        with `create_drawing_view`; for a whole drawing from a model in one call, use
        `generate_drawing_package` instead. Needs: SolidWorks running. Changes: opens a
        new drawing document. Returns: `drawing` with `name` and `template`.

        Args:
            input_data: The drawing. `name` is required but only a label: the
                reply gives SolidWorks' own name (Draw1, ...); `template` has a
                default.

        Returns:
            dict[str, Any]: The new drawing.
        """
        input_data = normalize_input(input_data, CreateDrawingInput)
        result = await adapter.create_drawing(input_data.name, input_data.template)

        require_success(result, context="Failed to create drawing")
        model = result.data
        drawing_name = _result_value(model, "name", default=input_data.name)
        return {
            "status": "success",
            "message": f"Created new drawing: {drawing_name}",
            "drawing": {
                "name": drawing_name,
                "template": input_data.template,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def close_model(input_data: CloseModelInput) -> dict[str, Any]:
        """Close the active document, saving it first only if asked.

        Use it when you are done with a document. Unsaved changes are lost unless `save`
        is on. With `close_leftovers` it also closes parts SolidWorks still has loaded
        with no window and no open document using them (left by a closed assembly; their
        `~$` lock files stay until then), never one with unsaved changes;
        `list_open_documents` with `check_hidden` shows them first. Needs: an open,
        active document, unless `keep_active`. Changes: closes the document, and with
        `save` writes it first, without SolidWorks dialogs; a save that fails, or that
        would overwrite the user's own modified part files (`confirmation_required`),
        leaves the document open. Returns: `saved`, whether a save was asked for, and
        with `close_leftovers` `leftovers` (`closed`, `still_loaded`,
        `kept_modified`).

        Args:
            input_data: The close. Nothing is required: `save`,
                `overwrite_modified_references`, `close_leftovers` and
                `keep_active` are off.

        Returns:
            dict[str, Any]: The close result.
        """
        input_data = normalize_input(input_data, CloseModelInput)
        execution_time = 0.0
        closed_active = False
        if not input_data.keep_active:
            result = await adapter.close_model(
                input_data.save, input_data.overwrite_modified_references
            )
            nothing_open = (
                input_data.close_leftovers
                and result.status == AdapterResultStatus.WARNING
            )
            if not nothing_open:
                require_success(result, context="Failed to close model")
                closed_active = True
            execution_time += result.execution_time or 0.0
        reply: dict[str, Any] = {
            "status": "success",
            "message": "Model closed successfully" if closed_active else "",
            "saved": input_data.save and closed_active,
            "execution_time": execution_time,
        }
        if not input_data.close_leftovers:
            return reply
        require_capability(
            adapter,
            "close_leftover_documents",
            "The active adapter can't close documents loaded without a window.",
        )
        left = await adapter.close_leftover_documents()
        require_success(left, context="Failed to close the leftover documents")
        leftovers = left.data if isinstance(left.data, dict) else {}
        closed = leftovers.get("closed") or []
        reply["leftovers"] = leftovers
        reply["execution_time"] = execution_time + (left.execution_time or 0.0)
        reply["message"] = "; ".join(
            part
            for part in (
                reply["message"],
                f"closed {len(closed)} leftover document(s)",
            )
            if part
        )
        notes = []
        if kept := leftovers.get("kept_modified"):
            notes.append(
                "Left open because they have unsaved changes: "
                + ", ".join(d["title"] for d in kept)
                + ". Ask the user whether to save or discard them."
            )
        if still := leftovers.get("still_loaded"):
            notes.append(
                "SolidWorks kept these loaded, so another loaded document still "
                "uses them: "
                + ", ".join(d["title"] for d in still)
                + ". Closing that document releases them."
            )
        if notes:
            reply["warning"] = " ".join(notes)
        return reply

    @mcp.tool()
    async def create_extrusion(input_data: CreateExtrusionInput) -> dict[str, Any]:
        """Extrude the sketch you just closed into a solid boss, added to the active part.

        Use it after `exit_sketch`; to remove material, use `create_cut_extrude`. It
        extrudes the sketch `sketch_name` names, or the one `exit_sketch` closed last.
        End conditions: Blind (default), MidPlane, ThroughAll, UpToNext;
        `both_directions` means MidPlane. `start_offset_mm` starts it off the sketch
        plane; a `draft_angle` tapers inward unless `draft_outward`. With
        `thin_feature` and `thin_thickness` it makes a wall from the sketch's outline,
        and an open chain works. Contours that cross (touching circles, hexagons):
        `regions: "all"` builds their union. Needs: an open part and a closed profile
        (open is fine when thin). Changes: adds a Boss-Extrude feature. Returns:
        `extrusion` (`name`, `sketch` when named, `depth`, the `end_condition` used,
        `both_directions`, `thin`, `regions`, and the start offset and draft when
        set) and `measured_change` (volume, bounding box and solid-body count before
        and after).

        Args:
            input_data: The extrusion. `depth` (mm) is required; `sketch_name`,
                `end_condition`, `both_directions`, `reverse_direction`,
                `start_offset_mm`, `draft_angle`/`draft_outward`,
                `thin_feature`/`thin_thickness` and `merge_result` have defaults.

        Returns:
            dict[str, Any]: The created feature.
        """
        input_data = normalize_input(input_data, CreateExtrusionInput)
        # Convert input to ExtrusionParameters
        params = ExtrusionParameters(
            depth=input_data.depth,
            draft_angle=input_data.draft_angle,
            draft_outward=input_data.draft_outward,
            start_offset=input_data.start_offset_mm,
            reverse_direction=input_data.reverse_direction,
            both_directions=input_data.both_directions,
            thin_feature=input_data.thin_feature,
            thin_thickness=input_data.thin_thickness,
            thin_side=input_data.thin_side,
            end_condition=input_data.end_condition,
            merge_result=input_data.merge_result,
            feature_scope=False,
            auto_select=True,
            sketch_name=input_data.sketch_name,
            regions=input_data.regions,
        )

        before = await _read_summary(adapter)
        result = await adapter.create_extrusion(params)

        require_success(result, context="Failed to create extrusion")
        after = await _read_summary(adapter) if before is not None else None
        feature = result.data
        name = _result_value(feature, "feature_name", "name", default="Extrusion")
        applied = _result_value(feature, "parameters", default=None) or {}
        extrusion: dict[str, Any] = {
            "name": name,
            "sketch": applied.get("sketch_name"),
            "depth": input_data.depth,
            "end_condition": applied.get("end_condition")
            or ("MidPlane" if input_data.both_directions else input_data.end_condition),
            "both_directions": input_data.both_directions,
        }
        if input_data.thin_feature:
            extrusion["thin"] = {
                "thickness": input_data.thin_thickness,
                "side": input_data.thin_side,
            }
        extrusion.update(_offset_and_draft(input_data))
        notes = _regions_reply(extrusion, applied)
        reply = {
            "status": "success",
            "message": f"Created extrusion: {name}",
            "extrusion": extrusion,
            "execution_time": result.execution_time,
        }
        return _add_warnings(_attach_measured_change(reply, before, after), notes)

    @mcp.tool()
    async def create_revolve(input_data: CreateRevolveInput) -> dict[str, Any]:
        """Revolve a closed sketch around an axis, adding a solid to the active part.

        Use it for turned shapes (shafts, knobs). It revolves the sketch `sketch_name`
        names, or the one `exit_sketch` closed last, about the reference axis
        `axis_entity` names (from `create_axis`), or about the centreline drawn in the
        sketch with `add_centerline`. A sketch or axis that isn't in the model fails
        before anything is built. Needs: an open part and a closed profile with a
        centreline or a reference axis. Changes: adds a Revolve feature. Returns:
        `revolve` (`name`, `sketch` and `axis_entity` when named, `angle`) and
        `measured_change` (volume, bounding box and solid-body count before and
        after; left out when the model can't be read).

        Args:
            input_data: The revolve. `angle` (degrees, 360 for a full turn) is
                required; `sketch_name`, `axis_entity`, `both_directions`,
                `reverse_direction`, `thin_feature` and `merge_result` have defaults.

        Returns:
            dict[str, Any]: The created feature.
        """
        input_data = normalize_input(input_data, CreateRevolveInput)
        # Convert input to RevolveParameters
        params = RevolveParameters(
            angle=input_data.angle,
            reverse_direction=input_data.reverse_direction,
            both_directions=input_data.both_directions,
            thin_feature=input_data.thin_feature,
            thin_thickness=input_data.thin_thickness,
            merge_result=input_data.merge_result,
            sketch_name=input_data.sketch_name,
            axis_entity=input_data.axis_entity,
        )

        before = await _read_summary(adapter)
        result = await adapter.create_revolve(params)

        require_success(result, context="Failed to create revolve")
        after = await _read_summary(adapter) if before is not None else None
        feature = result.data
        name = _result_value(feature, "feature_name", "name", default="Revolve")
        applied = _result_value(feature, "parameters", default=None) or {}
        reply = {
            "status": "success",
            "message": f"Created revolve: {name}",
            "revolve": {
                "name": name,
                "sketch": applied.get("sketch_name"),
                "axis_entity": applied.get("axis_entity"),
                "angle": input_data.angle,
            },
            "execution_time": result.execution_time,
        }
        return _attach_measured_change(reply, before, after)

    @mcp.tool()
    async def create_cut_revolve(input_data: CreateCutRevolveInput) -> dict[str, Any]:
        """Cut material away by revolving a closed sketch around an axis: grooves, seats.

        Use it for round cuts that a straight cut can't make in one feature: a
        conical groove, a chamfered seat, an O-ring gland. Draw the cross-section
        of the material to remove on a plane through the axis, then call it with
        the sketch `sketch_name` names (or the one `exit_sketch` closed last) and
        the reference axis `axis_entity` names (from `create_axis`), or the
        centreline drawn in the sketch with `add_centerline`. To add material, use
        `create_revolve`. Needs: an open part with a solid body the profile
        overlaps. Changes: adds a Cut-Revolve feature. Returns: `cut_revolve`
        (`name`, `sketch`, `axis_entity`, `angle`) and `measured_change` (volume,
        bounding box and solid-body count before and after), with a `warning`
        when the volume didn't change.

        Args:
            input_data: The cut. Everything has a default: `angle` 360,
                `sketch_name` and `axis_entity` from the sketch closed last and
                its centreline, `reverse_direction` and `both_directions` off.

        Returns:
            dict[str, Any]: The cut made.
        """
        input_data = normalize_input(input_data, CreateCutRevolveInput)
        params = RevolveParameters(
            angle=input_data.angle,
            reverse_direction=input_data.reverse_direction,
            both_directions=input_data.both_directions,
            sketch_name=input_data.sketch_name,
            axis_entity=input_data.axis_entity,
            cut=True,
        )
        before = await _read_summary(adapter)
        result = await adapter.create_revolve(params)
        require_success(result, context="Failed to create cut-revolve")
        after = await _read_summary(adapter) if before is not None else None
        feature = result.data
        name = _result_value(feature, "feature_name", "name", default="Cut-Revolve")
        applied = _result_value(feature, "parameters", default=None) or {}
        reply = {
            "status": "success",
            "message": f"Created cut-revolve: {name}",
            "cut_revolve": {
                "name": name,
                "sketch": applied.get("sketch_name"),
                "axis_entity": applied.get("axis_entity"),
                "angle": input_data.angle,
            },
            "execution_time": result.execution_time,
        }
        return _attach_measured_change(reply, before, after)

    @mcp.tool()
    async def get_dimension(input_data: GetDimensionInput) -> dict[str, Any]:
        """Read one dimension of the active model by its full name, e.g. "D1@Sketch1".

        Find names with `list_model_dimensions`. Needs: an open part or assembly.
        Read-only. Returns: `dimension` with `name`, `value`, `units` and `kind`:
        mm for a length, degrees (`deg`) for an angle, a whole number for a count
        such as a pattern's instances.

        Args:
            input_data: The dimension. Give `name` (or `dimension_name`), e.g.
                "D1@Boss-Extrude1".

        Returns:
            dict[str, Any]: The dimension's value.
        """
        input_data = normalize_input(input_data, GetDimensionInput)
        result = await adapter.get_dimension(input_data.name)

        require_success(result, context="Failed to get dimension")
        value = result.data
        dimension_value = _result_value(value, "value", default=value)
        dimension_units = _result_value(value, "units", default="mm")
        return {
            "status": "success",
            "message": (
                f"Dimension {input_data.name} = "
                f"{_with_units(dimension_value, dimension_units)}"
            ),
            "dimension": {
                "name": input_data.name,
                "value": dimension_value,
                "units": dimension_units,
                "kind": _result_value(value, "kind", default="length"),
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def set_dimension(input_data: SetDimensionInput) -> dict[str, Any]:
        """Change one dimension of the active model and rebuild.

        Use it to resize an existing model; find the full name with
        `list_model_dimensions` and check the result with `rebuild_model` or
        `verify_model`. The value is in the dimension's own unit: millimetres for
        a length, degrees for an angle, a whole number for a count (a pattern's
        instances). Needs: an open part or assembly. Changes: the dimension and
        the geometry it drives. Returns: `dimension_update` with `name`,
        `old_value`, `new_value` (read back), `units` and `kind`.

        Args:
            input_data: The change. `value` is required, with `name` (or
                `dimension_name`) such as "D1@Sketch1"; `units` is not used.

        Returns:
            dict[str, Any]: The old and new values.
        """
        input_data = normalize_input(input_data, SetDimensionInput)
        result = await adapter.set_dimension(input_data.name, input_data.value)

        require_success(result, context="Failed to set dimension")
        payload = result.data
        units = _result_value(payload, "units", default="mm")
        new_value = _result_value(payload, "new_value", default=input_data.value)
        return {
            "status": "success",
            "message": (
                f"Set dimension {input_data.name} = {_with_units(new_value, units)}"
            ),
            "dimension_update": {
                "name": input_data.name,
                "old_value": _result_value(payload, "old_value"),
                "new_value": new_value,
                "units": units,
                "kind": _result_value(payload, "kind", default="length"),
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def set_units(input_data: SetUnitsInput) -> dict[str, Any]:
        """Set the active document's length units: mm, cm, m, in or ft.

        Use it on a new part before sketching if you want other than the template's
        units. Aliases such as "inch" are accepted. The setting is read back, so success
        means it applied. Tool inputs stay in mm whatever the document shows. Needs: an
        open, active document. Changes: the document's unit system. Returns: `data` with
        `unit_system`, the values applied and observed, and `verified`.

        Args:
            input_data: The units. `unit_system` (mm, cm, m, in or ft) is
                required.

        Returns:
            dict[str, Any]: The units set.
        """
        input_data = normalize_input(input_data, SetUnitsInput)
        result = await adapter.set_units(input_data.unit_system)
        require_success(result, context="Failed to set units")
        data = result.data if isinstance(result.data, dict) else {}
        applied = data.get("unit_system", input_data.unit_system)
        message = f"Set document units to {applied}"
        if data.get("verified") is None:
            message += " (could not be read back to confirm)"
        return {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def create_cut_extrude(input_data: CreateCutExtrudeInput) -> dict[str, Any]:
        """Cut material from the active part with a closed sketch profile: holes, slots, windows.

        Use it after `exit_sketch`. Pass `sketch_name` unless the sketch you just closed
        is the one: with no name only that sketch is used, and any doubt is an error
        naming the sketches found. With `reverse_direction` unset, a one-way cut tries
        both sides of the sketch and keeps the one that removes more material (a tie
        keeps the side along the sketch normal, where `create_extrusion` grows). Set,
        true cuts along the sketch normal and false against it, and the other side is
        tried only when that one has no material. `direction_flipped` says the cut
        went the other way. For a sketch inside the body, use MidPlane (blind) or
        ThroughAllBoth. `start_offset_mm` starts the cut off the sketch plane; a
        `draft_angle` narrows it with depth unless `draft_outward`. For contours that
        cross, `regions: "all"` cuts their union. Needs: an open part with a solid body.
        Changes: adds a Cut-Extrude feature. Returns: `cut_extrude` (`name`,
        `sketch_name`, `direction_flipped`, and the options set) and
        `measured_change` (volume, bounding box and solid-body count before and
        after; left out when the model can't be read).

        Args:
            input_data: The cut. `depth` (mm) is required; `sketch_name`,
                `end_condition` (Blind, MidPlane, ThroughAll, ThroughAllBoth),
                `both_directions`, `reverse_direction`, `start_offset_mm` and
                `draft_angle`/`draft_outward` have defaults.

        Returns:
            dict[str, Any]: The created cut.
        """
        input_data = normalize_input(input_data, CreateCutExtrudeInput)
        params = ExtrusionParameters(
            depth=input_data.depth,
            draft_angle=input_data.draft_angle,
            draft_outward=input_data.draft_outward,
            start_offset=input_data.start_offset_mm,
            both_directions=input_data.both_directions,
            thin_feature=False,
            thin_thickness=None,
            end_condition=input_data.end_condition,
            merge_result=False,
            feature_scope=False,
            auto_select=True,
            sketch_name=input_data.sketch_name,
            regions=input_data.regions,
        )
        if input_data.reverse_direction is not None:
            # Set only when given: left unset, the adapter picks the side.
            params.reverse_direction = input_data.reverse_direction
        before = await _read_summary(adapter)
        result = await adapter.create_cut_extrude(params)
        require_success(result, context="Failed to create cut-extrude")
        after = await _read_summary(adapter) if before is not None else None
        feature = result.data
        feature_params = _result_value(feature, "parameters", default={})
        sketch_used = (
            feature_params.get("sketch_name")
            if isinstance(feature_params, dict)
            else None
        ) or input_data.sketch_name
        flipped = bool(
            isinstance(feature_params, dict) and feature_params.get("direction_flipped")
        )
        name = _result_value(feature, "feature_name", "name", default="Cut-Extrude")
        reply = {
            "status": "success",
            "message": f"Created cut-extrude: {name}"
            + _cut_direction_note(feature_params),
            "cut_extrude": {
                "name": name,
                "depth": input_data.depth,
                "sketch_name": sketch_used,
                "direction_flipped": flipped,
            },
            "execution_time": result.execution_time,
        }
        if isinstance(feature_params, dict) and feature_params.get("mid_plane"):
            reply["cut_extrude"]["end_condition"] = "MidPlane"
        reply["cut_extrude"].update(_offset_and_draft(input_data))
        notes = _regions_reply(reply["cut_extrude"], feature_params)
        reply = _attach_measured_change(reply, before, after)
        return _add_warnings(
            _flag_a_shallow_cut(reply, feature_params, input_data), notes
        )

    async def _text_sketch(
        input_data: EmbossTextInput,
    ) -> tuple[str, dict[str, Any], AdapterResult[dict[str, Any]], list[str]]:
        """Open the label's sketch, write the text, and close it.

        Returns:
            tuple: The sketch's name, its ``sketch`` reply block, the text's
            result and any warnings.

        Raises:
            ToolOperationError: A step failed; a sketch left open by a failed
                text is closed first (an empty one goes away).
        """
        notes: list[str] = []
        plane = str(input_data.plane)
        plane_created = None
        if input_data.face_point is not None:
            made = await adapter.create_reference_plane(
                "", 0.0, 0.0, False, None, list(input_data.face_point)
            )
            require_success(made, context="Failed to make a plane on the face")
            plane = plane_created = str((made.data or {}).get("name"))
        opened = await adapter.create_sketch(plane)
        require_success(opened, context="Failed to open a sketch for the text")
        sketch_name = str(
            opened.data.get("sketch_name")
            if isinstance(opened.data, dict)
            else opened.data
        )
        found = opened.metadata if isinstance(opened.metadata, dict) else {}
        axes = found.get("axes")
        x, y = input_data.x, input_data.y
        if input_data.face_point is not None:
            at = sketch_point(axes, input_data.face_point)
            if at is None:
                notes.append(
                    "The sketch's axes couldn't be read, so x and y were taken "
                    "from the sketch origin, not from face_point."
                )
            else:
                x, y = at[0] + x, at[1] + y
        text = await adapter.add_sketch_text(
            input_data.text,
            x,
            y,
            input_data.height,
            input_data.font,
            input_data.angle,
            input_data.bold,
            input_data.italic,
        )
        if not text.is_success:
            await adapter.exit_sketch()
        require_success(text, context="Failed to add the text")
        closed = await adapter.exit_sketch()
        require_success(closed, context="Failed to close the text sketch")
        block = {
            "name": sketch_name,
            "plane": found.get("plane") or plane,
            "axes": axes,
            "plane_created": plane_created,
        }
        return sketch_name, block, text, notes

    @mcp.tool()
    async def emboss_text(input_data: EmbossTextInput) -> dict[str, Any]:
        """Raise a text label off the active part, or engrave it in, in one call.

        Use it for labels (MIN/MAX, a name and version): it opens a sketch on
        `plane`, or on the flat face at `face_point` (a reference plane is made
        there, and `x`, `y` count from that point), writes the text as
        `add_sketch_text` does, closes the sketch and extrudes it `depth` mm
        (`mode: "emboss"`) or cuts it in (`"engrave"`). An emboss that added
        nothing grew into the part, so it is made again the other way; an engrave
        cuts the side with more material. For text in a sketch you build, use
        `add_sketch_text`. Needs: an open part. Changes: adds a text sketch, a
        Boss-Extrude or Cut-Extrude, and for `face_point` a plane. Returns:
        `text` (read back), `sketch` (`name`, `plane`, `axes`, `plane_created`),
        `feature` (`name`, `mode`, `depth`, `direction_flipped`) and
        `measured_change`.

        Args:
            input_data: The label. `text`, `depth` (mm) and `plane` or
                `face_point` are required; `mode` is emboss, and `x`, `y`,
                `height`, `font`, `angle`, `bold`, `italic` default as in
                `add_sketch_text`.

        Returns:
            dict[str, Any]: The text and the feature made from it.
        """
        input_data = normalize_input(input_data, EmbossTextInput)
        sketch_name, sketch_block, text, notes = await _text_sketch(input_data)
        emboss = input_data.mode == "emboss"
        params = ExtrusionParameters(
            depth=input_data.depth, sketch_name=sketch_name, merge_result=emboss
        )
        if input_data.reverse_direction is not None:
            params.reverse_direction = input_data.reverse_direction
        before = await _read_summary(adapter)
        make = adapter.create_extrusion if emboss else adapter.create_cut_extrude
        result = await make(params)
        require_success(result, context=f"Failed to {input_data.mode} the text")
        after = await _read_summary(adapter) if before is not None else None
        flipped = bool(
            (_result_value(result.data, "parameters", default=None) or {}).get(
                "direction_flipped"
            )
        )
        if (
            emboss
            and input_data.reverse_direction is None
            and _added_nothing(before, after)
        ):
            # Grown into the part: a raised label goes the other way.
            first = _result_value(result.data, "feature_name", "name", default="")
            removed = await adapter.delete_feature(str(first))
            if removed.is_success:
                result = await adapter.create_extrusion(
                    params.model_copy(update={"reverse_direction": True})
                )
                require_success(
                    result, context="Failed to emboss the text the other way"
                )
                after = await _read_summary(adapter)
                flipped = True
        name = _result_value(result.data, "feature_name", "name", default="")
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"{'Embossed' if emboss else 'Engraved'} {input_data.text!r} "
                f"{input_data.depth:g} mm on {sketch_block['plane']}: {name}"
            ),
            "text": text.data,
            "sketch": sketch_block,
            "feature": {
                "name": name,
                "mode": input_data.mode,
                "depth": input_data.depth,
                "direction_flipped": flipped,
            },
            "execution_time": result.execution_time,
        }
        reply = _attach_measured_change(reply, before, after)
        notes += text_warnings(input_data, dict(text.data or {}))
        if notes:
            reply["warning"] = " ".join(filter(None, [reply.get("warning"), *notes]))
        return reply

    @mcp.tool()
    async def add_fillet(input_data: AddFilletInput) -> dict[str, Any]:
        """Round edges of the active part with a constant-radius fillet.

        Name the edges as "Edge<1>", a point "x,y,z" (metres) or "face:x,y,z" for every
        edge of a face; give `edge_ids` from `select_edges`; or leave `edge_names`
        empty to use the current selection (e.g. from `select_face_edges`). A fillet
        that leaves the volume unchanged is an error. Needs: an open part with a solid
        body. Changes: adds a Fillet feature, then clears the selection. Returns:
        `fillet` with `name`, `radius`, `edges`, `edges_selected` and `edges_held`
        (what SolidWorks kept), and `measured_change` (volume before and after). A
        `warning` says when it holds fewer edges than were selected, and when the
        rollback bar is up: check it again after rolling forward.

        Args:
            input_data: The fillet. `radius` (mm) is required; `edge_names`
                defaults to the current selection.

        Returns:
            dict[str, Any]: The created fillet.
        """
        input_data = normalize_input(input_data, AddFilletInput)
        await _select_edge_ids(input_data.edge_ids)
        result = await adapter.add_fillet(input_data.radius, input_data.edge_names)
        require_success(result, context="Failed to add fillet")
        feature = result.data
        reply = {
            "status": "success",
            "message": f"Created fillet: {_result_value(feature, 'feature_name', 'name', default='Fillet')}",
            "fillet": {
                "name": _result_value(
                    feature, "feature_name", "name", default="Fillet"
                ),
                "radius": input_data.radius,
                "edges": input_data.edge_names,
            },
            "execution_time": result.execution_time,
        }
        return _edge_feature_reply("fillet", reply, feature)

    @mcp.tool()
    async def add_chamfer(input_data: AddChamferInput) -> dict[str, Any]:
        """Bevel edges of the active part with a chamfer: `distance` along one face at `angle` (45 by default).

        Name the edges as "Edge<1>", a point "x,y,z" (metres) or "face:x,y,z" for every
        edge of a face; give `edge_ids` from `select_edges`; or leave `edge_names`
        empty to use the current selection (e.g. from `select_face_edges`). At 45
        degrees both legs equal `distance`; at another angle, `flip` swaps which face
        `distance` is measured on. Needs: an open part with a solid body. Changes:
        adds a Chamfer feature, then clears the selection. Returns: `chamfer` with
        `name`, `distance`, `angle`, `flip`, `edges`, `edges_selected` and
        `edges_held` (what SolidWorks kept), and `measured_change` (volume before and
        after). A `warning` says when it holds fewer edges than were selected, and
        when the rollback bar is up.

        Args:
            input_data: The chamfer. `distance` (mm) is required; `edge_names`
                defaults to the current selection.

        Returns:
            dict[str, Any]: The created chamfer.
        """
        input_data = normalize_input(input_data, AddChamferInput)
        await _select_edge_ids(input_data.edge_ids)
        result = await adapter.add_chamfer(
            input_data.distance,
            input_data.edge_names,
            input_data.angle,
            input_data.flip,
        )
        require_success(result, context="Failed to add chamfer")
        feature = result.data
        reply = {
            "status": "success",
            "message": f"Created chamfer: {_result_value(feature, 'feature_name', 'name', default='Chamfer')}",
            "chamfer": {
                "name": _result_value(
                    feature, "feature_name", "name", default="Chamfer"
                ),
                "distance": input_data.distance,
                "angle": input_data.angle,
                "flip": input_data.flip,
                "edges": input_data.edge_names,
            },
            "execution_time": result.execution_time,
        }
        return _edge_feature_reply("chamfer", reply, feature)

    @mcp.tool()
    async def create_sweep(input_data: CreateSweepInput) -> dict[str, Any]:
        """Sweep a closed profile sketch along a path sketch to make a solid (Insert > Boss/Base > Sweep).

        Draw the closed profile and the open path as two sketches first. The profile is
        the sketch you closed last that isn't the path. Needs: an open part with both
        sketches. Changes: adds a Sweep feature. Returns: `sweep` with `name`, `path`,
        `twist_along_path` and `twist_angle`.

        Args:
            input_data: The sweep. `path` (the path sketch's name) is required;
                `twist_along_path`, `twist_angle` (degrees) and `merge_result` have
                defaults.

        Returns:
            dict[str, Any]: The created sweep.
        """
        input_data = normalize_input(input_data, CreateSweepInput)
        params = SweepParameters(
            path=input_data.path,
            twist_along_path=input_data.twist_along_path,
            twist_angle=input_data.twist_angle,
            merge_result=input_data.merge_result,
        )
        result = await adapter.create_sweep(params)
        require_success(result, context="Failed to create sweep")
        feature = result.data
        return {
            "status": "success",
            "message": f"Created sweep: {_result_value(feature, 'feature_name', 'name', default='Sweep')}",
            "sweep": {
                "name": _result_value(feature, "feature_name", "name", default="Sweep"),
                "path": input_data.path,
                "twist_along_path": input_data.twist_along_path,
                "twist_angle": input_data.twist_angle,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def create_loft(input_data: CreateLoftInput) -> dict[str, Any]:
        """Loft a solid through two or more closed profile sketches, in order (Insert > Boss/Base > Loft).

        Use it for shapes that change section, such as a wing or a transition duct. Put
        each profile on its own plane (see `create_reference_plane`). Needs: an open
        part with the profile sketches. Changes: adds a Loft feature. Returns: `loft`
        with `name`, `profiles` and `guide_curves`.

        Args:
            input_data: The loft. `profiles` (sketch names, in order) is required;
                `guide_curves`, `start_tangent`, `end_tangent` and `merge_result`
                have defaults.

        Returns:
            dict[str, Any]: The created loft.
        """
        input_data = normalize_input(input_data, CreateLoftInput)
        params = LoftParameters(
            profiles=input_data.profiles,
            guide_curves=input_data.guide_curves,
            start_tangent=input_data.start_tangent,
            end_tangent=input_data.end_tangent,
            merge_result=input_data.merge_result,
        )
        result = await adapter.create_loft(params)
        require_success(result, context="Failed to create loft")
        feature = result.data
        return {
            "status": "success",
            "message": f"Created loft: {_result_value(feature, 'feature_name', 'name', default='Loft')}",
            "loft": {
                "name": _result_value(feature, "feature_name", "name", default="Loft"),
                "profiles": input_data.profiles,
                "guide_curves": input_data.guide_curves,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def insert_component(input_data: InsertComponentInput) -> dict[str, Any]:
        """Insert a part or sub-assembly file into the active assembly with its origin at a point in mm.

        Use it after `create_assembly`, then position components with `add_mate`. The
        component's own origin goes at `x`, `y`, `z`, unrotated (SolidWorks places a
        new component by its centre; it is moved so its origin is there). `fixed`
        fixes or floats it; SolidWorks fixes the first one by itself. Turn or move it
        later with `set_component_transform`. The file must contain solid geometry;
        an empty or missing file is an error. Needs: an open, active assembly and the
        file on disk. Changes: adds a component. Returns: `component` with its
        instance name (`component`, which `add_mate` takes), `file_path`,
        `position`, `placement` (`placed`, `fixed`, and the `origin_mm` and
        `rotation` read back) and the counts before and after; a `warning` when the
        origin could not be put there.

        Args:
            input_data: The component. `file_path` is required; `x`, `y`, `z` (mm
                from the assembly origin) default to 0.

        Returns:
            dict[str, Any]: The inserted component.
        """
        input_data = normalize_input(input_data, InsertComponentInput)
        result = await adapter.insert_component(
            input_data.file_path,
            input_data.x,
            input_data.y,
            input_data.z,
            input_data.fixed,
        )
        require_success(result, context="Failed to insert component")
        data = result.data if isinstance(result.data, dict) else {}
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Inserted {data.get('component', 'component')}",
            "component": data,
            "execution_time": result.execution_time,
        }
        placement = data.get("placement")
        if isinstance(placement, dict) and placement.get("placed") is False:
            at = placement.get("origin_mm")
            reply["warning"] = (
                "The component is in the assembly, but its origin could not be "
                f"moved to ({input_data.x:g}, {input_data.y:g}, {input_data.z:g}) mm"
                + (f"; it is at {at} mm" if at else "")
                + ". Mate it into place, or check it with list_components."
            )
        return reply

    @mcp.tool()
    async def add_mate(input_data: AddMateInput) -> dict[str, Any]:
        """Mate two components of the active assembly: coincident, concentric, parallel, perpendicular, tangent, distance or angle.

        Each side is a named reference plane or axis (`entity_a`, e.g. "Front Plane"),
        or a face given by a point on it (`face_a`, mm, in that part's own coordinates):
        a pin's round face and a hole's face make a concentric mate. Get instance names
        from `list_components`. A distance or angle mate can land on the side you
        didn't mean: `flip: true` puts it on the other. Needs: an open assembly with
        both components. Changes: adds a mate and may move components. Returns: `mate`
        with `mate_type`, `components`, `entities`, `flipped` (distance and angle
        mates), `geometry_moved` and `moved_components`; a `warning` when a flip
        didn't take.

        Args:
            input_data: The mate. `component_a` and `component_b` are required;
                each side takes `entity_*` or `face_*` (default "Front Plane");
                `mate_type` (coincident), `alignment`, `distance` (mm) and `angle`
                (degrees) have defaults.

        Returns:
            dict[str, Any]: The mate added.
        """
        input_data = normalize_input(input_data, AddMateInput)
        result = await adapter.add_mate(
            input_data.component_a,
            input_data.component_b,
            input_data.entity_a or "Front Plane",
            input_data.entity_b or "Front Plane",
            input_data.mate_type,
            input_data.alignment,
            input_data.distance,
            input_data.angle,
            input_data.face_a,
            input_data.face_b,
            input_data.flip,
        )
        require_success(result, context="Failed to add mate")
        data = result.data if isinstance(result.data, dict) else {}
        message = (
            f"Added {input_data.mate_type} mate between "
            f"{input_data.component_a} and {input_data.component_b}"
        )
        # SolidWorks accepts a mate whose constraint is already
        # satisfied and moves nothing. That is a real success, but
        # saying so plainly beats leaving the caller to infer it.
        if data.get("geometry_moved") is False:
            message += " (no component moved - the constraint was already satisfied)"
        reply: dict[str, Any] = {
            "status": "success",
            "message": message,
            "mate": data,
            "execution_time": result.execution_time,
        }
        if input_data.flip and data.get("flipped") is False:
            reply["warning"] = (
                "The mate was added, but SolidWorks did not flip it: the component "
                "is on the side SolidWorks chose. Check its position, then delete the "
                "mate and add it again, or flip it in SolidWorks."
            )
        return reply

    @mcp.tool()
    async def set_component_state(input_data: SetComponentStateInput) -> dict[str, Any]:
        """Fix or float a component of the active assembly, or make a sub-assembly rigid or flexible.

        Float a part so mates can position it (the first part inserted is fixed); fix
        one to hold it still. Make a sub-assembly flexible so its own mates move in
        this assembly (a crank whose knob turns). Only what you pass changes: the
        component's suppression, visibility and configuration are kept. Needs: an
        open assembly. Changes: the component's fixed or solving state. Returns:
        `state` with `component`, `before` and `after` (`fixed`, `flexible` and the
        properties kept).

        Args:
            input_data: The component and what to change: `fixed`, `flexible` or
                both.

        Returns:
            dict[str, Any]: The state before and after.
        """
        input_data = normalize_input(input_data, SetComponentStateInput)
        result = await adapter.set_component_state(
            input_data.component, input_data.fixed, input_data.flexible
        )
        require_success(result, context="Failed to set the component state")
        data = result.data if isinstance(result.data, dict) else {}
        after = data.get("after", {})
        words = []
        if input_data.fixed is not None:
            words.append("fixed" if after.get("fixed") else "floating")
        if input_data.flexible is not None:
            words.append("flexible" if after.get("flexible") else "rigid")
        return {
            "status": "success",
            "message": f"{input_data.component} is now {' and '.join(words)}",
            "state": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def set_component_transform(
        input_data: SetComponentTransformInput,
    ) -> dict[str, Any]:
        """Place a component of the active assembly: put its origin at a point, turn it, and fix or float it.

        Use it to put parts at exact positions without mates: a stand-in at a
        height, a second rack 137 mm up, a base back on the origin. `origin_mm`
        moves the component's own origin; `rotation_deg` ([rx, ry, rz], about the
        assembly's X, then Y, then Z axis) or `matrix` (its axes as rows) turns it
        from unturned; what you leave out stays. `turn_deg` then turns it from
        there about any axis (`turn_axis`) through a point (`turn_pivot_mm`), e.g.
        a lid about its hinge. A fixed component is floated for the move and fixed
        again unless `fixed` says otherwise. Mates that hold the component can stop
        the move: then `placed` is false and a warning says so. Needs: an open
        assembly. Changes: the component's position and fixed state. Returns:
        `component`, `placed`, `fixed`, `mates` and `transform` (`origin_mm`,
        `rotation` as rows), all read back from SolidWorks.

        Args:
            input_data: The component and where it goes. `component` is
                required; `origin_mm`, `rotation_deg` or `matrix`, `turn_deg`
                with `turn_axis` and `turn_pivot_mm`, and `fixed` are optional.

        Returns:
            dict[str, Any]: The placement read back.
        """
        input_data = normalize_input(input_data, SetComponentTransformInput)
        turn_axis = input_data.turn_axis_vector()
        if turn_axis is None:
            result = await adapter.set_component_transform(
                input_data.component,
                input_data.origin_mm,
                input_data.rotation_rows(),
                input_data.fixed,
            )
        else:
            result = await adapter.set_component_transform(
                input_data.component,
                input_data.origin_mm,
                input_data.rotation_rows(),
                input_data.fixed,
                turn_axis,
                input_data.turn_pivot_mm,
                input_data.turn_deg,
            )
        require_success(result, context=f"Failed to place {input_data.component}")
        data = result.data if isinstance(result.data, dict) else {}
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"Placed {input_data.component} at {data.get('origin_mm')} mm"
                + (" (fixed)" if data.get("fixed") else "")
            ),
            "component": input_data.component,
            "placed": data.get("placed"),
            "fixed": data.get("fixed"),
            "mates": data.get("mates", 0),
            "transform": {
                "origin_mm": data.get("origin_mm"),
                "rotation": data.get("rotation"),
            },
            "execution_time": result.execution_time,
        }
        if data.get("placed") is False:
            mates = int(data.get("mates") or 0)
            reply["message"] = f"{input_data.component} did not move where asked"
            reply["warning"] = (
                "SolidWorks kept the component where `transform` says"
                + (
                    f"; it has {mates} mate(s), which likely hold it there. "
                    "Suppress or delete them, or float the component, and try again."
                    if mates
                    else "; check that it isn't fixed by its mates or a pattern."
                )
            )
        return reply

    @mcp.tool()
    async def list_components() -> dict[str, Any]:
        """List the top-level components of the active assembly by instance name, e.g. "bracket-1".

        Use it before `add_mate`, which takes these names. Needs: an open, active
        assembly. Read-only. Returns: `components` (instance names).
        """
        result = await adapter.list_components()
        require_success(result, context="Failed to list components")
        components = result.data if isinstance(result.data, list) else []
        return {
            "status": "success",
            "message": f"{len(components)} component(s) in the assembly",
            "components": components,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def delete_feature(input_data: DeleteFeatureInput) -> dict[str, Any]:
        """Delete a feature or sketch from the active model, along with the features that depend on it.

        Ask the user before you call it: deleting a sketch also deletes every feature
        built from it (a cut, a boss), and deleting a feature deletes everything that
        depends on it. Use `suppress_feature` instead when you might want it back. Find
        names and dependents with `list_features`. A feature's own sketch stays unless
        `delete_absorbed`. Needs: an open part or assembly. Changes: removes the
        feature and its dependents (`undo` can restore them). Returns: `data` with
        `deleted`, `removed_features` (every feature that went, dependents included),
        `absorbed_sketches_left` (its sketches still in the tree), `features_before`
        and `features_after`; the message names the dependents, and a `warning` says
        so whenever more than the named feature went: tell the user.

        Args:
            input_data: The feature. `name` (e.g. "Fillet1") is required;
                `delete_absorbed` is off.

        Returns:
            dict[str, Any]: What was deleted.
        """
        input_data = normalize_input(input_data, DeleteFeatureInput)
        result = await adapter.delete_feature(
            input_data.name, input_data.delete_absorbed
        )
        require_success(result, context="Failed to delete feature")
        data = result.data if isinstance(result.data, dict) else {}
        removed = data.get("removed_features") or [input_data.name]
        absorbed = set(data.get("absorbed_sketches") or [])
        own = input_data.name.split("@", 1)[0]
        dependents = [n for n in removed if n != own and n not in absorbed]
        left = data.get("absorbed_sketches_left") or []
        message = f"Deleted {input_data.name}"
        if dependents:
            message += f" and its dependents: {', '.join(dependents)}"
        if absorbed and not left:
            message += f"; its sketch {', '.join(sorted(absorbed))} went with it"
        if left:
            message += (
                f"; its sketch {', '.join(left)} is still in the tree "
                "(delete_absorbed removes it too)"
            )
        reply: dict[str, Any] = {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }
        if dependents:
            reply["warning"] = (
                f"Deleting {input_data.name} also deleted {', '.join(dependents)}. "
                "Tell the user; `undo` restores them."
            )
        return reply

    @mcp.tool()
    async def suppress_feature(input_data: SuppressFeatureInput) -> dict[str, Any]:
        """Suppress or unsuppress a feature of the active model; suppressed features stay in the tree but are not built.

        Use it to turn off a feature that breaks the rebuild without deleting it. Needs:
        an open part or assembly. Changes: the feature's suppression state, and its
        children's. Returns: `data` with `feature`, `requested`, `suppressed` (read
        back; null when unreadable) and `was_suppressed`.

        Args:
            input_data: The feature. `name` is required; `suppress` defaults to on
                (pass false to unsuppress).

        Returns:
            dict[str, Any]: The new state.
        """
        input_data = normalize_input(input_data, SuppressFeatureInput)
        result = await adapter.suppress_feature(input_data.name, input_data.suppress)
        require_success(result, context="Failed to suppress feature")
        data = result.data if isinstance(result.data, dict) else {}
        verb = "Suppressed" if input_data.suppress else "Unsuppressed"
        message = f"{verb} {input_data.name}"
        if data.get("suppressed") is None:
            message += " (state could not be read back)"
        return {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def rename_feature(input_data: RenameFeatureInput) -> dict[str, Any]:
        """Rename a feature in the active model's feature tree.

        Use it to give features meaningful names (e.g. "MountingHoles"). The new name is
        read back: a name another feature already uses is an error. Renaming to the
        current name succeeds with no change. Needs: an open part or assembly. Changes:
        the feature's name. Returns: `data` with `old_name`, `new_name`, `renamed` and,
        when unchanged, `reason`.

        Args:
            input_data: The rename. `old_name` and `new_name` are required.

        Returns:
            dict[str, Any]: The rename.
        """
        input_data = normalize_input(input_data, RenameFeatureInput)
        result = await adapter.rename_feature(input_data.old_name, input_data.new_name)
        require_success(result, context="Failed to rename feature")
        data = result.data if isinstance(result.data, dict) else {}
        if data.get("renamed") is False:
            return {
                "status": "success",
                "message": (
                    f"{input_data.old_name} unchanged: "
                    + str(data.get("reason") or "no change")
                ),
                "data": data,
                "execution_time": result.execution_time,
            }
        return {
            "status": "success",
            "message": (
                f"Renamed {data.get('old_name', input_data.old_name)} to "
                f"{data.get('new_name', input_data.new_name)}"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def select_feature(input_data: SelectFeatureInput) -> dict[str, Any]:
        """Select a feature, sketch, plane or component by name in the active document.

        Use it to set up a selection that the next SolidWorks operation acts on.
        `append` adds to the selection instead of replacing it. A name that can't be
        selected is an error. Needs: an open document. Changes: the selection only.
        Returns: `data` with `selected_name` and `selection_count`.

        Args:
            input_data: The selection. `feature_name` is required; `append` is off.

        Returns:
            dict[str, Any]: The selection.
        """
        input_data = normalize_input(input_data, SelectFeatureInput)
        result = await adapter.select_feature(
            input_data.feature_name, input_data.append
        )
        require_success(result)
        data = result.data if isinstance(result.data, dict) else {}
        return {
            "status": "success",
            "message": (
                f"Selected {data.get('selected_name', input_data.feature_name)} "
                f"({data.get('selection_count', '?')} selected)"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def select_face_edges(input_data: SelectFaceEdgesInput) -> dict[str, Any]:
        """Select every edge of one face of the active part, ready for `add_fillet` or `add_chamfer` with no edge names.

        Pick the face with one of `point` (a point on it, mm), `side` (top, bottom,
        front, back, right, left or +X..-Z; the furthest flat face that way) or `plane`
        (the face lying on that plane). `edge_type` narrows to linear or circular edges
        (hole rims); for a feature's edges or edges along an axis or inside a box, use
        `select_edges`. Needs: an open part. Changes: the selection only. Returns:
        `data` with `face`, `edges`, `selected_edges` and `selection_count`.

        Args:
            input_data: The face. Give exactly one of `point`, `side` or `plane`;
                `edge_type` (all) and `append` (off) have defaults.

        Returns:
            dict[str, Any]: The edges selected.
        """
        input_data = normalize_input(input_data, SelectFaceEdgesInput)
        result = await adapter.select_face_edges(
            input_data.point,
            input_data.side,
            input_data.plane,
            input_data.edge_type,
            input_data.append,
        )
        require_success(result)
        data = result.data if isinstance(result.data, dict) else {}
        kinds = sorted({e.get("curve", "other") for e in data.get("edges", [])})
        return {
            "status": "success",
            "message": (
                f"Selected {data.get('selected_edges', 0)} edge(s) "
                f"({', '.join(kinds)}) on the face at "
                f"{data.get('face', {}).get('center_mm')} mm"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def select_edges(input_data: SelectEdgesInput) -> dict[str, Any]:
        """Select the edges of the active part that match rules, ready for `add_fillet` or `add_chamfer` with no edge names.

        Take edges from one source: a `feature` (the faces it made, e.g. a cut's rims),
        a face (`face_point`, `side`, `plane`), the edges two faces share
        (`between_faces`), `edge_ids` from an earlier call, or the whole part; `body`
        keeps one body's. Then keep those passing every filter: `edge_type`,
        `parallel_to` (x, y, z or a direction), `inside_box` (mm),
        `min_length`/`max_length` (mm), `convexity`. Faces outside an `inside_box` are
        not read, and what was read is kept until the next rebuild; give several
        rules as `any_of` in one call. `with_ids` adds `edge_ids` that last across
        rebuilds. Needs: an open part. Changes: the selection only, all or nothing.
        Returns: `data` with `source`, `matched` (edges left after each rule, per rule
        with `any_of`; an error names the rule that removed them), `edges` (up to
        50), `selected_edges`, `selection_count` and `read`.

        Args:
            input_data: The rules. Nothing is required: at most one source, any
                filters, or `any_of`; `append` is off.

        Returns:
            dict[str, Any]: The edges selected.
        """
        input_data = normalize_input(input_data, SelectEdgesInput)
        result = await adapter.select_edges(input_data.query(), input_data.append)
        require_success(result)
        data = result.data if isinstance(result.data, dict) else {}
        return {
            "status": "success",
            "message": (
                f"Selected {data.get('selected_edges', 0)} edge(s) of "
                f"{data.get('source', 'the part')}"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def set_visibility(input_data: SetVisibilityInput) -> dict[str, Any]:
        """Show or hide planes, axes, points, origins and sketches in the active document.

        Use it to clear the view before a screenshot or a review, or to show a plane
        again. By `names` it hides or shows those features (planes, axes, points,
        coordinate systems, sketches) as the tree's Hide and Show do; a boss or a cut
        can't be hidden this way (use `suppress_feature`). By `types` it switches
        every entity of each type at once, as View > Hide/Show does, and the reply
        gives each type's `before`, so you can put it back. Needs: an open document.
        Changes: what is shown, not the geometry. Returns: `data` with `visible`,
        `features` (name, type, visible) and `types` (each one's `before` and `after`).

        Args:
            input_data: What to show or hide. `visible` is required, with `names`,
                `types` or both.

        Returns:
            dict[str, Any]: What was shown or hidden.
        """
        input_data = normalize_input(input_data, SetVisibilityInput)
        result = await adapter.set_visibility(
            input_data.names, list(input_data.types), input_data.visible
        )
        require_success(result, context="Failed to show or hide")
        data = result.data if isinstance(result.data, dict) else {}
        what = [*input_data.names, *(f"all {t}" for t in input_data.types)]
        return {
            "status": "success",
            "message": (
                f"{'Showed' if input_data.visible else 'Hid'} {', '.join(what)}"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def edit_feature(input_data: EditFeatureInput) -> dict[str, Any]:
        """Change an existing feature in place: an extrude's or cut's depth, end condition or direction, a revolve's angle, a fillet's radius, a fillet's or chamfer's edges, an offset plane's distance.

        Use it instead of deleting and re-creating a feature, which takes its
        dependents (fillets, patterns) with it. Give `name` and only the changes:
        `depth` (mm), `end_condition`, `reverse_direction` (extrudes and cuts),
        `angle` (revolves), `radius` (fillets), `offset` (mm) and `flip` (offset
        planes: what stands on it moves too). To repair a fillet or chamfer whose
        edges an edit removed, give `edges: "selection"` or `edge_rules`: they replace
        all its edges (faces a fillet also uses stay); its name, sizes and place in
        the tree stay. Other sizes change with `set_dimension`. Needs: an open
        part. Changes: the feature, then a rebuild; `undo` reverts it. Returns:
        `data` with `feature`, `type`, `before` and `after` (read back after the
        rebuild); with new edges `edges_before`, `edges_given`, `edges_after` and
        `measured_change` (volume), and a `warning` when SolidWorks dropped edges
        it can't treat.

        Args:
            input_data: The change. `name` and at least one change are required;
                `edges` and `edge_rules` exclude each other.

        Returns:
            dict[str, Any]: The feature's values before and after.
        """
        input_data = normalize_input(input_data, EditFeatureInput)
        changes = input_data.changes()
        result = await adapter.edit_feature(input_data.name, changes)
        require_success(result, context=f"Failed to edit {input_data.name}")
        data = result.data if isinstance(result.data, dict) else {}
        after = data.get("after") or {}

        def _shown(value: Any) -> str:
            return f"{value:g}" if isinstance(value, float) else str(value)

        described = [
            f"{k} {_shown(after.get(k, v))}"
            for k, v in changes.items()
            if k not in ("edges", "edge_rules")
        ]
        given, kept = data.get("edges_given"), data.get("edges_after")
        if given is not None:
            had = data.get("edges_before")
            described.append(
                f"edges {given} given, {'?' if kept is None else kept} kept "
                f"(had {'?' if had is None else had})"
            )
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Changed {input_data.name}: {', '.join(described)}",
            "data": data,
            "execution_time": result.execution_time,
        }
        if isinstance(given, int) and isinstance(kept, int) and kept < given:
            reply["warning"] = (
                f"SolidWorks kept {kept} of the {given} edges given: it dropped "
                f"{given - kept}. It drops edges a fillet can't take, such as its "
                "own tangent edges."
            )
        return reply

    @mcp.tool()
    async def get_feature_definition(
        input_data: FeatureDefinitionInput,
    ) -> dict[str, Any]:
        """Read a feature's parameters without changing it: what `edit_feature` would edit.

        Use it to learn how an existing model was built before changing it: an
        extrude's or cut's depth, end condition, direction, both directions and
        draft; a revolve's angle; a fillet's radius and edge count; a chamfer's
        distances or angle and edge count; a linear or circular pattern's count and
        spacing; a sketch's plane, segment counts and text (for its coordinates,
        `get_sketch`). Any other type gives its type and dimensions. Needs: an open
        part with the feature. Read-only. The feature isn't opened for editing, so
        the part isn't marked changed. Returns: `data` with `feature`, `type`,
        `suppressed`, `known_type`, `parameters` (mm and degrees), `sketch` (its
        name and text) when it is built from one, `dimensions` (the names
        `set_dimension` takes, with values) and a `note` when parameters couldn't be
        read.

        Args:
            input_data: The feature. `name` is required.

        Returns:
            dict[str, Any]: The feature's parameters.
        """
        input_data = normalize_input(input_data, FeatureDefinitionInput)
        require_capability(
            adapter,
            "get_feature_definition",
            "The active adapter cannot read feature definitions; nothing was read.",
        )
        result = await adapter.get_feature_definition(input_data.name)
        require_success(result, context=f"Failed to read {input_data.name}")
        data = result.data if isinstance(result.data, dict) else {}
        return {
            "status": "success",
            "message": (
                f"Read {input_data.name} ({data.get('type') or 'feature'}): "
                f"{len(data.get('parameters') or {})} parameter(s), "
                f"{len(data.get('dimensions') or [])} dimension(s)"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def set_rollback(
        input_data: SetRollbackInput | None = None,
    ) -> dict[str, Any]:
        """Move the rollback bar before or after a feature, so new features go in there, or back to the end.

        Use it to add a feature in the middle of the tree: roll back to just after the
        feature it builds on, add it, then call this with no input to roll forward.
        Features below the bar are not built while it is up; rolling forward rebuilds
        them, and a feature added above them can lose edges then. Needs: an open part
        or assembly. Changes: the rollback bar's position. Returns: `data` with
        `position`, `rolled_back` (the features now below the bar) and `rebuild` (the
        errors and warnings SolidWorks reports after the move, as `rebuild_model`
        gives them); a `warning` names each.

        Args:
            input_data: Where to put the bar: `before` or `after` a feature; neither
                (or no input) moves it to the end.

        Returns:
            dict[str, Any]: Where the bar is now.
        """
        input_data = normalize_input(input_data, SetRollbackInput)
        result = await adapter.set_rollback(input_data.before, input_data.after)
        require_success(result, context="Failed to move the rollback bar")
        data = result.data if isinstance(result.data, dict) else {}
        rolled = data.get("rolled_back") or []
        rebuild = data.get("rebuild") if isinstance(data.get("rebuild"), dict) else {}
        flagged = (rebuild.get("error_count") or 0) + (
            rebuild.get("warning_count") or 0
        )
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"Rollback bar at {data.get('position', 'the end')}"
                + (f"; {len(rolled)} feature(s) below it" if rolled else "")
                + (f"; {rebuild.get('summary')}" if flagged else "")
            ),
            "data": data,
            "execution_time": result.execution_time,
        }
        warning = issues_warning(rebuild.get("errors") or [], errors_too=True)
        if warning:
            reply["warning"] = warning
        return reply

    @mcp.tool()
    async def rebuild_model(
        input_data: RebuildModelInput | None = None,
    ) -> dict[str, Any]:
        """Rebuild the active model and report each feature that fails, with the reason and a fix.

        Use it after `set_dimension` or other edits to confirm the model is sound.
        Failing features make the call an error whose `data.errors` names each one, its
        SolidWorks error, what it depends on and a `hint`. Warnings alone (a fillet that
        lost some edges: swFeatureErrorFilletNoEdge) still succeed, with a `warning`
        naming each. Needs: an open part or assembly. Changes: rebuilds the model.
        Returns: `data` with `rebuilt`, `errors`, `error_count`, `warning_count` and
        `summary`.

        Args:
            input_data: Nothing is required: `top_level_only` is off (a full
                rebuild).

        Returns:
            dict[str, Any]: The rebuild report.
        """
        input_data = normalize_input(input_data or {}, RebuildModelInput)
        result = require_success(
            await adapter.rebuild_model(input_data.top_level_only), with_data=True
        )
        data = result.data if isinstance(result.data, dict) else None
        response: dict[str, Any] = {
            "status": "success",
            "message": (data or {}).get("summary") or "Rebuilt",
            "execution_time": result.execution_time,
        }
        if data is not None:
            response["data"] = data
            warning = issues_warning(data.get("errors") or [])
            if warning:
                response["warning"] = warning
        return response

    @mcp.tool()
    async def undo(input_data: UndoInput | None = None) -> dict[str, Any]:
        """Undo the last operations in the active model (Edit > Undo).

        Use it to step back after a feature went wrong. SolidWorks accepts an undo with
        nothing to undo, so the reply says whether the feature tree changed. Needs: an
        open document. Changes: reverts the last `count` operations. Returns: `data`
        with `requested_steps`, `features_before`, `features_after` and `tree_changed`.

        Args:
            input_data: Nothing is required: `count` defaults to 1.

        Returns:
            dict[str, Any]: What changed.
        """
        input_data = normalize_input(input_data or UndoInput(), UndoInput)
        result = await adapter.undo(input_data.count)
        require_success(result, context="Failed to undo")
        data = result.data if isinstance(result.data, dict) else {}
        changed = data.get("tree_changed")
        if changed is False:
            message = (
                f"Undo of {input_data.count} step(s) left the feature "
                "tree unchanged - there was nothing to undo"
            )
        else:
            message = f"Undid {input_data.count} step(s)"
        return {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def create_reference_plane(
        input_data: CreateReferencePlaneInput,
    ) -> dict[str, Any]:
        """Add a reference plane offset from, at an angle to, or on a plane or face, or through any point.

        Use it to sketch somewhere other than the three default planes: pass the new
        plane's name to `create_sketch`. Offset: `reference` and a non-zero `offset`
        (mm). Angle: `reference`, `angle` (degrees) and `axis`, an axis lying in the
        reference (make one with `create_axis`). Face: `face_point`, a point on a flat
        face (sloped or not), with an `offset` or 0 for a plane on the face. Anywhere:
        `point` (mm) and `normal`, built on a line in a 3D sketch of its own (no
        sketch may be open). Needs: an open part. Changes: adds a plane feature (and
        that 3D sketch). Returns: `plane` with `name`, the inputs used and `axes`
        (where a sketch on it points; null when unread), and a `warning` when the
        normal came out reversed.

        Args:
            input_data: The plane. Exactly one of `reference`, `face_point` and
                `point`; then `offset`, or `angle` with `axis`, or `normal` with
                `point`; `flip` is off.

        Returns:
            dict[str, Any]: The new plane.
        """
        input_data = normalize_input(input_data, CreateReferencePlaneInput)
        if input_data.point is not None:
            return await _plane_through(input_data)
        result = await adapter.create_reference_plane(
            input_data.reference,
            input_data.offset,
            input_data.angle,
            input_data.flip,
            input_data.axis,
            input_data.face_point,
        )
        require_success(result, context="Failed to create reference plane")
        data = result.data if isinstance(result.data, dict) else {}
        return {
            "status": "success",
            "message": f"Created reference plane: {data.get('name', 'Plane')}",
            "plane": data,
            "execution_time": result.execution_time,
        }

    async def _plane_through(input_data: CreateReferencePlaneInput) -> dict[str, Any]:
        """``create_reference_plane`` with ``point`` and ``normal``."""
        require_capability(
            adapter,
            "create_plane_through",
            "The active adapter cannot make a plane through a point; nothing was made.",
        )
        result = await adapter.create_plane_through(
            list(input_data.point or []), list(input_data.normal or [])
        )
        require_success(result, context="Failed to create reference plane")
        data = result.data if isinstance(result.data, dict) else {}
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Created reference plane: {data.get('name', 'Plane')}",
            "plane": data,
            "execution_time": result.execution_time,
        }
        notes = []
        normal = (data.get("axes") or {}).get("normal")
        if data.get("normal_matches") is False:
            notes.append(
                f"SolidWorks pointed the plane's normal the other way, to {normal}: "
                "a sketch on it faces that way, and an extrusion grows along it "
                "unless reverse_direction is set."
            )
        if data.get("through_point") is False:
            notes.append(
                "The plane read back doesn't pass through point; check it with "
                "plane_axes before sketching on it."
            )
        return _add_warnings(reply, notes)

    async def _axis_through(start: list[float], end: list[float]) -> dict[str, Any]:
        """Make a reference axis through two points; its data.

        Raises:
            UnsupportedOperationError: The adapter can't.
            ToolOperationError: SolidWorks made no axis.
        """
        require_capability(
            adapter,
            "create_axis_through",
            "The active adapter cannot make an axis through a point; nothing was made.",
        )
        result = await adapter.create_axis_through(start, end)
        require_success(result, context="Failed to create axis")
        return dict(result.data) if isinstance(result.data, dict) else {}

    @mcp.tool()
    async def create_axis(input_data: CreateAxisInput) -> dict[str, Any]:
        """Add a reference axis along the model's x, y or z through the origin, or through any point.

        Use it before `pattern_circular`, which rotates features about an axis, or for
        `create_reference_plane`'s `axis`. `reference` builds it where two default
        planes meet. `point` with `direction` or `second_point` (mm) builds it on a
        line in a 3D sketch of its own, so a plane or a pattern can turn about a
        vertical axis through any centre; no sketch may be open. Needs: an open part.
        Changes: adds an axis feature (and that 3D sketch). Returns: `axis` with
        `name` (e.g. "Axis1"), `reference` and `planes`, or `point_mm`, `direction`,
        `sketch` and `read_back` (where SolidWorks says it runs), and the feature
        counts.

        Args:
            input_data: The axis. `reference` ("x", "y" or "z"), or `point` with
                `direction` or `second_point`.

        Returns:
            dict[str, Any]: The new axis.
        """
        input_data = normalize_input(input_data, CreateAxisInput)
        if input_data.point is not None:
            start, end = input_data.line()
            data = await _axis_through(start, end)
            reply: dict[str, Any] = {
                "status": "success",
                "message": (
                    f"Created {data.get('name') or 'an axis'} through {start} mm "
                    f"along {data.get('direction')}"
                ),
                "axis": data,
                "execution_time": 0.0,
            }
            read_back = data.get("read_back")
            if isinstance(read_back, dict) and read_back.get("on_line") is False:
                reply["warning"] = (
                    "The axis SolidWorks made doesn't run along the line asked for: "
                    f"it reads from {read_back.get('start_mm')} to "
                    f"{read_back.get('end_mm')} mm."
                )
            return reply
        result = await adapter.create_axis(input_data.reference)
        require_success(result, context="Failed to create axis")
        data = result.data if isinstance(result.data, dict) else {}
        axis_name = data.get("reference", input_data.reference)
        return {
            "status": "success",
            "message": f"Created {axis_name} axis",
            "axis": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def create_reference_point(
        input_data: CreateReferencePointInput,
    ) -> dict[str, Any]:
        """Add a reference point on an edge (at a distance or percentage along it) or at the centre of a face.

        Use it to mark a position for a later feature or measurement. Pick the edge or
        face with a point on it in mm. Needs: an open part with that edge or face.
        Changes: adds a point feature. Returns: `point` with `mode`, `at_mm`,
        `distance_mm` or `percent`, and the feature counts.

        Args:
            input_data: The point. `mode` (along_curve or face_center) and `x`,
                `y`, `z` (mm) are required; for along_curve give one of `distance`
                (mm) or `percent`.

        Returns:
            dict[str, Any]: The new point.
        """
        input_data = normalize_input(input_data, CreateReferencePointInput)
        result = await adapter.create_reference_point(
            input_data.mode,
            input_data.x,
            input_data.y,
            input_data.z,
            input_data.distance,
            input_data.percent,
        )
        require_success(result, context="Failed to create reference point")
        data = result.data if isinstance(result.data, dict) else {}
        where = (
            f"{data.get('percent')}% along a curve"
            if data.get("percent") is not None
            else (
                f"{data.get('distance_mm')} mm along a curve"
                if data.get("distance_mm") is not None
                else "a face centre"
            )
        )
        return {
            "status": "success",
            "message": f"Created a reference point at {where}",
            "point": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def mirror_feature(input_data: MirrorFeatureInput) -> dict[str, Any]:
        """Mirror solid bodies or features of the active part about a plane.

        Use it for symmetric parts: model one half, then mirror. Body mirroring (the
        default) is what works for lofts and sweeps; to mirror a cut, name the cut and
        set `mirror_bodies` off. It fails when the volume doesn't change, which means
        nothing was mirrored. Needs: an open part with the named bodies or features.
        Changes: adds a Mirror feature. Returns: `mirror` with `name`, `mirrored`,
        `mirror_plane`, `merge`, `mirror_bodies`, and `volume_before`,
        `volume_after` and `volume_change` (mm³, negative for a cut).

        Args:
            input_data: The mirror. `features` (at least one name) and
                `mirror_plane` (e.g. "Right Plane") are required; `merge` and
                `mirror_bodies` are on.

        Returns:
            dict[str, Any]: The mirror created.
        """
        input_data = normalize_input(input_data, MirrorFeatureInput)
        result = await adapter.mirror_feature(
            input_data.features,
            input_data.mirror_plane,
            input_data.merge,
            input_data.mirror_bodies,
        )
        require_success(result, context="Failed to mirror feature")
        data = result.data if isinstance(result.data, dict) else {}
        return {
            "status": "success",
            "message": f"Mirrored {', '.join(input_data.features)} "
            f"about {input_data.mirror_plane}",
            "mirror": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def pattern_circular(input_data: PatternCircularInput) -> dict[str, Any]:
        """Repeat features of the active part around an axis, e.g. a ring of bosses or ribs.

        Name an axis made by `create_axis`, or give `axis_point` and `axis_direction`
        (mm) and an axis is made there first, kept for later patterns (a cage's six
        faces: one cut, one pattern). Bosses and cuts both work. Give `angle` to spread the instances evenly over
        that many degrees (360 by default), or `step_angle` for the degrees between
        neighbours, e.g. 3 instances 60 degrees apart; `reverse_direction` turns the
        other way. It fails when the volume doesn't change, which means no instance
        landed on the part. Needs: an open part with the named features and axis.
        Changes: adds a circular pattern feature. Returns: `pattern` with `name`,
        `patterned`, `axis`, `count`, `step_angle`, `total_angle`,
        `reverse_direction`, and `volume_before`, `volume_after` and `volume_change`
        (mm³, negative for cuts).

        Args:
            input_data: The pattern. `features` (at least one), `axis` and `count`
                (2 or more, the original included) are required; then `angle` or
                `step_angle`, and `reverse_direction` (off).

        Returns:
            dict[str, Any]: The pattern created.
        """
        input_data = normalize_input(input_data, PatternCircularInput)
        spacing = input_data.spacing()
        axis, made = input_data.axis, None
        if input_data.axis_point is not None:
            made = await _axis_through(*input_data.axis_line())
            axis = str(made.get("name") or "")
        result = await adapter.pattern_circular(
            input_data.features,
            axis,
            input_data.count,
            spacing["angle"],
            spacing["equal_spacing"],
            input_data.reverse_direction,
        )
        if not result.is_success and made is not None:
            # A failed call leaves the part as it was: the axis goes, with its sketch.
            await adapter.delete_feature(str(made.get("sketch") or axis))
        require_success(result, context="Failed to pattern feature")
        data = result.data if isinstance(result.data, dict) else {}
        if made is not None:
            data["axis_created"] = {
                key: made.get(key)
                for key in ("name", "sketch", "point_mm", "direction")
            }
        # The adapter's angle/equal_spacing are SolidWorks' two readings of one
        # number; the reply gives both angles plainly instead.
        for key in ("angle", "equal_spacing"):
            data.pop(key, None)
        data["step_angle"] = round(spacing["step_angle"], 9)
        data["total_angle"] = round(spacing["total_angle"], 9)
        data.setdefault("reverse_direction", input_data.reverse_direction)
        return {
            "status": "success",
            "message": (
                f"Patterned {', '.join(input_data.features)} around "
                f"{axis} ({input_data.count} instances, "
                f"{data['step_angle']:g} degrees apart)"
            ),
            "pattern": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def pattern_linear(input_data: PatternLinearInput) -> dict[str, Any]:
        """Repeat features or bodies of the active part in a row or a grid: slots, holes, ribs.

        Use it to make many identical features from one (8 slot cuts, 16 drafted
        lead-ins): cut or extrude the first, then repeat it `count` times
        `spacing` mm apart along `direction`, and optionally `count2` rows
        `spacing2` apart along `direction2`. A direction is `x`, `y` or `z`
        (`-x` the other way), a reference axis or plane by name (the plane's
        normal), or a point on a straight edge as "x,y,z" mm. Cuts and bosses
        both work; `bodies` repeats solid bodies. For a ring, use
        `pattern_circular`. It fails when the volume doesn't change: no instance
        landed on the part, and the empty pattern is deleted. Needs: an open part
        with the named features. Changes: adds a linear pattern feature.
        Returns: `pattern` (`name`, `patterned`, `direction` and `direction2`
        with `follows`, `count`, `spacing_mm`, `flipped`, and `instances`) and
        `measured_change`.

        Args:
            input_data: The pattern. `features`, `direction`, `count` (2 or
                more, the original included) and `spacing` (mm) are required;
                `direction2` with `count2` and `spacing2` adds rows, and
                `reverse_direction`, `reverse_direction2` and `bodies` are off.

        Returns:
            dict[str, Any]: The pattern created.
        """
        input_data = normalize_input(input_data, PatternLinearInput)
        before = await _read_summary(adapter)
        result = await adapter.pattern_linear(
            input_data.features,
            input_data.direction,
            input_data.count,
            input_data.spacing,
            input_data.direction2,
            input_data.count2 or 1,
            input_data.spacing2 or 0.0,
            input_data.reverse_direction,
            input_data.reverse_direction2,
            input_data.bodies,
        )
        require_success(result, context="Failed to make the linear pattern")
        after = await _read_summary(adapter) if before is not None else None
        data = dict(result.data or {})
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"Patterned {', '.join(input_data.features)}: "
                f"{data.get('instances', input_data.count)} instances "
                f"({data.get('name')})"
            ),
            "pattern": data,
            "execution_time": result.execution_time,
        }
        reply = _attach_measured_change(reply, before, after)
        delta = (reply.get("measured_change") or {}).get("volume_delta_mm3")
        if delta == 0:
            removed = await adapter.delete_feature(str(data.get("name")))
            raise ToolOperationError(
                f"The linear pattern built no instance: the volume stayed at "
                f"{reply['measured_change']['volume_before_mm3']:g} mm3. "
                + (
                    f"The empty pattern {data.get('name')} was deleted. "
                    if removed.is_success
                    else f"The empty pattern {data.get('name')} is still in the "
                    f"tree ({removed.error}). "
                )
                + "Check each direction and its sense (reverse_direction) and that "
                "the instances land on the part.",
                execution_time=result.execution_time,
            )
        return reply

    @mcp.tool()
    async def measure_clearance(input_data: MeasureClearanceInput) -> dict[str, Any]:
        """Measure the smallest gap between two components of the active assembly, in mm.

        Use it to check a fit's clearance, such as a latch in its slot or a pin in
        its hole, where `check_interference` only reports overlaps. It measures the
        components' solid faces pair by pair, nearest first, a cylinder to its
        surface (not its axis), and gives where the gap is. Touching or overlapping
        components read 0 with `touching: true`; for an overlap's volume, use
        `check_interference`. To see the gap, take `export_image` with `section`.
        Needs: an open assembly whose two components are unsuppressed and hold a
        solid body. Read-only. Returns: `data` with `clearance_mm`, `touching`,
        `point_a_mm` and `point_b_mm` (the nearest points, assembly coordinates),
        `face_a` and `face_b` (`kind`: plane, cylinder or other, and `box_mm`),
        `pairs_checked` and `pairs_total`.

        Args:
            input_data: The two components, by instance name as `list_components`
                reports them: `component_a` and `component_b`, both required and
                different.

        Returns:
            dict[str, Any]: The clearance found.
        """
        input_data = normalize_input(input_data, MeasureClearanceInput)
        result = await adapter.measure_clearance(
            input_data.component_a, input_data.component_b
        )
        require_success(result, context="Failed to measure the clearance")
        data = result.data if isinstance(result.data, dict) else {}
        pair = f"{input_data.component_a} and {input_data.component_b}"
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"{pair} touch or overlap"
                if data.get("touching")
                else f"Clearance between {pair}: {data.get('clearance_mm')} mm"
            ),
            "data": data,
            "execution_time": result.execution_time,
        }
        if data.get("touching"):
            reply["warning"] = (
                f"{pair} touch or overlap, so there is no clearance to report; "
                "use check_interference for the overlap's volume."
            )
        return reply

    # Counted from the registry rather than hand-maintained. The literal that
    # used to live here had drifted: it read 15 while 16 tools were actually
    # registered, so the function misreported its own work and the test
    # enshrined the wrong number.
    tool_count = len(await mcp.list_tools())
    return tool_count
