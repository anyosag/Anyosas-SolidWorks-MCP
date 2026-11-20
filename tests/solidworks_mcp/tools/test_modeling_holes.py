"""``create_hole_wizard_hole`` and ``create_thread``: inputs, replies and refusals."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.modeling import (
    CreateHoleWizardHoleInput,
    CreateThreadInput,
    register_modeling_tools,
)


async def _tools(mcp_server, adapter) -> dict[str, Any]:
    await register_modeling_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}


def _volume(mm3: float) -> AdapterResult[Any]:
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data={"volume_mm3": mm3, "bounding_box_mm": None, "solid_body_count": 1},
    )


def test_thread_input_writes_the_size_as_the_library_does() -> None:
    assert (
        CreateThreadInput(size="m30X1", edge_point=[0, 0, 0], length_mm=6).size
        == "M30x1"
    )
    with pytest.raises(ValueError, match="M10x1.5"):
        CreateThreadInput(size="1/4-20", edge_point=[0, 0, 0], length_mm=6)
    with pytest.raises(ValueError):
        CreateThreadInput(size="M10x1.5", edge_point=[0, 0], length_mm=6)
    with pytest.raises(ValueError):
        CreateThreadInput(size="M10x1.5", edge_point=[0, 0, 0], length_mm=0)


def test_hole_input_refusals() -> None:
    assert (
        CreateHoleWizardHoleInput(kind="tapped", size="m3", face_point=[0, 0, 0]).size
        == "M3"
    )
    with pytest.raises(ValueError, match="no pitch"):
        CreateHoleWizardHoleInput(kind="clearance", size="M3x0.5", face_point=[0, 0, 0])
    with pytest.raises(ValueError, match="fit is for"):
        CreateHoleWizardHoleInput(
            kind="tapped", size="M3", face_point=[0, 0, 0], fit="close"
        )
    with pytest.raises(ValueError, match="only for tapped"):
        CreateHoleWizardHoleInput(
            kind="clearance", size="M3", face_point=[0, 0, 0], thread="modeled"
        )
    with pytest.raises(ValueError):
        CreateHoleWizardHoleInput(
            kind="clearance", size="M3", face_point=[0, 0, 0], depth_mm=0
        )


async def test_a_thread_through_the_mock(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    mock_adapter.mock_add_round_edge([0, 0, 40], 10.0)
    tools = await _tools(mcp_server, mock_adapter)

    reply = await tools["create_thread"](
        {"size": "M10x1.5", "edge_point": [5, 0, 40], "length_mm": 20}
    )

    assert reply["status"] == "success", reply
    assert reply["message"] == "Created external M10x1.5 thread 20 mm long: Thread1"
    assert reply["data"]["thread"]["pitch_mm"] == 1.5
    assert reply["data"]["measured_change"]["volume_delta_mm3"] is None
    assert "note" in reply["data"]["measured_change"]


async def test_an_offset_thread_through_the_mock(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    mock_adapter.mock_add_round_edge([0, 1.77, 0], 29.5, axis=[0, 1, 0])
    tools = await _tools(mcp_server, mock_adapter)
    call = {"size": "M30x1", "edge_point": [14.75, 1.77, 0], "length_mm": 5}

    refused = await tools["create_thread"](call)
    reply = await tools["create_thread"](
        call | {"diameter_offset_mm": -0.5, "start_offset_mm": 0.5}
    )

    assert refused["error_type"] == "invalid_input" and "Ø30 rod" in refused["message"]
    assert reply["status"] == "success", reply
    thread = reply["data"]["thread"]
    assert thread["cut_major_diameter_mm"] == 29.5
    assert thread["start_offset_mm"] == 0.5


async def test_a_refused_thread_is_invalid_input(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    mock_adapter.mock_add_round_edge([15, 15, 20], 8.5)
    tools = await _tools(mcp_server, mock_adapter)

    reply = await tools["create_thread"](
        {
            "size": "M10x1.5",
            "edge_point": [19.25, 15, 20],
            "length_mm": 12,
            "internal": True,
        }
    )

    assert reply["status"] == "error"
    assert reply["error_type"] == "invalid_input"
    assert "Ø8.376" in reply["message"]


async def test_a_hole_through_the_mock(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    tools = await _tools(mcp_server, mock_adapter)

    reply = await tools["create_hole_wizard_hole"](
        {
            "kind": "tapped",
            "size": "M3",
            "face_point": [20, 10, 10],
            "depth_mm": 8,
            "thread": "modeled",
        }
    )

    assert reply["status"] == "success", reply
    assert reply["message"] == "Created M3 tapped hole, 8 mm deep: M3 Tapped Hole1"
    assert reply["data"]["hole"]["thread"] == "modeled"
    assert reply["data"]["thread"]["size"] == "M3x0.5"


async def test_a_hole_that_removes_nothing_warns(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    mock_adapter.get_model_geometry_summary = AsyncMock(return_value=_volume(8000.0))
    tools = await _tools(mcp_server, mock_adapter)

    reply = await tools["create_hole_wizard_hole"](
        {"kind": "clearance", "size": "M3", "face_point": [20, 10, 10]}
    )

    assert (
        reply["message"] == "Created M3 clearance hole, through all: M3 Clearance Hole1"
    )
    assert reply["data"]["measured_change"]["volume_delta_mm3"] == 0
    assert "changed no volume" in reply["warning"]
    assert "warning" not in reply["data"]


async def test_a_wrong_size_is_refused_before_the_adapter(
    mcp_server, mock_adapter
) -> None:
    await mock_adapter.create_part()
    mock_adapter.create_hole_wizard_hole = AsyncMock()
    tools = await _tools(mcp_server, mock_adapter)

    reply = await tools["create_hole_wizard_hole"](
        {"kind": "clearance", "size": "M3x0.5", "face_point": [20, 10, 10]}
    )

    assert reply["status"] == "error" and reply["error_type"] == "invalid_input"
    mock_adapter.create_hole_wizard_hole.assert_not_called()
