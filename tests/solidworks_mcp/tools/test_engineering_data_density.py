"""The ``set_density`` tool: a document density, the material it takes off."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.engineering_data import (
    SetDensityInput,
    register_engineering_data_tools,
)


@pytest.fixture
async def tools(mock_config: Any) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("density")
    await register_engineering_data_tools(mcp, adapter, mock_config)
    return adapter, {tool.name: tool.fn for tool in await mcp.list_tools()}


async def test_a_resin_density(tools) -> None:
    _adapter, tool = tools

    reply = await tool["set_density"]({"density_g_cm3": 1.15})

    assert reply["status"] == "success", reply
    assert reply["message"] == "Density 1.15 g/cm³ set on the part; it weighs 18.4 g"
    assert reply["data"]["applied"] is True


async def test_a_material_taken_off(tools) -> None:
    _adapter, tool = tools
    await tool["set_material"]({"material_name": "ABS"})

    refused = await tool["set_density"]({"density_g_cm3": 1.15})
    reply = await tool["set_density"]({"density_g_cm3": 1.15, "remove_material": True})

    assert refused["status"] == "error"
    assert refused["error_type"] == "invalid_input"
    assert "(material ABS taken off)" in reply["message"]


async def test_a_density_solidworks_does_not_use_is_flagged(tools) -> None:
    adapter, tool = tools
    adapter.set_density = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "applied": False,
                "mass_properties_density_g_cm3": 1.0,
                "mass_g": None,
            },
        )
    )

    reply = await tool["set_density"]({"density_g_cm3": 1.15})

    assert reply["message"] == "Density 1.15 g/cm³ set on the part"
    assert "mass properties use 1.0 g/cm³" in reply["warning"]


@pytest.mark.parametrize("value", [0, -1, 30])
def test_a_density_out_of_range_is_refused(value: float) -> None:
    with pytest.raises(ValueError):
        SetDensityInput(density_g_cm3=value)
