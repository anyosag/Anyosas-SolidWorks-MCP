"""``add_mate`` with ``check_interference``: the overlap check in the same call.

Agents called ``check_interference`` right after ``add_mate`` (speed report,
section 3). The mate stays whatever the check gives: a check that fails is a
warning, not an error.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.modeling import interference_summary, register_modeling_tools

MATE = {"mate_type": "coincident", "geometry_moved": True}
OVERLAPS = {
    "interference_found": True,
    "interference_count": 3,
    "interferences": [
        {"components": ["pin-1", "plate-1"], "volume_mm3": 2.0},
        {"components": ["plate-1", "pin-1"], "volume_mm3": 1.0},
        {"components": ["cap-1", "pin-1"], "volume_mm3": 0.5},
    ],
    "scope": "whole assembly",
}


def _result(data: Any = None, error: str | None = None) -> AdapterResult[Any]:
    status = AdapterResultStatus.ERROR if error else AdapterResultStatus.SUCCESS
    return AdapterResult(status=status, data=data, error=error)


async def _add_mate(adapter: Any) -> Any:
    mcp = FastMCP("mates")
    await register_modeling_tools(mcp, adapter, {})
    return next(t.fn for t in await mcp.list_tools() if t.name == "add_mate")


@pytest.fixture
def adapter() -> AsyncMock:
    fake = AsyncMock()
    fake.add_mate = AsyncMock(return_value=_result(MATE))
    fake.check_interference = AsyncMock(return_value=_result(OVERLAPS))
    return fake


async def test_the_check_runs_after_the_mate_and_is_summarised(
    adapter: AsyncMock,
) -> None:
    add_mate = await _add_mate(adapter)

    reply = await add_mate(
        {"component_a": "pin-1", "component_b": "plate-1", "check_interference": True}
    )

    assert reply["status"] == "success"
    adapter.check_interference.assert_awaited_once_with({"ignore_hidden": True})
    assert reply["interference"] == {
        "interference_found": True,
        "interference_count": 3,
        "pair_count": 2,
        "total_volume_mm3": 3.5,
        "top_pairs": [
            {
                "components": ["pin-1", "plate-1"],
                "count": 2,
                "volume_mm3": 3.0,
                "largest_mm3": 2.0,
            },
            {
                "components": ["cap-1", "pin-1"],
                "count": 1,
                "volume_mm3": 0.5,
                "largest_mm3": 0.5,
            },
        ],
    }
    assert reply["message"] == (
        "Added coincident mate between pin-1 and plate-1; 3 interference(s) "
        "between 2 pair(s) of components; most overlap: pin-1 / plate-1 3 mm³ "
        "(2 places); cap-1 / pin-1 0.5 mm³"
    )


async def test_a_clear_assembly_says_so(adapter: AsyncMock) -> None:
    adapter.check_interference.return_value = _result(
        {"interference_found": False, "interference_count": 0, "interferences": []}
    )
    add_mate = await _add_mate(adapter)

    reply = await add_mate(
        {"component_a": "a-1", "component_b": "b-1", "check_interference": True}
    )

    assert reply["message"].endswith("; no interference found")
    assert reply["interference"]["interference_found"] is False


async def test_no_check_unless_asked(adapter: AsyncMock) -> None:
    add_mate = await _add_mate(adapter)

    reply = await add_mate({"component_a": "a-1", "component_b": "b-1"})

    assert "interference" not in reply
    adapter.check_interference.assert_not_awaited()


async def test_a_failed_check_is_a_warning_and_the_mate_stays(
    adapter: AsyncMock,
) -> None:
    adapter.check_interference.return_value = _result(error="InterferenceDetection")
    add_mate = await _add_mate(adapter)

    reply = await add_mate(
        {"component_a": "a-1", "component_b": "b-1", "check_interference": True}
    )

    assert reply["status"] == "success"
    assert reply["mate"] == MATE
    assert reply["interference"] == {"error": "InterferenceDetection"}
    assert reply["warning"].startswith(
        "The mate was added, but the interference check failed: InterferenceDetection."
    )


async def test_an_adapter_that_cannot_check_says_so() -> None:
    class _NoCheck:
        add_mate = AsyncMock(return_value=_result(MATE))

    add_mate = await _add_mate(_NoCheck())

    reply = await add_mate(
        {"component_a": "a-1", "component_b": "b-1", "check_interference": True}
    )

    assert reply["interference"] == {"error": "the adapter can't check interference"}


def test_the_summary_leaves_out_each_interference() -> None:
    summary = interference_summary({"interference_count": 0})

    assert summary == {
        "interference_count": 0,
        "pair_count": 0,
        "total_volume_mm3": 0.0,
        "top_pairs": [],
    }
