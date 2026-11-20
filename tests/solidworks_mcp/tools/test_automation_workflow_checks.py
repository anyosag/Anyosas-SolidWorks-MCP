"""``execute_workflow``: short steps, every step checked first, and its own time.

From the 2026-11-20 speed report: steps written ``{"tool", "arguments":
{"input_data": {...}}}`` cost 12.8k output tokens of wrapper alone, 17 runs
stopped part-way on a step's bad input, and a successful run said nothing
about how long it took.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import ANY

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.automation import (
    WorkflowInput,
    WorkflowStep,
    register_automation_tools,
)
from solidworks_mcp.tools.export import register_export_tools
from solidworks_mcp.tools.modeling import register_modeling_tools
from solidworks_mcp.tools.sketching import register_sketching_tools

_SHORT_BOX = [
    {"create_sketch": {"plane": "Top"}},
    {"add_rectangle": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}},
    {"exit_sketch": None},
    {"create_extrusion": {"depth": 5}, "id": "boss"},
]


@pytest.fixture
async def part(mock_config: Any) -> tuple[MockSolidWorksAdapter, Any]:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    mcp = FastMCP("workflow")
    await register_sketching_tools(mcp, adapter, mock_config)
    await register_modeling_tools(mcp, adapter, mock_config)
    await register_export_tools(mcp, adapter, mock_config)
    await register_automation_tools(mcp, adapter, mock_config)
    run = next(t.fn for t in await mcp.list_tools() if t.name == "execute_workflow")
    return adapter, run


# ---- short steps ---------------------------------------------------------------------


async def test_a_step_can_name_its_tool_as_its_key(part: Any) -> None:
    adapter, run = part

    result = await run({"steps": _SHORT_BOX})

    assert result["status"] == "success", result
    assert [s["tool"] for s in result["steps"]] == [
        "create_sketch",
        "add_rectangle",
        "exit_sketch",
        "create_extrusion",
    ]
    assert result["steps"][3]["id"] == "boss"
    assert any(f.type == "Extrusion" for f in adapter._features.values())


async def test_a_short_step_takes_a_document_and_references(part: Any) -> None:
    _adapter, run = part
    steps = [
        *_SHORT_BOX,
        {"rename_feature": {"old_name": "$steps.boss.extrusion.name", "new_name": "B"}},
    ]

    result = await run({"steps": steps})

    assert result["status"] == "success", result
    assert result["steps"][4]["data"]["old_name"] == "Boss-Extrude1"


def test_a_short_step_reads_as_the_long_form() -> None:
    short = WorkflowStep.model_validate({"add_line": {"x1": 1}, "document": "Lid"})

    assert short == WorkflowStep(tool="add_line", arguments={"x1": 1}, document="Lid")


@pytest.mark.parametrize(
    ("step", "fragment"),
    [
        (
            {"exit_sketch": 5},
            "'exit_sketch' is not a step field (tool, arguments, id, document); as "
            "a short step, {'exit_sketch': {...}}, its value must be the tool's "
            "arguments, an object, not 5",
        ),
        ({"add_line": {}, "add_circle": {}}, "steps.0.tool: Field required"),
        ({"arguments": {"x1": 1}}, "steps.0.tool: Field required"),
        ({"add_line": {}, "arguments": {"x1": 1}}, "steps.0.add_line: Extra inputs"),
        ("add_line", "steps.0: Input should be a valid dictionary"),
    ],
)
async def test_a_step_that_is_neither_form_is_invalid_input(
    part: Any, step: Any, fragment: str
) -> None:
    adapter, run = part

    result = await run({"steps": [step]})

    assert result["error_type"] == "invalid_input", result
    assert fragment in result["message"]
    assert adapter._current_sketch is None


def test_the_listed_schema_takes_the_short_form() -> None:
    step = WorkflowInput.model_json_schema()["$defs"]["WorkflowStep"]

    assert "required" not in step
    assert step["additionalProperties"]["type"] == "object"
    assert "{'add_circle':" in step["additionalProperties"]["description"]


# ---- every step's arguments checked before any runs ----------------------------------------


async def test_every_bad_step_is_named_before_any_step_runs(part: Any) -> None:
    adapter, run = part
    steps = [
        {"create_sketch": {"plane": "Top"}},
        {"add_circle": {"center_x": 0, "center_y": 0}},
        {"exit_sketch": None},
        {"make_coffee": {}},
        {"create_extrusion": {"depth": "deep"}},
    ]

    result = await run({"steps": steps})

    assert result["error_type"] == "invalid_input", result
    lines = result["message"].split("\n")
    assert lines[0] == "Invalid input: 3 problems, so no step ran:"
    assert lines[1].startswith(
        "- steps.1 (add_circle): radius: Field required. add_circle takes "
        "center_x*, center_y*, radius*, center_z"
    )
    assert lines[1].endswith(" (* required)")
    assert lines[2] == "- steps.3: no tool named 'make_coffee'"
    assert lines[3].startswith(
        "- steps.4 (create_extrusion): depth: Input should be a valid number"
    )
    # Nothing ran, the good first step included.
    assert adapter._current_sketch is None
    assert not adapter._features


async def test_the_field_list_leaves_out_aliases(part: Any) -> None:
    _adapter, run = part

    result = await run({"steps": [{"add_line": {"x1": "left"}}]})

    message = result["message"]
    assert message.startswith("Invalid input: steps.0 (add_line): x1: Input should be")
    assert "add_line takes x1, y1, x2, y2, z1, z2, coordinates, construction" in message
    assert "start_x" not in message


async def test_a_reference_stands_in_for_any_value(part: Any) -> None:
    _adapter, run = part
    steps = [
        *_SHORT_BOX,
        {"create_sketch": {"plane": "Top"}},
        {"add_circle": {"center_x": 5, "center_y": 5, "radius": 2}},
        {"exit_sketch": None},
        # A number from an earlier reply, where the tool wants a number.
        {"create_extrusion": {"depth": "$steps.boss.extrusion.depth"}},
    ]

    result = await run({"steps": steps, "detail": "full"})

    assert result["status"] == "success", result
    assert result["steps"][7]["result"]["extrusion"]["depth"] == 5


async def test_a_reference_inside_an_object_stands_in_for_that_field(
    part: Any, tmp_path: Any
) -> None:
    _adapter, run = part
    steps = [
        {
            "create_reference_plane": {"reference": "Top Plane", "offset": 10},
            "id": "deck",
        },
        {
            "export_image": {
                "file_path": str(tmp_path / "cut.png"),
                "section": {"plane": "$steps.deck.plane.name"},
            }
        },
    ]

    result = await run({"steps": steps, "detail": "full"})

    assert result["status"] == "success", result
    section = result["steps"][1]["result"]["data"]["section"]
    assert section["plane"] == result["steps"][0]["result"]["plane"]["name"]


async def test_a_check_of_the_whole_input_still_runs_without_references(
    part: Any,
) -> None:
    _adapter, run = part
    shot = {
        "file_path": "x.png",
        "zoom_box_mm": [0, 0, 0, 1, 1, 1],
        "zoom_to_fit": True,
    }

    result = await run({"steps": [{"export_image": shot}]})

    assert result["error_type"] == "invalid_input"
    assert (
        "steps.0 (export_image): input: Value error, zoom_box_mm and "
        in (result["message"])
    )


async def test_a_removed_input_is_refused_before_any_step_runs(part: Any) -> None:
    adapter, run = part
    steps = [
        {"create_sketch": {"plane": "Top"}},
        {"create_assembly": {"name": "A", "components": ["p.sldprt"]}},
    ]

    result = await run({"steps": steps})

    assert result["error_type"] == "invalid_input"
    assert (
        "steps.1 (create_assembly): `components` is no longer an input"
        in (result["message"])
    )
    assert adapter._current_sketch is None


async def test_bad_references_are_all_named(part: Any) -> None:
    _adapter, run = part
    steps = [
        {"create_sketch": {"plane": "Top"}},
        {"add_line": {"x1": "$steps.nope.x", "y1": "$steps.5.y"}},
    ]

    result = await run({"steps": steps})

    assert result["message"].startswith("Invalid input: 2 problems, so no step ran:")
    assert (
        "steps.1.arguments.x1: '$steps.nope.x' names step 'nope'" in result["message"]
    )
    assert "steps.1.arguments.y1: '$steps.5.y' names step 5" in result["message"]


# ---- the run's own time --------------------------------------------------------------------


async def test_a_run_says_how_long_it_took(part: Any) -> None:
    _adapter, run = part

    result = await run({"steps": _SHORT_BOX})

    assert isinstance(result["execution_time"], float)
    assert result["server_time_s"] >= round(result["execution_time"], 3) - 0.001
    # Each step says its own server time too.
    assert result["steps"][0]["server_time_s"] == ANY
