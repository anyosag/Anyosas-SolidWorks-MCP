"""Equations and global variables on real SolidWorks (``parametric-equations`` 7.1).

One test per spec scenario that touches SolidWorks, on a 40 x 20 x 10 mm block
whose width is the sketch dimension the test adds (``D1@Sketch1``):

- ``"W" = 45`` linked to the width gives 9000 mm³; ``"W" = 50`` replaces it
  in place and gives 10000 mm³;
- a refused middle equation leaves the list as it was;
- an equation left behind by ``delete_feature`` is ``broken`` and is deleted
  by its index;
- ``driven_by``, and ``set_dimension`` refusing a driven dimension;
- an equation that makes a cut deeper than the part fails the cut and is
  still a success, with a warning;
- the same block on an inch part, with units written in the equations.

Run it with::

    powershell -ExecutionPolicy Bypass -File tests\\scripts\\live_demo.ps1 `
        -Test tests/live/test_live_sw_equations.py

Collected only with ``SOLIDWORKS_MCP_RUN_REAL_INTEGRATION=1`` on Windows (see
``tests/live/conftest.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.live.evidence import evidence

pytestmark = pytest.mark.asyncio


async def _tools(adapter: Any) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.engineering_data import register_engineering_data_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("equations-live")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_engineering_data_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _volume(adapter: Any) -> float:
    result = await adapter.get_model_geometry_summary()
    assert result.is_success, result.error
    return float(result.data["volume_mm3"])


async def _block(adapter: Any, tools: dict[str, Any], units: str = "mm") -> dict:
    """A 40 x 20 x 10 mm block on Front; the width dimension and the boss."""
    assert (await adapter.create_part()).is_success
    # Always: the default part template can be in inches (pitfall #60), and a
    # bare number in an equation is in document units.
    assert (await tools["set_units"]({"unit_system": units}))["status"] == "success"
    sketch = await tools["create_sketch"]({"plane": "Front"})
    assert sketch["status"] == "success", sketch
    corners = [(-20, -10), (20, -10), (20, 10), (-20, 10)]
    lines = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1], strict=True):
        line = await tools["add_line"]({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        assert line["status"] == "success", line
        lines.append(line["line"]["id"])
    width = await tools["add_sketch_dimension"]({"entity1": lines[0], "value": 40})
    assert width["status"] == "success", width
    assert (await tools["exit_sketch"]())["status"] == "success"
    boss = await tools["create_extrusion"]({"depth": 10.0})
    assert boss["status"] == "success", boss
    return {
        "width": width["dimension"]["name"],
        "sketch": sketch["sketch"]["name"],
        "boss": boss["extrusion"]["name"],
    }


async def test_a_variable_drives_the_width_and_is_replaced(scratch) -> None:
    adapter = scratch.adapter
    tools = await _tools(adapter)
    block = await _block(adapter, tools)

    added = await tools["set_equations"](
        {"equations": ['"W" = 45', f'"{block["width"]}" = "W"']}
    )
    after_45 = await _volume(adapter)
    replaced = await tools["set_equations"]({"equations": ['"W" = 50']})
    after_50 = await _volume(adapter)
    listed = await tools["list_equations"]({})

    evidence("W = 45 then 50 drives the width", {"added": added, "replaced": replaced})
    assert added["status"] == "success", added
    assert [e["replaced"] for e in added["data"]["equations"]] == [False, False]
    assert added["data"]["failing_features"] == []
    assert added["data"]["measured_change"]["volume_delta_mm3"] == pytest.approx(
        1000, rel=1e-6
    )
    assert after_45 == pytest.approx(9000, rel=1e-6)
    assert replaced["data"]["equations"][0]["replaced"] is True
    assert (
        replaced["data"]["equations"][0]["index"]
        == added["data"]["equations"][0]["index"]
    )
    assert after_50 == pytest.approx(10000, rel=1e-6)
    names = [e["name"] for e in listed["data"]["equations"]]
    assert names.count("W") == 1


async def test_a_refused_middle_equation_restores_the_list(scratch) -> None:
    adapter = scratch.adapter
    tools = await _tools(adapter)
    await _block(adapter, tools)
    assert (await tools["set_equations"]({"equations": ['"W" = 40']}))[
        "status"
    ] == "success"

    refused = await tools["set_equations"](
        {"equations": ['"W" = 45', '"D1@NoSuchSketch" = "W"', '"H" = 5']}
    )
    listed = await tools["list_equations"]({})

    evidence("the second of three is refused", {"refused": refused, "list": listed})
    assert refused["status"] == "error"
    assert refused["error_type"] == "invalid_input"
    assert '"D1@NoSuchSketch" = "W"' in refused["message"]
    assert [e["equation"].replace(" ", "") for e in listed["data"]["equations"]] == [
        '"W"=40'
    ]


async def test_a_broken_equation_is_found_and_deleted(scratch) -> None:
    adapter = scratch.adapter
    tools = await _tools(adapter)
    block = await _block(adapter, tools)
    second = await tools["create_sketch"]({"plane": "Top"})
    circle = await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 3})
    diameter = await tools["add_sketch_dimension"](
        {"entity1": circle["circle"]["id"], "dimension_type": "diameter", "value": 6}
    )
    assert (await tools["exit_sketch"]())["status"] == "success"
    depth = f"D1@{block['boss']}"
    await tools["set_equations"](
        {"equations": [f'"{depth}" = 2 * "{diameter["dimension"]["name"]}"']}
    )

    deleted_sketch = await tools["delete_feature"]({"name": second["sketch"]["name"]})
    listed = await tools["list_equations"]({})
    broken = [e for e in listed["data"]["equations"] if e["broken"]]
    removed = await tools["delete_equation"]({"equation": broken[0]["index"]})

    evidence("delete_feature leaves a broken equation", {"list": listed})
    assert deleted_sketch["status"] == "success", deleted_sketch
    assert len(broken) == 1
    assert removed["status"] == "success", removed
    assert removed["data"]["count"] == listed["data"]["count"] - 1
    assert removed["data"]["failing_features"] == []


async def test_driven_by_and_the_set_dimension_refusal(scratch) -> None:
    adapter = scratch.adapter
    tools = await _tools(adapter)
    block = await _block(adapter, tools)
    await tools["set_equations"](
        {"equations": ['"W" = 45', f'"{block["width"]}" = "W"']}
    )

    listed = await tools["list_model_dimensions"]({})
    refused = await tools["set_dimension"]({"name": block["width"], "value": 30})
    kept = await tools["get_dimension"]({"name": block["width"]})

    driven = {d["full_name"]: d["driven_by"] for d in listed["data"]}
    evidence("driven_by and the refusal", {"driven": driven, "refused": refused})
    assert driven[block["width"]] is not None
    assert [k for k, v in driven.items() if v is not None] == [block["width"]]
    assert refused["error_type"] == "invalid_input"
    assert "set_equations" in refused["message"]
    assert kept["dimension"]["value"] == pytest.approx(45)


async def test_a_cut_deeper_than_the_part_is_a_success_with_a_warning(
    scratch,
) -> None:
    adapter = scratch.adapter
    tools = await _tools(adapter)
    block = await _block(adapter, tools)
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 3})
    assert (await tools["exit_sketch"]())["status"] == "success"
    cut = await tools["create_cut_extrude"]({"depth": 4.0})
    assert cut["status"] == "success", cut
    cut_name = cut["cut_extrude"]["name"]

    done = await tools["set_equations"](
        {"equations": [f'"D1@{block["boss"]}" = 2mm', f'"D1@{cut_name}" = 50mm']}
    )

    evidence("an equation fails a cut", done)
    assert done["status"] == "success", done
    assert cut_name in [f["feature"] for f in done["data"]["failing_features"]]
    assert "fail to rebuild" in done["warning"]


async def test_units_on_an_inch_part(scratch) -> None:
    adapter = scratch.adapter
    tools = await _tools(adapter)
    block = await _block(adapter, tools, units="in")

    done = await tools["set_equations"]({"equations": [f'"{block["width"]}" = 45mm']})
    listed = await tools["list_equations"]({})
    read = await tools["get_dimension"]({"name": block["width"]})

    evidence("45mm on an inch part", {"done": done, "list": listed, "read": read})
    assert done["status"] == "success", done
    assert done["data"]["document_units"] == "in"
    assert read["dimension"]["value"] == pytest.approx(45, rel=1e-6)
    assert await _volume(adapter) == pytest.approx(9000, rel=1e-6)
