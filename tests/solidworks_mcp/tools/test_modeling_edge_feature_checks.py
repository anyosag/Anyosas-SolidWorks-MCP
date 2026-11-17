"""``add_fillet`` / ``add_chamfer`` say what they took; rolling forward and rebuilding say what warns.

The Dipper: a fillet made with the rollback bar above two Save Bodies features
took 4 edges; after ``set_rollback`` to the end and ``rebuild_model`` it warned
swFeatureErrorFilletNoEdge and rounded 2. No reply showed it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    SolidWorksFeature,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks.rebuild_diagnostics import RawRebuildIssue
from solidworks_mcp.tools.modeling import register_modeling_tools


@pytest.fixture
async def part(mock_config: Any) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    await adapter.create_sketch("Top")
    await adapter.add_rectangle(0, 0, 10, 10)
    await adapter.exit_sketch()
    await adapter.create_extrusion(ExtrusionParameters(depth=5))
    mcp = FastMCP("edge-feature-checks")
    await register_modeling_tools(mcp, adapter, mock_config)
    return adapter, {tool.name: tool.fn for tool in await mcp.list_tools()}


def _made(kind: str, **properties: Any) -> AdapterResult[SolidWorksFeature]:
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data=SolidWorksFeature(
            name=f"{kind}3", type=kind, id="", parameters={}, properties=properties
        ),
    )


def _geometry(volume: float) -> dict[str, Any]:
    return {
        "volume_mm3": volume,
        "solid_body_count": 2,
        "bounding_box_mm": {"size": [40.0, 10.0, 40.0]},
    }


async def test_a_fillet_that_kept_half_its_edges_says_so(part: Any) -> None:
    adapter, tools = part
    adapter.add_fillet = AsyncMock(  # type: ignore[method-assign]
        return_value=_made(
            "Fillet",
            geometry_before=_geometry(5000.0),
            geometry_after=_geometry(4999.3),
            edges_selected=4,
            selection_other=0,
            edges_held=2,
            features_below_bar=["Save Bodies1", "Save Bodies2"],
        )
    )

    reply = await tools["add_fillet"]({"radius": 0.8, "edge_names": []})

    assert reply["status"] == "success", reply
    assert reply["fillet"]["edges_selected"] == 4
    assert reply["fillet"]["edges_held"] == 2
    change = reply["measured_change"]
    assert (change["volume_before_mm3"], change["volume_after_mm3"]) == (
        5000.0,
        4999.3,
    )
    assert change["volume_delta_mm3"] == pytest.approx(-0.7)
    assert reply["warning"].startswith(
        "Fillet3 holds 2 of the 4 edges selected: SolidWorks left 2 out."
    )
    assert "The rollback bar is up: 2 feature(s) after Fillet3" in reply["warning"]


async def test_a_chamfer_whose_after_read_failed_says_so(part: Any) -> None:
    adapter, tools = part
    adapter.add_chamfer = AsyncMock(  # type: ignore[method-assign]
        return_value=_made(
            "Chamfer",
            geometry_before=_geometry(5000.0),
            geometry_after=None,
            edges_selected=2,
            edges_held=2,
        )
    )

    reply = await tools["add_chamfer"]({"distance": 0.5})

    assert reply["chamfer"]["edges_held"] == 2
    assert reply["measured_change"]["volume_after_mm3"] is None
    assert reply["measured_change"]["note"] == (
        "Could not read the model after the feature: the summary didn't read"
    )
    assert "warning" not in reply


async def test_a_chamfer_that_removed_nothing_is_warned_about(part: Any) -> None:
    adapter, tools = part
    adapter.add_chamfer = AsyncMock(  # type: ignore[method-assign]
        return_value=_made(
            "Chamfer",
            geometry_before=_geometry(5000.0),
            geometry_after=_geometry(5000.0),
        )
    )

    reply = await tools["add_chamfer"]({"distance": 0.5})

    assert reply["chamfer"]["edges_selected"] is None
    assert "changed no volume" in reply["warning"]


async def test_on_the_mock_a_fillet_holds_its_edges_and_no_change_is_measured(
    part: Any,
) -> None:
    _adapter, tools = part

    reply = await tools["add_fillet"]({"radius": 1, "edge_names": ["Edge1", "Edge2"]})

    assert (reply["fillet"]["edges_selected"], reply["fillet"]["edges_held"]) == (2, 2)
    assert "measured_change" not in reply
    assert "warning" not in reply


async def test_a_fillet_made_with_the_bar_up_and_rolling_forward(part: Any) -> None:
    adapter, tools = part
    first = await adapter.add_fillet(1.0, ["Edge1"])
    boss = next(f.name for f in adapter._features.values() if f.type == "Extrusion")
    up = await tools["set_rollback"]({"after": boss})

    made = await tools["add_fillet"]({"radius": 0.8, "edge_names": ["Edge5"]})
    adapter.injected_rebuild_issues = [
        RawRebuildIssue(made["fillet"]["name"], "Fillet", 13, True)
    ]
    forward = await tools["set_rollback"]({})
    rebuilt = await tools["rebuild_model"]({})

    assert up["data"]["rolled_back"] == [first.data.name]
    assert f"after {made['fillet']['name']} ({first.data.name})" in made["warning"]
    assert forward["data"]["rebuild"]["warning_count"] == 1
    assert forward["message"] == (
        f"Rollback bar at the end; Rebuilt with 1 warning(s): {made['fillet']['name']}."
    )
    assert forward["warning"].startswith(
        f"SolidWorks reports: {made['fillet']['name']} warns "
        "swFeatureErrorFilletNoEdge: An edge the fillet referenced no longer exists"
    )
    assert "edit_feature with edges: 'selection'" in forward["warning"]
    assert rebuilt["status"] == "success"
    assert forward["warning"].startswith(rebuilt["warning"])  # and the level's note


async def test_rolling_forward_names_errors_too(part: Any) -> None:
    adapter, tools = part
    adapter.injected_rebuild_issues = [
        RawRebuildIssue("Cut-Extrude2", "Cut", 71, False)
    ]

    forward = await tools["set_rollback"]({})

    assert forward["status"] == "success"
    assert forward["warning"].startswith(
        "SolidWorks reports: Cut-Extrude2 fails swFeatureError"
    )


async def test_a_clean_move_and_rebuild_say_nothing_more(part: Any) -> None:
    _adapter, tools = part

    forward = await tools["set_rollback"]({})
    rebuilt = await tools["rebuild_model"]({})

    assert forward["message"] == "Rollback bar at the end"
    assert "SolidWorks reports" not in forward.get("warning", "")
    assert "warning" not in rebuilt


async def test_an_adapter_without_a_rebuild_report_moves_quietly(part: Any) -> None:
    adapter, tools = part
    adapter.set_rollback = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"position": "the end", "rolled_back": [], "rebuild": None},
        )
    )

    forward = await tools["set_rollback"]({})

    assert forward["message"] == "Rollback bar at the end"
    assert "SolidWorks reports" not in forward.get("warning", "")
