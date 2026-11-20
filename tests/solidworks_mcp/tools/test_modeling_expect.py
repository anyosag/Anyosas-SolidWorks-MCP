"""``expect`` on the feature tools: the part checked as ``verify_model`` checks it.

Agents called ``verify_model`` right after a fillet, a chamfer or a loft
(speed report, section 3). With ``expect`` the feature tool checks the summary
it reads anyway; a mismatch is an ``expectation_failed`` error carrying the
feature and the checks, so neither the agent nor a workflow can miss it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from pydantic import ValidationError

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    SolidWorksFeature,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.analysis import FeatureExpectations
from solidworks_mcp.tools.automation import register_automation_tools
from solidworks_mcp.tools.modeling import (
    CreateExtrusionInput,
    meet_expectations,
    register_modeling_tools,
)
from solidworks_mcp.tools.sketching import register_sketching_tools
from solidworks_mcp.tools.tool_boundary import ToolOperationError


def _ok(data: Any) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)


def _summary(volume: float, size: list[float], bodies: int = 1) -> dict[str, Any]:
    return {
        "document_type": "part",
        "volume_mm3": volume,
        "bounding_box_mm": {"size": size},
        "solid_body_count": bodies,
    }


BEFORE = _summary(0.0, [0.0, 0.0, 0.0], 0)
AFTER = _summary(1000.0, [10.0, 10.0, 10.0])


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    await adapter.create_part("Cube")
    return adapter


async def _tools(adapter: Any) -> dict[str, Any]:
    mcp = FastMCP("expect")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_automation_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _sketched(adapter: MockSolidWorksAdapter) -> None:
    await adapter.create_sketch("Top Plane")
    await adapter.add_rectangle(0, 0, 10, 10)
    await adapter.exit_sketch()


def _measured(adapter: MockSolidWorksAdapter, *summaries: dict[str, Any]) -> AsyncMock:
    reader = AsyncMock(side_effect=[_ok(s) for s in summaries])
    adapter.get_model_geometry_summary = reader  # type: ignore[method-assign]
    return reader


# ---- the check ---------------------------------------------------------------------


def _reply() -> dict[str, Any]:
    return {
        "status": "success",
        "message": "Created extrusion: Boss-Extrude1",
        "extrusion": {"name": "Boss-Extrude1"},
        "execution_time": 0.5,
    }


def test_without_expect_the_reply_is_unchanged() -> None:
    assert meet_expectations(_reply(), None, AFTER) == _reply()


def test_a_met_expectation_is_reported_as_verify_model_would() -> None:
    expect = FeatureExpectations(volume_mm3=1005, bounding_box_mm=(10, 10, 10.05))

    reply = meet_expectations(_reply(), expect, AFTER)

    verification = reply["verification"]
    assert verification["verdict"] == "pass"
    assert verification["message"] == "Verification passed: 2 of 2 checks passed"
    assert [c["name"] for c in verification["checks"]] == [
        "volume_mm3",
        "bounding_box_mm",
    ]
    assert "warning" not in reply


def test_a_missed_expectation_is_an_error_carrying_the_feature() -> None:
    expect = FeatureExpectations(volume_mm3=2000, solid_body_count=1)

    with pytest.raises(ToolOperationError) as raised:
        meet_expectations(_reply(), expect, AFTER)

    error = raised.value
    assert str(error).startswith(
        "Created extrusion: Boss-Extrude1, but the part does not meet `expect`: "
        "volume_mm3 measured 1000.0 mm³, expected 2000.0. The feature is in the "
        "model"
    )
    assert "calling again adds another" in str(error)
    assert error.execution_time == 0.5
    assert error.data["extrusion"] == {"name": "Boss-Extrude1"}
    assert error.data["verification"]["verdict"] == "fail"
    assert set(error.data) == {"extrusion", "verification"}


def test_a_turned_part_says_so_in_the_error() -> None:
    expect = FeatureExpectations(bounding_box_mm=(10, 20, 30))

    with pytest.raises(ToolOperationError, match="oriented differently"):
        meet_expectations(_reply(), expect, _summary(6000.0, [30.0, 10.0, 20.0]))


@pytest.mark.parametrize("summary", [None, {}])
def test_a_part_that_couldnt_be_measured_is_a_warning(summary: Any) -> None:
    reply = meet_expectations(
        _reply(), FeatureExpectations(solid_body_count=1), summary
    )

    assert reply["verification"]["verdict"] == "inconclusive"
    assert reply["warning"].startswith("`expect` could not check solid_body_count")


def test_expect_needs_something_to_check_and_its_own_names() -> None:
    with pytest.raises(ValidationError, match="expect needs volume_mm3"):
        FeatureExpectations(relative_tolerance=0.05)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FeatureExpectations(volume=5)

    renamed = FeatureExpectations.model_validate({"expected_volume_mm3": 5})
    inside = CreateExtrusionInput(depth=5, expect={"solid_body_count": 2})

    assert renamed.volume_mm3 == 5
    assert inside.expect is not None and inside.expect.solid_body_count == 2


def test_the_listed_schema_names_the_fields_without_a_nested_model() -> None:
    schema = CreateExtrusionInput.model_json_schema()["properties"]["expect"]

    assert schema["type"] == "object"
    assert "properties" not in schema
    assert schema["description"].startswith(
        "Check after it: volume_mm3, bounding_box_mm [x, y, z], solid_body_count."
    )


# ---- the tools ------------------------------------------------------------------------


async def test_an_extrusion_checks_the_summary_it_read_anyway(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)
    await _sketched(mock)
    reader = _measured(mock, BEFORE, AFTER)

    reply = await tools["create_extrusion"](
        {"depth": 10, "expect": {"volume_mm3": 1000, "solid_body_count": 1}}
    )

    assert reply["status"] == "success", reply
    assert reply["verification"]["verdict"] == "pass"
    assert reader.await_count == 2  # before and after the feature, as without


async def test_a_missed_extrusion_is_an_expectation_failed_error(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)
    await _sketched(mock)
    _measured(mock, BEFORE, AFTER)

    reply = await tools["create_extrusion"](
        {"depth": 10, "expect": {"volume_mm3": 500}}
    )

    assert reply["status"] == "error"
    assert reply["error_type"] == "expectation_failed"
    assert "Don't call the tool again" in reply["hint"]
    assert reply["data"]["extrusion"]["name"] == "Boss-Extrude1"
    assert reply["data"]["measured_change"]["volume_after_mm3"] == 1000.0
    assert reply["data"]["verification"]["checks"][0]["passed"] is False
    # The feature is there all the same.
    assert any(f.type == "Extrusion" for f in mock._features.values())


async def test_a_workflow_stops_at_the_missed_expectation(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)
    _measured(mock, BEFORE, AFTER)

    result = await tools["execute_workflow"](
        {
            "steps": [
                {"tool": "create_sketch", "arguments": {"plane": "Top"}},
                {
                    "tool": "add_rectangle",
                    "arguments": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
                },
                {"tool": "exit_sketch"},
                {
                    "tool": "create_extrusion",
                    "arguments": {"depth": 10, "expect": {"volume_mm3": 500}},
                },
                {"tool": "add_fillet", "arguments": {"radius": 1}},
            ]
        }
    )

    assert result["status"] == "error"
    assert result["error_type"] == "expectation_failed"
    assert result["data"]["failed_step"] == 3


async def test_a_sweep_reads_the_part_only_when_asked(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)
    reader = _measured(mock, AFTER)
    mock.create_sweep = AsyncMock(  # type: ignore[method-assign]
        return_value=_ok(SolidWorksFeature(name="Sweep1", type="Sweep"))
    )

    plain = await tools["create_sweep"]({"path": "Sketch2"})
    reads_without = reader.await_count
    checked = await tools["create_sweep"](
        {"path": "Sketch2", "expect": {"solid_body_count": 1}}
    )

    assert plain["status"] == "success" and "verification" not in plain
    assert reads_without == 0
    assert checked["verification"]["verdict"] == "pass"
    assert reader.await_count == 1


async def test_a_fillet_checks_what_it_read_after_itself(
    mock: MockSolidWorksAdapter,
) -> None:
    tools = await _tools(mock)
    reader = _measured(mock)
    fillet = SolidWorksFeature(
        name="Fillet1",
        type="Fillet",
        properties={"geometry_before": BEFORE, "geometry_after": AFTER},
    )
    mock.add_fillet = AsyncMock(return_value=_ok(fillet))  # type: ignore[method-assign]

    reply = await tools["add_fillet"](
        {"radius": 1, "edge_names": ["Edge<1>"], "expect": {"volume_mm3": 1000}}
    )

    assert reply["verification"]["verdict"] == "pass"
    assert reader.await_count == 0


@pytest.mark.parametrize(
    ("tool", "arguments", "method", "data"),
    [
        ("create_loft", {"profiles": ["S1", "S2"]}, "create_loft", None),
        (
            "mirror_feature",
            {"features": ["Boss-Extrude1"], "mirror_plane": "Right Plane"},
            "mirror_feature",
            {"name": "Mirror1"},
        ),
        (
            "pattern_circular",
            {"features": ["Cut1"], "axis": "Axis1", "count": 4},
            "pattern_circular",
            {"name": "CirPattern1"},
        ),
        (
            "add_chamfer",
            {"distance": 1, "edge_names": ["Edge<1>"]},
            "add_chamfer",
            None,
        ),
    ],
)
async def test_the_other_feature_tools_take_expect(
    mock: MockSolidWorksAdapter,
    tool: str,
    arguments: dict[str, Any],
    method: str,
    data: Any,
) -> None:
    tools = await _tools(mock)
    _measured(mock, AFTER)
    made = data if data is not None else SolidWorksFeature(name="F1", type="F")
    setattr(mock, method, AsyncMock(return_value=_ok(made)))

    reply = await tools[tool]({**arguments, "expect": {"solid_body_count": 2}})

    assert reply["error_type"] == "expectation_failed", reply
    assert reply["data"]["verification"]["checks"][0]["actual"] == 1
