"""``execute_workflow``: runs of sketch-entity steps in one operation, and entity points.

In the speed sample, 1,975 sketch-entity steps inside workflows ran as 542
runs of consecutive steps; each step was its own SolidWorks operation and
paid the 0.25 s settle gap. A run of ``add_line``, ``add_circle``,
``add_rectangle``, ``add_arc`` and ``add_centerline`` steps is now drawn by
one ``add_sketch_entities`` call (``draw_entity_steps``), each step still
replying as its own tool does. A session also wrote
``"$steps.r1.circle.id.center"`` for a circle's centre, which
``add_sketch_constraint`` and ``add_sketch_dimension`` take as
``'<id>.center'``; that reference now names it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks.sketch import MAX_OUTLINE_SEGMENTS
from solidworks_mcp.adapters.solidworks.sketch_points import POINT_GETTERS
from solidworks_mcp.tools import automation
from solidworks_mcp.tools.automation import (
    MAX_WORKFLOW_STEPS,
    _look_up,
    _Reference,
    register_automation_tools,
)
from solidworks_mcp.tools.modeling import register_modeling_tools
from solidworks_mcp.tools.sketching import ENTITY_STEP_TOOLS, register_sketching_tools
from solidworks_mcp.tools.support_levels import SUPPORT_LEVELS

_PART = "Plate"
#: A circle wider than this is refused, as SolidWorks refuses an entity.
_REFUSED_RADIUS = 50
_REFUSAL = "SolidWorks could not create the circle"


class _Recorder(MockSolidWorksAdapter):
    """The mock, with one id per entity however it is drawn, noting each draw.

    A circle wider than ``_REFUSED_RADIUS`` is refused by its own tool's
    adapter call and inside a batch alike; a batch then stops there, as the
    live adapter's does.
    """

    def __init__(self) -> None:
        super().__init__(
            {
                "mock_connect_delay": 0,
                "mock_model_delay": 0,
                "mock_feature_delay": 0,
                "mock_sketch_delay": 0,
            }
        )
        #: Each drawing call: a single tool's adapter method, or add_sketch_entities.
        self.calls: list[str] = []
        #: The items each add_sketch_entities call was given.
        self.batches: list[list[dict[str, Any]]] = []
        #: The entities each add_sketch_dimension call was given.
        self.dimensioned: list[tuple[str, str | None]] = []

    async def _drawn(
        self, method: str, prefix: str, radius: float = 0.0
    ) -> AdapterResult[str]:
        self.calls.append(method)
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if radius > _REFUSED_RADIUS:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=_REFUSAL, execution_time=0.25
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=self._new_sketch_entity_id(prefix),
            execution_time=0.25,
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return await self._drawn("add_line", "Line")

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        return await self._drawn("add_circle", "Circle", radius)

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return await self._drawn("add_rectangle", "Rectangle")

    async def add_sketch_entities(
        self, items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        self.calls.append("add_sketch_entities")
        self.batches.append(items)
        refused = next(
            (
                number
                for number, item in enumerate(items)
                if item.get("radius", 0) > _REFUSED_RADIUS
            ),
            None,
        )
        if refused is None:
            return await super().add_sketch_entities(items, rollback_on_error)
        before = []  # what was drawn before the refused item
        if refused:
            drawn = await super().add_sketch_entities(
                items[:refused], rollback_on_error
            )
            before = drawn.data["items"]
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=f"item {refused} was not created: {_REFUSAL}",
            data={
                "items": before,
                "failed_index": refused,
                "failed": f"item {refused}",
                "error": _REFUSAL,
                "removed": False,
            },
            execution_time=0.25,
        )

    async def add_sketch_dimension(
        self, entity1: str, entity2: str | None, dimension_type: str, value: float
    ) -> AdapterResult[dict[str, Any]]:
        self.dimensioned.append((entity1, entity2))
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"id": "D1", "name": "D1@Sketch1"},
            execution_time=0.25,
        )


async def _server(mock_config: Any) -> tuple[_Recorder, Any]:
    """A recorder with a part open, and its execute_workflow."""
    adapter = _Recorder()
    await adapter.connect()
    await adapter.create_part(name=_PART)
    mcp = FastMCP("sketch steps")
    await register_sketching_tools(mcp, adapter, mock_config)
    await register_modeling_tools(mcp, adapter, mock_config)
    await register_automation_tools(mcp, adapter, mock_config)
    run = next(t.fn for t in await mcp.list_tools() if t.name == "execute_workflow")
    return adapter, run


def _one_by_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every step through its own tool, as the runner did before runs."""
    monkeypatch.setattr(automation, "ENTITY_STEP_TOOLS", frozenset())


_TIMING = frozenset({"execution_time", "server_time_s"})


def _untimed(value: Any) -> Any:
    """``value`` without its timing fields, at any depth."""
    if isinstance(value, dict):
        return {k: _untimed(v) for k, v in value.items() if k not in _TIMING}
    if isinstance(value, list):
        return [_untimed(item) for item in value]
    return value


def _line(x1: float, y1: float, x2: float, y2: float) -> dict[str, Any]:
    return {"add_line": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


#: A sketch with five circles and lines, an extrude, then more lines.
_MIXED: list[dict[str, Any]] = [
    {"create_sketch": {"plane": "Top"}},
    {"add_circle": {"center_x": 0, "center_y": 0, "radius": 5}, "id": "hole"},
    _line(-20, -15, 20, -15),
    {"add_circle": {"center_x": 10, "center_y": 0, "radius": 2, "construction": True}},
    {"add_line": {"start_x": 20, "start_y": -15, "end_x": 20, "end_y": 15}},
    {"add_circle": {"center_x": -10, "center_y": 0, "radius": 2}},
    {"exit_sketch": None},
    {"create_extrusion": {"depth": 5}},
    {"create_sketch": {"plane": "Front"}},
    _line(0, 0, 10, 0),
    _line(10, 0, 10, 10),
    _line(10, 10, 0, 0),
    {"exit_sketch": None},
]


# ---- runs of sketch-entity steps ---------------------------------------------------


@pytest.mark.parametrize("detail", ["summary", "full"])
async def test_a_run_gives_the_entries_its_steps_give_one_by_one(
    mock_config: Any, monkeypatch: pytest.MonkeyPatch, detail: str
) -> None:
    batched_adapter, batched = await _server(mock_config)
    result = await batched({"steps": _MIXED, "detail": detail})
    _one_by_one(monkeypatch)
    single_adapter, single = await _server(mock_config)
    expected = await single({"steps": _MIXED, "detail": detail})

    assert result["status"] == "success", result
    assert expected["status"] == "success", expected
    assert _untimed(result["steps"]) == _untimed(expected["steps"])
    assert result["warnings"] == expected["warnings"] == []
    assert result["completed"] == len(_MIXED)
    # Each run was one adapter call; one by one, each step was its own.
    assert batched_adapter.calls == ["add_sketch_entities"] * 2
    assert [len(items) for items in batched_adapter.batches] == [5, 3]
    assert single_adapter.calls == [
        "add_circle",
        "add_line",
        "add_circle",
        "add_line",
        "add_circle",
        "add_line",
        "add_line",
        "add_line",
    ]


async def test_consecutive_entities_are_one_adapter_call_on_the_mock(
    mock_config: Any,
) -> None:
    adapter = MockSolidWorksAdapter({"mock_sketch_delay": 0})
    await adapter.connect()
    await adapter.create_part()
    entities = AsyncMock(wraps=adapter.add_sketch_entities)
    adapter.add_sketch_entities = entities  # type: ignore[method-assign]
    singles = {
        name: AsyncMock(wraps=getattr(adapter, name))
        for name in sorted(ENTITY_STEP_TOOLS)
    }
    for name, mock in singles.items():
        setattr(adapter, name, mock)
    mcp = FastMCP("mock run")
    await register_sketching_tools(mcp, adapter, mock_config)
    await register_automation_tools(mcp, adapter, mock_config)
    run = next(t.fn for t in await mcp.list_tools() if t.name == "execute_workflow")
    arc = {
        "center_x": 0,
        "center_y": 0,
        "start_x": 5,
        "start_y": 0,
        "end_x": 0,
        "end_y": 5,
    }
    steps = [
        {"create_sketch": {"plane": "Top"}},
        _line(0, 0, 10, 0),
        {"add_circle": {"center_x": 5, "center_y": 5, "radius": 2}},
        {"add_rectangle": {"x1": 0, "y1": 0, "x2": 4, "y2": 3}},
        {"add_arc": arc},
        {"add_centerline": {"x1": 0, "y1": -5, "x2": 0, "y2": 5}},
        {"exit_sketch": None},
    ]

    result = await run({"steps": steps})

    assert result["status"] == "success", result
    entities.assert_awaited_once()
    (items,), options = entities.await_args
    assert [item["tool"] for item in items] == [
        "add_line",
        "add_circle",
        "add_rectangle",
        "add_arc",
        "add_centerline",
    ]
    # What it drew stays if one fails, as separate calls leave it.
    assert options == {"rollback_on_error": False}
    assert not any(mock.await_count for mock in singles.values())
    nouns = ["line", "circle", "rectangle", "arc", "centerline"]
    drawn = zip(result["steps"][1:6], nouns, strict=True)
    assert [step[noun]["id"] for step, noun in drawn] == [
        "Line_1",
        "Circle_2",
        "Rectangle_3",
        "Arc_4",
        "Centerline_5",
    ]


_REFUSED_IN_A_RUN: list[dict[str, Any]] = [
    {"create_sketch": {"plane": "Top"}},
    {"add_rectangle": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}},
    {"exit_sketch": None},
    {"create_extrusion": {"depth": 5}},
    {"create_sketch": {"plane": "Front"}},
    _line(0, 0, 10, 0),
    {"add_circle": {"center_x": 0, "center_y": 0, "radius": 99}},
    _line(10, 0, 10, 10),
    {"exit_sketch": None},
]


@pytest.mark.parametrize("rollback", [False, True])
async def test_a_step_refused_inside_a_run_stops_the_workflow_there(
    mock_config: Any, monkeypatch: pytest.MonkeyPatch, rollback: bool
) -> None:
    adapter, run = await _server(mock_config)

    result = await run({"steps": _REFUSED_IN_A_RUN, "rollback_on_error": rollback})

    assert result["status"] == "error"
    assert result["message"] == (
        "Workflow 'workflow' stopped at step 7 of 9 (add_circle): Failed to add "
        f"circle: {_REFUSAL}"
    )
    data = result["data"]
    assert data["failed_step"] == 6
    assert data["completed"] == 6
    assert [step["status"] for step in data["steps"]] == ["success"] * 6 + ["error"]
    assert data["steps"][6]["result"]["status"] == "error"
    # The rectangle ran on its own; the line, the circle and the line after
    # it were one run, which stopped at the circle.
    assert adapter.calls == ["add_rectangle", "add_sketch_entities"]
    assert [len(items) for items in adapter.batches] == [3]
    if rollback:
        assert data["rolled_back"]["done"] is True
        assert data["rolled_back"]["deleted"][0].startswith("Boss-Extrude")
        assert not adapter._features
    else:
        assert data["rolled_back"] is None
        assert any(f.type == "Extrusion" for f in adapter._features.values())

    # The same reply, field for field, as running each step on its own.
    _one_by_one(monkeypatch)
    single_adapter, single = await _server(mock_config)
    expected = await single({"steps": _REFUSED_IN_A_RUN, "rollback_on_error": rollback})
    assert _untimed(result) == _untimed(expected)
    assert single_adapter.calls == ["add_rectangle", "add_line", "add_circle"]


async def test_a_reference_to_a_step_in_the_run_splits_the_run(
    mock_config: Any,
) -> None:
    adapter, run = await _server(mock_config)
    steps = [
        {"create_sketch": {"plane": "Top"}},
        {**_line(0, 0, 10, 0), "id": "base"},
        _line(0, 0, 0, 10),
        # Starts where `base` ends, so it needs base's reply: a new run.
        {
            "add_line": {
                "x1": "$steps.base.line.end.x",
                "y1": "$steps.base.line.end.y",
                "x2": 10,
                "y2": 10,
            }
        },
        # Names `base` too, which is before this run: it stays in it.
        {
            "add_circle": {
                "center_x": 5,
                "center_y": 5,
                "radius": "$steps.base.line.length",
            }
        },
        {"exit_sketch": None},
    ]

    result = await run({"steps": steps})

    assert result["status"] == "success", result
    assert adapter.calls == ["add_sketch_entities"] * 2
    first, second = adapter.batches
    assert [item["tool"] for item in first] == ["add_line", "add_line"]
    assert second == [
        {
            "x1": 10,
            "y1": 0,
            "x2": 10,
            "y2": 10,
            "tool": "add_line",
            "construction": False,
        },
        {
            "tool": "add_circle",
            "center_x": 5,
            "center_y": 5,
            "radius": 10,
            "construction": False,
        },
    ]


async def test_a_step_with_model_coordinates_runs_through_its_own_tool(
    mock_config: Any,
) -> None:
    adapter, run = await _server(mock_config)
    ctx = AsyncMock()
    # Model (5, 0, 5) is on the Top Plane, at sketch (5, -5).
    model = {
        "add_circle": {
            "center_x": 5,
            "center_y": 0,
            "center_z": 5,
            "radius": 1,
            "coordinates": "model",
        }
    }
    steps = [
        {"create_sketch": {"plane": "Top"}},
        model,  # first of a run: the run draws nothing
        _line(0, 0, 10, 0),
        model,  # inside a run: the run stops before it
        _line(10, 0, 10, 10),
        _line(10, 10, 0, 0),
        {"exit_sketch": None},
    ]

    result = await run({"steps": steps}, ctx)

    assert result["status"] == "success", result
    assert adapter.calls == [
        "add_circle",
        "add_sketch_entities",
        "add_circle",
        "add_sketch_entities",
    ]
    assert [len(items) for items in adapter.batches] == [1, 2]
    for step in (result["steps"][1], result["steps"][3]):
        assert step["projection"]["coordinates"] == "model"
        assert step["circle"]["id"].startswith("Circle_")
    progress = [
        (c.kwargs["progress"], c.kwargs["message"])
        for c in ctx.report_progress.await_args_list
    ]
    # The step a run hands back runs on that run's notice: each notice's
    # progress is above the one before, as MCP wants.
    assert progress == [
        (0, "Step 1 of 7: create_sketch"),
        (1, "Steps 2-6 of 7: sketch entities"),
        (2, "Steps 3-6 of 7: sketch entities"),
        (3, "Step 4 of 7: add_circle"),
        (4, "Steps 5-6 of 7: sketch entities"),
        (6, "Step 7 of 7: exit_sketch"),
    ]


async def test_a_step_with_a_document_is_not_drawn_in_a_run(mock_config: Any) -> None:
    adapter, run = await _server(mock_config)
    steps = [
        {"create_sketch": {"plane": "Top"}},
        _line(0, 0, 10, 0),
        {**_line(10, 0, 10, 10), "document": _PART},
        _line(10, 10, 0, 10),
        _line(0, 10, 0, 0),
        {"exit_sketch": None},
    ]

    result = await run({"steps": steps})

    assert result["status"] == "success", result
    # Step 1 is a run of one, step 2 runs on its document through its own
    # tool, and steps 3 and 4 are one run.
    assert adapter.calls == ["add_line", "add_line", "add_sketch_entities"]
    assert [len(items) for items in adapter.batches] == [2]
    assert adapter._current_model.name == _PART


async def test_progress_is_reported_once_before_a_run(mock_config: Any) -> None:
    adapter, run = await _server(mock_config)
    ctx = AsyncMock()
    # One timeline for the notices and the drawing calls.
    ctx.report_progress.side_effect = lambda **kw: adapter.calls.append(kw["message"])
    steps = [
        {"create_sketch": {"plane": "Top"}},
        _line(0, 0, 10, 0),
        {"add_circle": {"center_x": 5, "center_y": 5, "radius": 2}},
        _line(10, 0, 10, 10),
        {"exit_sketch": None},
        {"create_extrusion": {"depth": 5}},
    ]

    result = await run({"steps": steps}, ctx)

    assert result["status"] == "success", result
    assert adapter.calls == [
        "Step 1 of 6: create_sketch",
        "Steps 2-4 of 6: sketch entities",
        "add_sketch_entities",
        "Step 5 of 6: exit_sketch",
        "Step 6 of 6: create_extrusion",
    ]
    assert [
        (c.kwargs["progress"], c.kwargs["total"])
        for c in ctx.report_progress.await_args_list
    ] == [(0, 6), (1, 6), (4, 6), (5, 6)]


async def test_a_reference_to_nothing_ends_the_run_and_fails_its_step(
    mock_config: Any,
) -> None:
    adapter, run = await _server(mock_config)
    steps = [
        {"create_sketch": {"plane": "Top"}, "id": "sketch"},
        _line(0, 0, 10, 0),
        _line(0, 0, 0, 10),
        {"add_line": {"x1": "$steps.sketch.sketch.nope", "y1": 0, "x2": 5, "y2": 5}},
        _line(0, 0, 1, 1),
    ]

    result = await run({"steps": steps})

    assert result["error_type"] == "invalid_input"
    assert result["data"]["failed_step"] == 3
    assert (
        "steps.3.arguments.x1: '$steps.sketch.sketch.nope' names 'nope', which "
        "$steps.sketch.sketch doesn't have" in result["message"]
    )
    # The two lines before it were one run; it ran on its own and drew nothing.
    assert adapter.calls == ["add_sketch_entities"]
    assert [len(items) for items in adapter.batches] == [2]


def test_the_tools_a_run_draws_carry_no_level_warning() -> None:
    """A step drawn in a run skips its tool's boundary, which adds a beta tool's warning."""
    assert all(SUPPORT_LEVELS[tool].warning() is None for tool in ENTITY_STEP_TOOLS)


def test_the_longest_run_fits_one_add_sketch_entities_call() -> None:
    """Every step a rectangle, four segments each: still under the call's cap."""
    assert MAX_WORKFLOW_STEPS * 4 <= MAX_OUTLINE_SEGMENTS


# ---- a reference to a point of an entity -------------------------------------------


async def test_a_reference_names_a_point_of_an_entity(mock_config: Any) -> None:
    """The session's '$steps.r1.circle.id.center', which named nothing."""
    adapter, run = await _server(mock_config)
    steps = [
        {"create_sketch": {"plane": "Top"}},
        {"add_circle": {"center_x": 5, "center_y": 5, "radius": 2}, "id": "c"},
        {**_line(0, 0, 10, 0), "id": "l"},
        {
            "add_sketch_dimension": {
                "entity1": "$steps.c.circle.id.center",
                "entity2": "$steps.l.line.id.start",
                "value": 7,
            }
        },
        {
            "add_sketch_dimension": {
                "entity1": "$steps.l.line.id.end",
                "entity2": "Origin",
                "value": 10,
            }
        },
        # A key of an object is still that key: the centre's x, not a point.
        {
            "add_circle": {
                "center_x": "$steps.c.circle.center.x",
                "center_y": 0,
                "radius": 1,
            }
        },
    ]

    result = await run({"steps": steps, "detail": "full"})

    assert result["status"] == "success", result
    assert adapter.dimensioned == [
        ("Circle_1.center", "Line_2.start"),
        ("Line_2.end", "Origin"),
    ]
    assert result["steps"][3]["result"]["dimension"]["entity1"] == "Circle_1.center"
    assert result["steps"][5]["result"]["circle"]["center"] == {"x": 5, "y": 0}


@pytest.mark.parametrize("point", sorted(POINT_GETTERS))
def test_each_point_name_the_tools_take_can_follow_an_id(point: str) -> None:
    reference = _Reference(
        f"$steps.c.circle.id.{point}", 0, ("circle", "id", point), "c"
    )

    assert _look_up(reference, [{"circle": {"id": "Circle_1"}}]) == (
        f"Circle_1.{point}",
        None,
    )


@pytest.mark.parametrize(
    ("path", "named"),
    [
        ("circle.id.middle", "'middle', which $steps.c.circle.id"),
        ("circle.id.center.x", "'center', which $steps.c.circle.id"),
        ("circle.radius.center", "'center', which $steps.c.circle.radius"),
    ],
)
async def test_any_other_part_after_a_value_still_names_nothing(
    mock_config: Any, path: str, named: str
) -> None:
    adapter, run = await _server(mock_config)
    reference = f"$steps.c.{path}"
    steps = [
        {"create_sketch": {"plane": "Top"}},
        {"add_circle": {"center_x": 5, "center_y": 5, "radius": 2}, "id": "c"},
        {"add_sketch_dimension": {"entity1": reference, "value": 7}},
    ]

    result = await run({"steps": steps})

    assert result["error_type"] == "invalid_input"
    assert result["data"]["failed_step"] == 2
    assert f"{reference!r} names {named} doesn't have; it is " in result["message"]
    assert "not an object or a list" in result["message"]
    assert adapter.dimensioned == []
