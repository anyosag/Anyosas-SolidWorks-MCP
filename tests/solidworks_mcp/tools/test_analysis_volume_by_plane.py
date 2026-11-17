"""The ``volume_by_plane`` tool: one plane, both sides in words, and warnings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import VolumeByPlaneInput, register_analysis_tools


@pytest.fixture
async def tools(mock_config: Any) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("volume-by-plane")
    await register_analysis_tools(mcp, adapter, mock_config)
    return adapter, {tool.name: tool.fn for tool in await mcp.list_tools()}


async def test_a_fill_line_on_the_mock_block(tools) -> None:
    _adapter, tool = tools

    reply = await tool["volume_by_plane"](
        {"point_mm": [0, 4, 0], "normal": "y", "density_g_cm3": 1.15}
    )

    assert reply["status"] == "success", reply
    assert reply["message"] == (
        "Above the plane through [0.0, 4.0, 0.0] mm: 9600.0 mm³ (11.04 g); below: "
        "6400.0 mm³ (7.36 g)"
    )
    assert "No density" not in reply.get("warning", "")


async def test_what_the_cup_holds_and_the_warnings(tools) -> None:
    adapter, tool = tools
    mock = AsyncMock(
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "plane": {"name": "Fill line"},
                "above": {"volume_mm3": 100.0, "mass_g": None, "enclosed_ml": 0.0},
                "below": {
                    "volume_mm3": 200.0,
                    "mass_g": None,
                    "enclosed_mm3": 13500.0,
                    "enclosed_ml": 13.5,
                    "pockets": 1,
                },
                "check_ok": False,
                "check_mm3": 3.0,
                "density_g_cm3": None,
            },
        )
    )
    adapter.volume_by_plane = mock  # type: ignore[method-assign]

    reply = await tool["volume_by_plane"](
        {"plane": " Fill line ", "enclosed": True, "bodies": [" Cup "]}
    )

    mock.assert_awaited_once_with("Fill line", None, None, ["Cup"], True, None)
    assert reply["message"] == (
        "Above Fill line: 100.0 mm³; below: 200.0 mm³; filled to it, the part holds "
        "13.5 ml below it (1 pocket(s))"
    )
    assert "differ from the bodies' volume by 3.0 mm³" in reply["warning"]
    assert "No density is known" in reply["warning"]


async def test_a_normal_by_name_reaches_the_adapter_as_a_vector(tools) -> None:
    adapter, tool = tools
    mock = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    )
    adapter.volume_by_plane = mock  # type: ignore[method-assign]

    await tool["volume_by_plane"]({"point_mm": [1, 2, 3], "normal": "-z"})

    mock.assert_awaited_once_with(None, [1, 2, 3], [0.0, 0.0, -1.0], None, False, None)


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({}, "give plane"),
        ({"plane": "Top", "normal": "y"}, "not both"),
        ({"point_mm": [0, 0, 0], "normal": "q"}, "not 'q'"),
        ({"plane": "Top", "bodies": ["A", "A"]}, "blank or repeated"),
        ({"plane": "Top", "bodies": [" "]}, "blank or repeated"),
    ],
)
def test_bad_inputs_are_refused(payload: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        VolumeByPlaneInput(**payload)


def test_a_named_plane_has_no_normal_vector() -> None:
    assert VolumeByPlaneInput(plane="Top").normal_vector() is None


async def test_an_adapter_without_it(mock_config: Any) -> None:
    class _Bare:
        pass

    mcp = FastMCP("bare")
    await register_analysis_tools(mcp, _Bare(), mock_config)  # type: ignore[arg-type]
    fn = next(t.fn for t in await mcp.list_tools() if t.name == "volume_by_plane")

    reply = await fn({"plane": "Top"})

    assert reply["error_type"] == "not_supported"
