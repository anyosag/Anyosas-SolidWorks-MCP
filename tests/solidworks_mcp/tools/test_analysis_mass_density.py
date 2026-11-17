"""The mass properties tools with ``density_g_cm3``: the mass at a named density."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    MassProperties,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import register_analysis_tools


@pytest.fixture
async def tools(mock_config: Any) -> dict[str, Any]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    props = MassProperties(
        volume=16000.0,
        surface_area=4800.0,
        mass=0.016,
        center_of_mass=[20.0, 5.0, 20.0],
        moments_of_inertia=dict.fromkeys(
            ("Ixx", "Iyy", "Izz", "Ixy", "Ixz", "Iyz"), 0.0
        ),
    )
    adapter.get_mass_properties = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data=props)
    )
    mcp = FastMCP("mass-density")
    await register_analysis_tools(mcp, adapter, mock_config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


@pytest.mark.parametrize("name", ["calculate_mass_properties", "get_mass_properties"])
async def test_the_mass_at_a_resin_density(tools, name: str) -> None:
    reply = await tools[name]({"density_g_cm3": 1.15})

    at = reply["mass_properties"]["mass_at_density"]
    assert at["value"] == pytest.approx(0.0184)
    assert at["units"] == "kg" and at["density_g_cm3"] == 1.15
    assert reply["mass_properties"]["mass"]["value"] == 0.016
    assert reply["message"].endswith("; at 1.15 g/cm³ the part is 18.4 g")


async def test_no_density_no_extra_mass(tools) -> None:
    reply = await tools["get_mass_properties"]()

    assert "mass_at_density" not in reply["mass_properties"]
    assert reply["message"] == "Mass properties calculated successfully"
