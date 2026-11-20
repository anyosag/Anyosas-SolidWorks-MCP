"""Tools that look after the server itself rather than a SolidWorks model.

``reset_circuit_breaker`` re-opens operations the server's circuit breaker has
paused after repeated failures (#208). ``set_solidworks_window`` minimizes or
restores the SolidWorks window (keep-solidworks-minimized).
``enable_tool_groups`` lists more tool groups mid-session (``tool_groups.py``),
and ``describe_tool_inputs`` gives a tool's input fields in full, since the
listed schemas cut each field's description short (``schema_compaction.py``).
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import Context, FastMCP
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

from ..adapters.base import SolidWorksAdapter
from .input_compat import CompatInput
from .schema_compaction import compact_schema
from .tool_boundary import (
    InvalidInputError,
    ToolOperationError,
    normalize_input,
    require_success,
    with_tool_boundary,
)
from .tool_groups import GROUPS, group_tag

#: The most tools one ``describe_tool_inputs`` call describes.
MAX_DESCRIBED_TOOLS = 10


class ResetCircuitBreakerInput(CompatInput):
    """Input schema for resetting circuit breakers.

    Attributes:
        operation (str | None): The operation to reset, e.g. ``create_extrusion``.
    """

    operation: str | None = Field(
        default=None,
        description=(
            "The operation to reset, as named in the circuit_open error "
            "(e.g. create_extrusion); leave it out to reset every operation"
        ),
    )


class SetSolidWorksWindowInput(CompatInput):
    """Input schema for minimizing or restoring the SolidWorks window.

    Attributes:
        state (str): ``minimize`` or ``restore``.
    """

    state: Literal["minimize", "restore"] = Field(
        description=(
            "minimize: hide SolidWorks in the taskbar and keep it there while "
            "tools run; restore: bring it back up for the user to see"
        ),
    )


class EnableToolGroupsInput(CompatInput):
    """Input schema for turning tool groups on.

    Attributes:
        groups (list[str]): The groups to list, or ``["all"]``.
    """

    groups: list[str] = Field(
        min_length=1,
        description="Groups to turn on, e.g. ['bodies', 'drawings'], or ['all']",
    )


class DescribeToolInputsInput(CompatInput):
    """Input schema for describing tools' inputs in full.

    Attributes:
        tools (list[str]): The tool names.
    """

    tools: list[str] = Field(
        min_length=1,
        max_length=MAX_DESCRIBED_TOOLS,
        description="Tool names, e.g. ['select_edges'] (up to 10)",
    )


def group_names(groups: list[str]) -> list[str]:
    """The groups asked for, checked; ``all`` means every group.

    Args:
        groups: Group names as given.

    Returns:
        list[str]: The groups, in the order given, without repeats.

    Raises:
        InvalidInputError: For a name that is no group.
    """
    wanted = [g.strip().lower() for g in groups if g and g.strip()]
    if "all" in wanted:
        return list(GROUPS)
    unknown = [g for g in wanted if g not in GROUPS]
    if unknown or not wanted:
        raise InvalidInputError(
            f"no tool group named {', '.join(unknown) or 'nothing'}; the groups "
            f"are {', '.join(GROUPS)} (or all)"
        )
    return list(dict.fromkeys(wanted))


async def register_server_admin_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: dict[str, Any]
) -> int:
    """Register the server-administration tools with FastMCP.

    Args:
        mcp (FastMCP): The MCP server.
        adapter (SolidWorksAdapter): Adapter used by every tool.
        config (dict[str, Any]): Server configuration (unused).

    Returns:
        int: Number of tools registered.
    """
    mcp = with_tool_boundary(mcp, adapter)
    show_planned = bool(getattr(config, "show_planned_tools", False))

    @mcp.tool()
    async def reset_circuit_breaker(
        input_data: ResetCircuitBreakerInput | None = None,
    ) -> dict[str, Any]:
        """Let calls through again to operations the server paused after repeated failures.

        Use it when a tool returns `error_type: circuit_open` and you have fixed what
        made the calls fail (a missing sketch, a wrong reference): without a reset, the
        operation stays paused until its timeout. Resetting does not fix the cause; a
        call that fails again counts towards a new pause. Changes: only the server's
        own state; no SolidWorks document is touched. Returns: `data` with `reset`, the
        operations that were paused (empty when none were).

        Args:
            input_data: Optional: `operation` resets only that operation; leave it out
                to reset every one.

        Returns:
            dict[str, Any]: The operations that were reset.
        """
        input_data = normalize_input(input_data, ResetCircuitBreakerInput)
        result = await adapter.reset_circuit_breakers(input_data.operation)
        require_success(result, context="Failed to reset the circuit breaker")
        names = list((result.data or {}).get("reset", []))
        message = f"Reset {', '.join(names)}" if names else "No operation was paused"
        return {"status": "success", "message": message, "data": {"reset": names}}

    @mcp.tool()
    async def set_solidworks_window(
        input_data: SetSolidWorksWindowInput,
    ) -> dict[str, Any]:
        """Minimize or restore the SolidWorks window, so the user can work in other apps while you model.

        Use `minimize` when the user asks to keep SolidWorks out of the way: while it
        is minimized, every tool keeps it minimized and hands the keyboard focus back,
        and `export_image` still renders. Use `restore` when the user wants to look at
        the model. The server never minimizes a window the user restored. Changes:
        only the window; no document is touched. Returns: `data` with `window_state`
        (`minimized`, `normal`, `maximized`, `hidden` or `unknown`).

        Args:
            input_data: `state` is required: `minimize` or `restore`.

        Returns:
            dict[str, Any]: The window's state afterwards.
        """
        input_data = normalize_input(input_data, SetSolidWorksWindowInput)
        result = await adapter.set_window_state(input_data.state)
        require_success(result, context="Failed to change the SolidWorks window")
        state = (result.data or {}).get("window_state", "unknown")
        return {
            "status": "success",
            "message": f"SolidWorks window is {state}",
            "data": {"window_state": state},
        }

    @mcp.tool()
    async def enable_tool_groups(
        input_data: EnableToolGroupsInput, ctx: Context | None = None
    ) -> dict[str, Any]:
        """Turn on more tool groups for this session, so their tools appear in your tool list.

        The server may start with only some groups listed, to save context
        (`SOLIDWORKS_MCP_TOOL_GROUPS`, e.g. `core,bodies`). The groups: core
        (always on), sketching, features, assembly, bodies, drawings, analysis,
        engineering, export, files, vba, docs, templates; or all. Use it when the
        tool you need is missing from your list. The server then sends
        `tools/list_changed`; if your client doesn't refresh its list, ask the
        user to set `SOLIDWORKS_MCP_TOOL_GROUPS` and restart the server. Changes:
        only which tools this session lists. Returns: `enabled` groups and the
        `tools` added to the list.

        Args:
            input_data: `groups` is required: the group names, or ["all"].

        Returns:
            dict[str, Any]: The groups turned on and the tools added.
        """
        input_data = normalize_input(input_data, EnableToolGroupsInput)
        groups = group_names(input_data.groups)
        if ctx is None:
            raise ToolOperationError(
                "enable_tool_groups needs an MCP session; set "
                "SOLIDWORKS_MCP_TOOL_GROUPS and restart the server instead"
            )
        before = {tool.name for tool in await mcp.list_tools()}
        await ctx.enable_components(
            tags={group_tag(group) for group in groups}, components={"tool"}
        )
        if not show_planned:
            await ctx.disable_components(tags={"level:planned"}, components={"tool"})
        added = sorted({tool.name for tool in await mcp.list_tools()} - before)
        return {
            "status": "success",
            "message": f"Turned on {', '.join(groups)}: {len(added)} more tools listed",
            "data": {"enabled": groups, "tools": added},
        }

    @mcp.tool()
    async def describe_tool_inputs(
        input_data: DescribeToolInputsInput,
    ) -> dict[str, Any]:
        """Give the full input schema of one or more tools, with each field's whole description.

        The tool list cuts each field's description to its first sentence or two,
        to save context; the rest (limits, examples, how fields combine) is here.
        Use it when a short description leaves you unsure, or after an
        `invalid_input` error. Works for tools in groups that are off, too.
        Read-only. Returns: `tools`, each tool's input schema by name.

        Args:
            input_data: `tools` is required: up to 10 tool names.

        Returns:
            dict[str, Any]: Each tool's full input schema.
        """
        input_data = normalize_input(input_data, DescribeToolInputsInput)
        registered = {tool.name: tool for tool in await mcp.local_provider.list_tools()}
        unknown = [name for name in input_data.tools if name not in registered]
        if unknown:
            raise InvalidInputError(f"no tool named {', '.join(unknown)}")
        schemas = {
            name: compact_schema(
                dereference_refs(registered[name].parameters), limit=None
            )
            for name in input_data.tools
        }
        return {
            "status": "success",
            "message": f"Input schema of {', '.join(input_data.tools)}",
            "data": {"tools": schemas},
        }

    return 4
