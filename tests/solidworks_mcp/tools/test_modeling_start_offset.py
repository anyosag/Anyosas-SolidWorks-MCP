"""``start_offset_mm``, ``draft_outward`` and blind ``MidPlane`` on the extrude and cut tools."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    SolidWorksFeature,
)
from solidworks_mcp.tools.modeling import (
    CreateCutExtrudeInput,
    CreateExtrusionInput,
    register_modeling_tools,
)


def _ok(data: Any = None) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)


async def _tools(mcp_server, adapter) -> dict[str, Any]:
    await register_modeling_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}


async def test_an_extrusion_passes_and_shows_its_offset_and_draft(
    mcp_server, mock_adapter
) -> None:
    mock_adapter.create_extrusion = AsyncMock(
        return_value=_ok(SolidWorksFeature(name="Boss-Extrude2", type="Extrusion"))
    )
    tool = (await _tools(mcp_server, mock_adapter))["create_extrusion"]

    reply = await tool(
        {"depth": 8, "start_offset_mm": -2.5, "draft_angle": 3, "draft_outward": True}
    )

    params = mock_adapter.create_extrusion.await_args.args[0]
    assert (params.start_offset, params.draft_angle, params.draft_outward) == (
        -2.5,
        3.0,
        True,
    )
    assert reply["extrusion"]["start_offset_mm"] == -2.5
    assert reply["extrusion"]["draft_angle"] == 3
    assert reply["extrusion"]["draft_outward"] is True


async def test_an_extrusion_left_as_before_shows_neither(
    mcp_server, mock_adapter
) -> None:
    mock_adapter.create_extrusion = AsyncMock(
        return_value=_ok(SolidWorksFeature(name="Boss-Extrude2", type="Extrusion"))
    )
    tool = (await _tools(mcp_server, mock_adapter))["create_extrusion"]

    reply = await tool({"depth": 8})

    assert "start_offset_mm" not in reply["extrusion"]
    assert "draft_outward" not in reply["extrusion"]


async def test_a_mid_plane_cut_with_an_offset_says_so(mcp_server, mock_adapter) -> None:
    feature = SolidWorksFeature(
        name="Cut-Extrude3",
        type="Cut-Extrude",
        parameters={"sketch_name": "Sketch3", "mid_plane": True},
    )
    mock_adapter.create_cut_extrude = AsyncMock(return_value=_ok(feature))
    tool = (await _tools(mcp_server, mock_adapter))["create_cut_extrude"]

    reply = await tool(
        {
            "depth": 4,
            "end_condition": "MidPlane",
            "start_offset_mm": 1,
            "draft_angle": 2,
        }
    )

    params = mock_adapter.create_cut_extrude.await_args.args[0]
    assert (params.end_condition, params.start_offset, params.draft_outward) == (
        "MidPlane",
        1.0,
        False,
    )
    assert reply["cut_extrude"]["end_condition"] == "MidPlane"
    assert reply["cut_extrude"]["start_offset_mm"] == 1
    assert reply["cut_extrude"]["draft_outward"] is False


@pytest.mark.parametrize("model", [CreateExtrusionInput, CreateCutExtrudeInput])
def test_a_draft_direction_without_a_draft_is_refused(model: type) -> None:
    with pytest.raises(ValueError, match="draft_outward needs a draft_angle"):
        model(depth=5, draft_outward=True)
