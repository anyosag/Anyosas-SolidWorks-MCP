"""``enable_tool_groups`` and ``describe_tool_inputs``: the tool list, on demand."""

from __future__ import annotations

from typing import Any

import mcp.types
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.transforms import Visibility

from solidworks_mcp.tools import register_tools
from solidworks_mcp.tools.schema_compaction import (
    CompactToolSchemas,
    shorten_description,
)
from solidworks_mcp.tools.server_admin import group_names
from solidworks_mcp.tools.tool_boundary import InvalidInputError
from solidworks_mcp.tools.tool_groups import GROUPS, group_tag


async def _server(adapter: Any, config: Any = None) -> FastMCP:
    """All tools, listing only core and with planned tools hidden."""
    server = FastMCP("tool-list")
    await register_tools(server, adapter, config or {})
    server.add_transform(Visibility(False, tags={"level:planned"}))
    server.add_transform(
        Visibility(False, tags={group_tag(g) for g in GROUPS if g != "core"})
    )
    server.add_transform(CompactToolSchemas())
    return server


class _Notices:
    def __init__(self) -> None:
        self.tool_list_changed = 0

    async def __call__(self, message: Any) -> None:
        root = getattr(message, "root", None)
        if isinstance(root, mcp.types.ToolListChangedNotification):
            self.tool_list_changed += 1


def test_group_names_are_checked() -> None:
    assert group_names(["Bodies", "bodies", " drawings "]) == ["bodies", "drawings"]
    assert group_names(["all"]) == list(GROUPS)
    with pytest.raises(InvalidInputError, match="no tool group named widgets"):
        group_names(["widgets"])
    with pytest.raises(InvalidInputError, match="nothing"):
        group_names([" "])


@pytest.mark.asyncio
async def test_a_group_is_turned_on_and_the_client_is_told(mock_adapter) -> None:
    server = await _server(mock_adapter)
    notices = _Notices()

    async with Client(server, message_handler=notices) as client:
        before = {t.name for t in await client.list_tools()}
        reply = await client.call_tool(
            "enable_tool_groups", {"input_data": {"groups": ["bodies"]}}
        )
        after = {t.name for t in await client.list_tools()}

    data = reply.structured_content
    assert "combine_bodies" not in before and "create_part" in before
    assert data["status"] == "success", data
    assert data["data"]["enabled"] == ["bodies"]
    assert "combine_bodies" in data["data"]["tools"]
    assert set(data["data"]["tools"]) == after - before
    assert notices.tool_list_changed >= 1
    # A planned tool stays hidden though its group is on.
    assert "analyze_geometry" not in after


@pytest.mark.asyncio
async def test_planned_tools_follow_the_server_setting(mock_adapter) -> None:
    class _Config:
        show_planned_tools = True

    server = FastMCP("tool-list")
    await register_tools(server, mock_adapter, _Config())
    server.add_transform(Visibility(False, tags={group_tag("analysis")}))

    async with Client(server) as client:
        await client.call_tool(
            "enable_tool_groups", {"input_data": {"groups": ["analysis"]}}
        )
        names = {t.name for t in await client.list_tools()}

    assert "analyze_geometry" in names


@pytest.mark.asyncio
async def test_an_unknown_group_is_invalid_input(mock_adapter) -> None:
    server = await _server(mock_adapter)

    async with Client(server) as client:
        reply = await client.call_tool(
            "enable_tool_groups",
            {"input_data": {"groups": ["widgets"]}},
            raise_on_error=False,
        )

    assert reply.structured_content["error_type"] == "invalid_input"


@pytest.mark.asyncio
async def test_without_a_session_it_says_to_use_the_environment(mock_adapter) -> None:
    server = await _server(mock_adapter)
    tool = next(
        t
        for t in await server.local_provider.list_tools()
        if t.name == "enable_tool_groups"
    )

    reply = await tool.fn({"groups": ["bodies"]})

    assert reply["status"] == "error"
    assert "SOLIDWORKS_MCP_TOOL_GROUPS" in reply["message"]


@pytest.mark.asyncio
async def test_describe_gives_the_whole_descriptions(mock_adapter) -> None:
    server = await _server(mock_adapter)

    async with Client(server) as client:
        listed = {t.name for t in await client.list_tools()}
        reply = await client.call_tool(
            "describe_tool_inputs",
            {"input_data": {"tools": ["select_edges", "combine_bodies"]}},
        )

    data = reply.structured_content
    assert data["status"] == "success", data
    schemas = data["data"]["tools"]
    # Tools whose group is off are described too.
    assert "select_edges" not in listed
    assert set(schemas) == {"select_edges", "combine_bodies"}
    full = schemas["select_edges"]["properties"]["input_data"]["properties"]
    text = full["feature"]["description"]
    assert "at most one of" in text
    assert shorten_description(text).endswith(" ...")


@pytest.mark.asyncio
async def test_describe_refuses_an_unknown_tool(mock_adapter) -> None:
    server = await _server(mock_adapter)

    async with Client(server) as client:
        reply = await client.call_tool(
            "describe_tool_inputs",
            {"input_data": {"tools": ["make_coffee"]}},
            raise_on_error=False,
        )

    assert reply.structured_content["error_type"] == "invalid_input"
    assert "make_coffee" in reply.structured_content["message"]
