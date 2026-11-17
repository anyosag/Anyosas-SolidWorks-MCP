"""Export tools for SolidWorks MCP Server.

Provides tools for exporting SolidWorks models to various formats including STEP, IGES,
STL, PDF, DWG, and image formats.
"""

import os
import string
from typing import Any, Literal, TypeVar

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from ..adapters.base import SolidWorksAdapter
from ..utils import export_check, stl_mesh
from .input_compat import CompatInput
from .modeling import DisplayType
from .tool_boundary import (
    InvalidInputError,
    ToolOperationError,
    UnsupportedOperationError,
    normalize_input,
    require_capability,
    require_success,
    with_tool_boundary,
)

TInput = TypeVar("TInput", bound=BaseModel)


# Input schemas using Python 3.14 built-in types


class ExportFileInput(CompatInput):
    """Input schema for exporting files.

    Attributes:
        file_path (str): The file path value.
        format_type (str): The format type value.
    """

    file_path: str = Field(description="Full path for the exported file")
    format_type: str | None = Field(
        default=None,
        description=(
            "Not needed: the file extension sets the format; defaults to the "
            "tool's own format"
        ),
    )


class StlMeshOptions(CompatInput):
    """Optional STL mesh settings, for one export.

    Attributes:
        resolution (str | None): coarse, fine or custom.
        deviation_mm (float | None): Largest gap to the true surface, mm.
        angle_deg (float | None): Largest angle between facets, degrees.
        units (str | None): The units written in the file.
        binary (bool | None): Binary (True) or ASCII (False) STL.
    """

    resolution: Literal["coarse", "fine", "custom"] | None = Field(
        default=None,
        description=(
            "Mesh resolution for this file: coarse, fine, or custom with "
            "deviation_mm and angle_deg. Left out: the user's STL setting"
        ),
    )
    deviation_mm: float | None = Field(
        default=None,
        gt=0,
        le=100,
        description=(
            "Largest distance between the mesh and the true surface, in mm. "
            "Smaller is finer; it makes the resolution custom"
        ),
    )
    angle_deg: float | None = Field(
        default=None,
        gt=0,
        le=90,
        description=(
            "Largest angle between neighbouring facets, in degrees. Smaller "
            "is finer on small curved details; it makes the resolution custom"
        ),
    )
    units: Literal["mm", "cm", "m", "in", "ft"] | None = Field(
        default=None,
        description="Units written in the file. Left out: the user's STL setting",
    )
    binary: bool | None = Field(
        default=None,
        description="Binary STL (true) or ASCII (false). Left out: the user's setting",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a deviation or angle with coarse or fine resolution."""
        tolerances = [
            name
            for name in ("deviation_mm", "angle_deg")
            if getattr(self, name) is not None
        ]
        if tolerances and self.resolution in ("coarse", "fine"):
            raise ValueError(
                f"{' and '.join(tolerances)} need resolution 'custom', not "
                f"{self.resolution!r}"
            )
        if self.resolution == "custom" and not tolerances:
            raise ValueError(
                "resolution 'custom' needs deviation_mm or angle_deg: SolidWorks "
                "takes custom only with its own tolerances"
            )

    def mesh_settings(self) -> dict[str, Any]:
        """The mesh settings given, by name (empty: keep the user's)."""
        names = ("resolution", "deviation_mm", "angle_deg", "units", "binary")
        return {n: getattr(self, n) for n in names if getattr(self, n) is not None}


class ExportStlInput(StlMeshOptions, ExportFileInput):
    """Input schema for STL exports, with optional mesh settings for this file."""


#: The placeholders ``export_assembly_parts_stl``'s ``name_pattern`` takes.
PART_NAME_FIELDS = ("name", "config", "assembly", "index")


class ExportAssemblyPartsStlInput(StlMeshOptions):
    """Input schema for exporting each part of an assembly to its own STL.

    Attributes:
        folder (str): The folder the files go to.
        name_pattern (str): Each file's name, with placeholders.
    """

    folder: str = Field(description="Folder for the STL files; created if missing")
    name_pattern: str = Field(
        default="{name}.stl",
        description=(
            "Each file's name: {name} (the part file's name), {config} (the "
            "configuration the assembly uses), {assembly} and {index}; must end "
            "in .stl"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        """Check the folder and the name pattern, then the mesh settings."""
        super().model_post_init(__context)
        if not self.folder.strip():
            raise ValueError("folder is required")
        pattern = self.name_pattern.strip()
        if not pattern.lower().endswith(".stl"):
            raise ValueError("name_pattern must end in .stl")
        if any(sep in pattern for sep in ("/", "\\", ":")):
            raise ValueError("name_pattern is a file name; put folders in folder")
        try:
            fields = [f for _t, f, _s, _c in string.Formatter().parse(pattern) if f]
        except ValueError as exc:
            raise ValueError(f"name_pattern is not a pattern: {exc}") from exc
        unknown = sorted(set(fields) - set(PART_NAME_FIELDS))
        if unknown:
            raise ValueError(
                f"name_pattern can use {', '.join('{' + f + '}' for f in PART_NAME_FIELDS)}"
                f"; not {', '.join(unknown)}"
            )


class ImageSection(BaseModel):
    """A plane to cut the model on for one picture.

    Attributes:
        plane (str): The plane's name.
        offset_mm (float): How far along its normal the cut is, mm.
        reverse (bool): Keep the other side.
    """

    model_config = ConfigDict(extra="forbid")

    plane: str = Field(
        min_length=1,
        description=(
            "The plane to cut on, by name: Front Plane, Top Plane, Right Plane "
            "or a reference plane such as Plane1"
        ),
    )
    offset_mm: float = Field(
        default=0.0,
        description="Move the cut this far along the plane's normal, in mm",
    )
    reverse: bool = Field(
        default=False,
        description="Keep the other side of the plane (false keeps the default side)",
    )

    def model_post_init(self, __context: Any) -> None:
        """Refuse a blank plane name."""
        self.plane = self.plane.strip()
        if not self.plane:
            raise ValueError("section.plane needs a plane's name")


class ExportImageInput(CompatInput):
    """Input schema for exporting images.

    Attributes:
        file_path (str | None): The file path value.
        format (str | None): The format value.
        format_type (str): The format type value.
        height (int): The height value.
        image_format (str | None): The image format value.
        model_path (str | None): The model path value.
        orientation (str | None): The orientation value.
        output_path (str | None): The output path value.
        resolution (str | None): The resolution value.
        view_orientation (str): The view orientation value.
        width (int): The width value.
        zoom_to_fit (bool | None): Whether to zoom so the model fills the view.
        zoom_box_mm (list[float] | None): Two corners of a box to zoom to, mm.
        hide (list[str] | None): Display types hidden for the picture only.
        section (ImageSection | None): A plane to cut the model on, for the
            picture only.
    """

    file_path: str | None = Field(
        default=None, description="Full path for the exported image"
    )
    output_path: str | None = Field(default=None, description="Alternative output path")
    model_path: str | None = Field(default=None, description="Path to the model file")
    format_type: str = Field(
        default="jpg", description="Image format (jpg, png, bmp, tiff)"
    )
    format: str | None = Field(default=None, description="Alternative format field")
    image_format: str | None = Field(default=None, description="Image format alias")
    width: int = Field(default=1920, description="Image width in pixels")
    height: int = Field(default=1080, description="Image height in pixels")
    resolution: str | None = Field(default=None, description="Resolution alias")
    view_orientation: str = Field(
        default="isometric",
        description=(
            "View to turn the model to (front, back, left, right, top, bottom, "
            "isometric, dimetric, trimetric), or current to keep the view on "
            "screen exactly as it is, zoom included"
        ),
    )
    orientation: str | None = Field(
        default=None, description="Alias of view_orientation"
    )
    zoom_to_fit: bool | None = Field(
        default=None,
        description=(
            "Zoom so the whole model fills the picture. Left out: yes for a "
            "named view, no for current, so a close-up stays a close-up"
        ),
    )
    zoom_box_mm: list[float] | None = Field(
        default=None,
        min_length=6,
        max_length=6,
        description=(
            "Zoom to this part of the model: two opposite corners of a box in "
            "model coordinates, mm, as [x1, y1, z1, x2, y2, z2]"
        ),
    )
    hide: list[DisplayType] | None = Field(
        default=None,
        description=(
            'Hide these for the picture only, e.g. ["planes", "sketches"]; '
            "they are shown again afterwards as they were"
        ),
    )
    section: ImageSection | None = Field(
        default=None,
        description=(
            "Cut the model on a plane for this picture only, with the cut faces "
            'capped, e.g. {"plane": "Front Plane", "offset_mm": 0}, to show a '
            "snap inside its socket; the cut is taken off afterwards"
        ),
    )

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the export image input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.
        """
        if self.file_path is None:
            self.file_path = self.output_path
        if (
            self.orientation is not None
            and "view_orientation" not in self.model_fields_set
        ):
            self.view_orientation = self.orientation
        if self.image_format is not None:
            self.format_type = self.image_format.lower()
        if self.resolution and "x" in self.resolution.lower():
            width, height = self.resolution.lower().split("x", 1)
            self.width = int(width)
            self.height = int(height)
        if self.zoom_box_mm is not None:
            if self.zoom_to_fit:
                raise ValueError("zoom_box_mm and zoom_to_fit: true can't both apply")
            first, second = self.zoom_box_mm[:3], self.zoom_box_mm[3:]
            if first == second:
                raise ValueError("zoom_box_mm needs two different corners")


class BatchExportInput(CompatInput):
    """Input schema for batch export operations.

    Attributes:
        export_format (str | None): The export format value.
        file_pattern (str | None): The file pattern value.
        file_patterns (list[str]): The file patterns value.
        format_type (str | None): The format type value.
        include_subdirectories (bool): The include subdirectories value.
        output_directory (str): The output directory value.
        recursive (bool): The recursive value.
        source_directory (str): The source directory value.
    """

    source_directory: str = Field(description="Directory containing SolidWorks files")
    output_directory: str = Field(description="Directory for exported files")
    format_type: str | None = Field(default=None, description="Target export format")
    export_format: str | None = Field(
        default=None, description="Alternative export format"
    )
    file_pattern: str | None = Field(default=None, description="File pattern alias")
    recursive: bool = Field(
        default=False, description="Search subdirectories recursively"
    )
    include_subdirectories: bool = Field(
        default=False, description="Include subdirectories in search"
    )
    file_patterns: list[str] = Field(
        default=["*.sldprt", "*.sldasm", "*.slddrw"],
        description="File patterns to include",
    )

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the batch export input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.
        """
        if self.format_type is None:
            self.format_type = self.export_format
        if self.file_pattern:
            self.file_patterns = [self.file_pattern]
        if self.include_subdirectories:
            self.recursive = True


def _file_input(input_data: Any, format_type: str) -> Any:
    """Fill ``format_type`` with the tool's own format when it was left out.

    The file extension decides the format; the adapter is still told which
    format the tool exports.
    """
    if not isinstance(input_data, ExportFileInput) or input_data.format_type:
        return input_data
    return input_data.model_copy(update={"format_type": format_type})


def _file_check(
    file_path: str | None, format_name: str, execution_time: float | None
) -> dict[str, Any]:
    """Read back the file an export wrote; raise when it isn't a good one.

    Args:
        file_path: The file the export wrote.
        format_name: The tool's own format, for an extension
            :mod:`export_check` doesn't know.
        execution_time: The export's timing, for the error.

    Returns:
        dict[str, Any]: :func:`export_check.check_export`'s result.

    Raises:
        ToolOperationError: The file is missing, empty or not its format
            (``export_invalid``), with the check as ``data.file_check``.
    """
    check = export_check.check_export(file_path or "", format_name)
    if not export_check.passed(check):
        raise ToolOperationError(
            export_check.failure_message(check),
            execution_time,
            {"file_check": check},
        )
    return check


async def _stl_mesh_check(
    adapter: Any, file_path: str, units: str | None, document_type: Any
) -> dict[str, Any]:
    """Read back the STL just written and, for a part, compare its volume.

    The part's volume comes from ``get_model_geometry_summary``, which
    doesn't rebuild or touch the selection. An assembly's would come from a
    mass property that depends on the selection, so it is left out.

    Args:
        adapter: The active adapter.
        file_path: The ``.stl`` file written.
        units: The unit the file was written in, ``None`` when not known.
        document_type: ``part``, ``assembly`` or ``None``, as the export said.

    Returns:
        dict[str, Any]: :func:`stl_mesh.check_stl`'s check, with
        ``cad_volume_mm3`` and ``volume_ratio`` (mesh / model; ``None`` when
        the model's volume isn't known); only ``error`` when the file
        couldn't be read.
    """
    check = stl_mesh.check_stl(file_path, units)
    if "error" in check:
        return check
    check["cad_volume_mm3"] = None
    check["volume_ratio"] = None
    if document_type != "part" or not hasattr(adapter, "get_model_geometry_summary"):
        return check
    summary = await adapter.get_model_geometry_summary()
    volume = (summary.data or {}).get("volume_mm3") if summary.is_success else None
    if isinstance(volume, int | float) and volume > 0:
        check["cad_volume_mm3"] = round(float(volume), 3)
        check["volume_ratio"] = round(check["volume_mm3"] / float(volume), 4)
    return check


#: A part's STL whose volume is outside this share of the model's is suspect.
_VOLUME_RATIO_RANGE = (0.9, 1.1)


def _partial_mesh_warning(mesh_check: dict[str, Any]) -> str | None:
    """A warning when the STL's volume is far from the part's.

    An export made while a face was selected held only that face: 592
    triangles of a chamfer band instead of the 18,738-triangle lid (Lid
    build, SolidWorks 2026 SP4.1). The adapter now clears the selection
    first; this catches what is left (a body left out, a mesh that lost
    faces).

    Args:
        mesh_check: The export's ``mesh_check``.

    Returns:
        str | None: The warning, or ``None`` when the ratio is unknown or
        within :data:`_VOLUME_RATIO_RANGE`.
    """
    ratio = mesh_check.get("volume_ratio")
    low, high = _VOLUME_RATIO_RANGE
    if not isinstance(ratio, int | float) or low <= ratio <= high:
        return None
    return (
        f"The STL encloses {mesh_check.get('volume_mm3')} mm3, {ratio:.1%} of the "
        f"part's {mesh_check.get('cad_volume_mm3')} mm3, so the file may hold only "
        "part of the model. Check its size and triangle count before printing; "
        "export again with nothing selected and every body shown."
    )


async def register_export_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register export tools with FastMCP.

    Registers comprehensive file export tools for SolidWorks automation supporting multiple
    industry-standard formats including neutral CAD formats, manufacturing formats, and
    documentation outputs.

    Args:
        mcp (FastMCP): The mcp value.
        adapter (SolidWorksAdapter): Adapter instance used for the operation.
        config (dict[str, Any]): Configuration values for the operation.

    Returns:
        int: The computed numeric result.

    Example:
                        ```python
                        from solidworks_mcp.tools.export import register_export_tools

                        tool_count = await register_export_tools(mcp, adapter, config)
                        print(f"Registered {tool_count} export tools")
                        ```
    """
    mcp = with_tool_boundary(mcp, adapter)
    tool_count = 0

    @mcp.tool()
    async def export_step(input_data: ExportFileInput) -> dict[str, Any]:
        """Export the active document to STEP, like File > Save As with the STEP type.

        Use it to hand a model to another CAD system; it is the most widely read
        exchange format. SolidWorks picks the format from the file's extension, so end
        `file_path` in .step or .stp. Needs: an open, active part or assembly. Changes:
        writes the file, creating its folder and replacing a file already there; nothing
        stays selected (an export writes only the selection) and the model is
        unchanged. Returns: `export` with `file_path` and `format`, and `file_check`
        (the file read back: `exists`, `size_bytes`, `format_ok`, `schema`). A file
        that is missing, empty, not STEP or cut short is an `export_invalid` error.

        Args:
            input_data: The export. `file_path` (ending in .step or .stp) is required;
                `format_type` is not needed: the file extension decides the
                format.

        Returns:
            dict[str, Any]: The file written.
        """
        input_data = _file_input(input_data, "step")
        file_path = input_data.file_path

        if hasattr(adapter, "export_step"):
            result = await adapter.export_step(input_data.model_dump())
        else:
            result = await adapter.export_file(file_path, "step")

        require_success(result, context="Failed to export STEP")
        file_check = _file_check(file_path, "STEP", result.execution_time)
        if hasattr(adapter, "export_step"):
            return {
                "status": "success",
                "message": f"Exported to STEP format: {file_path}",
                "data": result.data,
                "file_check": file_check,
                "execution_time": result.execution_time,
            }
        return {
            "status": "success",
            "message": f"Exported to STEP format: {file_path}",
            "export": {
                "file_path": file_path,
                "format": "STEP",
                "size_estimate": "Unknown",  # Would get actual file size
            },
            "file_check": file_check,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def export_iges(input_data: ExportFileInput) -> dict[str, Any]:
        """Export the active document to IGES, like File > Save As with the IGES type.

        Use it only when the receiver can't read STEP; prefer `export_step`. SolidWorks
        picks the format from the file's extension, so end `file_path` in .igs or .iges.
        Needs: an open, active part or assembly. Changes: writes the file, creating its
        folder and replacing a file already there; nothing stays selected (an export
        writes only the selection) and the model is unchanged. Returns: `export` with
        `file_path` and `format`, and `file_check` (the file read back: `exists`,
        `size_bytes`, `format_ok`). A file that is missing, empty, not IGES or cut
        short is an `export_invalid` error.

        Args:
            input_data: The export. `file_path` (ending in .igs or .iges) is required;
                `format_type` is not needed: the file extension decides the
                format.

        Returns:
            dict[str, Any]: The file written.
        """
        input_data = _file_input(input_data, "iges")
        if hasattr(adapter, "export_iges"):
            result = await adapter.export_iges(input_data.model_dump())
        else:
            result = await adapter.export_file(input_data.file_path, "iges")

        require_success(result, context="Failed to export IGES")
        file_check = _file_check(input_data.file_path, "IGES", result.execution_time)
        if hasattr(adapter, "export_iges"):
            return {
                "status": "success",
                "message": f"Exported to IGES format: {input_data.file_path}",
                "data": result.data,
                "file_check": file_check,
                "execution_time": result.execution_time,
            }
        return {
            "status": "success",
            "message": f"Exported to IGES format: {input_data.file_path}",
            "export": {
                "file_path": input_data.file_path,
                "format": "IGES",
                "size_estimate": "Unknown",
            },
            "file_check": file_check,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def export_stl(input_data: ExportStlInput) -> dict[str, Any]:
        """Export the active document to STL, like File > Save As with the STL type.

        Use it for 3D printing. An assembly is exported whole into the one file,
        with lightweight components resolved first. SolidWorks picks the format
        from the file's extension, so end `file_path` in .stl. The mesh follows the
        user's STL options unless this call gives `resolution`, `deviation_mm`,
        `angle_deg`, `units` or `binary`; those apply to this file only, and the
        user's options are put back afterwards. Needs: an open, active part or
        assembly. Changes: writes the file, creating its folder and replacing a file
        already there; nothing stays selected (an export writes only the selection)
        and the model is unchanged. Returns: `export` with `file_path`, `format`,
        `mesh_check` (the file read back: `triangles`, `closed`, `open_edges`,
        `non_manifold_edges`, `volume_mm3`, `size_mm`, `units`, and
        `cad_volume_mm3` and `volume_ratio` for a part; only `error` when it
        couldn't be read) and, when the call changed STL options (mesh settings,
        an assembly's one-file option), `mesh` (`applied`, `restored`). A
        `warning` says when a part's `volume_ratio` is outside 0.9-1.1: the file
        may hold only part of the model.

        Args:
            input_data: The export. `file_path` (ending in .stl) is required;
                `format_type` is not needed: the file extension decides the
                format. The mesh settings are optional.

        Returns:
            dict[str, Any]: The file written.
        """
        input_data = _file_input(normalize_input(input_data, ExportStlInput), "stl")
        settings = input_data.mesh_settings()
        native = False  # an adapter with its own export_stl
        if settings:
            require_capability(
                adapter,
                "export_stl_with_settings",
                "The active adapter cannot set STL mesh options; nothing was "
                "exported. Export without them, or set them in SolidWorks.",
            )
            result = await adapter.export_stl_with_settings(
                input_data.file_path, settings
            )
        elif hasattr(adapter, "export_stl"):
            native = True
            result = await adapter.export_stl(input_data.model_dump())
        else:
            result = await adapter.export_file(input_data.file_path, "stl")
        require_success(result, context="Failed to export STL")

        data = result.data if isinstance(result.data, dict) else {}
        mesh_check = await _stl_mesh_check(
            adapter,
            data.get("file_path") or input_data.file_path,
            settings.get("units") or data.get("units"),
            data.get("document_type"),
        )
        message = f"Exported to STL format: {input_data.file_path}"
        export: dict[str, Any] = {"file_path": input_data.file_path, "format": "STL"}
        warnings = [w for w in [_partial_mesh_warning(mesh_check)] if w]
        if native:
            export["mesh_check"] = mesh_check
            native_reply: dict[str, Any] = {
                "status": "success",
                "message": message,
                "data": result.data,
                "export": export,
                "execution_time": result.execution_time,
            }
            if warnings:
                native_reply["warning"] = " ".join(warnings)
            return native_reply
        if not settings:
            export["use_case"] = "3D printing and rapid prototyping"
            export["size_estimate"] = "Unknown"
        mesh = data.get("mesh", {} if settings else None)
        if mesh is not None:
            export["mesh"] = mesh
        export["mesh_check"] = mesh_check
        reply: dict[str, Any] = {
            "status": "success",
            "message": message,
            "export": export,
            "execution_time": result.execution_time,
        }
        if mesh is not None and mesh.get("restored") is False:
            warnings.append(
                "The file was written, but the user's STL options could not "
                "all be put back; check Tools > Options > Export > STL."
            )
        if warnings:
            reply["warning"] = " ".join(warnings)
        return reply

    @mcp.tool()
    async def export_pdf(input_data: ExportFileInput) -> dict[str, Any]:
        """Export the active document to PDF, like File > Save As with the PDF type.

        Use it to share a drawing; `generate_drawing_package` already saves a PDF with
        the drawing it creates. SolidWorks picks the format from the file's extension,
        so end `file_path` in .pdf. Needs: an open, active drawing (a part or assembly
        gives a PDF of the view). Changes: writes the file, creating its folder and
        replacing a file already there; nothing stays selected (an export writes only
        the selection) and the model is unchanged. Returns: `export` with `file_path`
        and `format`, and `file_check` (the file read back: `exists`, `size_bytes`,
        `format_ok`, `version`). A file that is missing, empty, not PDF or cut short
        is an `export_invalid` error.

        Args:
            input_data: The export. `file_path` (ending in .pdf) is required;
                `format_type` is not needed: the file extension decides the
                format.

        Returns:
            dict[str, Any]: The file written.
        """
        input_data = _file_input(input_data, "pdf")
        if hasattr(adapter, "export_pdf"):
            result = await adapter.export_pdf(input_data.model_dump())
        else:
            result = await adapter.export_file(input_data.file_path, "pdf")

        require_success(result, context="Failed to export PDF")
        file_check = _file_check(input_data.file_path, "PDF", result.execution_time)
        if hasattr(adapter, "export_pdf"):
            return {
                "status": "success",
                "message": f"Exported to PDF format: {input_data.file_path}",
                "data": result.data,
                "file_check": file_check,
                "execution_time": result.execution_time,
            }
        return {
            "status": "success",
            "message": f"Exported to PDF format: {input_data.file_path}",
            "export": {
                "file_path": input_data.file_path,
                "format": "PDF",
                "use_case": "Documentation and sharing",
                "size_estimate": "Unknown",
            },
            "file_check": file_check,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def export_dwg(input_data: ExportFileInput) -> dict[str, Any]:
        """Export the active document to DWG, like File > Save As with the DWG type.

        Use it to hand a drawing to AutoCAD users. SolidWorks picks the format from the
        file's extension, so end `file_path` in .dwg (or .dxf for DXF). Needs: an open,
        active drawing. Changes: writes the file, creating its folder and replacing a
        file already there; nothing stays selected (an export writes only the
        selection) and the model is unchanged. Returns: `export` with `file_path` and
        `format`, and `file_check` (the file read back: `exists`, `size_bytes`,
        `format_ok`, and `version` and `release` for a DWG). A file that is missing,
        empty or not DWG (or DXF) is an `export_invalid` error.

        Args:
            input_data: The export. `file_path` (ending in .dwg or .dxf) is required;
                `format_type` is not needed: the file extension decides the
                format.

        Returns:
            dict[str, Any]: The file written.
        """
        input_data = _file_input(input_data, "dwg")
        if hasattr(adapter, "export_dwg"):
            result = await adapter.export_dwg(input_data.model_dump())
        else:
            result = await adapter.export_file(input_data.file_path, "dwg")

        require_success(result, context="Failed to export DWG")
        file_check = _file_check(input_data.file_path, "DWG", result.execution_time)
        if hasattr(adapter, "export_dwg"):
            return {
                "status": "success",
                "message": f"Exported to DWG format: {input_data.file_path}",
                "data": result.data,
                "file_check": file_check,
                "execution_time": result.execution_time,
            }
        return {
            "status": "success",
            "message": f"Exported to DWG format: {input_data.file_path}",
            "export": {
                "file_path": input_data.file_path,
                "format": "DWG",
                "use_case": "AutoCAD compatibility",
                "size_estimate": "Unknown",
            },
            "file_check": file_check,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def export_image(input_data: ExportImageInput) -> dict[str, Any]:
        """Save a picture of the active model, in a standard view or as it is on screen, as PNG or JPG.

        Use it to show a result, or to check a model's shape after a change. A standard
        view (front, back, left, right, top, bottom, isometric, dimetric, trimetric)
        turns the model to it and zooms so the whole model fits. `current` keeps the
        view exactly as it is, zoom included, so a close-up set up first comes out as
        that close-up. `zoom_to_fit` overrides the zoom either way; `zoom_box_mm`
        zooms to a box of the model (model coordinates). `hide` hides planes,
        sketches and the like, and `section` cuts the model on a plane (a snap inside
        its socket), both for this picture only. The picture keeps the graphics
        area's shape, padded out to `width` x `height`. Needs: an open, active
        document. Changes: writes the image file; a standard view or a zoom also
        turns or zooms the model on screen. Returns: `data` with `file_path`,
        `format`, `dimensions` (the size written), `requested_dimensions`, `view`,
        `zoomed_to_fit`, `zoom_box_mm`, `zoom_box_view_mm`, `hidden`, `section` and
        `window_state`, and `file_check` (the picture read back); a bad file is an
        `export_invalid` error.

        Args:
            input_data: The image. `file_path` (or `output_path`) is needed; the
                format follows its extension. `width`/`height` (or `resolution` as
                "1920x1080") and `view_orientation` (isometric) have defaults;
                `zoom_to_fit` defaults to yes except for `current`; `section` is
                off.

        Returns:
            dict[str, Any]: The image written.
        """
        input_data = normalize_input(input_data, ExportImageInput)
        if hasattr(adapter, "export_image"):
            result = await adapter.export_image(input_data.model_dump())
            require_success(result, "Failed to export image")
            data = result.data if isinstance(result.data, dict) else {}
            reply: dict[str, Any] = {
                "status": "success",
                "message": f"Exported image: {input_data.file_path}",
                "data": result.data,
                "file_check": _file_check(
                    data.get("file_path") or input_data.file_path,
                    input_data.format_type,
                    result.execution_time,
                ),
                "execution_time": result.execution_time,
            }
            section = data.get("section")
            if isinstance(section, dict) and section.get("removed") is False:
                reply["warning"] = (
                    f"The section on {section.get('plane')} could not be taken off, "
                    "so the model still shows it cut. Take it off in SolidWorks "
                    "(View > Display > Section View)."
                )
            return reply

        if hasattr(adapter, "export_file") and input_data.file_path:
            result = await adapter.export_file(  # type: ignore[assignment]
                input_data.file_path, input_data.format_type
            )
            require_success(result, "Failed to export image")
            file_check = _file_check(
                input_data.file_path, input_data.format_type, result.execution_time
            )
            return {
                "status": "success",
                "message": f"Exported image: {input_data.file_path}",
                "file_check": file_check,
                "export": {
                    "file_path": input_data.file_path,
                    "format": input_data.format_type.upper(),
                    "dimensions": f"{input_data.width}x{input_data.height}",
                    "view": input_data.view_orientation,
                    "use_case": "Documentation and presentations",
                },
                "execution_time": result.execution_time,
            }

        raise UnsupportedOperationError(
            "Active adapter does not support export_image or "
            "export_file; no image was exported."
        )

    @mcp.tool()
    async def batch_export(input_data: BatchExportInput) -> dict[str, Any]:
        """Export every matching SolidWorks file in a folder to one format.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. Today, open
        each file with `open_model` and use an `export_*` tool. Changes: once available,
        writes one exported file per source file. Returns: once available, a per-file
        result under `data`.

        Args:
            input_data: The batch. `source_directory` and `output_directory` are
                required; `format_type`, `file_pattern` and `recursive` have
                defaults.

        Returns:
            dict[str, Any]: The results, or a `not_supported` error.
        """
        require_capability(
            adapter,
            "batch_export",
            "Active adapter does not support batch_export; no files were exported.",
        )
        result = await adapter.batch_export(input_data.model_dump())
        require_success(result, "Batch export failed")
        return {
            "status": "success",
            "message": f"Batch export completed to {input_data.format_type} format",
            "data": result.data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def export_assembly_parts_stl(
        input_data: ExportAssemblyPartsStlInput,
    ) -> dict[str, Any]:
        """Export every part of the active assembly to its own STL file, one per part.

        Use it to send an assembly's parts to a 3D printer separately; for the whole
        assembly in one file, use `export_stl`. A part used several times is written
        once; files are named by `name_pattern`. Each part is exported as `export_stl`
        does, from its own window (opened and closed again for a part only the assembly
        had loaded), and read back. Mesh settings apply to these files only; the user's
        STL options are put back. A part whose components use another configuration
        than the part's active one is skipped. Needs: an open, active assembly.
        Changes: writes the files, replacing ones already there; the assembly is active
        again afterwards. Returns: `files` (each `part`, `file_path`, `instances` and
        `mesh_check`), `skipped` and `failed`.

        Args:
            input_data: The export. `folder` is required; `name_pattern`
                defaults to "{name}.stl"; the mesh settings are optional.

        Returns:
            dict[str, Any]: The files written.
        """
        input_data = normalize_input(input_data, ExportAssemblyPartsStlInput)
        require_capability(
            adapter,
            "list_assembly_parts",
            "The active adapter can't list an assembly's parts; nothing was exported.",
        )
        settings = input_data.mesh_settings()
        listed = await adapter.list_assembly_parts()
        require_success(listed, context="Failed to list the assembly's parts")
        data = listed.data if isinstance(listed.data, dict) else {}
        assembly = data.get("assembly") or {}
        parts = list(data.get("parts") or [])
        if not parts:
            raise ToolOperationError(
                f"No parts found in {assembly.get('title') or 'the assembly'}.",
                listed.execution_time,
            )
        folder = os.path.abspath(input_data.folder)
        plan, skipped = _plan_part_files(
            parts, input_data.name_pattern, assembly, folder
        )
        os.makedirs(folder, exist_ok=True)
        files: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        try:
            for part, target in plan:
                outcome = await _export_part(adapter, part, target, settings)
                (files if "error" not in outcome else failed).append(outcome)
        finally:
            back = await adapter.activate_document(
                assembly.get("path") or assembly.get("title") or ""
            )
        reply_data = {"files": files, "skipped": skipped, "failed": failed}
        if not files:
            raise ToolOperationError(
                "No part was exported; see `failed` and `skipped` in `data`.",
                listed.execution_time,
                reply_data,
            )
        reply: dict[str, Any] = {
            "status": "success",
            "message": f"Exported {len(files)} part(s) to STL in {folder}",
            **reply_data,
            "execution_time": listed.execution_time,
        }
        notes = [w for f in files if (w := f.get("warning"))]
        if failed:
            notes.append(
                f"{len(failed)} part(s) failed: "
                + ", ".join(f"{f['part']} ({f['error']})" for f in failed)
                + "."
            )
        if skipped:
            notes.append(f"{len(skipped)} part(s) were skipped; see `skipped`.")
        if not back.is_success:
            notes.append(f"The assembly could not be made active again: {back.error}")
        if notes:
            reply["warning"] = " ".join(notes)
        return reply

    tool_count = 8  # Number of tools registered
    return tool_count


def _plan_part_files(
    parts: list[dict[str, Any]],
    pattern: str,
    assembly: dict[str, Any],
    folder: str,
) -> tuple[list[tuple[dict[str, Any], str]], list[dict[str, Any]]]:
    """Each part to export with its file, and the parts skipped with why.

    Raises:
        InvalidInputError: Two parts would get the same file name; nothing
            is exported.
    """
    stem = os.path.splitext(
        os.path.basename(assembly.get("path") or assembly.get("title") or "")
    )[0]
    plan: list[tuple[dict[str, Any], str]] = []
    skipped: list[dict[str, Any]] = []
    for part in parts:
        label = part.get("title") or part.get("path") or "?"
        config, active = part.get("configuration"), part.get("active_configuration")
        if part.get("virtual"):
            skipped.append({"part": label, "reason": "virtual: it has no file"})
        elif config and active and config != active:
            skipped.append(
                {
                    "part": label,
                    "reason": (
                        f"the assembly uses configuration {config!r}, the part's "
                        f"active one is {active!r}; activate it and use export_stl"
                    ),
                }
            )
        else:
            name = os.path.splitext(os.path.basename(part.get("path") or label))[0]
            file_name = pattern.format(
                name=name, config=config or "", assembly=stem, index=len(plan) + 1
            )
            plan.append((part, os.path.join(folder, file_name)))
    seen: dict[str, str] = {}
    for part, target in plan:
        key = os.path.normcase(target)
        if key in seen:
            raise InvalidInputError(
                f"name_pattern gives {os.path.basename(target)} to both "
                f"{seen[key]} and {part.get('title')}; add {{config}} or {{index}}."
            )
        seen[key] = str(part.get("title"))
    return plan, skipped


async def _export_part(
    adapter: Any, part: dict[str, Any], target: str, settings: dict[str, Any]
) -> dict[str, Any]:
    """Export one part to ``target`` from its own window, and read the file back.

    Returns:
        dict[str, Any]: ``part``, ``file_path``, ``instances``,
        ``configuration``, ``mesh_check`` and maybe ``warning``; or ``part``
        and ``error``.
    """
    label = part.get("title") or part.get("path")
    activated = await adapter.activate_document(part.get("path") or label)
    if not activated.is_success:
        return {"part": label, "error": activated.error or "could not activate it"}
    opened = bool((activated.data or {}).get("opened_window"))
    try:
        if settings:
            result = await adapter.export_stl_with_settings(target, settings)
        else:
            result = await adapter.export_file(target, "stl")
        if not result.is_success:
            return {"part": label, "error": result.error or "the export failed"}
        data = result.data if isinstance(result.data, dict) else {}
        mesh_check = await _stl_mesh_check(
            adapter, target, settings.get("units") or data.get("units"), "part"
        )
    finally:
        if opened:
            await adapter.close_model()
    outcome: dict[str, Any] = {
        "part": label,
        "file_path": target,
        "instances": part.get("instances"),
        "configuration": part.get("configuration"),
        "mesh_check": mesh_check,
    }
    notes = [w for w in [_partial_mesh_warning(mesh_check)] if w]
    mesh = data.get("mesh")
    if isinstance(mesh, dict) and mesh.get("restored") is False:
        notes.append(
            "The user's STL options could not all be put back; check Tools > "
            "Options > Export > STL."
        )
    if notes:
        outcome["warning"] = f"{label}: " + " ".join(notes)
    return outcome
