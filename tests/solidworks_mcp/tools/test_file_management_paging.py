"""``list_features`` returns a long tree a page at a time, after its filters."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.file_management import register_file_management_tools


async def _list_features(mcp_server: Any, adapter: Any, rows: list[dict]) -> Any:
    adapter.list_features = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=rows)
    )
    await register_file_management_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}["list_features"]


def _rows(count: int) -> list[dict[str, Any]]:
    return [
        {"name": f"Cut{i}", "type": "Cut", "suppressed": False} for i in range(count)
    ]


async def test_a_long_tree_is_paged(mcp_server, mock_adapter) -> None:
    tool = await _list_features(mcp_server, mock_adapter, _rows(130))

    first = await tool({})
    second = await tool({"offset": 50, "limit": 60})

    assert [f["name"] for f in first["features"]] == [f"Cut{i}" for i in range(50)]
    assert first["count"] == 50 and first["total"] == 130
    assert first["truncated"] is True
    assert (first["full_count"], first["next_offset"]) == (130, 50)
    assert second["features"][0]["name"] == "Cut50"
    assert second["next_offset"] == 110


async def test_filters_apply_before_the_page(mcp_server, mock_adapter) -> None:
    tool = await _list_features(mcp_server, mock_adapter, _rows(130))

    reply = await tool({"last": 5})

    assert [f["name"] for f in reply["features"]] == [
        f"Cut{i}" for i in range(125, 130)
    ]
    assert "truncated" not in reply
