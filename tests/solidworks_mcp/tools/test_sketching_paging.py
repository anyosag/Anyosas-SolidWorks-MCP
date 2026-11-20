"""``get_sketch`` returns a big sketch's segments a page at a time."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.sketching import register_sketching_tools


async def _get_sketch(mcp_server: Any, adapter: Any, data: dict[str, Any]) -> Any:
    adapter.get_sketch = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
    )
    await register_sketching_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}["get_sketch"]


async def test_segments_are_paged_and_the_message_counts_all(
    mcp_server, mock_adapter
) -> None:
    segments = [{"index": i, "type": "line"} for i in range(75)]
    tool = await _get_sketch(
        mcp_server, mock_adapter, {"plane": "Top Plane", "segments": segments}
    )

    first = await tool({"name": "Sketch1"})
    rest = await tool({"name": "Sketch1", "offset": first["next_offset"]})

    assert first["message"] == "Sketch1: 75 segment(s) on Top Plane"
    assert len(first["sketch"]["segments"]) == 50
    assert first["truncated"] is True and first["full_count"] == 75
    assert [s["index"] for s in rest["sketch"]["segments"]] == list(range(50, 75))
    assert "truncated" not in rest


async def test_a_summary_has_no_segments_to_page(mcp_server, mock_adapter) -> None:
    tool = await _get_sketch(
        mcp_server, mock_adapter, {"segment_counts": {"line": 200}}
    )

    reply = await tool({"name": "Sketch1", "detail": "summary"})

    assert reply["message"] == "Sketch1: 200 segment(s)"
    assert "truncated" not in reply
