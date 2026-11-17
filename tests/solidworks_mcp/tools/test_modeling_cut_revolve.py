"""The ``create_cut_revolve`` tool: a revolve with ``cut`` on, and its reply."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    SolidWorksFeature,
)
from solidworks_mcp.tools.modeling import CreateCutRevolveInput, register_modeling_tools


def _ok(data: Any = None) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)


async def _tool(mcp_server, adapter) -> Any:
    await register_modeling_tools(mcp_server, adapter, {})
    return {t.name: t.fn for t in await mcp_server.list_tools()}["create_cut_revolve"]


async def test_the_revolve_is_asked_to_cut(mcp_server, mock_adapter) -> None:
    feature = SolidWorksFeature(
        name="Cut-Revolve1",
        type="Cut-Revolve",
        parameters={"sketch_name": "Sketch5", "axis_entity": "Axis1"},
    )
    mock_adapter.create_revolve = AsyncMock(return_value=_ok(feature))
    mock_adapter.get_model_geometry_summary = AsyncMock(
        side_effect=[
            _ok({"volume_mm3": 5000.0, "bounding_box_mm": None}),
            _ok({"volume_mm3": 4870.5, "bounding_box_mm": None}),
        ]
    )
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"sketch_name": "Sketch5", "axis_entity": "Axis1"})

    params = mock_adapter.create_revolve.await_args.args[0]
    assert (params.cut, params.angle, params.thin_feature) == (True, 360.0, False)
    assert reply["message"] == "Created cut-revolve: Cut-Revolve1"
    assert reply["cut_revolve"] == {
        "name": "Cut-Revolve1",
        "sketch": "Sketch5",
        "axis_entity": "Axis1",
        "angle": 360.0,
    }
    assert reply["measured_change"]["volume_delta_mm3"] == -129.5


async def test_the_mock_names_it_a_cut(mcp_server, mock_adapter) -> None:
    await mock_adapter.create_part()
    await mock_adapter.create_sketch("Front")
    await mock_adapter.exit_sketch()
    tool = await _tool(mcp_server, mock_adapter)

    reply = await tool({"angle": 180})

    assert reply["status"] == "success", reply
    assert reply["cut_revolve"]["name"].startswith("Cut-Revolve")


@pytest.mark.parametrize(
    "fields",
    [{"angle": 0}, {"angle": 361}, {"angle": 270, "both_directions": True}],
)
def test_a_revolve_angle_that_cannot_be_cut_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        CreateCutRevolveInput(**fields)


def test_a_two_way_revolve_of_half_a_turn_each_way_is_fine() -> None:
    assert CreateCutRevolveInput(angle=180, both_directions=True).angle == 180
