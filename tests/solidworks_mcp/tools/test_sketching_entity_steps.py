"""``draw_entity_steps``: a run of single-entity workflow steps as one operation.

``execute_workflow`` can hand it consecutive ``add_line``, ``add_circle``,
``add_rectangle``, ``add_arc`` and ``add_centerline`` steps; each step's reply
must be the one its own tool gives, so ``$steps`` references keep working.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.sketching import (
    ENTITY_STEP_TOOLS,
    draw_entity_steps,
    register_sketching_tools,
)


class _Recorder(MockSolidWorksAdapter):
    """The mock, with the same ids and times whichever way an entity is drawn."""

    def __init__(self) -> None:
        super().__init__({"mock_sketch_delay": 0})
        self.batches: list[tuple[list[dict[str, Any]], bool]] = []

    async def _drawn(self, prefix: str) -> AdapterResult[str]:
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=self._new_sketch_entity_id(prefix),
            execution_time=0.25,
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return await self._drawn("Line")

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        return await self._drawn("Circle")

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return await self._drawn("Rectangle")

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        direction: str = "ccw",
    ) -> AdapterResult[str]:
        return await self._drawn("Arc")

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return await self._drawn("Centerline")

    async def add_sketch_entities(
        self, items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        self.batches.append((items, rollback_on_error))
        result = await super().add_sketch_entities(items, rollback_on_error)
        for entry in (result.data or {}).get("items", []):
            entry["execution_time"] = 0.25
        return result


async def _recorder(sketch: bool = True) -> _Recorder:
    adapter = _Recorder()
    await adapter.connect()
    await adapter.create_part()
    if sketch:
        await adapter.create_sketch("Top")
    return adapter


async def _tools(adapter: Any, mock_config: Any) -> dict[str, Any]:
    mcp = FastMCP("entity steps")
    await register_sketching_tools(mcp, adapter, mock_config)
    return {tool.name: tool.fn for tool in await mcp.list_tools()}


#: One step of each tool, with the inputs agents give: aliases, construction,
#: a clockwise arc.
_STEPS: list[tuple[str, dict[str, Any]]] = [
    ("add_line", {"start_x": 0, "start_y": 0, "end_x": 10, "end_y": 0}),
    ("add_circle", {"center_x": 5, "center_y": 5, "radius": 2, "construction": True}),
    ("add_rectangle", {"corner1_x": 0, "corner1_y": 0, "x2": 4, "y2": 3}),
    (
        "add_arc",
        {
            "center_x": 0,
            "center_y": 0,
            "start_x": 5,
            "start_y": 0,
            "end_x": 0,
            "end_y": 5,
            "direction": "cw",
        },
    ),
    ("add_centerline", {"x1": 0, "y1": -5, "x2": 0, "y2": 5, "construction": True}),
    ("add_line", {"x1": 1, "y1": 1, "x2": 2, "y2": 2, "construction": True}),
]


def test_the_five_single_entity_tools_are_the_ones_it_draws() -> None:
    assert ENTITY_STEP_TOOLS == {
        "add_line",
        "add_circle",
        "add_rectangle",
        "add_arc",
        "add_centerline",
    }


async def test_each_step_replies_as_its_own_tool_does(mock_config: Any) -> None:
    one_by_one = await _recorder()
    tools = await _tools(one_by_one, mock_config)
    expected = [await tools[tool](dict(arguments)) for tool, arguments in _STEPS]
    batch = await _recorder()

    replies = await draw_entity_steps(batch, _STEPS)

    assert replies == expected
    assert [reply["status"] for reply in replies] == ["success"] * len(_STEPS)
    # One operation, keeping what it drew if one fails, as separate calls do.
    ((items, rollback),) = batch.batches
    assert rollback is False
    assert items == [
        {
            "x1": 0,
            "y1": 0,
            "x2": 10,
            "y2": 0,
            "tool": "add_line",
            "construction": False,
        },
        {
            "tool": "add_circle",
            "center_x": 5,
            "center_y": 5,
            "radius": 2,
            "construction": True,
        },
        {
            "x1": 0,
            "y1": 0,
            "x2": 4,
            "y2": 3,
            "tool": "add_rectangle",
            "construction": False,
        },
        {
            "tool": "add_arc",
            "center_x": 0,
            "center_y": 0,
            "start_x": 5,
            "start_y": 0,
            "end_x": 0,
            "end_y": 5,
            "direction": "cw",
        },
        {"x1": 0, "y1": -5, "x2": 0, "y2": 5, "tool": "add_centerline"},
        {"x1": 1, "y1": 1, "x2": 2, "y2": 2, "tool": "add_line", "construction": True},
    ]


async def test_it_stops_before_a_step_with_model_coordinates() -> None:
    """That step's own tool projects its points onto the sketch."""
    adapter = await _recorder()
    model = {
        "center_x": 0,
        "center_y": 0,
        "center_z": 5,
        "radius": 1,
        "coordinates": "model",
    }
    steps = [_STEPS[1], ("add_circle", model), _STEPS[0]]

    replies = await draw_entity_steps(adapter, steps)

    assert [reply["circle"]["id"] for reply in replies] == ["Circle_1"]
    assert [len(items) for items, _ in adapter.batches] == [1]


@pytest.mark.parametrize(
    "refused",
    [
        ("add_circle", {"center_x": 0, "center_y": 0, "radius": -1}),
        ("add_line", ["not", "arguments"]),
        ("add_spline", {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}),
    ],
)
async def test_it_stops_before_a_step_its_tool_would_refuse(refused: Any) -> None:
    """That step runs on its own, and its tool gives the error."""
    adapter = await _recorder()

    first = await draw_entity_steps(adapter, [refused, _STEPS[0]])
    after_one = await draw_entity_steps(adapter, [_STEPS[0], refused, _STEPS[0]])

    assert first == []
    assert len(after_one) == 1 and after_one[0]["status"] == "success"
    assert [len(items) for items, _ in adapter.batches] == [1]


async def test_a_refused_step_ends_the_replies_with_its_tools_error(
    mock_config: Any,
) -> None:
    """The same payload add_circle gives when SolidWorks refuses the circle."""

    async def refused(*_args: Any, **_kwargs: Any) -> AdapterResult[Any]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Failed to create circle",
            execution_time=0.5,
        )

    single = await _recorder()
    single.add_circle = refused  # type: ignore[method-assign]
    expected = await (await _tools(single, mock_config))["add_circle"](
        dict(_STEPS[1][1])
    )
    batch = await _recorder()

    async def stop_at_the_circle(
        items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="item 1 (circle at (5, 5), radius 2) was not created: ...",
            data={
                "items": [
                    {
                        "index": 0,
                        "tool": "add_line",
                        "id": "Line_1",
                        "execution_time": 0.25,
                    }
                ],
                "failed_index": 1,
                "failed": "item 1",
                "error": "Failed to create circle",
                "removed": False,
            },
            execution_time=0.5,
        )

    batch.add_sketch_entities = stop_at_the_circle  # type: ignore[method-assign]

    replies = await draw_entity_steps(batch, _STEPS[:3])

    assert len(replies) == 2
    assert replies[0]["line"]["id"] == "Line_1"
    assert replies[1] == expected
    assert replies[1]["message"] == "Failed to add circle: Failed to create circle"


async def test_an_entity_not_made_construction_is_named_as_its_tool_names_it() -> None:
    adapter = await _recorder()

    async def construction_refused(
        items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="...",
            data={
                "items": [],
                "failed_index": 0,
                "error": "SolidWorks did not make Line_1 construction geometry",
                "failed_id": "Line_1",
                "removed": False,
            },
        )

    adapter.add_sketch_entities = construction_refused  # type: ignore[method-assign]

    (reply,) = await draw_entity_steps(adapter, [_STEPS[5]])

    assert reply["status"] == "error"
    assert reply["message"] == (
        "The line Line_1 was drawn as normal geometry, not construction: SolidWorks "
        "did not make Line_1 construction geometry"
    )


async def test_an_error_before_any_step_ran_is_the_first_steps(
    mock_config: Any,
) -> None:
    """No open sketch: the first step fails as its own tool would."""
    single = await _recorder(sketch=False)
    expected = await (await _tools(single, mock_config))["add_line"](dict(_STEPS[0][1]))
    batch = await _recorder(sketch=False)

    (reply,) = await draw_entity_steps(batch, _STEPS[:2])

    assert reply["message"] == "Failed to add line: No active sketch"
    assert reply["error_type"] == "no_active_sketch"
    assert {k: v for k, v in reply.items() if k != "execution_time"} == {
        k: v for k, v in expected.items() if k != "execution_time"
    }


async def test_a_dialog_after_the_last_step_is_that_steps_error() -> None:
    adapter = await _recorder()
    drawn = [
        {"index": i, "tool": tool, "id": entity_id, "execution_time": 0.25}
        for i, (tool, entity_id) in enumerate(
            [("add_line", "Line_1"), ("add_circle", "Circle_2")]
        )
    ]

    async def dialog(
        items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=(
                "SolidWorks showed a dialog during add_sketch_entities, 'Rebuild?'; "
                "the server cancelled it."
            ),
            data={
                "dialog": {"title": "SOLIDWORKS"},
                "items": drawn,
                "failed_index": None,
                "removed": False,
            },
            execution_time=1.5,
        )

    adapter.add_sketch_entities = dialog  # type: ignore[method-assign]

    replies = await draw_entity_steps(adapter, _STEPS[:2])

    assert replies[0]["line"]["id"] == "Line_1"
    assert replies[1]["status"] == "error"
    assert replies[1]["error_type"] == "solidworks_dialog"
    assert replies[1]["data"] == {"dialog": {"title": "SOLIDWORKS"}}
    assert replies[1]["execution_time"] == 1.5


async def test_an_error_with_no_message_still_names_the_step() -> None:
    adapter = await _recorder()

    async def silent(
        items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        return AdapterResult(status=AdapterResultStatus.ERROR)

    adapter.add_sketch_entities = silent  # type: ignore[method-assign]

    (reply,) = await draw_entity_steps(adapter, [_STEPS[4]])

    assert reply["message"] == "Failed to add centerline: Operation failed"
