"""``execute_workflow``: a feature's after summary is the next feature's before.

Feature tools read the part's geometry summary before and after each feature
for ``measured_change``, each read a COM operation and a settle gap. Most
workflows chain sketch, cut, sketch, cut, and sketches change no solid, so
inside one call a cut's after summary stands for the next cut's before
(report section 11). Any other step, a failure, a step on its own document
or a new call reads the part again.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools import automation, register_tools
from solidworks_mcp.tools.automation import register_automation_tools
from solidworks_mcp.tools.modeling import register_modeling_tools
from solidworks_mcp.tools.sketching import register_sketching_tools
from solidworks_mcp.tools.tool_hints import TOOL_HINTS

_PART = "Bracket"


class _Part(MockSolidWorksAdapter):
    """The mock with a part that measures: bosses add volume, cuts take it away.

    Its summaries are a live part's, measured from its solid bodies (an
    extreme-point box), and each read is counted.
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
        self.volume = 0.0
        self.bodies = 0
        self.cuts = 0
        #: How many times the summary was read.
        self.reads = 0
        #: The reads (counted from 1) that fail, as a busy SolidWorks's would.
        self.failing_reads: set[int] = set()

    async def get_model_geometry_summary(self) -> AdapterResult[dict[str, Any]]:
        self.reads += 1
        if self.reads in self.failing_reads:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="SolidWorks is busy"
            )
        height = round(self.volume / 1200.0, 4)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "document_type": "part",
                "volume_mm3": round(self.volume, 4),
                "solid_body_count": self.bodies,
                "bounding_box_method": "extreme_points" if self.bodies else "part_box",
                "bounding_box_mm": {
                    "min": [0.0, 0.0, 0.0],
                    "max": [40.0, 30.0, height],
                    "size": [40.0, 30.0, height],
                },
            },
        )

    async def _drawn(self, prefix: str) -> AdapterResult[str]:
        """An entity with the same id in every run (the mock's are random)."""
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=self._new_sketch_entity_id(prefix),
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        return await self._drawn("Rectangle")

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        return await self._drawn("Circle")

    async def create_extrusion(
        self,
        params: ExtrusionParameters | str,
        depth: float | None = None,
        direction: str | None = None,
    ) -> Any:
        result = await super().create_extrusion(params, depth, direction)
        if result.is_success and isinstance(params, ExtrusionParameters):
            self.volume += 1200.0 * params.depth
            self.bodies = 1
        return result

    async def create_cut_extrude(self, params: ExtrusionParameters) -> Any:
        sketch = self._mock_named_sketch(params.sketch_name, "cut")
        if isinstance(sketch, AdapterResult):
            return sketch
        self.cuts += 1
        self.volume -= 50.0 * params.depth
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": f"Cut-Extrude{self.cuts}",
                "parameters": {"sketch_name": sketch},
            },
        )

    async def set_dimension(self, name: str, value: float) -> Any:
        result = await super().set_dimension(name, value)
        self.volume += 100.0  # the boss it drives grows
        return result


async def _server(mock_config: Any) -> tuple[_Part, dict[str, Any]]:
    """A part open, and the tools by name."""
    adapter = _Part()
    await adapter.connect()
    await adapter.create_part(name=_PART)
    mcp = FastMCP("summaries")
    await register_sketching_tools(mcp, adapter, mock_config)
    await register_modeling_tools(mcp, adapter, mock_config)
    await register_automation_tools(mcp, adapter, mock_config)
    return adapter, {t.name: t.fn for t in await mcp.list_tools()}


def _sharing_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every step drops the summary, as before reuse: each feature reads twice."""
    monkeypatch.setattr(automation, "_KEEPS_SOLIDS", frozenset())


_TIMING = frozenset({"execution_time", "server_time_s"})


def _untimed(value: Any) -> Any:
    """``value`` without its timing fields, at any depth."""
    if isinstance(value, dict):
        return {k: _untimed(v) for k, v in value.items() if k not in _TIMING}
    if isinstance(value, list):
        return [_untimed(item) for item in value]
    return value


_BLOCK: list[dict[str, Any]] = [
    {"create_sketch": {"plane": "Top"}},
    {"add_rectangle": {"x1": 0, "y1": 0, "x2": 40, "y2": 30}},
    {"exit_sketch": None},
    {"create_extrusion": {"depth": 10}},
]


def _hole_sketch() -> list[dict[str, Any]]:
    return [
        {"create_sketch": {"plane": "Front"}},
        {"add_circle": {"center_x": 10, "center_y": 5, "radius": 2}},
        {"exit_sketch": None},
    ]


def _cut(depth: float, **more: Any) -> dict[str, Any]:
    return {"create_cut_extrude": {"depth": depth, **more}}


#: A block (12000 mm3), then sketch, cut (-100), sketch, cut (-150) checked
#: with `expect`. The cuts are steps 7 and 11.
_CUTS: list[dict[str, Any]] = [
    *_BLOCK,
    *_hole_sketch(),
    _cut(2),
    *_hole_sketch(),
    _cut(3, expect={"volume_mm3": 11750, "solid_body_count": 1}),
]


@pytest.mark.parametrize("detail", ["summary", "full"])
async def test_a_cut_after_a_sketch_takes_the_last_features_after_summary(
    mock_config: Any, monkeypatch: pytest.MonkeyPatch, detail: str
) -> None:
    shared_part, shared = await _server(mock_config)
    result = await shared["execute_workflow"]({"steps": _CUTS, "detail": detail})
    _sharing_nothing(monkeypatch)
    unshared_part, unshared = await _server(mock_config)
    expected = await unshared["execute_workflow"]({"steps": _CUTS, "detail": detail})

    assert result["status"] == "success", result
    assert expected["status"] == "success", expected
    # The block reads before and after; each cut after it reads only after.
    assert unshared_part.reads == 6
    assert shared_part.reads == 4
    # measured_change and the expect check are the unshared path's.
    assert _untimed(result["steps"]) == _untimed(expected["steps"])
    steps = result["steps"]
    first = steps[7] if detail == "summary" else steps[7]["result"]
    second = steps[11] if detail == "summary" else steps[11]["result"]
    assert first["measured_change"]["volume_before_mm3"] == 12000.0
    assert first["measured_change"]["volume_after_mm3"] == 11900.0
    assert second["measured_change"]["volume_before_mm3"] == 11900.0
    assert second["measured_change"]["volume_delta_mm3"] == -150.0
    assert second["verification"]["verdict"] == "pass"


async def test_a_step_that_may_change_a_solid_makes_the_next_feature_read_again(
    mock_config: Any,
) -> None:
    part, tools = await _server(mock_config)
    steps = [
        *_BLOCK,
        {"set_dimension": {"name": "D1@Sketch1", "value": 12}},
        *_hole_sketch(),
        _cut(2),
    ]

    result = await tools["execute_workflow"]({"steps": steps})

    assert result["status"] == "success", result
    assert part.reads == 4
    # The part after the dimension changed, not the block's 12000.
    assert result["steps"][8]["measured_change"]["volume_before_mm3"] == 12100.0


async def test_a_summary_that_failed_to_read_is_not_reused(mock_config: Any) -> None:
    part, tools = await _server(mock_config)
    part.failing_reads = {3}  # the first cut's after summary
    steps = [*_BLOCK, *_hole_sketch(), _cut(2), *_hole_sketch(), _cut(3)]

    result = await tools["execute_workflow"]({"steps": steps})

    assert result["status"] == "success", result
    first = result["steps"][7]["measured_change"]
    assert first["volume_after_mm3"] is None
    assert first["note"].startswith("Could not read the model after the feature")
    # The second cut read its before itself: 2 + 1 + 2.
    assert part.reads == 5
    assert result["steps"][11]["measured_change"]["volume_before_mm3"] == 11900.0


async def test_a_failed_step_ends_the_reuse_and_the_next_call_reads_again(
    mock_config: Any,
) -> None:
    part, tools = await _server(mock_config)
    refused = [*_BLOCK, *_hole_sketch(), _cut(2, sketch_name="Nope")]

    failed = await tools["execute_workflow"]({"steps": refused})
    after_failure = part.reads
    again = await tools["execute_workflow"]({"steps": [*_hole_sketch(), _cut(2)]})

    assert failed["status"] == "error"
    assert failed["data"]["failed_step"] == 7
    # The refused cut took the block's after summary, then failed.
    assert after_failure == 2
    assert again["status"] == "success", again
    # A new call trusts nothing from the last: the cut reads before and after.
    assert part.reads == 4
    assert again["steps"][3]["measured_change"]["volume_before_mm3"] == 12000.0


async def test_a_step_on_its_own_document_reads_the_part_and_drops_the_summary(
    mock_config: Any,
) -> None:
    part, tools = await _server(mock_config)
    steps = [
        *_BLOCK,
        *_hole_sketch(),
        {**_cut(2), "document": _PART},
        *_hole_sketch(),
        _cut(3),
    ]

    result = await tools["execute_workflow"]({"steps": steps})

    assert result["status"] == "success", result
    assert part.reads == 6
    assert result["steps"][11]["measured_change"]["volume_before_mm3"] == 11900.0


async def test_outside_a_workflow_every_feature_reads_the_part_twice(
    mock_config: Any,
) -> None:
    part, tools = await _server(mock_config)

    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 40, "y2": 30})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 10})
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_circle"]({"center_x": 10, "center_y": 5, "radius": 2})
    await tools["exit_sketch"]()
    cut = await tools["create_cut_extrude"]({"depth": 2})

    assert cut["status"] == "success", cut
    assert part.reads == 4
    assert cut["measured_change"]["volume_before_mm3"] == 12000.0


async def test_the_allowlist_names_tools_and_leaves_out_what_changes_solids(
    mock_config: Any,
) -> None:
    mcp = FastMCP("every tool")
    await register_tools(mcp, MockSolidWorksAdapter({}), mock_config)
    registered = {t.name for t in await mcp.list_tools()}
    read_only = {name for name, hints in TOOL_HINTS.items() if hints.read_only}

    assert automation._KEEPS_SOLIDS - read_only <= registered
    assert not automation._KEEPS_SOLIDS & {
        "create_extrusion",
        "create_cut_extrude",
        "add_fillet",
        "set_dimension",
        "set_equations",
        "edit_sketch",
        "edit_feature",
        "delete_sketch_entities",
        "undo",
        "rebuild_model",
        "export_image",
        "open_model",
        "activate_document",
    }
