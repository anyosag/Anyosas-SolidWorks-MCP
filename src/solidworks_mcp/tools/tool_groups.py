"""Tool groups: which tools a session lists, so an agent pays only for what it uses.

Every tool belongs to exactly one group. ``core`` holds what a normal part
build needs (documents, basic sketching, extrude/cut/revolve, fillet and
chamfer, the checks, the common exports and error recovery); the other
groups follow the tool families. ``ToolRegistrar`` tags each tool
``group:<name>`` at registration.

A session lists every group by default. ``SOLIDWORKS_MCP_TOOL_GROUPS``
(e.g. ``core,bodies``) lists only those groups, ``core`` always included,
and the ``enable_tool_groups`` tool turns more on mid-session.
"""

from __future__ import annotations

from collections.abc import Iterable

#: The group every session lists.
CORE = "core"

#: Each group and what it holds, in the order agents see them.
GROUPS: dict[str, str] = {
    CORE: "open/create/save, basic sketching, extrude/cut/revolve, fillet/chamfer, "
    "checks, STEP/STL/image export, workflows, error recovery",
    "sketching": "splines, ellipses, text, outlines, many circles/lines/rectangles "
    "in one call, sketch patterns, mirror, offset",
    "features": "loft, sweep, patterns, mirror, holes, threads, reference geometry, "
    "dimensions, "
    "editing, suppress/rollback, edge selection by rule",
    "assembly": "assemblies: components, mates, transforms, interference, clearance",
    "bodies": "multibody parts: combine, split, move, save bodies as parts",
    "drawings": "drawings: views, dimensions, notes, GD&T, BOM, PDF/DWG",
    "analysis": "mass properties, geometry queries, wall thickness, printability",
    "engineering": "materials, custom properties, equations, model dimensions",
    "export": "IGES, glTF for Blender, flat patterns of assembly parts",
    "files": "loading, references, configurations, file properties, moving files",
    "vba": "VBA generation, macros, API snippets",
    "docs": "SolidWorks API documentation lookup",
    "templates": "template comparison",
}

#: Tools in ``core``, whatever their family.
CORE_TOOLS = frozenset(
    {
        # documents
        "create_part",
        "open_model",
        "close_model",
        "save_part",
        "save_file",
        "save_as",
        "get_model_info",
        "list_open_documents",
        "activate_document",
        "list_features",
        # sketching basics
        "create_sketch",
        "exit_sketch",
        "edit_sketch",
        "get_sketch",
        "add_line",
        "add_rectangle",
        "add_circle",
        "add_arc",
        "add_centerline",
        "add_polygon",
        "add_sketch_dimension",
        "add_sketch_constraint",
        # features
        "create_extrusion",
        "create_cut_extrude",
        "create_revolve",
        "create_cut_revolve",
        "add_fillet",
        "add_chamfer",
        "select_face_edges",
        "delete_feature",
        # checks and recovery
        "verify_model",
        "rebuild_model",
        "undo",
        "reset_circuit_breaker",
        "set_solidworks_window",
        "enable_tool_groups",
        "describe_tool_inputs",
        # exports and batching
        "export_step",
        "export_stl",
        "export_image",
        "execute_workflow",
    }
)

#: The group of each tool family, for tools outside ``core``.
FAMILY_GROUPS: dict[str, str] = {
    "analysis": "analysis",
    "automation": "files",
    "bodies": "bodies",
    "docs_discovery": "docs",
    "drawing": "drawings",
    "drawing_analysis": "drawings",
    "drawing_automation": "drawings",
    "engineering_data": "engineering",
    "export": "export",
    "file_management": "files",
    "macro_recording": "vba",
    "modeling": "features",
    "server_admin": CORE,
    "sketching": "sketching",
    "template_management": "templates",
    "vba_generation": "vba",
}

#: The tool families (module names), as tagged at registration.
FAMILIES = frozenset(FAMILY_GROUPS)

#: Tools whose group isn't their family's.
TOOL_GROUPS: dict[str, str] = {
    # assembly
    "create_assembly": "assembly",
    "insert_component": "assembly",
    "add_mate": "assembly",
    "list_components": "assembly",
    "set_component_state": "assembly",
    "set_component_transform": "assembly",
    "set_component_visibility": "assembly",
    "measure_clearance": "assembly",
    "load_assembly": "assembly",
    "save_assembly": "assembly",
    "pack_and_go_assembly": "assembly",
    "export_assembly_parts_stl": "assembly",
    "check_interference": "assembly",
    "rotation_clearance": "assembly",
    # bodies kept in other modules
    "save_body_as_part": "bodies",
    "resplit_bodies": "bodies",
    "list_save_bodies": "bodies",
    "edit_save_bodies": "bodies",
    # drawings kept in other modules
    "create_drawing": "drawings",
    "export_dwg": "drawings",
    "export_pdf": "drawings",
    # engineering
    "measure_entities": "engineering",
    "export_flat_pattern": "engineering",
    # vba kept in other modules
    "generate_vba_code": "vba",
    "automation_start_macro_recording": "vba",
    "automation_stop_macro_recording": "vba",
    "create_template": "templates",
}


def group_for(name: str, family: str) -> str:
    """The group of the tool ``name`` from the module ``family``.

    Args:
        name: The tool's name.
        family: Its module's name (see ``tool_boundary.tool_family``).

    Returns:
        str: One of :data:`GROUPS`. A family without a group (a tool defined
        outside the tool modules, as tests do) goes to ``core``, so it is
        always listed; ``test_tool_groups`` checks every real family has one.
    """
    if name in CORE_TOOLS:
        return CORE
    if name in TOOL_GROUPS:
        return TOOL_GROUPS[name]
    return FAMILY_GROUPS.get(family, CORE)


def group_tag(group: str) -> str:
    """The tag a tool of ``group`` carries."""
    return f"group:{group}"


def parse_groups(
    value: str | Iterable[str] | None,
) -> tuple[set[str] | None, list[str]]:
    """Read a group selection such as ``"core,bodies"``.

    Args:
        value: Comma-separated group names, a list of them, ``"all"``, or
            None (all).

    Returns:
        tuple: The groups to list (``core`` always among them), or None for
        every group; and the names given that are no group.
    """
    if value is None:
        return None, []
    names = value.split(",") if isinstance(value, str) else list(value)
    chosen = {n.strip().lower() for n in names if n and n.strip()}
    if not chosen or "all" in chosen:
        return None, []
    unknown = sorted(chosen - set(GROUPS))
    return (chosen & set(GROUPS)) | {CORE}, unknown


def hidden_groups(selected: set[str] | None) -> set[str]:
    """The groups a selection leaves out of the tool list."""
    return set() if selected is None else set(GROUPS) - selected
