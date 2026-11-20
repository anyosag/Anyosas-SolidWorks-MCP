"""``list_components`` pages a long list; ``select_edges`` says when it described fewer."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.modeling import register_modeling_tools


def _ok(data: Any) -> AsyncMock:
    return AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
    )


async def _tools(mcp_server: Any, adapter: Any) -> dict[str, Any]:
    await register_modeling_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}


async def test_components_come_fifty_at_a_time(mcp_server, mock_adapter) -> None:
    mock_adapter.list_components = _ok([f"part-{i}" for i in range(120)])
    tool = (await _tools(mcp_server, mock_adapter))["list_components"]

    first = await tool()
    last = await tool({"offset": 100})

    assert first["message"] == "120 component(s) in the assembly"
    assert first["components"] == [f"part-{i}" for i in range(50)]
    assert first["truncated"] is True
    assert (first["full_count"], first["next_offset"]) == (120, 50)
    assert last["components"] == [f"part-{i}" for i in range(100, 120)]
    assert "truncated" not in last


async def test_a_short_component_list_is_unchanged(mcp_server, mock_adapter) -> None:
    mock_adapter.list_components = _ok(["a-1", "b-1"])
    tool = (await _tools(mcp_server, mock_adapter))["list_components"]

    reply = await tool()

    assert reply["components"] == ["a-1", "b-1"]
    assert "truncated" not in reply and "full_count" not in reply


async def test_select_edges_says_when_it_described_fewer(
    mcp_server, mock_adapter
) -> None:
    edges = [{"index": i} for i in range(50)]
    mock_adapter.select_edges = _ok(
        {"source": "the part", "edges": edges, "selected_edges": 80}
    )
    tool = (await _tools(mcp_server, mock_adapter))["select_edges"]

    reply = await tool({})

    assert reply["data"]["truncated"] is True
    assert reply["data"]["full_count"] == 80
    assert len(reply["data"]["edges"]) == 50


async def test_select_edges_all_described(mcp_server, mock_adapter) -> None:
    mock_adapter.select_edges = _ok(
        {"source": "the part", "edges": [{"index": 0}], "selected_edges": 1}
    )
    tool = (await _tools(mcp_server, mock_adapter))["select_edges"]

    reply = await tool({})

    assert "truncated" not in reply["data"]
