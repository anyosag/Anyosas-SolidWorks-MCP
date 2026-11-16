"""Multibody-part tools: list, insert, combine, move, delete, split, measure, cut a negative.

A build ("Lid with inverted text features") had to cut a holder's raised
text labels, with a clearance, into a lid's underside and dropped to the raw
API for every step. These tools are those steps; the COM is in
``adapters/solidworks/bodies.py``. Every tool calls a real adapter method and
returns the adapter's own result.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import AliasChoices, Field, field_validator

from ..adapters.base import AdapterResult, SolidWorksAdapter
from ..adapters.solidworks.bodies import RESIDUAL_WARN_MM3, SHIFT_TOLERANCE_MM
from .input_compat import CompatInput
from .tool_boundary import (
    normalize_input,
    require_capability,
    require_success,
    with_tool_boundary,
)

Axis = Literal["x", "y", "z"]

_AXIS_DESCRIPTION = "The model axis to turn about: x, y or z"
_ABOUT_DESCRIPTION = (
    "A point on the axis of the turn, [x, y, z] in mm; the origin when omitted"
)


def _clean_names(values: list[str], field: str) -> list[str]:
    """Strip each name; refuse a blank one or the same one twice."""
    cleaned = [str(v).strip() for v in values]
    if any(not v for v in cleaned):
        raise ValueError(f"{field} holds a blank body name")
    if len(set(cleaned)) != len(cleaned):
        raise ValueError(f"{field} names a body twice")
    return cleaned


def _clean_name(value: str, field: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        raise ValueError(f"{field} is required")
    return cleaned


class ListBodiesInput(CompatInput):
    """Input schema for listing a part's solid bodies.

    Attributes:
        visible_only (bool): Only the visible bodies.
    """

    visible_only: bool = Field(
        default=False, description="List only the visible bodies; all when false"
    )


class _Placement(CompatInput):
    """A shift and a turn about one model axis."""

    translate_mm: list[float] | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="The shift [dx, dy, dz] in mm",
    )
    rotate_deg: float = Field(
        default=0.0,
        ge=-360.0,
        le=360.0,
        description="The turn in degrees about `axis` (right-hand rule), after "
        "the shift",
    )
    axis: Axis = Field(default="z", description=_AXIS_DESCRIPTION)
    rotate_about_mm: list[float] | None = Field(
        default=None, min_length=3, max_length=3, description=_ABOUT_DESCRIPTION
    )

    def moves(self) -> bool:
        """Whether the placement shifts or turns anything."""
        return bool(self.translate_mm and any(self.translate_mm)) or bool(
            self.rotate_deg
        )


class InsertPartInput(_Placement):
    """Input schema for inserting a part's bodies into the active part.

    Attributes:
        file_path (str): The part file.
        configuration (str | None): Its configuration.
        break_link (bool): Break the link to the file.
    """

    file_path: str = Field(
        description="Absolute path of the saved .sldprt whose solid bodies to insert"
    )
    configuration: str | None = Field(
        default=None,
        description="The inserted part's configuration; its active one when omitted",
    )
    break_link: bool = Field(
        default=False,
        description="Break the link to the file, so later edits to it don't "
        "update these bodies",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a path that isn't a part file."""
        self.file_path = _clean_name(self.file_path, "file_path")
        if not self.file_path.lower().endswith(".sldprt"):
            raise ValueError(f"file_path must be a .sldprt, got {self.file_path!r}")


class CombineBodiesInput(CompatInput):
    """Input schema for combining bodies.

    Attributes:
        operation (str): add, subtract or common.
        main (str): The main body.
        tools (list[str]): The tool bodies.
    """

    operation: Literal["add", "subtract", "common"] = Field(
        description="add: merge them; subtract: cut the tools out of `main`; "
        "common: keep only what they all share"
    )
    main: str = Field(description="The main body's name, as list_bodies reports it")
    tools: list[str] = Field(
        min_length=1, description="The tool bodies' names; they are consumed"
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse blank or repeated names, or the main body among the tools."""
        self.main = _clean_name(self.main, "main")
        self.tools = _clean_names(self.tools, "tools")
        if self.main in self.tools:
            raise ValueError(f"main {self.main!r} is also among the tools")


class MoveCopyBodyInput(_Placement):
    """Input schema for moving or copying bodies.

    Attributes:
        bodies (list[str]): The bodies.
        copy_bodies (bool): Copy instead of moving (``copy`` in the schema;
            ``copy`` itself is a pydantic model method).
        copies (int): How many copies.
    """

    bodies: list[str] = Field(
        min_length=1, description="The bodies' names, as list_bodies reports them"
    )
    copy_bodies: bool = Field(
        default=False,
        alias="copy",
        description="Copy the bodies instead of moving them",
    )
    copies: int = Field(
        default=1,
        ge=1,
        le=50,
        description="How many copies, each one more shift (or turn) from the "
        "last; with copy only",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a move that moves nothing, or copies without ``copy``."""
        self.bodies = _clean_names(self.bodies, "bodies")
        if not self.moves():
            raise ValueError("give translate_mm or rotate_deg; nothing would move")
        if self.copies > 1 and not self.copy_bodies:
            raise ValueError("copies needs copy: true")


class DeleteKeepBodyInput(CompatInput):
    """Input schema for deleting bodies, or keeping only them.

    Attributes:
        bodies (list[str]): The bodies.
        keep (bool): Keep these, delete the rest.
    """

    bodies: list[str] = Field(
        min_length=1, description="The bodies' names, as list_bodies reports them"
    )
    keep: bool = Field(
        default=False,
        description="Keep these bodies and delete every other; false deletes these",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse blank or repeated names."""
        self.bodies = _clean_names(self.bodies, "bodies")


class SplitBodyInput(CompatInput):
    """Input schema for splitting bodies.

    Attributes:
        plane (str | None): The plane to split along.
        sketch (str | None): The sketch to split along.
        body (str | None): Only this body.
    """

    plane: str | None = Field(
        default=None,
        description="The plane: Front, Top, Right, or a plane's feature name "
        "such as 'Plane1'",
    )
    sketch: str | None = Field(
        default=None,
        description="Or a sketch's name, e.g. 'Sketch3'; its lines cut through "
        "the bodies",
    )
    body: str | None = Field(
        default=None,
        description="Split only this body; every body the plane or sketch cuts "
        "when omitted",
    )

    def model_post_init(self, __context: Any) -> None:
        """Require exactly one of ``plane`` and ``sketch``."""
        self.plane = (self.plane or "").strip() or None
        self.sketch = (self.sketch or "").strip() or None
        self.body = (self.body or "").strip() or None
        if (self.plane is None) == (self.sketch is None):
            raise ValueError("give plane or sketch, one of them")


class BodyClearanceInput(CompatInput):
    """Input schema for the gap between two bodies.

    Attributes:
        body_a (str): One body.
        body_b (str): The other.
    """

    body_a: str = Field(description="One body's name, as list_bodies reports it")
    body_b: str = Field(description="The other body's name")

    def model_post_init(self, __context: Any) -> None:
        """Refuse a blank name, or the same body twice."""
        self.body_a, self.body_b = self.body_a.strip(), self.body_b.strip()
        if not self.body_a or not self.body_b:
            raise ValueError("body_a and body_b are both required")
        if self.body_a == self.body_b:
            raise ValueError(
                f"body_a and body_b are both {self.body_a!r}; give two different bodies"
            )


class MakeMatingNegativeInput(CompatInput):
    """Input schema for cutting a mating negative.

    Attributes:
        target (str): The body to cut.
        tools (list[str]): The bodies whose negative is cut.
        clearance_mm (float): The gap around them.
        directions (int): Directions the copies are shifted in.
        axis (str): The axis the directions ring.
        method (str): auto, indent or shifted_copies.
    """

    target: str = Field(description="The body to cut into, e.g. the lid")
    tools: list[str] = Field(
        min_length=1,
        validation_alias=AliasChoices("tools", "tool"),
        description="The bodies whose shape is cut, e.g. the holder's labels; one "
        "name or a list. They stay in the part",
    )
    clearance_mm: float = Field(
        gt=0.0,
        le=5.0,
        description="The gap left around the tools, in mm, e.g. 0.15 for a 3D "
        "printed fit",
    )
    directions: int = Field(
        default=32,
        ge=4,
        le=128,
        description="Directions each tool's copies are shifted in around `axis`; "
        "8 under-cuts sharp tips, 32 left about 0.002 mm3",
    )
    axis: Axis = Field(
        default="y",
        description="The axis the shift directions ring: the direction the tools "
        "press into the target",
    )
    method: Literal["auto", "indent", "shifted_copies"] = Field(
        default="auto",
        description="auto: an Indent feature, else shifted copies; or only one",
    )

    @field_validator("tools", mode="before")
    @classmethod
    def _one_tool(cls, value: Any) -> Any:
        """Take one tool name as a list of one."""
        return [value] if isinstance(value, str) else value

    def model_post_init(self, __context: Any) -> None:
        """Refuse blank or repeated names, or the target among the tools."""
        self.target = _clean_name(self.target, "target")
        self.tools = _clean_names(self.tools, "tools")
        if self.target in self.tools:
            raise ValueError(f"target {self.target!r} is also among the tools")


def _success(
    result: AdapterResult[Any], message: str, warning: str | None = None
) -> dict[str, Any]:
    """Shape a successful adapter result as the standard tool response."""
    reply = {
        "status": "success",
        "message": message,
        "data": result.data,
        "execution_time": result.execution_time,
    }
    if warning:
        reply["warning"] = warning
    return reply


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _off_shifts(data: dict[str, Any], asked: list[float]) -> list[str]:
    """The moved bodies whose box centre didn't shift as asked."""
    off = []
    for moved in data.get("moved") or []:
        shift = moved.get("center_shift_mm")
        if shift and any(
            abs(float(s) - float(a)) > SHIFT_TOLERANCE_MM
            for s, a in zip(shift, asked, strict=True)
        ):
            off.append(f"{moved['name']} by {shift} mm")
    return off


async def register_bodies_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register the multibody-part tools with FastMCP.

    Args:
        mcp (FastMCP): The MCP server.
        adapter (SolidWorksAdapter): Adapter used by every tool.
        config (dict[str, Any]): Server configuration (unused).

    Returns:
        int: Number of tools registered.
    """

    mcp = with_tool_boundary(mcp, adapter)

    def _supports(method: str) -> None:
        require_capability(
            adapter,
            method,
            f"The active adapter does not support {method}; nothing was done.",
        )

    @mcp.tool()
    async def list_bodies(
        input_data: ListBodiesInput | None = None,
    ) -> dict[str, Any]:
        """List the solid bodies of the active part: each one's name, volume, box and face count.

        Use it before the other body tools, which take bodies by these names
        (`combine_bodies`, `move_copy_body`, `delete_keep_body`, `split_body`,
        `body_clearance`, `make_mating_negative`), and after them to check the
        result. Needs: an open part. Read-only. Returns: `data` with `count` and
        `bodies`, each with `name`, `volume_mm3`, `surface_area_mm2`,
        `center_of_mass_mm`, `box_mm` and `size_mm` (SolidWorks' approximate box),
        `face_count` and `visible`.

        Args:
            input_data: Nothing is required: `visible_only` lists only the visible
                bodies.

        Returns:
            dict[str, Any]: The bodies.
        """
        input_data = normalize_input(input_data, ListBodiesInput)
        _supports("list_bodies")
        result = await adapter.list_bodies(input_data.visible_only)
        require_success(result, context="Failed to list the bodies")
        names = [b["name"] for b in (result.data or {}).get("bodies", [])]
        message = f"The part has {_plural(len(names), 'solid body', 'solid bodies')}"
        if names:
            message += (
                ": " + ", ".join(names[:20]) + (" ..." if len(names) > 20 else "")
            )
        return _success(result, message)

    @mcp.tool()
    async def insert_part(input_data: InsertPartInput) -> dict[str, Any]:
        """Insert another part's solid bodies into the active part, at its origin or moved.

        Use it to bring a mating part (a holder, a pin) into this part as bodies, to
        cut its shape out with `make_mating_negative` or `combine_bodies`, or to
        check a fit with `body_clearance`. They stay linked to the file unless
        `break_link`. To place them, give `translate_mm`, then `rotate_deg` about
        `axis` (Move/Copy Body features). For an assembly, use `insert_component`.
        Needs: an open part, and another saved .sldprt. Changes: adds an inserted
        part feature (and any move features) to the active part. Returns: `data`
        with `feature`, `bodies_added` (their names), `bodies` (each one's volume
        and box) and `move_features`.

        Args:
            input_data: The part to insert. `file_path` (.sldprt) is required;
                `configuration`, a placement (`translate_mm`, `rotate_deg`, `axis`,
                `rotate_about_mm`) and `break_link` are optional.

        Returns:
            dict[str, Any]: The bodies inserted.
        """
        input_data = normalize_input(input_data, InsertPartInput)
        _supports("insert_part")
        result = await adapter.insert_part(
            input_data.file_path,
            input_data.configuration,
            input_data.translate_mm,
            input_data.rotate_deg,
            input_data.axis,
            input_data.rotate_about_mm,
            input_data.break_link,
        )
        require_success(result, context="Failed to insert the part")
        added = (result.data or {}).get("bodies_added", [])
        return _success(
            result,
            f"Inserted {_plural(len(added), 'body', 'bodies')} from "
            f"{input_data.file_path}: {', '.join(added)}",
        )

    @mcp.tool()
    async def combine_bodies(input_data: CombineBodiesInput) -> dict[str, Any]:
        """Combine solid bodies of the active part: add them, subtract tools from a main body, or keep what they share.

        Use it to merge bodies (`add`), cut one body's shape out of another
        (`subtract`: the `tools` come out of `main`), or keep only their overlap
        (`common`). The tool bodies are consumed, and the result takes the feature's
        name (Combine1): use the `result` names afterwards. To cut a shape with a
        clearance around it, use `make_mating_negative`. Needs: an open part holding
        the named bodies (`list_bodies`). Changes: adds a Combine feature. Returns:
        `data` with `feature`, `result` (the resulting bodies' names, volumes and boxes),
        `main_volume_before_mm3`, `result_volume_mm3`, `bodies_before` and
        `bodies_after`.

        Args:
            input_data: The combine. `operation` (add, subtract or common), `main`
                and `tools` (at least one, not `main`) are required, by body name.

        Returns:
            dict[str, Any]: The combined bodies.
        """
        input_data = normalize_input(input_data, CombineBodiesInput)
        _supports("combine_bodies")
        result = await adapter.combine_bodies(
            input_data.operation, input_data.main, input_data.tools
        )
        require_success(result, context="Failed to combine the bodies")
        data = result.data or {}
        results = [r["name"] for r in data.get("result", [])]
        return _success(
            result,
            f"Combined ({input_data.operation}) {input_data.main} with "
            f"{', '.join(input_data.tools)} in {data.get('feature')}: "
            f"{', '.join(results) or 'no body'}",
        )

    @mcp.tool()
    async def move_copy_body(input_data: MoveCopyBodyInput) -> dict[str, Any]:
        """Move or copy solid bodies of the active part: shift them, turn them about an axis, or both.

        Use it to place bodies inside one part, e.g. after `insert_part`, or to
        repeat a body (`copy`, `copies`). A shift and a turn make two Move/Copy Body
        features, the shift first. A moved body takes the feature's name
        (Body-Move/Copy1). For a component of an assembly, use
        `set_component_transform`. Needs: an open part holding the named bodies.
        Changes: adds one or two Move/Copy Body features. Returns: `data` with
        `features`, `new_bodies` (the copies' names, volumes and boxes), `moved`
        (each moved body's `name_now`, box centre before and after, and the shift)
        and `bodies_after`.

        Args:
            input_data: The bodies and the move. `bodies` and `translate_mm` (mm)
                or `rotate_deg` (about `axis`, through `rotate_about_mm`) are
                required; `copy` copies instead, `copies` times.

        Returns:
            dict[str, Any]: The bodies moved or copied.
        """
        input_data = normalize_input(input_data, MoveCopyBodyInput)
        _supports("move_copy_body")
        result = await adapter.move_copy_body(
            input_data.bodies,
            input_data.translate_mm,
            input_data.rotate_deg,
            input_data.axis,
            input_data.rotate_about_mm,
            input_data.copy_bodies,
            input_data.copies,
        )
        require_success(result, context="Failed to move the bodies")
        data = result.data or {}
        if input_data.copy_bodies:
            new = [b["name"] for b in data.get("new_bodies", [])]
            message = (
                f"Copied {', '.join(input_data.bodies)} into "
                f"{', '.join(new) or 'no new body'}"
            )
        else:
            message = f"Moved {', '.join(input_data.bodies)}"
        off = []
        if input_data.translate_mm and not input_data.rotate_deg:
            off = _off_shifts(data, input_data.translate_mm)
        warning = (
            f"SolidWorks shifted {'; '.join(off)}, not the {input_data.translate_mm} "
            "mm asked; check with list_bodies."
            if off
            else None
        )
        return _success(result, message, warning)

    @mcp.tool()
    async def delete_keep_body(input_data: DeleteKeepBodyInput) -> dict[str, Any]:
        """Delete solid bodies from the active part, or keep only them, with a Body-Delete/Keep feature.

        Use it to remove bodies you no longer need, such as a mating part's bodies
        once `make_mating_negative` has cut their negative, or to keep just the
        bodies to print. Deleting the feature (`delete_feature`) brings them back.
        Needs: an open part holding the named bodies. Changes: adds a
        Body-Delete/Keep feature. Returns: `data` with `feature`, `removed` (the
        bodies gone), `expected_removed` and `bodies_after`.

        Args:
            input_data: The bodies. `bodies` is required; `keep: true` keeps them
                and deletes the rest.

        Returns:
            dict[str, Any]: The bodies removed.
        """
        input_data = normalize_input(input_data, DeleteKeepBodyInput)
        _supports("delete_keep_body")
        result = await adapter.delete_keep_body(input_data.bodies, input_data.keep)
        require_success(result, context="Failed to delete the bodies")
        data = result.data or {}
        removed = data.get("removed", [])
        expected = data.get("expected_removed", removed)
        warning = (
            f"SolidWorks removed {', '.join(removed) or 'nothing'}, not "
            f"{', '.join(expected)}; check with list_bodies."
            if set(removed) != set(expected)
            else None
        )
        return _success(result, f"Deleted {', '.join(removed) or 'no body'}", warning)

    @mcp.tool()
    async def split_body(input_data: SplitBodyInput) -> dict[str, Any]:
        """Split solid bodies of the active part along a plane or a sketch, keeping every piece as a body.

        Use it to cut a part into pieces to print separately, or to split one body
        before working on part of it. A default plane can be named Front, Top or
        Right; any other plane or sketch by its feature name. Needs: an open part,
        and a plane or sketch that cuts through a body. Changes: adds a Split
        feature. Returns: `data` with `feature`, `tool` (the plane or sketch used),
        `pieces` (each new body's name, volume and box), `bodies_before` and
        `bodies_after`.

        Args:
            input_data: The split. Give `plane` or `sketch` (one of them); `body`
                splits only that body.

        Returns:
            dict[str, Any]: The pieces.
        """
        input_data = normalize_input(input_data, SplitBodyInput)
        _supports("split_body")
        result = await adapter.split_body(
            input_data.plane, input_data.sketch, input_data.body
        )
        require_success(result, context="Failed to split the body")
        data = result.data or {}
        pieces = [p["name"] for p in data.get("pieces", [])]
        return _success(
            result,
            f"Split along {data.get('tool')} into "
            f"{_plural(len(pieces), 'piece', 'pieces')}: {', '.join(pieces)}",
        )

    @mcp.tool()
    async def body_clearance(input_data: BodyClearanceInput) -> dict[str, Any]:
        """Measure the smallest gap between two solid bodies of the active part, in mm.

        Use it to check a fit between bodies of one part, e.g. a lid's pocket
        around a holder's label after `make_mating_negative`, or bodies placed with
        `move_copy_body`. Overlapping bodies read 0 with `touching: true` and their
        shared volume as `overlap_mm3`. For components of an assembly, use
        `measure_clearance`. Needs: an open part holding both bodies. Read-only.
        Returns: `data` with `clearance_mm`, `touching`, `overlap_mm3`,
        `point_a_mm` and `point_b_mm` (the nearest points), `face_a` and `face_b`
        (`kind` and `box_mm`), `pairs_checked` and `pairs_total`.

        Args:
            input_data: The two bodies, by name as `list_bodies` reports them:
                `body_a` and `body_b`, both required and different.

        Returns:
            dict[str, Any]: The clearance found.
        """
        input_data = normalize_input(input_data, BodyClearanceInput)
        _supports("body_clearance")
        result = await adapter.body_clearance(input_data.body_a, input_data.body_b)
        require_success(result, context="Failed to measure the clearance")
        data = result.data or {}
        pair = f"{input_data.body_a} and {input_data.body_b}"
        if not data.get("touching"):
            return _success(
                result, f"Clearance between {pair}: {data.get('clearance_mm')} mm"
            )
        overlap = data.get("overlap_mm3") or 0.0
        what = f"overlap by {overlap} mm3" if overlap else "touch"
        return _success(
            result,
            f"{pair} {what}",
            f"{pair} {what}, so there is no clearance to report.",
        )

    @mcp.tool()
    async def make_mating_negative(
        input_data: MakeMatingNegativeInput,
    ) -> dict[str, Any]:
        """Cut tool bodies, grown by a clearance, out of a target body: a pocket that fits them.

        Use it for a lid or holder that fits over raised features, e.g. a holder's
        text labels sunk into a lid's underside with 0.15 mm clearance, in one call.
        It tries an Indent feature, then the session's fix: copies of each tool
        shifted by the clearance in `directions` directions around `axis` and along
        it, joined and subtracted with Combine (2 x directions + 3 copies a tool).
        Slow: seconds per tool. The tools stay in the part (`delete_keep_body`
        removes them). Needs: an open part holding the target and tools
        (`insert_part` brings a mating part in). Changes: adds the features, in a
        folder. Returns: `data` with `method`, `volume_removed_mm3`,
        `residual_overlap_mm3` (what of the tools still lies in the target, ~0),
        `features`, `folder` and `result_body`.

        Args:
            input_data: The cut. `target`, `tools` (body names) and `clearance_mm`
                are required; `directions` (32), `axis` (y) and `method` (auto)
                have defaults.

        Returns:
            dict[str, Any]: The cut made.
        """
        input_data = normalize_input(input_data, MakeMatingNegativeInput)
        _supports("make_mating_negative")
        result = await adapter.make_mating_negative(
            input_data.target,
            input_data.tools,
            input_data.clearance_mm,
            input_data.directions,
            input_data.axis,
            input_data.method,
        )
        require_success(result, context="Failed to cut the mating negative")
        data = result.data or {}
        removed = data.get("volume_removed_mm3")
        residual = data.get("residual_overlap_mm3") or 0.0
        warnings = []
        if not removed or removed <= 0:
            warnings.append(
                "Nothing was removed: the tools, grown by the clearance, don't "
                f"reach {input_data.target}; check their placement with "
                "body_clearance."
            )
        if residual > RESIDUAL_WARN_MM3:
            warnings.append(
                f"The tools still overlap the result by {residual} mm3; call again "
                "with more directions."
            )
        return _success(
            result,
            f"Cut {removed} mm3 from {data.get('result_body')} around "
            f"{_plural(len(input_data.tools), 'tool', 'tools')} with "
            f"{input_data.clearance_mm} mm clearance ({data.get('method')}); the "
            "tool bodies are still in the part",
            " ".join(warnings) or None,
        )

    return 8
