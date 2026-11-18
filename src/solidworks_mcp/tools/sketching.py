"""Sketching tools for SolidWorks MCP Server.

Provides tools for creating and editing sketches, including geometric entities like
lines, circles, rectangles, arcs, and constraints.
"""

import math
from typing import Annotated, Any, Literal, Self, TypeVar

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..adapters.base import SolidWorksAdapter
from ..adapters.solidworks.plane_frame import (
    OFF_PLANE_TOLERANCE_MM,
    Frame,
    project,
)
from ..adapters.solidworks.rebuild_diagnostics import body_count_warning
from .input_compat import CompatInput
from .tool_boundary import (
    InvalidInputError,
    ToolOperationError,
    describe_validation_error,
    normalize_input,
    require_capability,
    require_success,
    with_tool_boundary,
)

TInput = TypeVar("TInput", bound=BaseModel)


# Input schemas using Python 3.14 built-in types

Coordinates = Literal["sketch", "model"]

#: The ``coordinates`` field every drawing tool takes.
_COORDINATES = (
    "'sketch' (the default): points are x, y in the sketch's own mm. 'model': "
    "points are x, y, z in model mm (give the z fields too), projected onto the "
    f"open sketch's plane; a point more than {OFF_PLANE_TOLERANCE_MM:g} mm off "
    "it is refused, naming it and its distance"
)


def _check_z(coordinates: str, given: dict[str, float | None]) -> None:
    """Require the z fields with model coordinates, and refuse them without.

    Raises:
        ValueError: A z is missing with ``model``, or given with ``sketch``.
    """
    if coordinates == "model":
        missing = [name for name, value in given.items() if value is None]
        if missing:
            raise ValueError(
                f"coordinates 'model' needs {', '.join(missing)} (model mm)"
            )
    elif any(value is not None for value in given.values()):
        raise ValueError(
            f"{', '.join(n for n, v in given.items() if v is not None)} only go "
            "with coordinates 'model'"
        )


class CreateSketchInput(CompatInput):
    """Input schema for creating a sketch.

    Attributes:
        plane (str): The plane value.
        sketch_name (str | None): The sketch name value.
    """

    plane: str = Field(
        description=(
            "Plane to sketch on: 'Front' (or 'XY'), 'Top' ('XZ'), 'Right' "
            "('YZ'), or the exact name of a plane in the part, e.g. 'Plane1'"
        )
    )
    sketch_name: str | None = Field(
        default=None,
        description=(
            "Name to give the sketch in the feature tree, applied when "
            "exit_sketch closes it; omit to keep SolidWorks' own (Sketch1, "
            "Sketch2, ...)"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the create sketch input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Plane is required.
        """
        if not self.plane.strip():
            raise ValueError("plane is required")


class AddLineInput(CompatInput):
    """Input schema for adding a line to sketch.

    Attributes:
        construction (bool): The construction value.
        end_x (float | None): The end x value.
        end_y (float | None): The end y value.
        start_x (float | None): The start x value.
        start_y (float | None): The start y value.
        x1 (float | None): The x1 value.
        x2 (float | None): The x2 value.
        y1 (float | None): The y1 value.
        y2 (float | None): The y2 value.
    """

    x1: float | None = Field(default=None, description="Start point X coordinate in mm")
    y1: float | None = Field(default=None, description="Start point Y coordinate in mm")
    x2: float | None = Field(default=None, description="End point X coordinate in mm")
    y2: float | None = Field(default=None, description="End point Y coordinate in mm")
    start_x: float | None = Field(default=None, description="Start point X alias")
    start_y: float | None = Field(default=None, description="Start point Y alias")
    end_x: float | None = Field(default=None, description="End point X alias")
    end_y: float | None = Field(default=None, description="End point Y alias")
    z1: float | None = Field(
        default=None,
        description="Start point Z in model mm; only with coordinates 'model'",
    )
    z2: float | None = Field(
        default=None,
        description="End point Z in model mm; only with coordinates 'model'",
    )
    coordinates: Coordinates = Field(default="sketch", description=_COORDINATES)
    construction: bool = Field(default=False, description="Construction geometry flag")

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the add line input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.
        """
        self.x1 = self.x1 if self.x1 is not None else self.start_x
        self.y1 = self.y1 if self.y1 is not None else self.start_y
        self.x2 = self.x2 if self.x2 is not None else self.end_x
        self.y2 = self.y2 if self.y2 is not None else self.end_y
        _check_z(self.coordinates, {"z1": self.z1, "z2": self.z2})

    def model_points(self) -> list[list[float]]:
        """The start and end in model mm, for coordinates 'model'."""
        return [
            [self.x1 or 0.0, self.y1 or 0.0, self.z1 or 0.0],
            [self.x2 or 0.0, self.y2 or 0.0, self.z2 or 0.0],
        ]


class AddCircleInput(CompatInput):
    """Input schema for adding a circle to sketch.

    Attributes:
        center_x (float): The center x value.
        center_y (float): The center y value.
        construction (bool): The construction value.
        radius (float): The radius value.
    """

    center_x: float = Field(description="Circle center X coordinate in mm")
    center_y: float = Field(description="Circle center Y coordinate in mm")
    center_z: float | None = Field(
        default=None, description="Center Z in model mm; only with coordinates 'model'"
    )
    radius: float = Field(description="Circle radius in mm")
    coordinates: Coordinates = Field(default="sketch", description=_COORDINATES)
    construction: bool = Field(default=False, description="Construction geometry flag")

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the add circle input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Radius must be positive.
        """
        if self.radius <= 0:
            raise ValueError("radius must be positive")
        _check_z(self.coordinates, {"center_z": self.center_z})


class AddRectangleInput(CompatInput):
    """Input schema for adding a rectangle to sketch.

    Attributes:
        construction (bool): The construction value.
        corner1_x (float | None): The corner1 x value.
        corner1_y (float | None): The corner1 y value.
        corner2_x (float | None): The corner2 x value.
        corner2_y (float | None): The corner2 y value.
        x1 (float | None): The x1 value.
        x2 (float | None): The x2 value.
        y1 (float | None): The y1 value.
        y2 (float | None): The y2 value.
    """

    x1: float | None = Field(
        default=None, description="First corner X coordinate in mm"
    )
    y1: float | None = Field(
        default=None, description="First corner Y coordinate in mm"
    )
    x2: float | None = Field(
        default=None, description="Opposite corner X coordinate in mm"
    )
    y2: float | None = Field(
        default=None, description="Opposite corner Y coordinate in mm"
    )
    corner1_x: float | None = Field(default=None, description="Corner 1 X alias")
    corner1_y: float | None = Field(default=None, description="Corner 1 Y alias")
    corner2_x: float | None = Field(default=None, description="Corner 2 X alias")
    corner2_y: float | None = Field(default=None, description="Corner 2 Y alias")
    z1: float | None = Field(
        default=None,
        description="First corner Z in model mm; only with coordinates 'model'",
    )
    z2: float | None = Field(
        default=None,
        description="Opposite corner Z in model mm; only with coordinates 'model'",
    )
    coordinates: Coordinates = Field(
        default="sketch",
        description=_COORDINATES + "; the sides still run along sketch x and y",
    )
    construction: bool = Field(default=False, description="Construction geometry flag")

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the add rectangle input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Rectangle corners must differ.
        """
        self.x1 = self.x1 if self.x1 is not None else self.corner1_x
        self.y1 = self.y1 if self.y1 is not None else self.corner1_y
        self.x2 = self.x2 if self.x2 is not None else self.corner2_x
        self.y2 = self.y2 if self.y2 is not None else self.corner2_y
        _check_z(self.coordinates, {"z1": self.z1, "z2": self.z2})
        if (
            self.coordinates == "sketch"
            and None not in (self.x1, self.y1, self.x2, self.y2)
            and self.x1 == self.x2
            and self.y1 == self.y2
        ):
            raise ValueError("rectangle corners must differ")


class AddArcInput(BaseModel):
    """Input schema for adding an arc to sketch.

    Attributes:
        center_x (float): The center x value.
        center_y (float): The center y value.
        end_x (float): The end x value.
        end_y (float): The end y value.
        start_x (float): The start x value.
        start_y (float): The start y value.
    """

    center_x: float = Field(description="Arc center X coordinate in mm")
    center_y: float = Field(description="Arc center Y coordinate in mm")
    start_x: float = Field(description="Arc start point X coordinate in mm")
    start_y: float = Field(description="Arc start point Y coordinate in mm")
    end_x: float = Field(description="Arc end point X coordinate in mm")
    end_y: float = Field(description="Arc end point Y coordinate in mm")
    center_z: float | None = Field(
        default=None, description="Center Z in model mm; only with coordinates 'model'"
    )
    start_z: float | None = Field(
        default=None, description="Start Z in model mm; only with coordinates 'model'"
    )
    end_z: float | None = Field(
        default=None, description="End Z in model mm; only with coordinates 'model'"
    )
    coordinates: Coordinates = Field(default="sketch", description=_COORDINATES)
    direction: Literal["ccw", "cw"] = Field(
        default="ccw",
        description=(
            "Which way the arc runs from start to end, looking at the sketch "
            "(x right, y up; in model coordinates, looking against its normal): "
            "'ccw' counter-clockwise (the default) or 'cw'"
        ),
    )

    @model_validator(mode="after")
    def _z_with_model(self) -> Self:
        _check_z(
            self.coordinates,
            {"center_z": self.center_z, "start_z": self.start_z, "end_z": self.end_z},
        )
        return self


#: An outline's points closer than this (mm) are the same point: the last
#: segment of a closed outline that ends this close to its start ends on it.
_SAME_POINT_MM = 1e-3

#: How far an arc's end may be from the circle through its start (mm).
#: SolidWorks puts the end on that circle, so a larger miss would leave a gap
#: between the arc and the next segment.
_ARC_RADIUS_TOLERANCE_MM = 1e-3

Point2 = Annotated[
    list[float],
    Field(
        min_length=2,
        max_length=3,
        description=(
            "A point [x, y] in sketch mm, or [x, y, z] in model mm with "
            "coordinates 'model'"
        ),
    ),
]


def _distance(a: list[float], b: list[float]) -> float:
    """Distance between two sketch points, mm."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def arc_sweep_deg(
    center: list[float], start: list[float], end: list[float], direction: str
) -> float:
    """The angle an arc turns through from start to end, degrees in [0, 360).

    Args:
        center: The arc's centre [x, y].
        start: Its start point.
        end: Its end point.
        direction: ``"ccw"`` or ``"cw"``.

    Returns:
        float: 90 for a quarter arc either way round; a ``"cw"`` arc from
        (10, 0) to (0, 10) about the origin turns through 270.
    """
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = math.degrees(end_angle - start_angle) % 360.0
    return (360.0 - sweep) % 360.0 if direction == "cw" else sweep


class OutlineSegment(BaseModel):
    """One segment of an outline, drawn from where the previous one ended.

    Attributes:
        type (str): ``"line"`` or ``"arc"``.
        to (list[float]): The segment's end point [x, y], mm.
        center (list[float] | None): The arc's centre [x, y], mm.
        direction (str): The arc's direction, ``"ccw"`` or ``"cw"``.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["line", "arc"] = Field(
        default="line", description="'line' (the default) or 'arc'"
    )
    to: Point2 = Field(
        description=(
            "End point [x, y] in mm. The segment starts where the previous one "
            "ended (the first at the outline's 'start')"
        )
    )
    center: Point2 | None = Field(
        default=None,
        description="Arc centre [x, y] in mm; required for an arc, not given for a line",
    )
    direction: Literal["ccw", "cw"] = Field(
        default="ccw",
        description="Arc direction from its start to 'to': 'ccw' (default) or 'cw'",
    )

    @model_validator(mode="after")
    def _center_matches_type(self) -> Self:
        if self.type == "arc" and self.center is None:
            raise ValueError("an arc needs 'center'")
        if self.type == "line" and self.center is not None:
            raise ValueError("a line takes no 'center'; set type to 'arc'")
        return self


class Outline(BaseModel):
    """One outline: a chain of lines and arcs, or a polyline through points.

    Attributes:
        start (list[float] | None): Where ``segments`` begins, mm.
        segments (list[OutlineSegment] | None): The chain, each from the last.
        points (list[list[float]] | None): Corner points joined by lines.
        closed (bool): Join the end back to the start.
        construction (bool): Draw it as construction geometry.
    """

    model_config = ConfigDict(extra="forbid")

    start: Point2 | None = Field(
        default=None, description="Start point [x, y] in mm; required with 'segments'"
    )
    segments: list[OutlineSegment] | None = Field(
        default=None,
        description="Lines and arcs in order, each starting where the last ended",
    )
    points: list[Point2] | None = Field(
        default=None,
        description=(
            "Instead of 'start' and 'segments': corner points [x, y] in mm, "
            "joined by straight lines"
        ),
    )
    closed: bool = Field(
        default=True,
        description=(
            "Join the last point back to the first (the default), as a profile "
            "for an extrude or cut needs; false leaves an open chain"
        ),
    )
    construction: bool = Field(
        default=False, description="Draw the outline as construction geometry"
    )

    @model_validator(mode="after")
    def _one_form(self) -> Self:
        if (self.points is None) == (self.segments is None):
            raise ValueError("give either 'points' or 'start' with 'segments'")
        if self.segments is not None and self.start is None:
            raise ValueError("'segments' needs 'start'")
        if self.points is not None and self.start is not None:
            raise ValueError("'start' goes with 'segments', not 'points'")
        sizes = {len(point) for _label, point in self.labelled_points()}
        if len(sizes) > 1:
            raise ValueError("give every point as [x, y], or every one as [x, y, z]")
        if sizes != {3}:
            # Model points are checked once they are on the sketch plane.
            self.resolve()  # refuses a degenerate outline before anything is drawn
        return self

    def dimension(self) -> int:
        """How many numbers each point has: 2 (sketch) or 3 (model)."""
        return len(self.labelled_points()[0][1])

    def labelled_points(self) -> list[tuple[str, list[float]]]:
        """Every point of the outline with where it is, in input order."""
        found: list[tuple[str, list[float]]] = []
        if self.points is not None:
            found += [(f"points[{i}]", p) for i, p in enumerate(self.points)]
        if self.start is not None:
            found.append(("start", self.start))
        for i, step in enumerate(self.segments or []):
            found.append((f"segments[{i}].to", step.to))
            if step.center is not None:
                found.append((f"segments[{i}].center", step.center))
        return found

    def on_sketch(self, flat: list[tuple[float, float]]) -> "Outline":
        """This outline with its points replaced, in :meth:`labelled_points` order.

        Raises:
            ValidationError: The outline isn't drawable in the sketch.
        """
        data = self.model_dump()
        points = iter([list(p) for p in flat])
        if data["points"] is not None:
            data["points"] = [next(points) for _ in data["points"]]
        if data["start"] is not None:
            data["start"] = next(points)
        for step in data["segments"] or []:
            step["to"] = next(points)
            if step["center"] is not None:
                step["center"] = next(points)
        return Outline.model_validate(data)

    def resolve(self) -> list[dict[str, Any]]:
        """The outline as the segments the adapter draws.

        Each segment starts at exactly the previous one's end, and a closed
        outline ends at exactly its start, so SolidWorks sees one connected
        loop. A closed outline whose last point is (within 0.001 mm) its
        first ends there; otherwise a closing line is added.

        Returns:
            list[dict[str, Any]]: ``{"type", "start", "end"}`` per segment,
            plus ``center`` and ``direction`` for an arc.

        Raises:
            ValueError: For a zero-length segment, an arc whose end isn't on
                its circle, or too few points.
        """
        if self.points is not None:
            points = [list(map(float, p)) for p in self.points]
            if (
                self.closed
                and len(points) > 1
                and _distance(points[0], points[-1]) < _SAME_POINT_MM
            ):
                points.pop()
            needed = 3 if self.closed else 2
            if len(points) < needed:
                raise ValueError(
                    f"a {'closed' if self.closed else 'open'} outline needs at "
                    f"least {needed} distinct points"
                )
            steps: list[OutlineSegment] = [OutlineSegment(to=p) for p in points[1:]]
            start = points[0]
        else:
            steps = list(self.segments or [])
            start = [float(v) for v in (self.start or [])]
            if not steps:
                raise ValueError("'segments' is empty")

        resolved: list[dict[str, Any]] = []
        here = start
        for index, step in enumerate(steps):
            end = [float(v) for v in step.to]
            last = index == len(steps) - 1
            if self.closed and last and _distance(end, start) < _SAME_POINT_MM:
                end = start
            if _distance(here, end) < _SAME_POINT_MM:
                raise ValueError(
                    f"segment {index} ends where it starts, at {end}"
                    + (
                        "; draw a full circle with add_circle"
                        if step.type == "arc"
                        else ""
                    )
                )
            segment: dict[str, Any] = {"type": step.type, "start": here, "end": end}
            if step.type == "arc":
                center = [float(v) for v in step.center or []]
                radius = _distance(center, here)
                if radius < _SAME_POINT_MM:
                    raise ValueError(f"segment {index}: the arc's centre is its start")
                miss = abs(_distance(center, end) - radius)
                if miss > _ARC_RADIUS_TOLERANCE_MM:
                    raise ValueError(
                        f"segment {index}: the arc's end is {miss:.4g} mm off the "
                        f"circle of radius {radius:.6g} mm through its start; "
                        "put 'to' on that circle"
                    )
                segment["center"] = center
                segment["direction"] = step.direction
            resolved.append(segment)
            here = end
        if self.closed and _distance(here, start) >= _SAME_POINT_MM:
            resolved.append({"type": "line", "start": here, "end": start})
        return resolved


class EditSketchInput(CompatInput):
    """Input schema for opening an existing sketch.

    Attributes:
        name (str): The sketch's tree name.
    """

    name: str = Field(description="The sketch to open, e.g. 'Sketch3'")

    def model_post_init(self, __context: Any) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name is required")


class GetSketchInput(CompatInput):
    """Input schema for reading a sketch without opening it.

    Attributes:
        name (str): The sketch's tree name.
        detail (str): ``full`` or ``summary``.
    """

    name: str = Field(description="The sketch to read, e.g. 'Sketch3'")
    detail: Literal["full", "summary"] = Field(
        default="full",
        description=(
            "'full' (the default): every segment with its points. 'summary': "
            "counts by type, circles as centre and radius, the dimensions "
            "(name, value), the relations and whether it is fully defined; "
            "fewer reads per segment, for large sketches"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("name is required")


class PlaneAxesInput(CompatInput):
    """Input schema for reading a plane's sketch axes.

    Attributes:
        plane (str): The plane, by any name ``create_sketch`` takes.
    """

    plane: str = Field(
        description=(
            "The plane: 'Front' ('XY'), 'Top' ('XZ'), 'Right' ('YZ'), or a "
            "plane's name, e.g. 'Plane3'"
        )
    )

    def model_post_init(self, __context: Any) -> None:
        self.plane = self.plane.strip()
        if not self.plane:
            raise ValueError("plane is required")


class DeleteSketchEntitiesInput(CompatInput):
    """Input schema for deleting entities of the open sketch.

    Attributes:
        ids (list[str] | None): Entity ids from ``edit_sketch`` or the ``add_*``
            tools.
        all (bool): Delete every entity of the open sketch instead.
    """

    ids: list[str] | None = Field(
        default=None,
        min_length=1,
        description=(
            "Ids of the entities to delete, from the latest edit_sketch reply or "
            "the add_* tools since (ids change each time a sketch is opened)"
        ),
    )
    all: bool = Field(
        default=False,
        description=(
            "true deletes every entity of the open sketch, ids or not; give it "
            "instead of ids"
        ),
    )

    @model_validator(mode="after")
    def _ids_or_all(self) -> Self:
        if self.all and self.ids:
            raise ValueError("give ids or all: true, not both")
        if not self.all and not self.ids:
            raise ValueError("give the ids to delete, or all: true")
        return self


class AddOutlinesInput(CompatInput):
    """Input schema for drawing whole outlines in one call.

    Attributes:
        outlines (list[Outline]): The outlines to draw.
    """

    outlines: list[Outline] = Field(
        min_length=1,
        description=(
            "One or more outlines, each a closed loop by default. Give each "
            "either 'points' (a polygon) or 'start' plus 'segments' (lines and "
            "arcs)"
        ),
    )
    coordinates: Coordinates = Field(
        default="sketch",
        description=(
            _COORDINATES + "; every point is then [x, y, z], an arc's centre "
            "too, and an arc's direction is as seen looking against the normal"
        ),
    )

    @model_validator(mode="after")
    def _points_match_coordinates(self) -> Self:
        wanted = 3 if self.coordinates == "model" else 2
        for index, outline in enumerate(self.outlines):
            if outline.dimension() != wanted:
                raise ValueError(
                    f"outlines[{index}]: with coordinates '{self.coordinates}' "
                    f"every point is {'[x, y, z]' if wanted == 3 else '[x, y]'}"
                )
        return self


class AddSketchTextInput(CompatInput):
    """Input schema for writing text in the open sketch.

    Attributes:
        text (str): The text.
        x (float): Where it starts, sketch x in mm.
        y (float): Where it starts, sketch y in mm.
        height (float | None): Character height in mm.
        font (str | None): Font name.
        angle (float): Degrees counter-clockwise from sketch x.
        bold (bool): Bold characters.
        italic (bool): Italic characters.
    """

    text: str = Field(description="The text to write, e.g. 'MAX' or 'DIPPER v2'")
    x: float = Field(
        default=0.0, description="Where the text starts (its left end), sketch x in mm"
    )
    y: float = Field(
        default=0.0, description="Where the text starts (its baseline), sketch y in mm"
    )
    height: float | None = Field(
        default=None,
        gt=0,
        description="Character height in mm; omit for the document's text height",
    )
    font: str | None = Field(
        default=None,
        description=(
            "Font name, e.g. 'Arial'; omit for the document's. A plain sans-serif "
            "font extrudes most reliably"
        ),
    )
    angle: float = Field(
        default=0.0,
        ge=-360,
        le=360,
        description=(
            "Degrees counter-clockwise from sketch x: 90 reads upwards. The text "
            "follows a construction line drawn at that angle"
        ),
    )
    bold: bool = Field(default=False, description="Bold characters")
    italic: bool = Field(default=False, description="Italic characters")

    def model_post_init(self, __context: Any) -> None:
        """Refuse empty text.

        Raises:
            ValueError: The text is blank.
        """
        if not self.text.strip():
            raise ValueError("text is required")


def text_warnings(asked: AddSketchTextInput, data: dict[str, Any]) -> list[str]:
    """How the text SolidWorks holds differs from what was asked.

    Args:
        asked: The text's input.
        data: The adapter's reply for it.

    Returns:
        list[str]: One sentence per difference; empty when it is as asked.
    """
    warnings = []
    others = data.get("shares_sketch_with") or []
    if others:
        warnings.append(
            f"The sketch also holds {', '.join(map(str, others))}: a sketch with "
            "both a profile and text won't extrude or cut. Put the text in a "
            "sketch of its own."
        )
    if data.get("format_applied") is False:
        warnings.append(
            "SolidWorks did not take the font settings; the text keeps the "
            "document's font and height."
        )
    height = data.get("height_mm")
    if (
        asked.height is not None
        and isinstance(height, int | float)
        and abs(float(height) - asked.height) > 1e-3
    ):
        warnings.append(
            f"The text is {float(height):g} mm high, not the {asked.height:g} mm asked."
        )
    font = data.get("font")
    if asked.font and isinstance(font, str) and font.lower() != asked.font.lower():
        warnings.append(
            f"SolidWorks used the font '{font}', not '{asked.font}' (is it installed?)."
        )
    return warnings


class AddSplineInput(BaseModel):
    """Input schema for adding a spline to sketch.

    Attributes:
        points (list[dict[str, float]]): The points value.
    """

    points: list[dict[str, float]] = Field(
        description="List of spline control points with x, y coordinates"
    )


class AddDimensionInput(BaseModel):
    """Input schema for adding a dimension to sketch.

    Attributes:
        dimension_type (str): The dimension type value.
        entity1 (str): The entity1 value.
        entity2 (str | None): The entity2 value.
        value (float): The value value.
    """

    entity1: str = Field(
        description=(
            "First entity: an id from the add_* tools or edit_sketch, 'Origin', "
            "or a point of an entity: '<id>.center', '<id>.start', '<id>.end'"
        )
    )
    entity2: str | None = Field(
        default=None,
        description=(
            "Second entity, named the same way, for a distance or an angle; a "
            "circle stands for its centre in a distance"
        ),
    )
    dimension_type: Literal[
        "linear", "horizontal", "vertical", "angular", "radial", "diameter"
    ] = Field(
        default="linear",
        description=(
            "'linear': a line's length, or the shortest distance between two "
            "points, circles (centres) or lines; 'horizontal' / 'vertical': the "
            "distance along sketch x / y (or a line's run / rise); 'angular': "
            "two connected lines; 'radial' / 'diameter': an arc or circle"
        ),
    )
    value: float = Field(description="Dimension value in mm, or degrees for angular")


class AddRelationInput(BaseModel):
    """Input schema for adding a geometric relation.

    Attributes:
        entity1 (str): The entity1 value.
        entity2 (str | None): The entity2 value.
        entity3 (str | None): Third entity ID — only used by the ``symmetric``
            relation (the centerline of symmetry). All other relation types
            reject a non-null ``entity3``.
        relation_type (str): The relation type value.
    """

    entity1: str = Field(
        description=(
            "First entity: an id from the add_* tools or edit_sketch, 'Origin', "
            "or a point of an entity: '<id>.center', '<id>.start', '<id>.end'"
        )
    )
    entity2: str | None = Field(
        default=None,
        description="Second entity, named the same way, when the relation takes two",
    )
    entity3: str | None = Field(
        default=None,
        description=(
            "Third entity ID. Required for 'symmetric' (the centerline of "
            "symmetry); must be null for all other relations."
        ),
    )
    relation_type: str = Field(
        description="Relation type (parallel, perpendicular, tangent, coincident, etc.)"
    )


class TutorialSimpleHoleInput(CompatInput):
    """Input schema for creating a simple hole (tutorial example).

    Attributes:
        center_x (float): The center x value.
        center_y (float): The center y value.
        depth (float | None): The depth value.
        diameter (float): The diameter value.
        plane (str): The plane value.
    """

    plane: str = Field(description="Sketch plane for the hole")
    center_x: float = Field(description="Hole center X coordinate in mm")
    center_y: float = Field(description="Hole center Y coordinate in mm")
    diameter: float = Field(description="Hole diameter in mm")
    depth: float | None = Field(
        default=None, description="Hole depth in mm (None for through hole)"
    )

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the tutorial simple hole input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.

        Raises:
            ValueError: Depth must be positive.
        """
        if self.diameter <= 0:
            raise ValueError("diameter must be positive")
        if self.depth is not None and self.depth <= 0:
            raise ValueError("depth must be positive")


class CheckSketchDefinitionInput(CompatInput):
    """Input schema for checking sketch definition status.

    Attributes:
        sketch_name (str | None): Optional sketch name to inspect.
    """

    sketch_name: str | None = Field(
        default=None,
        description="Optional sketch name. If omitted, checks the active or last sketch.",
    )


class SketchLinearPatternInput(CompatInput):
    """Input schema for a linear sketch pattern.

    Attributes:
        entities (list[str]): Seed entity ids.
        direction_x (float): Direction x component.
        direction_y (float): Direction y component.
        spacing (float): Distance between instances, mm.
        count (int): Instances, the seed included.
        add_dimensions (bool): Add the spacing and count dimensions.
    """

    entities: list[str] = Field(
        min_length=1, description="Ids of the entities to repeat (from the add_* tools)"
    )
    direction_x: float = Field(default=1.0, description="Direction x component")
    direction_y: float = Field(default=0.0, description="Direction y component")
    spacing: float = Field(
        default=10.0, gt=0, description="Spacing between instances, mm"
    )
    count: int = Field(default=3, ge=2, description="Instances, the seed included")
    add_dimensions: bool = Field(
        default=True,
        description=(
            "Add the pattern's spacing and count dimensions, so set_dimension can "
            "change it later. False makes plain copies."
        ),
    )


class SketchCircularPatternInput(CompatInput):
    """Input schema for a circular sketch pattern.

    Attributes:
        entities (list[str]): Seed entity ids.
        count (int): Instances, the seed included.
        angle (float): Degrees spread over.
        center_x (float): Centre x, sketch mm.
        center_y (float): Centre y, sketch mm.
        add_dimensions (bool): Add the radius, angle and count dimensions.
    """

    entities: list[str] = Field(
        min_length=1,
        description=(
            "Ids of the entities to repeat; the first must be a circle, arc, "
            "ellipse, polygon or rectangle"
        ),
    )
    count: int = Field(default=6, ge=2, description="Instances, the seed included")
    angle: float = Field(
        default=360.0, gt=0, le=360, description="Degrees the instances spread over"
    )
    center_x: float = Field(
        default=0.0, description="Pattern centre x in sketch mm (default the origin)"
    )
    center_y: float = Field(
        default=0.0, description="Pattern centre y in sketch mm (default the origin)"
    )
    add_dimensions: bool = Field(
        default=True,
        description=(
            "Add the pattern's radius, angle and count dimensions, so set_dimension "
            "can change it later. False makes plain copies."
        ),
    )


def _pattern_metadata(result: Any) -> dict[str, Any]:
    """A sketch pattern result's ``metadata`` (dimensions, centre), or ``{}``."""
    metadata = getattr(result, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


async def register_sketching_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register sketching tools with FastMCP.

    Registers comprehensive sketching tools for SolidWorks automation including geometry
    creation, constraints, dimensions, and pattern operations.

    Args:
        mcp (FastMCP): The mcp value.
        adapter (SolidWorksAdapter): Adapter instance used for the operation.
        config (dict[str, Any]): Configuration values for the operation.

    Returns:
        int: The computed numeric result.

    Example:
                        ```python
                        from solidworks_mcp.tools.sketching import register_sketching_tools

                        tool_count = await register_sketching_tools(mcp, adapter, config)
                        print(f"Registered {tool_count} sketching tools")
                        ```
    """
    mcp = with_tool_boundary(mcp, adapter)
    tool_count = 0
    # SolidWorks won't rename a sketch while it is open for editing (it
    # accepts the name, but the tree keeps the old one), so the name
    # create_sketch was asked for is given when exit_sketch closes it:
    # SolidWorks' name -> the name asked for.
    names_on_exit: dict[str, str] = {}

    async def _on_sketch(
        points: list[list[float]], labels: list[str]
    ) -> tuple[list[tuple[float, float]], dict[str, Any]]:
        """Model points as the open sketch's (x, y), for coordinates 'model'.

        Returns:
            tuple: The sketch points (mm) and the reply's ``projection``.

        Raises:
            UnsupportedOperationError: The adapter can't read the sketch's axes.
            InvalidInputError: A point is off the sketch plane.
            ToolOperationError: The axes couldn't be read.
        """
        require_capability(
            adapter,
            "get_open_sketch_axes",
            "The active adapter can't read the open sketch's axes, so model "
            "coordinates can't be used; give sketch coordinates. Nothing was drawn.",
        )
        read = await adapter.get_open_sketch_axes()
        require_success(read, context="Failed to read the open sketch's axes")
        frame = Frame.from_mapping(read.data)
        if frame is None:
            raise ToolOperationError(
                "The open sketch's axes didn't read as a plane; nothing was drawn."
            )
        try:
            flat, off = project(frame, points, labels)
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc
        return flat, {
            "coordinates": "model",
            "axes": (read.data or {}).get("summary"),
            "max_off_plane_mm": round(off, 6),
        }

    async def _line_ends(
        input_data: AddLineInput,
    ) -> tuple[float, float, float, float, dict[str, Any] | None]:
        """A line's ends in sketch mm, projected for coordinates 'model'."""
        if input_data.coordinates != "model":
            return (
                input_data.x1 or 0.0,
                input_data.y1 or 0.0,
                input_data.x2 or 0.0,
                input_data.y2 or 0.0,
                None,
            )
        (a, b), projection = await _on_sketch(
            input_data.model_points(), ["start", "end"]
        )
        return a[0], a[1], b[0], b[1], projection

    async def _make_construction(entity_id: Any, construction: bool, what: str) -> None:
        """Turn a just-drawn entity into construction geometry when asked.

        The ``Create*`` calls have no construction flag, so it is set on the
        entity afterwards.

        Raises:
            ToolOperationError: The entity was drawn as normal geometry and
                could not be changed.
        """
        if not construction:
            return
        result = await adapter.set_sketch_construction([str(entity_id)], True)
        require_success(
            result,
            context=(
                f"The {what} {entity_id} was drawn as normal geometry, not construction"
            ),
        )

    @mcp.tool()
    async def create_sketch(input_data: CreateSketchInput) -> dict[str, Any]:
        """Open a new sketch on a plane of the active part, ready for lines, circles and other sketch entities.

        Use it first in every feature: sketch, draw, `exit_sketch`, then
        `create_extrusion` or `create_cut_extrude`. Planes: Front (XY), Top (XZ), Right
        (YZ), or a plane made by `create_reference_plane`; a plane not in the part is an
        error. Coordinates in the sketch are mm from its origin. Needs: an open part.
        Changes: adds a sketch and opens it for editing; give `sketch_name` and
        `exit_sketch` names it that when it closes it (SolidWorks won't rename an open
        sketch). Returns: `sketch` with `name` (the name in the feature tree now),
        `plane` (the plane used) and `axes`: where sketch x and y point in the model
        (`x`, `y`, `normal`, `origin_mm`, `summary`; on Top, sketch y is model -Z), plus
        `name_on_exit` when you gave `sketch_name`.

        Args:
            input_data: The sketch. `plane` is required; `sketch_name` names it.

        Returns:
            dict[str, Any]: The new sketch.
        """
        input_data = normalize_input(input_data, CreateSketchInput)
        result = await adapter.create_sketch(input_data.plane)

        require_success(result, context="Failed to create sketch")
        sketch_name = (
            result.data.get("sketch_name")
            if isinstance(result.data, dict)
            else result.data
        )
        metadata = getattr(result, "metadata", None)
        found = metadata if isinstance(metadata, dict) else {}
        plane_used = found.get("plane") or input_data.plane
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Created sketch '{sketch_name}' on {plane_used}",
            "sketch": {
                "name": sketch_name,
                "plane": plane_used,
            },
            "execution_time": result.execution_time,
        }
        axes = found.get("axes")
        if isinstance(axes, dict):
            reply["sketch"]["axes"] = axes
            reply["message"] += f" ({axes['summary']})"
        names_on_exit.clear()  # a sketch left open is abandoned with its name
        wanted = (input_data.sketch_name or "").strip()
        if wanted and wanted != sketch_name:
            names_on_exit[str(sketch_name)] = wanted
            reply["sketch"]["name_on_exit"] = wanted
            reply["message"] += f"; exit_sketch names it '{wanted}'"
        return reply

    @mcp.tool()
    async def add_line(input_data: AddLineInput) -> dict[str, Any]:
        """Draw a straight line between two points in the open sketch, in mm.

        Use it for profiles made of straight edges; for a closed box, use
        `add_rectangle`, and for a revolve axis, `add_centerline`. With `coordinates:
        "model"` the ends are model points on the sketch plane. Needs: an open sketch
        (`create_sketch`). Changes: adds a line (a construction line with
        `construction`). Returns: `line` with `id`, `start`, `end` (sketch mm) and
        `length`, and `projection` for model points. Its `id` is what
        `add_sketch_constraint` and `add_sketch_dimension` take.

        Args:
            input_data: The line. Give `x1`, `y1`, `x2`, `y2` (mm; or `start_x`,
                `start_y`, `end_x`, `end_y`), and `z1`, `z2` with `coordinates`
                "model"; `construction` is off.

        Returns:
            dict[str, Any]: The line added.
        """
        input_data = normalize_input(input_data, AddLineInput)
        x1, y1, x2, y2, projection = await _line_ends(input_data)
        result = await adapter.add_line(x1, y1, x2, y2)

        require_success(result, context="Failed to add line")
        line_id = (
            result.data.get("entity_id") or result.data.get("id")
            if isinstance(result.data, dict)
            else result.data
        )
        await _make_construction(line_id, input_data.construction, "line")
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Added line from ({x1}, {y1}) to ({x2}, {y2})",
            "line": {
                "id": line_id,
                "start": {"x": x1, "y": y1},
                "end": {"x": x2, "y": y2},
                "construction": input_data.construction,
                "length": ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5,
            },
            "execution_time": result.execution_time,
        }
        if projection is not None:
            reply["projection"] = projection
        return reply

    @mcp.tool()
    async def add_circle(input_data: AddCircleInput) -> dict[str, Any]:
        """Draw a circle in the open sketch from its centre and radius, in mm.

        Use it for holes and round bosses. With `coordinates: "model"` the centre is a
        model point (`center_z` too) on the sketch plane, so no axes need working out.
        Needs: an open sketch (`create_sketch`). Changes: adds a circle (a
        construction circle with `construction`). Returns: `circle` with `id`,
        `center` (sketch mm), `radius` and `diameter`, and `projection` for a model
        point. Its `id` is what `add_sketch_constraint` and `add_sketch_dimension` take.

        Args:
            input_data: The circle. `center_x`, `center_y` and `radius` (mm) are
                required, and `center_z` with `coordinates` "model";
                `construction` is off.

        Returns:
            dict[str, Any]: The circle added.
        """
        input_data = normalize_input(input_data, AddCircleInput)
        cx, cy, projection = input_data.center_x, input_data.center_y, None
        if input_data.coordinates == "model":
            ((cx, cy),), projection = await _on_sketch(
                [[cx, cy, input_data.center_z or 0.0]], ["center"]
            )
        result = await adapter.add_circle(cx, cy, input_data.radius)

        require_success(result, context="Failed to add circle")
        circle_id = (
            result.data.get("entity_id")
            if isinstance(result.data, dict)
            else result.data
        )
        await _make_construction(circle_id, input_data.construction, "circle")
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Added circle at ({cx}, {cy}) with radius {input_data.radius}mm",
            "circle": {
                "id": circle_id,
                "center": {"x": cx, "y": cy},
                "radius": input_data.radius,
                "construction": input_data.construction,
                "diameter": input_data.radius * 2,
            },
            "execution_time": result.execution_time,
        }
        if projection is not None:
            reply["projection"] = projection
        return reply

    @mcp.tool()
    async def add_rectangle(input_data: AddRectangleInput) -> dict[str, Any]:
        """Draw a closed, axis-aligned rectangle in the open sketch from two opposite corners, in mm.

        Use it for blocks, plates and rectangular cut-outs. With `coordinates:
        "model"` the corners are model points on the sketch plane; the sides still run
        along sketch x and y. Needs: an open sketch (`create_sketch`). Changes: adds
        four connected lines. Returns: `rectangle` with `id`, `corner1`, `corner2`
        (sketch mm), `width`, `height` and `area`, and `projection` for model points.
        Its `id` is what `add_sketch_constraint` and `add_sketch_dimension` take.

        Args:
            input_data: The rectangle. Give `x1`, `y1`, `x2`, `y2` (mm; or
                `corner1_x` ... `corner2_y`), and `z1`, `z2` with `coordinates`
                "model"; `construction` is off.

        Returns:
            dict[str, Any]: The rectangle added.
        """
        input_data = normalize_input(input_data, AddRectangleInput)
        rx1, ry1 = input_data.x1 or 0.0, input_data.y1 or 0.0
        rx2, ry2 = input_data.x2 or 0.0, input_data.y2 or 0.0
        projection = None
        if input_data.coordinates == "model":
            ((rx1, ry1), (rx2, ry2)), projection = await _on_sketch(
                [[rx1, ry1, input_data.z1 or 0.0], [rx2, ry2, input_data.z2 or 0.0]],
                ["corner 1", "corner 2"],
            )
            if abs(rx1 - rx2) < 1e-9 or abs(ry1 - ry2) < 1e-9:
                raise InvalidInputError(
                    f"The corners land at ({rx1:g}, {ry1:g}) and ({rx2:g}, {ry2:g}) "
                    "in the sketch: a side along sketch x or y has no length"
                )
        result = await adapter.add_rectangle(rx1, ry1, rx2, ry2)

        require_success(result, context="Failed to add rectangle")
        rect_id = (
            result.data.get("entity_id")
            if isinstance(result.data, dict)
            else result.data
        )
        await _make_construction(rect_id, input_data.construction, "rectangle")
        width = abs(rx2 - rx1)
        height = abs(ry2 - ry1)

        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Added rectangle from ({rx1}, {ry1}) to ({rx2}, {ry2})",
            "rectangle": {
                "id": rect_id,
                "corner1": {"x": rx1, "y": ry1},
                "corner2": {"x": rx2, "y": ry2},
                "construction": input_data.construction,
                "width": width,
                "height": height,
                "area": width * height,
            },
            "execution_time": result.execution_time,
        }
        if projection is not None:
            reply["projection"] = projection
        return reply

    @mcp.tool()
    async def exit_sketch() -> dict[str, Any]:
        """Close the open sketch so a feature can use it.

        Call it after drawing and before `create_extrusion`, `create_revolve` or
        `create_cut_extrude`; those use the sketch closed last. Needs: an open sketch.
        Changes: leaves sketch editing; the sketch stays in the feature tree, renamed
        to the `sketch_name` `create_sketch` was given. An empty sketch does not stay:
        SolidWorks deletes it, and the reply says `discarded` with a `warning`
        (`create_sketch`'s reply already gave the plane's `axes`). After an
        `edit_sketch` it rebuilds the part and gives `solid_body_count_before` and
        `solid_body_count_after`, with a `warning` when the edit split a body off.
        Returns: a message, plus, after a `sketch_name`, `sketch` with `name` (the name
        in the feature tree, which later tools take as `sketch_name`) and a `warning`
        when the name couldn't be given (another feature has it). After an
        `edit_sketch` that changed nothing it rebuilds nothing and says
        `sketch_unchanged`.
        """
        result = await adapter.exit_sketch()

        require_success(result, context="Failed to exit sketch")
        reply: dict[str, Any] = {
            "status": "success",
            "message": "Exited sketch editing mode",
            "execution_time": result.execution_time,
        }
        metadata = getattr(result, "metadata", None)
        round_trip = metadata if isinstance(metadata, dict) else {}
        discarded = round_trip.get("discarded")
        if discarded:
            names_on_exit.clear()  # nothing is left to name
            reply["message"] += f"; SolidWorks discarded the empty sketch '{discarded}'"
            reply["discarded"] = True
            reply["sketch"] = {"name": discarded, "discarded": True}
            reply["warning"] = (
                f"SolidWorks deleted the sketch '{discarded}' because it had "
                "nothing in it, so no later tool can find it by that name. Draw "
                "before exit_sketch to keep a sketch. To read a plane's axes, "
                "there is no need for an empty sketch: create_sketch's reply "
                "already gave them (`sketch.axes`)."
            )
            return reply
        counts = {
            key: round_trip[key]
            for key in (
                "solid_body_count_before",
                "solid_body_count_after",
                "solid_bodies_after",
            )
            if key in round_trip
        }
        reply.update(counts)
        split = body_count_warning(counts, "Rebuilding after the sketch edit")
        if split:
            reply["warning"] = split
        if round_trip.get("unchanged"):
            # An edit_sketch only read: left without a rebuild.
            reply["sketch_unchanged"] = True
            if round_trip.get("is_dirty"):
                reply["warning"] = (
                    "Nothing in the sketch changed, but SolidWorks marked the part "
                    "modified when the sketch was opened; the API can't clear that "
                    "mark. The geometry is as it was."
                )
        if names_on_exit:
            current, wanted = names_on_exit.popitem()
            renamed = await adapter.rename_feature(current, wanted)
            name = current
            if renamed.is_success:
                name = (renamed.data or {}).get("new_name") or wanted
            else:
                kept = (
                    f"The sketch keeps SolidWorks' name '{current}': it could "
                    f"not be named '{wanted}' ({renamed.error})."
                )
                reply["warning"] = " ".join(
                    w for w in (reply.get("warning"), kept) if w
                )
            reply["sketch"] = {"name": name}
        return reply

    @mcp.tool()
    async def check_sketch_fully_defined(
        input_data: CheckSketchDefinitionInput | None = None,
    ) -> dict[str, Any]:
        """Check whether a sketch is fully defined, under-defined or over-defined.

        Use it after adding relations and dimensions, to see if the sketch still has
        free geometry; `get_sketch` with detail 'summary' lists the relations and
        dimensions it has. Needs: an open part with the sketch. Read-only. Returns:
        `sketch` with `sketch_name`, `is_fully_defined` (null when SolidWorks can't
        tell), `definition_state` (fully_defined, under_defined, over_defined,
        no_solution, ...), `over_defining_relations`, `dangling_relations` and
        `source`. SolidWorks doesn't say which entities are still free.

        Args:
            input_data: Nothing is required: give `sketch_name`, or omit it for the
                open or last sketch.

        Returns:
            dict[str, Any]: The definition state.
        """
        normalized = normalize_input(
            input_data or {},
            CheckSketchDefinitionInput,
        )
        result = await adapter.check_sketch_fully_defined(normalized.sketch_name)

        require_success(result, context="Failed to inspect sketch definition")

        payload = result.data or {}
        state = payload.get("definition_state", "unknown")
        return {
            "status": "success",
            "message": f"Sketch definition state: {state}",
            "sketch": payload,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def add_arc(input_data: AddArcInput) -> dict[str, Any]:
        """Draw a circular arc in the open sketch from its centre, start point and end point, in mm.

        Use it for rounded ends and curved edges of a profile; for a whole outline in
        one call, use `add_outlines`. The arc runs counter-clockwise from start to end
        unless `direction` is "cw". Needs: an open sketch (`create_sketch`). Changes:
        adds an arc. Returns: `arc` with `id`, `center`, `start_point`, `end_point`,
        `direction`, `radius` and `sweep_deg`. Its `id` is what `add_sketch_constraint`
        and `add_sketch_dimension` take.

        Args:
            input_data: The arc. `center_x`, `center_y`, `start_x`, `start_y`,
                `end_x` and `end_y` (mm) are all required, and the `_z` ones with
                `coordinates` "model"; `direction` is "ccw".

        Returns:
            dict[str, Any]: The arc added.
        """
        input_data = normalize_input(input_data, AddArcInput)
        center = [input_data.center_x, input_data.center_y]
        start = [input_data.start_x, input_data.start_y]
        end = [input_data.end_x, input_data.end_y]
        projection = None
        if input_data.coordinates == "model":
            flat, projection = await _on_sketch(
                [
                    [*center, input_data.center_z or 0.0],
                    [*start, input_data.start_z or 0.0],
                    [*end, input_data.end_z or 0.0],
                ],
                ["center", "start", "end"],
            )
            center, start, end = (list(p) for p in flat)
        result = await adapter.add_arc(
            center[0],
            center[1],
            start[0],
            start[1],
            end[0],
            end[1],
            input_data.direction,
        )

        require_success(result, context="Failed to add arc")
        arc_id = result.data
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"Added {input_data.direction} arc centered at "
                f"({center[0]}, {center[1]})"
            ),
            "arc": {
                "id": arc_id,
                "center": {"x": center[0], "y": center[1]},
                "start_point": {"x": start[0], "y": start[1]},
                "end_point": {"x": end[0], "y": end[1]},
                "direction": input_data.direction,
                "radius": round(_distance(center, start), 6),
                "sweep_deg": round(
                    arc_sweep_deg(center, start, end, input_data.direction), 6
                ),
            },
            "execution_time": result.execution_time,
        }
        if projection is not None:
            reply["projection"] = projection
        return reply

    @mcp.tool()
    async def add_outlines(input_data: AddOutlinesInput) -> dict[str, Any]:
        """Draw whole outlines of lines and arcs in the open sketch in one call, in mm.

        Use it instead of many `add_line` and `add_arc` calls: a honeycomb of 25
        hexagons is one call with 25 `points` lists. Each outline is either `points`
        (a polygon) or `start` plus `segments`, each a line or an arc (`center`,
        `direction`) to `to`. Outlines close by default (`closed`), each segment
        starting exactly where the last ended, so the loop is connected. With
        `coordinates: "model"` every point is [x, y, z] in model mm on the sketch
        plane. Needs: an open sketch (`create_sketch`). Changes: adds the segments;
        if SolidWorks refuses one, the call removes what it drew and says which
        failed. Returns: `outlines` with each one's segment `ids`, plus
        `segment_count`, `line_count`, `arc_count`, and `projection` for model points.

        Args:
            input_data: The outlines. `outlines` is required: each needs `points`,
                or `start` and `segments`; `closed` is on, `construction` off,
                `coordinates` "sketch".

        Returns:
            dict[str, Any]: The segments drawn.
        """
        input_data = normalize_input(input_data, AddOutlinesInput)
        sources, projection = list(input_data.outlines), None
        if input_data.coordinates == "model":
            labelled = [
                (f"outlines[{i}].{label}", point)
                for i, outline in enumerate(sources)
                for label, point in outline.labelled_points()
            ]
            flat, projection = await _on_sketch(
                [point for _label, point in labelled],
                [label for label, _point in labelled],
            )
            projected = []
            for index, outline in enumerate(sources):
                count = len(outline.labelled_points())
                try:
                    projected.append(outline.on_sketch(flat[:count]))
                except ValidationError as exc:
                    raise InvalidInputError(
                        f"outlines[{index}] on the sketch plane: "
                        + describe_validation_error(exc)
                    ) from exc
                flat = flat[count:]
            sources = projected
        outlines = [
            {"segments": outline.resolve(), "construction": outline.construction}
            for outline in sources
        ]
        result = await adapter.add_sketch_outlines(outlines)
        require_success(result, context="Failed to draw outlines")
        data = result.data or {}
        drawn = data.get("outlines") or []
        replies = [
            {
                "index": index,
                "ids": reply.get("ids", []),
                "segment_count": len(reply.get("ids", [])),
                "closed": source.closed,
                "construction": source.construction,
            }
            for index, (reply, source) in enumerate(
                zip(drawn, input_data.outlines, strict=False)
            )
        ]
        count = data.get("segment_count", 0)
        reply: dict[str, Any] = {
            "status": "success",
            "message": (
                f"Drew {len(replies)} outline(s): {count} segments "
                f"({data.get('line_count', 0)} lines, {data.get('arc_count', 0)} arcs)"
            ),
            "outlines": replies,
            "segment_count": count,
            "line_count": data.get("line_count", 0),
            "arc_count": data.get("arc_count", 0),
            "execution_time": result.execution_time,
        }
        if projection is not None:
            reply["projection"] = projection
        return reply

    @mcp.tool()
    async def add_sketch_text(input_data: AddSketchTextInput) -> dict[str, Any]:
        """Write text in the open sketch, for a label to raise or cut into the part.

        Use it for part labels (MIN/MAX, a name, a version): `exit_sketch`, then
        `create_extrusion` raises the text and `create_cut_extrude` sinks it. Put
        the text in a sketch of its own: a sketch with both a profile and text
        won't extrude. For the whole label in one call, use `emboss_text`. The
        text starts at (`x`, `y`) in sketch mm and reads along sketch x, or at
        `angle` degrees along a construction line. Needs: an open sketch
        (`create_sketch`). Changes: adds the text (and that line, for an angle).
        Returns: `text` with `id` and what SolidWorks reads back: `text`,
        `position_mm`, `font`, `height_mm`, `bold`, `italic`, `angle_deg`; and a
        `warning` when the sketch holds other geometry or the font or height
        didn't take.

        Args:
            input_data: The text. `text` is required; `x`, `y` (mm), `height`
                (mm), `font`, `angle` (degrees), `bold` and `italic` have
                defaults (the document's font and height).

        Returns:
            dict[str, Any]: The text written.
        """
        input_data = normalize_input(input_data, AddSketchTextInput)
        result = await adapter.add_sketch_text(
            input_data.text,
            input_data.x,
            input_data.y,
            input_data.height,
            input_data.font,
            input_data.angle,
            input_data.bold,
            input_data.italic,
        )
        require_success(result, context="Failed to add sketch text")
        data = dict(result.data or {})
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Added text {input_data.text!r} at "
            f"({input_data.x:g}, {input_data.y:g}) mm",
            "text": data,
            "execution_time": result.execution_time,
        }
        warnings = text_warnings(input_data, data)
        if warnings:
            reply["warning"] = " ".join(warnings)
        return reply

    @mcp.tool()
    async def edit_sketch(input_data: EditSketchInput) -> dict[str, Any]:
        """Open an existing sketch for editing, listing its segments with ids.

        Use it to change a profile without deleting the feature built on it: open the
        sketch, `delete_sketch_entities` what goes, draw what's new with the `add_*`
        tools or `add_outlines`, then `exit_sketch`, which rebuilds the features that
        use it. Needs: an open part with the sketch. Changes: opens the sketch for
        editing. Returns: `sketch` with `name` and `segments` (each with `id`, `type`,
        and `start_mm`, `end_mm` or `center_mm` in sketch coordinates; a text has
        `text`, `height_mm`, `font` and `position_mm` instead).

        Args:
            input_data: The sketch. `name` is required.

        Returns:
            dict[str, Any]: The open sketch and its segments.
        """
        input_data = normalize_input(input_data, EditSketchInput)
        names_on_exit.clear()
        result = await adapter.edit_sketch(input_data.name)
        require_success(result, context=f"Failed to open {input_data.name}")
        data = result.data if isinstance(result.data, dict) else {}
        segments = data.get("segments") or []
        return {
            "status": "success",
            "message": f"Opened {input_data.name} for editing ({len(segments)} segments)",
            "sketch": {"name": input_data.name, "segments": segments},
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def get_sketch(input_data: GetSketchInput) -> dict[str, Any]:
        """Read a sketch's segments, plane and text without opening it for editing.

        Use it to see how an existing profile is drawn before changing it, to read
        its lettering, or with detail 'summary' to see what defines it. To change it,
        use `edit_sketch`. Needs: an open part with the sketch. Read-only. The part
        isn't marked changed. Returns: `sketch` with `name`, `kind`, `plane`,
        `references`, `axes`, `segments` (each `index`, `type`, `start_mm`, `end_mm`
        or `center_mm`, `construction`; a full circle is `circle` with `center_mm`
        and `radius_mm`; text has `text`, `font`, `height_mm`, `position_mm`) and
        `segment_counts`. A summary has `segment_counts`, `circles`, `dimensions`,
        `relations` (each `type` and `entities`: a `segment` index, which
        `edit_sketch` numbers from 1, or a point's `at_mm`), `relation_counts` and
        `definition_state`.

        Args:
            input_data: The sketch. `name` is required.

        Returns:
            dict[str, Any]: The sketch and its segments.
        """
        input_data = normalize_input(input_data, GetSketchInput)
        require_capability(
            adapter,
            "get_sketch",
            "The active adapter cannot read sketches; nothing was read.",
        )
        result = await adapter.get_sketch(input_data.name, input_data.detail)
        require_success(result, context=f"Failed to read {input_data.name}")
        data = result.data if isinstance(result.data, dict) else {}
        segments = data.get("segments") or []
        count = (
            sum((data.get("segment_counts") or {}).values())
            if input_data.detail == "summary"
            else len(segments)
        )
        plane = data.get("plane")
        state = data.get("definition_state")
        return {
            "status": "success",
            "message": (
                f"{input_data.name}: {count} segment(s)"
                + (f" on {plane}" if plane else "")
                + (f", {state}" if state else "")
            ),
            "sketch": {
                "name": input_data.name,
                **{k: v for k, v in data.items() if k != "sketch"},
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def plane_axes(input_data: PlaneAxesInput) -> dict[str, Any]:
        """Read where a sketch on a plane points: its x, y, normal and origin in the model.

        Use it before sketching on a plane made from a face or a point, whose sketch
        x and y can't be guessed, to work out sketch coordinates or a cut's side; to
        draw by model points instead, give the `add_*` tools `coordinates: "model"`.
        It reads the plane's own transform (IRefPlane.Transform): no sketch is made,
        so the part doesn't change. Needs: an open part with the plane. Read-only.
        Returns: `plane` (its tree name), `axes` (`x`, `y`, `normal`, `origin_mm` and
        `summary`, as `create_sketch` reports them) and `method`.

        Args:
            input_data: The plane. `plane` is required.

        Returns:
            dict[str, Any]: The plane's axes.
        """
        input_data = normalize_input(input_data, PlaneAxesInput)
        require_capability(
            adapter,
            "plane_axes",
            "The active adapter cannot read a plane's axes; nothing was read.",
        )
        result = await adapter.plane_axes(input_data.plane)
        require_success(
            result, context=f"Failed to read the axes of {input_data.plane}"
        )
        data = result.data if isinstance(result.data, dict) else {}
        axes = data.get("axes") or {}
        name = data.get("plane") or input_data.plane
        return {
            "status": "success",
            "message": f"{name}: {axes.get('summary', 'axes read')}",
            "plane": name,
            "axes": axes,
            "method": data.get("method"),
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def delete_sketch_entities(
        input_data: DeleteSketchEntitiesInput,
    ) -> dict[str, Any]:
        """Delete lines, arcs and other entities of the open sketch by id, or all of them.

        Use it after `edit_sketch` (whose reply lists the ids) or to remove something
        an `add_*` tool just drew. Ids change each time a sketch is opened, so take
        them from the latest `edit_sketch` reply; an unknown id's error lists the
        ones the sketch has. To redraw a profile from scratch, `all: true` deletes
        every entity, with an id or not; draw the new profile before `exit_sketch`,
        as SolidWorks deletes a sketch exited empty. Needs: an open sketch. Changes: removes the
        entities from the sketch. Returns: `deleted` and `remaining` (the ids still
        in the sketch); with `all`, also `deleted_count` and `skipped` (entities
        SolidWorks wouldn't select, left in the sketch).

        Args:
            input_data: The entities: `ids` (one or more), or `all: true`.

        Returns:
            dict[str, Any]: What was deleted.
        """
        input_data = normalize_input(input_data, DeleteSketchEntitiesInput)
        if input_data.all:
            result = await adapter.delete_all_sketch_entities()
        else:
            result = await adapter.delete_sketch_entities(list(input_data.ids or []))
        require_success(result, context="Failed to delete sketch entities")
        data = result.data if isinstance(result.data, dict) else {}
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Deleted {', '.join(input_data.ids or [])}",
            "deleted": data.get("deleted", list(input_data.ids or [])),
            "remaining": data.get("remaining", []),
        }
        if input_data.all:
            count = int(data.get("deleted_count", len(reply["deleted"])))
            skipped = list(data.get("skipped") or [])
            if skipped:
                reply["message"] = (
                    f"Deleted {count} of the open sketch's {count + len(skipped)} "
                    f"entities; SolidWorks could not select the other "
                    f"{len(skipped)} (see skipped)"
                )
            elif count:
                reply["message"] = f"Deleted every entity of the open sketch ({count})"
            else:
                reply["message"] = "The open sketch had no entities to delete"
            reply["deleted_count"] = count
            reply["skipped"] = skipped
        reply["execution_time"] = result.execution_time
        return reply

    @mcp.tool()
    async def add_spline(input_data: AddSplineInput) -> dict[str, Any]:
        """Draw a smooth spline through a list of points in the open sketch, in mm.

        Use it for free-form curves such as an aerofoil or a handle profile. Needs: an
        open sketch (`create_sketch`). Changes: adds a spline. Returns: `spline` with
        `id`, `control_points` and `point_count`.

        Args:
            input_data: The spline. `points` (a list of {"x", "y"} in mm) is
                required.

        Returns:
            dict[str, Any]: The spline added.
        """
        result = await adapter.add_spline(input_data.points)

        require_success(result, context="Failed to add spline")
        spline_id = result.data
        return {
            "status": "success",
            "message": f"Added spline with {len(input_data.points)} control points",
            "spline": {
                "id": spline_id,
                "control_points": input_data.points,
                "point_count": len(input_data.points),
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def add_centerline(input_data: AddLineInput) -> dict[str, Any]:
        """Draw a centreline (construction line) in the open sketch, in mm.

        Use it as the axis for `create_revolve`, or as the line for `sketch_mirror` and
        symmetric relations. It is never part of the profile. Needs: an open sketch
        (`create_sketch`). Changes: adds a centreline. Returns: `centerline` with `id`,
        `start_point` and `end_point` (sketch mm), and `projection` for model points.

        Args:
            input_data: The line. Give `x1`, `y1`, `x2`, `y2` (mm; or `start_x`,
                `start_y`, `end_x`, `end_y`), and `z1`, `z2` with `coordinates`
                "model"; `construction` is not used.

        Returns:
            dict[str, Any]: The centreline added.
        """
        input_data = normalize_input(input_data, AddLineInput)
        x1, y1, x2, y2, projection = await _line_ends(input_data)

        result = await adapter.add_centerline(x1, y1, x2, y2)

        require_success(result, context="Failed to add centerline")
        centerline_id = result.data
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Added centerline from ({x1}, {y1}) to ({x2}, {y2})",
            "centerline": {
                "id": centerline_id,
                "start_point": {"x": x1, "y": y1},
                "end_point": {"x": x2, "y": y2},
                "type": "construction",
            },
            "execution_time": result.execution_time,
        }
        if projection is not None:
            reply["projection"] = projection
        return reply

    @mcp.tool()
    async def add_polygon(input_data: dict[str, Any]) -> dict[str, Any]:
        """Draw a regular polygon (e.g. a hexagon) in the open sketch from its centre, radius and number of sides.

        Use it for nuts, hex bosses and pockets. Needs: an open sketch
        (`create_sketch`). Changes: adds the polygon's sides. Returns: `polygon` with
        `id`, `center`, `radius` and `sides`.

        Args:
            input_data: A mapping: `center_x`, `center_y` (mm, default 0), `radius`
                (mm, circumscribed, default 10) and `sides` (default 6).

        Returns:
            dict[str, Any]: The polygon added.
        """
        center_x = input_data.get("center_x", 0.0)
        center_y = input_data.get("center_y", 0.0)
        radius = input_data.get("radius", 10.0)
        sides = input_data.get("sides", 6)

        result = await adapter.add_polygon(center_x, center_y, radius, sides)

        require_success(result, context="Failed to add polygon")
        polygon_id = result.data
        return {
            "status": "success",
            "message": f"Added {sides}-sided polygon at ({center_x}, {center_y}) with radius {radius}mm",
            "polygon": {
                "id": polygon_id,
                "center": {"x": center_x, "y": center_y},
                "radius": radius,
                "sides": sides,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def add_ellipse(input_data: dict[str, Any]) -> dict[str, Any]:
        """Draw an ellipse in the open sketch from its centre and axis lengths, in mm.

        Use it for oval holes and bosses. Needs: an open sketch (`create_sketch`).
        Changes: adds an ellipse. Returns: `ellipse` with `id`, `center`, `major_axis`
        and `minor_axis`.

        Args:
            input_data: A mapping: `center_x`, `center_y` (mm, default 0),
                `major_axis` (default 20) and `minor_axis` (default 10), in mm.

        Returns:
            dict[str, Any]: The ellipse added.
        """
        center_x = input_data.get("center_x", 0.0)
        center_y = input_data.get("center_y", 0.0)
        major_axis = input_data.get("major_axis", 20.0)
        minor_axis = input_data.get("minor_axis", 10.0)

        result = await adapter.add_ellipse(center_x, center_y, major_axis, minor_axis)

        require_success(result, context="Failed to add ellipse")
        ellipse_id = result.data
        return {
            "status": "success",
            "message": f"Added ellipse at ({center_x}, {center_y}) with axes {major_axis}x{minor_axis}mm",
            "ellipse": {
                "id": ellipse_id,
                "center": {"x": center_x, "y": center_y},
                "major_axis": major_axis,
                "minor_axis": minor_axis,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def add_sketch_constraint(input_data: AddRelationInput) -> dict[str, Any]:
        """Add a geometric relation between sketch entities: horizontal, vertical, parallel, perpendicular, tangent, coincident, concentric, equal, symmetric, collinear or fix.

        Use it to hold design intent, then check with `check_sketch_fully_defined`.
        Entities are the `id`s the add_* tools or `edit_sketch` returned, 'Origin',
        or a point: '<id>.center', '<id>.start', '<id>.end'. A circle coincident
        with a line or point ties its centre. A relation that added nothing and
        moved nothing is an error. Needs: an open sketch. Changes: adds the
        relation. Returns: `constraint` with `id`, `type`, the entities as given,
        `entities_used` and `verified_by`.

        Args:
            input_data: The relation. `entity1` and `relation_type` are required;
                `entity2` when the relation needs two, and `entity3` (the
                centreline) only for symmetric.

        Returns:
            dict[str, Any]: The relation added.
        """
        input_data = normalize_input(input_data, AddRelationInput)
        result = await adapter.add_sketch_constraint(
            input_data.entity1,
            input_data.entity2,
            input_data.relation_type,
            input_data.entity3,
        )

        require_success(result, context="Failed to add constraint")
        added = result.data if isinstance(result.data, dict) else {"id": result.data}
        used = added.get("entities")
        return {
            "status": "success",
            "message": (
                f"Added {input_data.relation_type} constraint between "
                f"{input_data.entity1} and {input_data.entity2}"
                + (f" (tied {' and '.join(used)})" if used else "")
            ),
            "constraint": {
                "id": added.get("id"),
                "type": input_data.relation_type,
                "entity1": input_data.entity1,
                "entity2": input_data.entity2,
                "entity3": input_data.entity3,
                **({"entities_used": used} if used else {}),
                **(
                    {"verified_by": added["verified_by"]}
                    if "verified_by" in added
                    else {}
                ),
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def add_sketch_dimension(input_data: AddDimensionInput) -> dict[str, Any]:
        """Add a driving dimension to sketch entities: linear, horizontal, vertical, angular, radial or diameter.

        Use it to fix a size or a position so the sketch is fully defined and can be
        changed later with `set_dimension`. Entities are the `id`s the add_* tools or
        `edit_sketch` returned, 'Origin', or a point: '<id>.center', '<id>.start',
        '<id>.end'. A distance can join points, circles (their centres) and lines.
        The value is read back; one that didn't take is an error. Needs: an open
        sketch. Changes: adds the dimension and moves the geometry to `value`.
        Returns: `dimension` with `id`, `type`, `value`, the entities, and `name`
        (for `set_dimension`) and `measured` when they read.

        Args:
            input_data: The dimension. `entity1` and `value` (mm, or degrees for
                angular) are required; `entity2` and `dimension_type` (linear) have
                defaults.

        Returns:
            dict[str, Any]: The dimension added.
        """
        input_data = normalize_input(input_data, AddDimensionInput)
        result = await adapter.add_sketch_dimension(
            input_data.entity1,
            input_data.entity2,
            input_data.dimension_type,
            input_data.value,
        )

        require_success(result, context="Failed to add dimension")
        added = result.data if isinstance(result.data, dict) else {"id": result.data}
        unit = "deg" if input_data.dimension_type == "angular" else "mm"
        return {
            "status": "success",
            "message": (
                f"Added {input_data.dimension_type} dimension of "
                f"{input_data.value:g} {unit} to {input_data.entity1}"
                + (f" and {input_data.entity2}" if input_data.entity2 else "")
            ),
            "dimension": {
                "id": added.get("id"),
                "type": input_data.dimension_type,
                "value": input_data.value,
                "entity1": input_data.entity1,
                "entity2": input_data.entity2,
                **{k: added[k] for k in ("name", "measured") if k in added},
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def sketch_linear_pattern(
        input_data: SketchLinearPatternInput,
    ) -> dict[str, Any]:
        """Repeat sketch entities in a straight line as a parametric sketch pattern.

        Use it for rows of holes or slots before one cut. The pattern keeps its
        spacing and count as dimensions (`pattern.dimensions`, names such as
        "D3@Sketch2"): change them with `set_dimension` or drive them with
        `add_equation`, and a change to the seed carries to the copies. For a row of
        a feature already cut, use `pattern_linear`. Needs: an open sketch
        (`create_sketch`). Changes: adds the pattern to the sketch. Returns:
        `pattern` with `id`, `entities`, `count`, `spacing`, `direction`,
        `parametric` and `dimensions` (`name`, `role`, `value`, `units`).

        Args:
            input_data: The pattern. `entities` (ids to repeat) is required;
                `direction_x`/`direction_y` (1, 0), `spacing` (mm, 10), `count`
                (3) and `add_dimensions` (true) have defaults.

        Returns:
            dict[str, Any]: The pattern added.
        """
        input_data = normalize_input(input_data, SketchLinearPatternInput)
        result = await adapter.sketch_linear_pattern(
            input_data.entities,
            input_data.direction_x,
            input_data.direction_y,
            input_data.spacing,
            input_data.count,
            input_data.add_dimensions,
        )

        require_success(result, context="Failed to create linear pattern")
        metadata = _pattern_metadata(result)
        return {
            "status": "success",
            "message": (
                f"Created linear pattern with {input_data.count} instances, "
                f"spacing {input_data.spacing}mm"
            ),
            "pattern": {
                "id": result.data,
                "type": "linear",
                "entities": input_data.entities,
                "count": input_data.count,
                "spacing": input_data.spacing,
                "direction": {"x": input_data.direction_x, "y": input_data.direction_y},
                "parametric": metadata.get("parametric", input_data.add_dimensions),
                "dimensions": metadata.get("dimensions", []),
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def sketch_circular_pattern(
        input_data: SketchCircularPatternInput,
    ) -> dict[str, Any]:
        """Repeat sketch entities about a centre as a parametric sketch pattern, e.g. a bolt circle.

        The centre is `center_x`/`center_y` in sketch mm (the origin by default); the
        seed's distance from it is the radius. The pattern keeps its radius, angle
        and count as dimensions (`pattern.dimensions`, names such as "D4@Sketch3"):
        change them with `set_dimension` or drive them with `add_equation`, and a
        change to the seed carries to the copies. Then cut once with
        `create_cut_extrude`. Or cut one seed and repeat the feature with
        `pattern_circular`. Needs: an open sketch (`create_sketch`). Changes: adds the
        pattern to the sketch. Returns: `pattern` with `id`, `entities`, `count`,
        `angle`, `center`, `radius` (mm), `parametric` and `dimensions` (`name`,
        `role`, `value`, `units`).

        Args:
            input_data: The pattern. `entities` (ids to repeat) is required;
                `count` (6), `angle` (degrees, 360), `center_x`/`center_y` (mm,
                0) and `add_dimensions` (true) have defaults.

        Returns:
            dict[str, Any]: The pattern added.
        """
        input_data = normalize_input(input_data, SketchCircularPatternInput)
        result = await adapter.sketch_circular_pattern(
            input_data.entities,
            input_data.angle,
            input_data.count,
            input_data.center_x,
            input_data.center_y,
            input_data.add_dimensions,
        )

        require_success(result, context="Failed to create circular pattern")
        metadata = _pattern_metadata(result)
        center = {"x": input_data.center_x, "y": input_data.center_y}
        return {
            "status": "success",
            "message": (
                f"Created circular pattern with {input_data.count} instances "
                f"about ({input_data.center_x:g}, {input_data.center_y:g}) mm"
            ),
            "pattern": {
                "id": result.data,
                "type": "circular",
                "entities": input_data.entities,
                "count": input_data.count,
                "center": metadata.get("center", center),
                "radius": metadata.get("radius"),
                "angle": input_data.angle,
                "parametric": metadata.get("parametric", input_data.add_dimensions),
                "dimensions": metadata.get("dimensions", []),
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def sketch_mirror(input_data: dict[str, Any]) -> dict[str, Any]:
        """Mirror sketch entities about a centreline in the open sketch.

        Use it for symmetric profiles: draw one half, add a centreline with
        `add_centerline`, then mirror. Needs: an open sketch (`create_sketch`). Changes:
        adds mirrored copies. Returns: `mirror` with `id`, `entities` and `mirror_line`.

        Args:
            input_data: A mapping: `entities` (ids to mirror) and `mirror_line`
                (the centreline's id).

        Returns:
            dict[str, Any]: The mirror added.
        """
        entities = input_data.get("entities", [])
        mirror_line = input_data.get("mirror_line", "")

        result = await adapter.sketch_mirror(entities, mirror_line)

        require_success(result, context="Failed to mirror entities")
        mirror_id = result.data
        return {
            "status": "success",
            "message": f"Mirrored {len(entities)} entities about {mirror_line}",
            "mirror": {
                "id": mirror_id,
                "entities": entities,
                "mirror_line": mirror_line,
            },
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def sketch_offset(input_data: dict[str, Any]) -> dict[str, Any]:
        """Offset sketch entities by a distance in mm, outward or inward, e.g. for a wall or a clearance.

        Needs: an open sketch (`create_sketch`). Changes: adds the offset entities.
        Returns: `offset` with `id`, `entities`, `distance` and `direction` (outward or
        inward).

        Args:
            input_data: A mapping: `entities` (ids to offset), `offset_distance`
                (mm, default 5) and `reverse_direction` (inward; default off).

        Returns:
            dict[str, Any]: The offset added.
        """
        entities = input_data.get("entities", [])
        offset_distance = input_data.get("offset_distance", 5.0)
        reverse_direction = input_data.get("reverse_direction", False)

        result = await adapter.sketch_offset(
            entities, offset_distance, reverse_direction
        )

        require_success(result, context="Failed to create offset")
        offset_id = result.data
        direction = "outward" if not reverse_direction else "inward"
        return {
            "status": "success",
            "message": f"Created offset of {len(entities)} entities by {offset_distance}mm ({direction})",
            "offset": {
                "id": offset_id,
                "entities": entities,
                "distance": offset_distance,
                "direction": direction,
            },
            "execution_time": result.execution_time,
        }

    # Additional sketch tools can be added here in the future:
    # - add_arc (3-point arc, center-point arc)
    # - add_spline (free-form curves)
    # - add_dimension (dimensional constraints)
    # - add_relation (geometric constraints like parallel, perpendicular)
    # - add_pattern (circular, linear patterns)
    # - trim_entities (trim overlapping geometry)
    # - mirror_entities (mirror sketch geometry)

    @mcp.tool()
    async def sketch_tutorial_simple_hole() -> dict[str, Any]:
        """Demonstrate the sketch steps: draw a 5 mm circle at the origin on the Top plane and close the sketch.

        It is a fixed example; for a real hole, use `create_sketch`, `add_circle`,
        `exit_sketch` and `create_cut_extrude`. Needs: an open part. Changes: adds a
        sketch with one circle. Returns: `steps_completed`, `next_step` (cut the hole
        with `create_cut_extrude`) and `suggested_cut`, that call's input.
        """
        steps_completed = []

        # Step 1: Create sketch
        sketch_result = await adapter.create_sketch("Top")
        require_success(sketch_result, context="Failed to create sketch")
        sketch_name = (
            sketch_result.data.get("sketch_name")
            if isinstance(sketch_result.data, dict)
            else sketch_result.data
        )
        steps_completed.append(f"Created sketch: {sketch_name}")

        # Step 2: Add circle
        circle_result = await adapter.add_circle(0, 0, 2.5)
        require_success(circle_result, context="Failed to add circle")
        steps_completed.append(f"Added 5mm diameter circle: {circle_result.data}")

        # Step 3: Exit sketch
        exit_result = await adapter.exit_sketch()
        require_success(exit_result, context="Failed to exit sketch")
        steps_completed.append("Exited sketch")

        return {
            "status": "success",
            "message": "Tutorial completed: Simple hole sketch created",
            "steps_completed": steps_completed,
            "next_step": (
                "Cut the hole through the part with create_cut_extrude, using "
                "suggested_cut as its input"
            ),
            "suggested_cut": {
                "sketch_name": sketch_name,
                "depth": 10.0,
                "end_condition": "ThroughAll",
            },
        }

    @mcp.tool()
    async def tutorial_simple_hole(
        input_data: TutorialSimpleHoleInput,
    ) -> dict[str, Any]:
        """Draw a circle on a plane and cut it as a hole, as one worked example.

        Not available yet on real SolidWorks: its cut step has no COM implementation,
        so the call draws and closes the sketch, then fails. It is planned for a future
        release. Use `create_sketch`, `add_circle`, `exit_sketch` and
        `create_cut_extrude` instead. Needs: an open part with a solid body.
        Changes: adds a sketch (and, with the mock adapter, a cut). Returns: `tutorial`
        with `plane`, `diameter`, `depth` and `steps`.

        Args:
            input_data: The hole. `plane`, `center_x`, `center_y` and `diameter`
                (mm) are required; `depth` defaults to the diameter.

        Returns:
            dict[str, Any]: The steps run.
        """
        steps: list[dict[str, Any]] = []

        sketch_result = await adapter.create_sketch(input_data.plane)
        require_success(sketch_result, context="Failed to create sketch")
        steps.append({"step": "Create sketch", "status": "success"})

        circle_result = await adapter.add_sketch_circle(
            input_data.center_x,
            input_data.center_y,
            input_data.diameter / 2,
            False,
        )
        require_success(circle_result, context="Failed to add circle")
        steps.append({"step": "Add circle", "status": "success"})

        exit_result = await adapter.exit_sketch()
        require_success(exit_result, context="Failed to exit sketch")
        steps.append({"step": "Exit sketch", "status": "success"})

        cut_depth = (
            input_data.depth if input_data.depth is not None else input_data.diameter
        )
        cut_result = await adapter.create_cut("HoleSketch", cut_depth)
        require_success(cut_result, context="Failed to create cut extrude")
        steps.append({"step": "Create cut extrude", "status": "success"})

        return {
            "status": "success",
            "message": "Completed simple hole tutorial",
            "tutorial": {
                "plane": input_data.plane,
                "diameter": input_data.diameter,
                "depth": input_data.depth,
                "steps": steps,
            },
        }

    tool_count = 6  # Legacy count expected by tests
    return tool_count
