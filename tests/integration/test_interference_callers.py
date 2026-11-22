"""``check_interference``'s three callers, on the mock adapter's box geometry.

The tool, ``add_mate`` with ``check_interference`` and ``verify_model`` with
``no_interference`` all call the adapter's ``check_interference``. With the
mock's components standing in for their boxes, each gets a real answer: a
dry run can now fail on an overlap.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools import register_tools


async def _session(*origins: tuple[float, float, float]) -> dict[str, Any]:
    """The tools, on a mock assembly of 10 mm cubes at ``origins``."""
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_assembly()
    for x, y, z in origins:
        await adapter.insert_component("C:/parts/block.sldprt", x, y, z)
    mcp = FastMCP("interference-callers")
    await register_tools(mcp, adapter, {})
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


async def test_the_tool_names_the_overlap_and_the_method() -> None:
    tools = await _session((0, 0, 0), (5, 0, 0))

    reply = await tools["check_interference"]({})

    assert reply["status"] == "success"
    assert reply["data"]["method"] == "body_intersection"
    assert reply["data"]["top_pairs"] == [
        {
            "components": ["component-1", "component-2"],
            "count": 1,
            "volume_mm3": 500.0,
            "largest_mm3": 500.0,
        }
    ]
    assert reply["message"] == (
        "1 interference(s) between 1 pair(s) of components; most overlap: "
        "component-1 / component-2 500 mm³"
    )


async def test_add_mate_reports_the_overlap_it_leaves() -> None:
    tools = await _session((0, 0, 0), (5, 0, 0))

    reply = await tools["add_mate"](
        {
            "component_a": "component-1",
            "component_b": "component-2",
            "check_interference": True,
        }
    )

    assert reply["status"] == "success"
    assert reply["interference"]["interference_found"] is True
    assert reply["interference"]["total_volume_mm3"] == 500.0


async def test_verify_model_fails_an_overlap_and_passes_a_gap() -> None:
    overlapping = await _session((0, 0, 0), (5, 0, 0))
    apart = await _session((0, 0, 0), (20, 0, 0))

    failed = await overlapping["verify_model"]({"no_interference": True})
    passed = await apart["verify_model"]({"no_interference": True})

    assert failed["verdict"] == "fail"
    assert passed["verdict"] == "pass"
    assert passed["measured"]["interference"]["method"] == "body_intersection"
