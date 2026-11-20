"""The ``query_geometry`` tool: what lies near a point, in words, and its warnings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import QueryGeometryInput, register_analysis_tools


@pytest.fixture
async def tools(mock_config: Any) -> tuple[MockSolidWorksAdapter, Any]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("query-geometry")
    await register_analysis_tools(mcp, adapter, mock_config)
    fn = next(t.fn for t in await mcp.list_tools() if t.name == "query_geometry")
    return adapter, fn


async def test_a_corner_of_the_mock_block(tools: Any) -> None:
    _adapter, fn = tools

    reply = await fn({"point_mm": [10, 10, 10], "radius_mm": 0.5})

    assert reply["status"] == "success", reply
    assert reply["message"] == (
        "Within 0.5 mm of [10.0, 10.0, 10.0] mm: 3 face(s) (plane) and 3 edge(s) (line)"
    )
    assert "warning" not in reply  # the experimental note is in the description


async def test_a_pick_answer_and_a_cut_list_say_so(tools: Any) -> None:
    adapter, fn = tools
    mock = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "point_mm": [1.0, 2.0, 3.0],
                "complete": False,
                "faces": [{"kind": "cylinder"}],
                "faces_found": 4,
            },
        )
    )
    adapter.query_geometry = mock  # type: ignore[method-assign]

    reply = await fn(
        {"point_mm": [1, 2, 3], "radius_mm": 5, "kinds": "faces", "limit": 1}
    )

    mock.assert_awaited_once_with([1.0, 2.0, 3.0], 5.0, "faces", 1, False)
    assert reply["message"] == (
        "Within 5 mm of [1.0, 2.0, 3.0] mm: 4 face(s) (cylinder), from "
        "SolidWorks' picks around the point"
    )
    assert "complete: false" in reply["warning"]
    assert "nearest 1 of 4 faces" in reply["warning"]


@pytest.mark.parametrize(
    "payload",
    [
        {"point_mm": [1, 2]},
        {"point_mm": [1, 2, 3], "radius_mm": 0},
        {"point_mm": [1, 2, 3], "kinds": "vertices"},
        {"point_mm": [1, 2, 3], "limit": 51},
    ],
)
def test_bad_inputs_are_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        QueryGeometryInput(**payload)


async def test_an_adapter_without_it(mock_config: Any) -> None:
    class _Bare:
        pass

    mcp = FastMCP("bare")
    await register_analysis_tools(mcp, _Bare(), mock_config)  # type: ignore[arg-type]
    fn = next(t.fn for t in await mcp.list_tools() if t.name == "query_geometry")

    reply = await fn({"point_mm": [0, 0, 0]})

    assert reply["error_type"] == "not_supported"
