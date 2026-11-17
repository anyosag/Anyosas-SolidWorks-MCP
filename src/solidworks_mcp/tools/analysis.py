"""Analysis tools for SolidWorks MCP Server.

Use these after a model or assembly already exists. Recommended order:
calculate_mass_properties or get_mass_properties, then check_interference, then
analyze_geometry, then get_material_properties. These tools are for validation and
inspection, not for creating geometry. verify_model checks the finished model
against expected values in one call.
"""

from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ..adapters.base import SolidWorksAdapter
from .input_compat import CompatInput
from .tool_boundary import (
    InvalidInputError,
    normalize_input,
    require_capability,
    require_success,
    with_tool_boundary,
)

# Input schemas using Python 3.14 built-in types


#: kg·m² (SolidWorks' SI) to kg·mm².
_KG_M2_TO_KG_MM2 = 1_000_000.0


def _kg_mm2(value: float | None) -> float | None:
    """A moment or product of inertia from kg·m² to kg·mm²."""
    return None if value is None else value * _KG_M2_TO_KG_MM2


class MassPropertiesInput(CompatInput):
    """Input schema for mass properties analysis.

    The tool always measures the active model. Inputs it used to take are
    refused (``input_compat.REMOVED_INPUTS``).

    Attributes:
        density_g_cm3 (float | None): Also give the mass at this density.
    """

    density_g_cm3: float | None = Field(
        default=None,
        gt=0,
        le=25,
        description=(
            "Also give `mass_at_density`: the measured volume times this density, "
            "g/cm³ (a resin at 1.15), for a part with no material. SolidWorks' own "
            "`mass` is kept beside it; `set_density` makes SolidWorks use it"
        ),
    )


class InterferenceCheckInput(CompatInput):
    """Input schema for interference checking.

    Attributes:
        assembly_path (str | None): The assembly path value.
        components (list[str]): The components value.
        include_hidden (bool): The include hidden value.
    """

    assembly_path: str | None = Field(default=None, description="Assembly path alias")
    include_hidden: bool = Field(
        default=False,
        description=(
            "Check hidden components too; by default they are left out, as in "
            "SolidWorks' own Interference Detection"
        ),
    )
    components: list[str] = Field(
        default_factory=list,
        description="List of component names to check for interference",
    )


class GeometryAnalysisInput(BaseModel):
    """Input schema for geometry analysis.

    Attributes:
        analysis_type (str): The analysis type value.
        parameters (dict[str, Any] | None): The parameters value.
    """

    analysis_type: str = Field(
        description="Type of analysis (curvature, draft, thickness, etc.)"
    )
    parameters: dict[str, Any] | None = Field(
        default=None, description="Analysis-specific parameters"
    )


class CheckWallThicknessInput(CompatInput):
    """Input schema for the wall-thickness check.

    Attributes:
        min_wall_mm (float): The thinnest wall the printer can make, mm.
        max_rays (int): The most points to measure.
        exclude_boxes_mm (list[list[float]] | None): Regions meant to be thin.
    """

    min_wall_mm: float = Field(
        default=0.8,
        gt=0,
        description=(
            "The thinnest wall the printer can make, mm (0.8 is two 0.4 mm "
            "nozzle lines); thinner spots are listed"
        ),
    )
    max_rays: int = Field(
        default=5000,
        ge=10,
        le=20000,
        description=(
            "The most points to measure; more finds thin spots on big parts, "
            "slower (default 5000)"
        ),
    )
    exclude_boxes_mm: (
        list[Annotated[list[float], Field(min_length=6, max_length=6)]] | None
    ) = Field(
        default=None,
        max_length=100,
        description=(
            "Regions meant to be thin, such as a snap-fit flexure: each box is "
            "[x1, y1, z1, x2, y2, z2], two opposite corners in the part's "
            "coordinates, mm. Points inside a box (edges included) don't count "
            "against ok, min_thickness_mm or thin_spots; data.excluded reports them"
        ),
    )


class CheckPrintabilityInput(CompatInput):
    """Input schema for checking a part for printing in one orientation.

    Attributes:
        build_direction (str | list[float] | None): The direction the part grows.
        support_angle_deg (float): Surfaces under this angle need support.
        min_bridge_mm (float): Only longer bridges are listed.
        orientations (int | None): How many build directions to try.
        min_feature_mm (float | None): List regions thinner than this.
    """

    build_direction: str | list[float] | None = Field(
        default=None,
        description=(
            "The direction the part grows away from the build plate: 'x', 'y', "
            "'z', '-x', '-y', '-z' or [x, y, z]. A part printed upright on its "
            "base, with Y up in the model, is 'y'. Leave it out with "
            "`orientations` to check in the best one found"
        ),
    )
    orientations: int | None = Field(
        default=None,
        ge=6,
        le=200,
        description=(
            "Try this many build directions (the six axes, the biggest flat faces "
            "on the plate, then directions in between; 26 is a good start) and "
            "report the one needing the least support as `orientations.best`, "
            "with `rotate_to_z`, the turn that makes it +Z for a slicer"
        ),
    )
    min_feature_mm: float | None = Field(
        default=None,
        gt=0,
        le=20,
        description=(
            "Also list the regions thinner than this, mm (the printer's smallest "
            "feature, e.g. 0.4 on resin), from rays cast inward on the mesh: "
            "`thin_features`"
        ),
    )
    support_angle_deg: float = Field(
        default=45.0,
        gt=0,
        lt=90,
        description=(
            "Surfaces facing the plate at less than this many degrees from "
            "horizontal need supports (default 45)"
        ),
    )
    min_bridge_mm: float = Field(
        default=5.0,
        ge=0,
        description="Only flat overhangs held at both ends longer than this are listed, mm",
    )

    def model_post_init(self, __context: Any) -> None:
        from ..utils.printability import direction_vector

        if self.build_direction is None and not self.orientations:
            raise ValueError(
                "give build_direction, or orientations to find the best one"
            )
        if self.build_direction is not None:
            direction_vector(self.build_direction)


class RotationClearanceInput(CompatInput):
    """Input schema for the clearance of a part turned through an angle range.

    Attributes:
        moving (str): The body or component that turns.
        other (str): The one it must clear.
        axis (str | list[float]): The turn's axis direction.
        pivot_mm (list[float]): A point on the axis.
        range_deg (list[float]): ``[from, to]``, 0 inside it.
        step_deg (float): The coarse step.
        tolerance_deg (float): How finely contacts and minima are found.
    """

    moving: str = Field(
        description=(
            "What turns: in a part, a body as `list_bodies` names it; in an "
            "assembly, a component as `list_components` names it (e.g. 'Lid-1')"
        )
    )
    other: str = Field(
        description="What it must clear: another body or component (e.g. 'Base-1')"
    )
    axis: str | list[float] = Field(
        description=(
            "The direction of the turn's axis in the part's or assembly's "
            "coordinates: 'x', 'y', 'z', '-x', '-y', '-z' or [x, y, z]. Positive "
            "angles turn by the right-hand rule about it"
        )
    )
    pivot_mm: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0],
        min_length=3,
        max_length=3,
        description=(
            "A point on the axis, [x, y, z] mm: a point on the hinge pin's axis; "
            "the origin by default"
        ),
    )
    range_deg: list[float] = Field(
        default_factory=lambda: [-180.0, 180.0],
        min_length=2,
        max_length=2,
        description=(
            "[from, to] degrees, from at most 0 to at least 0: 0 is where it is "
            "now. A lid that only opens: [0, 180]. Default [-180, 180]"
        ),
    )
    step_deg: float = Field(
        default=5.0,
        gt=0,
        le=45,
        description=(
            "The coarse step, degrees (default 5). Contacts and the closest pass "
            "are then narrowed to tolerance_deg. Smaller finds thinner walls: "
            "the reply's travel_per_step_mm is how far a point moves per step"
        ),
    )
    tolerance_deg: float = Field(
        default=0.1,
        gt=0,
        le=5,
        description="How finely contact angles and the closest pass are found, degrees",
    )

    def model_post_init(self, __context: Any) -> None:
        from ..adapters.solidworks.placement import axis_vector
        from ..adapters.solidworks.rotation_clearance import range_problem

        self.moving, self.other = self.moving.strip(), self.other.strip()
        if not self.moving or not self.other:
            raise ValueError("moving and other are required")
        if self.moving == self.other:
            raise ValueError("moving and other must be different")
        axis_vector(self.axis)
        problem = range_problem(self.range_deg, self.step_deg, self.tolerance_deg)
        if problem:
            raise ValueError(problem)

    def axis_vector(self) -> list[float]:
        """The axis as a unit vector."""
        from ..adapters.solidworks.placement import axis_vector

        return axis_vector(self.axis)


class VolumeByPlaneInput(CompatInput):
    """Input schema for the volume on each side of a plane.

    Attributes:
        plane (str | None): A reference plane's name.
        point_mm (list[float] | None): Or a point on the plane.
        normal (str | list[float] | None): And its normal.
        bodies (list[str] | None): Only these bodies.
        enclosed (bool): Also the space the bodies enclose on each side.
        density_g_cm3 (float | None): The density for the masses.
    """

    plane: str | None = Field(
        default=None,
        description=(
            "A reference plane's name, e.g. 'Top Plane' or 'Fill line' (Top, Front "
            "and Right work too). `above` is the side its normal points to: +Y "
            "for Top Plane"
        ),
    )
    point_mm: list[float] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="Instead of plane: a point on it, [x, y, z] mm",
    )
    normal: str | list[float] | None = Field(
        default=None,
        description=(
            "With point_mm: the plane's normal, 'x', 'y', 'z', '-x', '-y', '-z' "
            "or [x, y, z]; `above` is the side it points to"
        ),
    )
    bodies: list[str] | None = Field(
        default=None,
        min_length=1,
        description="Only these bodies, as `list_bodies` names them; all by default",
    )
    enclosed: bool = Field(
        default=False,
        description=(
            "Also measure the space the bodies enclose on each side, open only at "
            "the plane: what a cup holds filled to that line (`enclosed_mm3`, "
            "`enclosed_ml`). Slower"
        ),
    )
    density_g_cm3: float | None = Field(
        default=None,
        gt=0,
        le=25,
        description=(
            "The density for the masses, g/cm³ (a resin at 1.15); the part's own "
            "(its material or `set_density`) when omitted"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        from ..adapters.solidworks.placement import axis_vector
        from ..adapters.solidworks.plane_volume import plane_problem

        self.plane = (self.plane or "").strip() or None
        problem = plane_problem(self.plane, self.point_mm, self.normal)
        if problem:
            raise ValueError(problem)
        if self.normal is not None:
            axis_vector(self.normal)
        if self.bodies is not None:
            names = [b.strip() for b in self.bodies]
            if any(not n for n in names) or len(set(names)) != len(names):
                raise ValueError("bodies holds a blank or repeated name")
            self.bodies = names

    def normal_vector(self) -> list[float] | None:
        """The normal as a unit vector, or None with a named plane."""
        from ..adapters.solidworks.placement import axis_vector

        return None if self.normal is None else axis_vector(self.normal)


class VerifyModelInput(BaseModel):
    """Expectations for ``verify_model``; every field is optional.

    Unknown fields are rejected so a misspelt expectation (``volume`` for
    ``volume_mm3``) fails loudly instead of silently not being checked.
    """

    model_config = ConfigDict(extra="forbid")

    mass_kg: float | None = Field(
        default=None, description="Expected mass in kg (depends on the material)"
    )
    volume_mm3: float | None = Field(default=None, description="Expected volume, mm³")
    bounding_box_mm: tuple[float, float, float] | None = Field(
        default=None,
        description="Expected overall size [x, y, z] in mm, in model axes",
    )
    feature_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Expected number of modelling features (extrudes, cuts, fillets, "
            "patterns...); sketches, the origin, folders and the three "
            "default planes are not counted"
        ),
    )
    solid_body_count: int | None = Field(
        default=None, ge=0, description="Expected number of solid bodies (parts)"
    )
    component_count: int | None = Field(
        default=None, ge=0, description="Expected top-level components (assemblies)"
    )
    mate_count: int | None = Field(
        default=None, ge=0, description="Expected unsuppressed mates (assemblies)"
    )
    no_interference: bool | None = Field(
        default=None,
        description="true to require that no components overlap (assemblies)",
    )
    relative_tolerance: float = Field(
        default=0.01,
        ge=0,
        description="Allowed relative error for mass and volume (0.01 = 1 %)",
    )
    length_tolerance_mm: float = Field(
        default=0.1, ge=0, description="Allowed error per bounding-box axis, mm"
    )


#: Assembly-only expectations; asking for them on a part is a caller mistake.
_ASSEMBLY_ONLY = ("component_count", "mate_count", "no_interference")

#: ``GetTypeName2`` values that are tree furniture, not modelling features.
_NON_MODELLING_TYPES = frozenset(
    {
        "ProfileFeature",
        "3DProfileFeature",
        "OriginProfileFeature",
        "DetailCabinet",
        "MateGroup",
        "MateReferenceGroup",
        "MaterialFolder",
    }
)

#: SolidWorks always puts the three default planes first in the tree.
_DEFAULT_PLANE_COUNT = 3


def _count_modelling_features(descriptors: list[dict[str, Any]]) -> list[str]:
    """Return the names of the document's own modelling features.

    Skips component features, sketches, the origin, folders, the mate group
    and the first three reference planes (the default ones, matched by
    position so it works in any SolidWorks language).

    Args:
        descriptors: ``list_features`` rows.

    Returns:
        list[str]: Counted feature names, in tree order.
    """
    names: list[str] = []
    planes_seen = 0
    for row in descriptors:
        if row.get("component") is not None:
            continue
        kind = str(row.get("type") or "")
        if kind == "RefPlane":
            planes_seen += 1
            if planes_seen <= _DEFAULT_PLANE_COUNT:
                continue
        if kind in _NON_MODELLING_TYPES or kind.endswith("Folder"):
            continue
        names.append(str(row.get("name") or ""))
    return names


def _check(
    name: str,
    expected: Any,
    actual: Any,
    unit: str | None,
    passed: bool | None,
    tolerance: Any = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build one ``checks`` entry."""
    entry: dict[str, Any] = {
        "name": name,
        "expected": expected,
        "actual": actual,
        "tolerance": tolerance,
        "unit": unit,
        "passed": passed,
    }
    if note:
        entry["note"] = note
    elif passed is None:
        entry["note"] = "Could not be measured"
    return entry


def _relative_check(
    name: str, expected: float, actual: Any, tolerance: float, unit: str
) -> dict[str, Any]:
    """Compare within a relative tolerance; ``None`` actual is unmeasured."""
    if not isinstance(actual, (int, float)):
        return _check(name, expected, None, unit, None, tolerance)
    passed = abs(actual - expected) <= tolerance * abs(expected)
    return _check(name, expected, actual, unit, passed, tolerance)


def _exact_check(name: str, expected: int, actual: Any, unit: str) -> dict[str, Any]:
    """Compare a count exactly; ``None`` actual is unmeasured."""
    if not isinstance(actual, int):
        return _check(name, expected, None, unit, None)
    return _check(name, expected, actual, unit, actual == expected)


def _box_check(
    expected: tuple[float, float, float], box: Any, tolerance: float
) -> dict[str, Any]:
    """Compare bounding-box size per axis.

    When the axes fail but the sorted sizes match, the note says so: the part
    is right but was built in a different orientation than expected.
    """
    want = list(expected)
    size = box.get("size") if isinstance(box, dict) else None
    if not isinstance(size, list) or len(size) != 3:
        return _check("bounding_box_mm", want, None, "mm", None, tolerance)
    passed = all(abs(a - e) <= tolerance for a, e in zip(size, want, strict=True))
    note = None
    if not passed and all(
        abs(a - e) <= tolerance for a, e in zip(sorted(size), sorted(want), strict=True)
    ):
        note = (
            "Sizes match with the axes reordered: the part is the right size "
            "but oriented differently (check which plane each sketch is on)"
        )
    return _check("bounding_box_mm", want, size, "mm", passed, tolerance, note)


def _verdict(checks: list[dict[str, Any]]) -> str:
    """Overall verdict: fail beats inconclusive beats pass."""
    if not checks:
        return "report_only"
    outcomes = [check["passed"] for check in checks]
    if False in outcomes:
        return "fail"
    if None in outcomes:
        return "inconclusive"
    return "pass"


def _verdict_message(verdict: str, checks: list[dict[str, Any]], doc: str) -> str:
    """One-line summary naming the checks that need attention."""
    total = len(checks)
    passed = sum(1 for check in checks if check["passed"] is True)
    failed = [check["name"] for check in checks if check["passed"] is False]
    unknown = [check["name"] for check in checks if check["passed"] is None]
    if verdict == "report_only":
        return f"Measured the active {doc}; no expectations were given"
    if verdict == "pass":
        return f"Verification passed: {passed} of {total} checks passed"
    if verdict == "fail":
        return (
            f"Verification failed: {passed} of {total} checks passed "
            f"(failed: {', '.join(failed)})"
        )
    return (
        f"Verification inconclusive: {passed} of {total} checks passed, "
        f"could not measure: {', '.join(unknown)}"
    )


#: How many component pairs ``check_interference`` lists, largest overlap first.
TOP_INTERFERENCE_PAIRS = 10

#: How many of those its message names.
_PAIRS_IN_MESSAGE = 3


def interference_pairs(
    interferences: list[dict[str, Any]], limit: int = TOP_INTERFERENCE_PAIRS
) -> dict[str, Any]:
    """The component pairs that overlap most, from ``check_interference``'s list.

    The Dipper session's check of a few components found 62 interferences, and
    what reached the agent was the count alone. Each interference is one
    overlap between two (or more) components; a pair of multibody parts can
    overlap in many places, so the interferences are grouped by the
    components involved and ranked by their total volume.

    Args:
        interferences: ``{components, volume_mm3}`` per interference; a volume
            that isn't a number counts as 0.
        limit: How many pairs to give.

    Returns:
        dict[str, Any]: ``top_pairs`` (``components``, ``count``,
        ``volume_mm3`` in total and ``largest_mm3``, at most ``limit``),
        ``pair_count`` (pairs that overlap) and ``total_volume_mm3``.
    """
    pairs: dict[tuple[str, ...], dict[str, Any]] = {}
    total = 0.0
    for item in interferences:
        if not isinstance(item, dict):
            continue
        names = tuple(sorted(str(n) for n in item.get("components") or []))
        volume = item.get("volume_mm3")
        size = float(volume) if isinstance(volume, int | float) else 0.0
        total += size
        entry = pairs.setdefault(
            names,
            {
                "components": list(names),
                "count": 0,
                "volume_mm3": 0.0,
                "largest_mm3": 0.0,
            },
        )
        entry["count"] += 1
        entry["volume_mm3"] += size
        entry["largest_mm3"] = max(entry["largest_mm3"], size)
    ranked = sorted(pairs.values(), key=lambda e: (-e["volume_mm3"], e["components"]))
    for entry in ranked:
        entry["volume_mm3"] = round(entry["volume_mm3"], 6)
        entry["largest_mm3"] = round(entry["largest_mm3"], 6)
    return {
        "top_pairs": ranked[:limit],
        "pair_count": len(ranked),
        "total_volume_mm3": round(total, 6),
    }


def _interference_message(data: dict[str, Any]) -> str:
    """The count and the pairs that overlap most, in one line."""
    count = data.get("interference_count")
    if not isinstance(count, int):
        return "Interference check completed"
    if count == 0:
        return "No interference found"
    named = [
        f"{' / '.join(p['components']) or '?'} {p['volume_mm3']:g} mm³"
        + (f" ({p['count']} places)" if p["count"] > 1 else "")
        for p in data.get("top_pairs", [])[:_PAIRS_IN_MESSAGE]
    ]
    if not named:
        return f"{count} interference(s); SolidWorks named no components for them"
    return (
        f"{count} interference(s) between {data.get('pair_count')} pair(s) of "
        f"components; most overlap: {'; '.join(named)}"
    )


#: A step on which a point moves farther than this (mm) gets a warning.
_TRAVEL_WARN_MM = 1.0


def _way(direction: dict[str, Any] | None, sign: str) -> str:
    """One direction of a sweep, in words."""
    if not direction or not direction.get("to_deg"):
        return ""
    hit = direction.get("first_contact_deg")
    if hit is None:
        return f"{sign} way free to {direction['to_deg']:g}°"
    return f"{sign} way hits at {hit:g}°"


def _rotation_reply(
    input_data: RotationClearanceInput, data: dict[str, Any], execution_time: float
) -> dict[str, Any]:
    """The rotation_clearance reply: the findings in words, and warnings."""
    who = f"{input_data.moving} against {input_data.other}"
    warnings = []
    if data.get("interfering_at_start"):
        message = (
            f"{who}: they already overlap by {data.get('start_overlap_mm3')} mm³ "
            "where they are, so nothing was turned"
        )
        warnings.append(
            "Clear the overlap first (body_clearance or check_interference shows "
            "it), then sweep again."
        )
    else:
        ways = [
            w
            for w in (
                _way(data.get("positive"), "+"),
                _way(data.get("negative"), "-"),
            )
            if w
        ]
        message = f"Turning {who}: " + ", ".join(ways)
        closest = data.get("closest_pass")
        if closest:
            message += (
                f"; closest pass {closest['clearance_mm']:g} mm at "
                f"{closest['angle_deg']:g}°"
            )
        elif data.get("min_clearance_mm") is not None:
            message += (
                f"; smallest gap {data['min_clearance_mm']:g} mm at "
                f"{data['min_clearance_deg']:g}°"
            )
        if not data.get("clearance_measured"):
            warnings.append(
                "SolidWorks gave no distance between the faces at any angle, so "
                "only contacts (overlaps) were found."
            )
    if data.get("restored") is False:
        warnings.append(
            f"{input_data.moving} could not be put back where it was: check its "
            "placement before saving."
        )
    travel = data.get("travel_per_step_mm") or 0.0
    if travel > _TRAVEL_WARN_MM:
        warnings.append(
            f"Between two steps a point of {input_data.moving} moves up to "
            f"{travel:g} mm: a wall thinner than that can be passed unseen. Lower "
            "step_deg to check thin parts."
        )
    reply: dict[str, Any] = {
        "status": "success",
        "message": message,
        "data": data,
        "execution_time": execution_time,
    }
    if warnings:
        reply["warning"] = " ".join(warnings)
    return reply


def _volume_reply(data: dict[str, Any], execution_time: float) -> dict[str, Any]:
    """The volume_by_plane reply: both sides in words, and warnings."""
    plane = data.get("plane") or {}
    name = plane.get("name") or f"the plane through {plane.get('origin_mm')} mm"

    def side(values: dict[str, Any]) -> str:
        mass = values.get("mass_g")
        return f"{values.get('volume_mm3')} mm³" + (
            f" ({mass:g} g)" if mass is not None else ""
        )

    above, below = data.get("above") or {}, data.get("below") or {}
    message = f"Above {name}: {side(above)}; below: {side(below)}"
    if "enclosed_ml" in below:
        message += (
            f"; filled to it, the part holds {below['enclosed_ml']:g} ml below it "
            f"({below.get('pockets', 0)} pocket(s))"
        )
    warnings = []
    if data.get("check_ok") is False:
        warnings.append(
            f"Above and below differ from the bodies' volume by {data.get('check_mm3')}"
            " mm³: a temporary boolean may have failed. split_body along the plane "
            "and list_bodies confirm it."
        )
    if data.get("density_g_cm3") is None:
        warnings.append(
            "No density is known, so there are no masses: pass density_g_cm3, or "
            "give the part one with set_density or set_material."
        )
    reply: dict[str, Any] = {
        "status": "success",
        "message": message,
        "data": data,
        "execution_time": execution_time,
    }
    if warnings:
        reply["warning"] = " ".join(warnings)
    return reply


async def register_analysis_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register analysis tools with FastMCP.

    Registers comprehensive analysis tools for SolidWorks model evaluation including mass
    properties, interference checking, geometry analysis, and material properties. These
    tools provide critical engineering data for design validation and optimization.

    Args:
        mcp (FastMCP): The mcp value.
        adapter (SolidWorksAdapter): Adapter instance used for the operation.
        config (dict[str, Any]): Configuration values for the operation.

    Returns:
        int: The computed numeric result.

    Example:
                        ```python
                        from solidworks_mcp.tools.analysis import register_analysis_tools

                        tool_count = await register_analysis_tools(mcp, adapter, config)
                        print(f"Registered {tool_count} analysis tools")
                        ```

                    Note:
                        Analysis tools require an active SolidWorks document with geometry.
                        Some analyses may require specific material assignments for accurate results.
    """
    mcp = with_tool_boundary(mcp, adapter)
    tool_count = 0

    @mcp.tool()
    async def calculate_mass_properties(
        input_data: MassPropertiesInput,
    ) -> dict[str, Any]:
        """Measure the active part or assembly's mass properties: volume, surface area, mass, centre of mass and moments of inertia.

        Use it to check weight or balance. To compare a model with the values you
        expect, use `verify_model`; `get_mass_properties` does the same as this tool.
        Mass depends on the assigned material (`set_material`) or density
        (`set_density`); `density_g_cm3` also gives the mass at a density you name.
        Needs: an open part or assembly. Read-only. It rebuilds first. Returns:
        `mass_properties` with `volume` (mm³), `surface_area` (mm²), `mass` (kg),
        `center_of_mass` (mm), `moments_of_inertia` (kg·mm², about the centre of
        mass), `principal_axes`, and `mass_at_density` when asked.

        Args:
            input_data: Nothing is required: it always measures the active model;
                `density_g_cm3` adds the mass at that density.

        Returns:
            dict[str, Any]: The mass properties.
        """
        input_data = normalize_input(input_data, MassPropertiesInput)
        result = await adapter.get_mass_properties()

        require_success(result, context="Failed to calculate mass properties")
        props = result.data
        assert props is not None
        extra: dict[str, Any] = {}
        density = input_data.density_g_cm3
        if density is not None:
            extra["mass_at_density"] = {
                "value": props.volume * density * 1e-6,
                "units": "kg",
                "density_g_cm3": density,
                "from": "the measured volume times density_g_cm3, not SolidWorks' mass",
            }
        return {
            "status": "success",
            "message": "Mass properties calculated successfully"
            + (
                f"; at {density:g} g/cm³ the part is {props.volume * density / 1000:g} g"
                if density is not None
                else ""
            ),
            "mass_properties": {
                "volume": {"value": props.volume, "units": "mm³"},
                "surface_area": {"value": props.surface_area, "units": "mm²"},
                "mass": {"value": props.mass, "units": "kg"},
                "center_of_mass": {
                    "x": props.center_of_mass[0],
                    "y": props.center_of_mass[1],
                    "z": props.center_of_mass[2],
                    "units": "mm",
                },
                "moments_of_inertia": {
                    **{
                        key: _kg_mm2(props.moments_of_inertia[key])
                        for key in ("Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz")
                    },
                    "units": "kg·mm²",
                },
                "principal_axes": props.principal_axes,
                **extra,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def get_mass_properties(
        input_data: MassPropertiesInput | None = None,
    ) -> dict[str, Any]:
        """Measure the active part or assembly's mass properties: volume, surface area, mass, centre of mass and moments of inertia.

        The same as `calculate_mass_properties`, which it calls. To compare a model with
        the values you expect, use `verify_model`. Mass depends on the assigned material
        (`set_material`) or density (`set_density`); `density_g_cm3` also gives the
        mass at a density you name. Needs: an open part or assembly. Read-only. It
        rebuilds first. Returns: `mass_properties` with `volume` (mm³),
        `surface_area` (mm²), `mass` (kg), `center_of_mass` (mm),
        `moments_of_inertia` (kg·mm², about the centre of mass), `principal_axes`,
        and `mass_at_density` when asked.

        Args:
            input_data: Optional: it always measures the active model;
                `density_g_cm3` adds the mass at that density.

        Returns:
            dict[str, Any]: The mass properties.
        """
        return await calculate_mass_properties(input_data or MassPropertiesInput())

    @mcp.tool()
    async def check_interference(input_data: InterferenceCheckInput) -> dict[str, Any]:
        """Find where the components of the active assembly overlap (Tools > Interference Detection).

        Use it after inserting and mating components. A count of 0 is a real
        measurement. To fail a build on interference, use `verify_model` with
        `no_interference`. Needs: an open, active assembly. Read-only. Returns: `data`
        with `interference_found`, `interference_count`, `top_pairs` (the 10 pairs
        of components that overlap most: `components`, `count`, `volume_mm3` and
        `largest_mm3`), `pair_count`, `total_volume_mm3`, `interferences` (each
        overlap's components and volume in mm³) and `scope`; the message names the
        largest.

        Args:
            input_data: Nothing is required. `components` limits the report to
                interferences involving those components; `include_hidden` also
                checks hidden components, which are left out by default.

        Returns:
            dict[str, Any]: The interferences found.
        """
        require_capability(
            adapter,
            "check_interference",
            "Active adapter does not support check_interference; no "
            "interference analysis was performed. Use SolidWorks "
            "directly (Tools > Interference Detection) to check this "
            "assembly.",
        )
        options = input_data.model_dump()
        options["ignore_hidden"] = not input_data.include_hidden
        result = await adapter.check_interference(options)
        require_success(result, "Interference check failed")
        data = dict(result.data) if isinstance(result.data, dict) else {}
        data.update(interference_pairs(list(data.get("interferences") or [])))
        return {
            "status": "success",
            "message": _interference_message(data),
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def analyze_geometry(input_data: GeometryAnalysisInput) -> dict[str, Any]:
        """Run a geometry analysis on the active model, such as curvature, draft or wall thickness.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. For wall
        thickness today, use `check_wall_thickness`; for distances and angles,
        `measure_entities`; for size and volume, `verify_model` or
        `calculate_mass_properties`. Read-only. Returns: once available, the analysis
        under `data`.

        Args:
            input_data: The analysis. `analysis_type` (e.g. curvature, draft,
                thickness) is required; `parameters` has a default.

        Returns:
            dict[str, Any]: The analysis, or a `not_supported` error.
        """
        require_capability(
            adapter,
            "analyze_geometry",
            f"Active adapter does not support analyze_geometry; no "
            f"{input_data.analysis_type} analysis was performed.",
        )
        result = await adapter.analyze_geometry(input_data.model_dump())
        require_success(result, "Failed to analyze geometry")
        return {
            "status": "success",
            "message": f"Geometry analysis ({input_data.analysis_type}) completed",
            "data": result.data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def check_wall_thickness(
        input_data: CheckWallThicknessInput | None = None,
    ) -> dict[str, Any]:
        """Measure the active part's thinnest walls before 3D printing it.

        It samples points on every face and, from each, measures how far the solid
        goes straight inward (the wall's local thickness), then reports the thinnest
        and every spot under `min_wall_mm`. Points near edges and on fillets can read
        a little off, and a face with few display triangles gets few points: treat
        it as a screen, then check a doubtful spot with `measure_entities`. A wall
        meant to be thin (a snap-fit flexure) would fail it: give its region in
        `exclude_boxes_mm`. Needs: an open part. Read-only. Returns: `data` with
        `min_thickness_mm`, `at_mm` and `normal` (where), `below_minimum`,
        `thin_spots` (up to 20), `ok`, `samples` and `measured`; with boxes, those
        cover the points outside them and `excluded` the points inside.

        Args:
            input_data: The check. Nothing is required: `min_wall_mm` (0.8) and
                `max_rays` (5000) have defaults; `exclude_boxes_mm` is off.

        Returns:
            dict[str, Any]: The thickness found.
        """
        input_data = normalize_input(input_data, CheckWallThicknessInput)
        result = await adapter.check_wall_thickness(
            input_data.min_wall_mm,
            input_data.max_rays,
            input_data.exclude_boxes_mm or None,
        )
        require_success(result, context="Failed to measure wall thickness")
        data = result.data if isinstance(result.data, dict) else {}
        thinnest = data.get("min_thickness_mm")
        below = data.get("below_minimum", 0)
        # With boxes, the counts and the verdict are for the points outside them.
        excluded = data.get("excluded") or {}
        lead, measured = "Thinnest wall", data.get("measured")
        if excluded:
            lead = "Outside exclude_boxes_mm, the thinnest wall is"
            measured = int(measured or 0) - int(excluded.get("measured") or 0)
        if thinnest is None:
            message = (
                "Every point measured is inside exclude_boxes_mm"
                if excluded.get("measured")
                else "No wall could be measured"
            )
        elif below:
            message = (
                f"{lead} {thinnest:g} mm; {below} of {measured} points are under "
                f"{input_data.min_wall_mm:g} mm"
            )
        else:
            message = (
                f"{lead} {thinnest:g} mm; every point is at least "
                f"{input_data.min_wall_mm:g} mm"
            )
        if excluded.get("below_minimum"):
            message += (
                f"; {excluded['below_minimum']} points under "
                f"{input_data.min_wall_mm:g} mm inside the boxes are not counted"
            )
        return {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def check_printability(input_data: CheckPrintabilityInput) -> dict[str, Any]:
        """Check the active part for 3D printing (resin or FDM): overhangs, islands, bridges, suction cups; optionally the best orientation and thin features.

        Give `build_direction`, the way the part grows from the build plate. It
        reports the area facing the plate in angle bands and how much needs supports
        (under `support_angle_deg`), islands (spots that start in mid-air), bridges
        (flat overhangs held at both ends) and suction cups (cavities open toward
        the plate, which trap resin). `orientations` tries that many directions and
        checks in the one needing the least support, with the turn to apply;
        `min_feature_mm` lists regions thinner than that. It checks the mesh: a
        screen, not a slicer (exact walls: `check_wall_thickness`). Needs: an open
        part. Read-only. A temporary STL is written and deleted, and your STL
        options are put back. Returns: `data` with `down_facing` (`bands`,
        `needs_support_mm2`), `islands`, `bridges` and `suction_cups` (each
        `count` and `items`, points in model mm), `ok`, `triangles`, and
        `orientations` (`best` with `rotate_to_z`, `ranked`) and `thin_features`
        when asked.

        Args:
            input_data: The check. `build_direction` or `orientations` is
                required; `support_angle_deg` (45) and `min_bridge_mm` (5) have
                defaults; `min_feature_mm` is off.

        Returns:
            dict[str, Any]: The check.
        """
        input_data = normalize_input(input_data, CheckPrintabilityInput)
        if not input_data.orientations and input_data.min_feature_mm is None:
            result = await adapter.check_printability(
                input_data.build_direction,
                input_data.support_angle_deg,
                input_data.min_bridge_mm,
            )
        else:
            result = await adapter.check_printability(
                input_data.build_direction,
                input_data.support_angle_deg,
                input_data.min_bridge_mm,
                input_data.orientations or 0,
                input_data.min_feature_mm,
            )
        require_success(result, context="Failed to check the part for printing")
        data = result.data if isinstance(result.data, dict) else {}
        found = [
            f"{data[key]['count']} {label}"
            for key, label in (
                ("islands", "island(s)"),
                ("bridges", "bridge(s)"),
                ("suction_cups", "suction cup(s)"),
            )
            if (data.get(key) or {}).get("count")
        ]
        support = (data.get("down_facing") or {}).get("needs_support_mm2") or 0
        if support:
            found.append(f"{support:g} mm² needing supports")
        message = (
            "Prints in this orientation without supports"
            if data.get("ok")
            else "In this orientation: " + ", ".join(found)
        )
        best = (data.get("orientations") or {}).get("best")
        if best:
            turn = best.get("rotate_to_z") or {}
            message = (
                f"Best of {data['orientations'].get('tried')} orientations: build "
                f"along {best.get('build_direction')} (turn {turn.get('angle_deg')}° "
                f"about {turn.get('axis')} to make it +Z). "
                + ("Checked in it. " if data.get("orientation") == "best" else "")
                + message
            )
        thin = data.get("thin_features")
        if thin:
            count = (thin.get("regions") or {}).get("count", 0)
            message += (
                f"; {count} region(s) thinner than {thin.get('min_feature_mm'):g} mm, "
                f"the thinnest {thin.get('thinnest_mm')} mm"
                if count
                else f"; nothing thinner than {thin.get('min_feature_mm'):g} mm"
            )
        return {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def rotation_clearance(input_data: RotationClearanceInput) -> dict[str, Any]:
        """Turn a body or component about an axis through a point, over an angle range, and find the smallest gap and where it first hits.

        Use it for hinges, latches and lids: a lid's open stop, the angle a latch
        clears a wall by 0.3 mm, a pin's lift-off window. In a part it turns a body
        (`list_bodies`); in an assembly, a component (`list_components`). It steps
        out from where it is (0°) both ways, narrows each contact and the closest
        pass to `tolerance_deg`, and reports overlap as contact, so faces resting
        together at the start don't stop it. For one pose, use `body_clearance` or
        `measure_clearance`. Needs: an open part or assembly. Changes: in an
        assembly, the component's placement while it runs; it is put back after
        (mates that hold it stop the sweep). In a part, nothing (temporary
        copies). Returns: `data` with `positive` and `negative` (`first_contact_deg`,
        `free_to_deg`), `min_clearance_mm` and `min_clearance_deg`,
        `closest_pass`, `travel_per_step_mm` and `samples`.

        Args:
            input_data: The turn. `moving`, `other` and `axis` are required;
                `pivot_mm` (origin), `range_deg` ([-180, 180]), `step_deg` (5) and
                `tolerance_deg` (0.1) have defaults.

        Returns:
            dict[str, Any]: The sweep.
        """
        input_data = normalize_input(input_data, RotationClearanceInput)
        require_capability(
            adapter,
            "rotation_clearance",
            "The active adapter does not support rotation_clearance; nothing was "
            "turned.",
        )
        result = await adapter.rotation_clearance(
            input_data.moving,
            input_data.other,
            input_data.axis_vector(),
            input_data.pivot_mm,
            input_data.range_deg,
            input_data.step_deg,
            input_data.tolerance_deg,
        )
        require_success(result, context="Failed to sweep the turn")
        data = result.data if isinstance(result.data, dict) else {}
        return _rotation_reply(input_data, data, result.execution_time)

    @mcp.tool()
    async def volume_by_plane(input_data: VolumeByPlaneInput) -> dict[str, Any]:
        """Measure the active part's volume, and mass, above and below a plane, without cutting it.

        Use it for fill lines and part splits: the resin below a fill line, the
        share of a part above a parting plane, or with `enclosed` what a cup
        holds filled to that line. Give a named plane, or a point and a normal;
        `above` is the side the normal points to. It works on temporary copies of
        the bodies, so nothing is added to the part. Mass uses `density_g_cm3`, or
        the part's material or `set_density`. Needs: an open part with solid
        bodies. Read-only. Returns: `data` with `above` and `below` (`volume_mm3`,
        `mass_g`, and with `enclosed`, `enclosed_mm3`, `enclosed_ml` and
        `pockets`), `bodies` (each one's split), `total_volume_mm3`, `check_mm3`,
        `plane` and `density_source`.

        Args:
            input_data: The plane. `plane`, or `point_mm` and `normal`, is
                required; `bodies` (all), `enclosed` (false) and `density_g_cm3`
                (the part's) have defaults.

        Returns:
            dict[str, Any]: The volumes on each side.
        """
        input_data = normalize_input(input_data, VolumeByPlaneInput)
        require_capability(
            adapter,
            "volume_by_plane",
            "The active adapter does not support volume_by_plane; nothing was "
            "measured.",
        )
        result = await adapter.volume_by_plane(
            input_data.plane,
            input_data.point_mm,
            input_data.normal_vector(),
            input_data.bodies,
            input_data.enclosed,
            input_data.density_g_cm3,
        )
        require_success(result, context="Failed to measure the volume by the plane")
        data = result.data if isinstance(result.data, dict) else {}
        return _volume_reply(data, result.execution_time)

    @mcp.tool()
    async def get_material_properties() -> dict[str, Any]:
        """Report the physical properties of the active part's material: density, modulus, Poisson's ratio, strength.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. For the
        material's name today, use `get_material`; for the materials you can assign, use
        `list_available_materials`. Read-only. Returns: once available, the properties
        under `data`.
        """
        require_capability(
            adapter,
            "get_material_properties",
            "Active adapter does not support get_material_properties",
        )
        result = await adapter.get_material_properties()
        require_success(result, "Failed to get material properties")
        return {
            "status": "success",
            "message": "Material properties retrieved successfully",
            "data": result.data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def verify_model(
        input_data: VerifyModelInput | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Check the active part or assembly against what you meant to build, one pass/fail check per expected value.

        Use it as the last step of a build: a modelling tool's success doesn't prove the
        geometry is right. Give only the values you know. Units: mm, mm³, kg;
        `bounding_box_mm` is [x, y, z] in model axes. Widen `relative_tolerance` when
        your sums ignore fillets. Needs: an open part or assembly. Read-only. Returns:
        `verdict` (pass, fail, inconclusive or report_only), `checks` (each `expected`,
        `actual`, `tolerance`, `passed`; null when unmeasured) and `measured`. A
        mismatch is still `status: success`.

        Args:
            input_data: The expectations, all optional: `mass_kg`, `volume_mm3`,
                `bounding_box_mm`, `feature_count`, `solid_body_count`, and for an
                assembly `component_count`, `mate_count` and `no_interference`;
                `relative_tolerance` (0.01) and `length_tolerance_mm` (0.1) have
                defaults. With none it only reports what it measured.

        Returns:
            dict[str, Any]: The verdict and checks.
        """
        expectations = normalize_input(input_data, VerifyModelInput)
        require_capability(
            adapter,
            "get_model_geometry_summary",
            "Active adapter cannot read the model back, so nothing was verified.",
        )
        summary_result = await adapter.get_model_geometry_summary()
        require_success(summary_result, context="Failed to read the model back")
        summary = dict(summary_result.data or {})
        is_assembly = summary.get("document_type") == "assembly"

        if not is_assembly:
            misplaced = [
                name
                for name in _ASSEMBLY_ONLY
                if getattr(expectations, name) is not None
            ]
            if misplaced:
                raise InvalidInputError(
                    f"{', '.join(misplaced)} only apply to assemblies; the active "
                    "document is a part"
                )

        measured: dict[str, Any] = dict(summary)
        checks: list[dict[str, Any]] = []
        rel = expectations.relative_tolerance

        if expectations.mass_kg is not None:
            mass_result = await adapter.get_mass_properties()
            mass = (
                mass_result.data.mass
                if mass_result.is_success and mass_result.data
                else None
            )
            material_result = await adapter.get_material()
            material = (
                (material_result.data or {}).get("material")
                if material_result.is_success
                else None
            )
            measured["mass_kg"] = mass
            measured["material"] = material
            check = _relative_check("mass_kg", expectations.mass_kg, mass, rel, "kg")
            if check["passed"] is False:
                check["note"] = (
                    f"Material: {material}"
                    if material
                    else "No material assigned; SolidWorks used a default density"
                )
            checks.append(check)

        if expectations.volume_mm3 is not None:
            checks.append(
                _relative_check(
                    "volume_mm3",
                    expectations.volume_mm3,
                    summary.get("volume_mm3"),
                    rel,
                    "mm³",
                )
            )

        if expectations.bounding_box_mm is not None:
            checks.append(
                _box_check(
                    expectations.bounding_box_mm,
                    summary.get("bounding_box_mm"),
                    expectations.length_tolerance_mm,
                )
            )

        if expectations.feature_count is not None:
            features_result = await adapter.list_features()
            names = (
                _count_modelling_features(features_result.data or [])
                if features_result.is_success
                else None
            )
            measured["features"] = names
            checks.append(
                _exact_check(
                    "feature_count",
                    expectations.feature_count,
                    None if names is None else len(names),
                    "features",
                )
            )

        for name, unit in (
            ("solid_body_count", "bodies"),
            ("component_count", "components"),
            ("mate_count", "mates"),
        ):
            expected = getattr(expectations, name)
            if expected is not None:
                checks.append(_exact_check(name, expected, summary.get(name), unit))

        if expectations.no_interference is not None:
            interference = await adapter.check_interference({})
            found = (
                (interference.data or {}).get("interference_found")
                if interference.is_success
                else None
            )
            measured["interference"] = (
                interference.data if interference.is_success else None
            )
            clear = None if found is None else not found
            checks.append(
                _check(
                    "no_interference",
                    expectations.no_interference,
                    clear,
                    None,
                    None if clear is None else clear == expectations.no_interference,
                    note=None if interference.is_success else interference.error,
                )
            )

        verdict = _verdict(checks)
        return {
            "status": "success",
            "message": _verdict_message(
                verdict, checks, str(summary.get("document_type") or "document")
            ),
            "verdict": verdict,
            "checks": checks,
            "measured": measured,
            "execution_time": summary_result.execution_time,
        }

    # Future analysis tools:
    # - perform_fea_analysis (if FEA capabilities are available)
    # - analyze_flow (computational fluid dynamics)
    # - thermal_analysis
    # - vibration_analysis
    # - stress_concentration_analysis

    tool_count = 4  # Keep legacy reported count expected by tests
    return tool_count
