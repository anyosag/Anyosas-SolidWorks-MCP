"""Hole Wizard holes and modeled threads on real SolidWorks (``hole-wizard-and-threads`` 8.1).

One test per spec scenario that touches SolidWorks, each checked against its
hand calculation, on scratch parts only:

- a 40 x 20 x 10 mm block (8000 mm³) on Front, top face at z = 10:
  M3 clearance close / normal / loose through (80.425, 90.792, 101.788 mm³)
  and blind (57.567), counterbore (172.745), countersink (110.299), tapped
  cosmetic blind (40.499), each also from the sizes the reply returns;
  modeled M3 blind (45.10 within 0.2%) and M4 through (100.95 within 0.6);
  the position (edge centre within 0.01 mm, ``fully_defined``,
  ``set_dimension`` moves it), a split top face, a side view; the refusals;
- a Ø10 x 40 mm rod and a 30 x 30 x 20 block with a Ø8.376202 hole: external
  and internal M10x1.5 (268 and 120.4 mm³ within 0.5%), ``M10x1.3``, the
  wrong-diameter refusals, a point in the air and the turn limit.

The "to confirm" sizes (probe 1.4) are asserted where the specs give them;
a failure there means the probe's value replaces the spec's, not a bug.

Run it with::

    powershell -ExecutionPolicy Bypass -File tests\\scripts\\live_demo.ps1 `
        -Test tests/live/test_live_sw_hole_wizard.py

Collected only with ``SOLIDWORKS_MCP_RUN_REAL_INTEGRATION=1`` on Windows (see
``tests/live/conftest.py``).
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from tests.live.evidence import evidence

pytestmark = pytest.mark.asyncio

#: A 118° drill point's height over its radius: 1 / tan(59°).
POINT = 1.0 / math.tan(math.radians(59.0))


async def _tools(adapter: Any) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("hole-wizard-live")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _ok(reply: dict[str, Any]) -> dict[str, Any]:
    assert reply["status"] == "success", reply
    return reply


async def _part(adapter: Any, tools: dict[str, Any]) -> None:
    assert (await adapter.create_part()).is_success
    await _ok(await tools["set_units"]({"unit_system": "mm"}))


async def _block(adapter: Any, tools: dict[str, Any]) -> None:
    await _part(adapter, tools)
    await _ok(await tools["create_sketch"]({"plane": "Front"}))
    await _ok(await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 40, "y2": 20}))
    await _ok(await tools["exit_sketch"]())
    await _ok(await tools["create_extrusion"]({"depth": 10.0}))


async def _rod(adapter: Any, tools: dict[str, Any], diameter: float = 10.0) -> None:
    await _part(adapter, tools)
    await _ok(await tools["create_sketch"]({"plane": "Front"}))
    await _ok(
        await tools["add_circle"](
            {"center_x": 0, "center_y": 0, "radius": diameter / 2}
        )
    )
    await _ok(await tools["exit_sketch"]())
    await _ok(await tools["create_extrusion"]({"depth": 40.0}))


async def _drilled(adapter: Any, tools: dict[str, Any], hole: float) -> None:
    await _part(adapter, tools)
    await _ok(await tools["create_sketch"]({"plane": "Front"}))
    await _ok(await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 30, "y2": 30}))
    await _ok(await tools["exit_sketch"]())
    await _ok(await tools["create_extrusion"]({"depth": 20.0}))
    await _ok(await tools["create_sketch"]({"plane": "Front"}))
    await _ok(
        await tools["add_circle"]({"center_x": 15, "center_y": 15, "radius": hole / 2})
    )
    await _ok(await tools["exit_sketch"]())
    await _ok(
        await tools["create_cut_extrude"](
            {"depth": 20.0, "end_condition": "ThroughAll"}
        )
    )


def _removed(reply: dict[str, Any]) -> float:
    return -float(reply["data"]["measured_change"]["volume_delta_mm3"])


async def _count(tools: dict[str, Any]) -> int:
    return len((await tools["list_features"]({}))["features"])


async def _hole(tools: dict[str, Any], **args: Any) -> dict[str, Any]:
    return await tools["create_hole_wizard_hole"](
        {"face_point": [20, 10, 10], "size": "M3"} | args
    )


@pytest.mark.parametrize(
    ("fit", "diameter", "volume"),
    [("close", 3.2, 80.425), ("normal", 3.4, 90.792), ("loose", 3.6, 101.788)],
)
async def test_clearance_holes_through(scratch, fit, diameter, volume) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="clearance", fit=fit))

    evidence(f"M3 {fit} clearance through", reply)
    hole = reply["data"]["hole"]
    assert hole["diameter_mm"] == pytest.approx(diameter, abs=1e-3)
    assert _removed(reply) == pytest.approx(volume, abs=0.01)
    measured = hole["diameter_mm"]
    assert _removed(reply) == pytest.approx(
        math.pi * (measured / 2) ** 2 * 10, abs=0.01
    )


async def test_a_blind_hole_ends_in_a_drill_point(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="clearance", depth_mm=6))

    r = reply["data"]["hole"]["diameter_mm"] / 2
    expected = math.pi * r * r * 6 + math.pi * r * r * (r * POINT) / 3
    evidence("M3 blind clearance", reply)
    assert _removed(reply) == pytest.approx(57.567, abs=0.01)
    assert _removed(reply) == pytest.approx(expected, abs=0.01)


async def test_a_counterbore(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="counterbore"))

    hole = reply["data"]["hole"]
    r, big, step = (
        hole["diameter_mm"] / 2,
        hole["cbore_diameter_mm"] / 2,
        hole["cbore_depth_mm"],
    )
    evidence("M3 counterbore", reply)
    assert (hole["diameter_mm"], hole["cbore_diameter_mm"], step) == pytest.approx(
        (3.4, 6.5, 3.4)
    )
    assert _removed(reply) == pytest.approx(
        math.pi * r * r * 10 + math.pi * (big * big - r * r) * step, abs=0.01
    )


async def test_a_countersink(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="countersink"))

    hole = reply["data"]["hole"]
    r, rim = hole["diameter_mm"] / 2, hole["csink_diameter_mm"] / 2
    angle = math.radians(hole["csink_angle_deg"])
    depth = (rim - r) / math.tan(angle / 2)
    cone = math.pi * depth / 3 * (rim * rim + rim * r + r * r) - math.pi * r * r * depth
    evidence("M3 countersink", reply)
    assert (hole["csink_diameter_mm"], hole["csink_angle_deg"]) == pytest.approx(
        (6.72, 90)
    )
    assert _removed(reply) == pytest.approx(math.pi * r * r * 10 + cone, abs=0.01)
    assert _removed(reply) == pytest.approx(110.299, abs=0.01)


async def test_a_tapped_hole_with_a_cosmetic_thread(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="tapped", depth_mm=8))

    hole = reply["data"]["hole"]
    r = hole["tap_drill_diameter_mm"] / 2
    evidence("M3 tapped cosmetic", reply)
    assert hole["cosmetic_thread"] is True and hole["thread"] == "cosmetic"
    assert (hole["tap_drill_diameter_mm"], hole["thread_depth_mm"]) == pytest.approx(
        (2.5, 6.0)
    )
    assert _removed(reply) == pytest.approx(
        math.pi * r * r * 8 + math.pi * r * r * (r * POINT) / 3, abs=0.01
    )


async def test_a_blind_modeled_m3(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="tapped", depth_mm=8, thread="modeled"))

    evidence("M3 modeled blind", reply)
    assert reply["data"]["thread"]["size"] == "M3x0.5"
    assert reply["data"]["thread"]["length_mm"] == pytest.approx(6.0)
    assert _removed(reply) == pytest.approx(45.10, rel=0.002)


async def test_a_through_modeled_m4(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="tapped", size="M4", thread="modeled"))

    evidence("M4 modeled through", reply)
    assert reply["data"]["thread"]["length_mm"] == pytest.approx(10.0)
    assert _removed(reply) == pytest.approx(100.95, abs=0.6)


async def test_the_hole_lands_on_the_point_and_moves(scratch) -> None:
    from solidworks_mcp.adapters.solidworks import holes

    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)

    reply = await _ok(await _hole(tools, kind="clearance", face_point=[20, 14, 10]))
    found = holes._circles_near_axis(scratch.adapter, [20, 14, 10], (0, 0, 1), 3.4)
    position = reply["data"]["position"]
    moved = await _ok(
        await tools["set_dimension"]({"name": position["dimensions"]["x"], "value": 30})
    )
    after = holes._circles_near_axis(scratch.adapter, [30, 14, 10], (0, 0, 1), 3.4)

    evidence("the hole at [20, 14, 10], then x = 30", {"reply": reply, "moved": moved})
    assert position["fully_defined"] is True
    assert any(math.dist(c, [20, 14, 10]) <= 0.01 for _d, c in found)
    assert any(math.dist(c, [30, 14, 10]) <= 0.01 for _d, c in after)


async def test_either_top_face_of_a_slotted_block(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)
    await _ok(await tools["create_sketch"]({"plane": "Front"}))
    await _ok(await tools["add_rectangle"]({"x1": 18, "y1": -1, "x2": 22, "y2": 21}))
    await _ok(await tools["exit_sketch"]())
    await _ok(
        await tools["create_cut_extrude"]({"depth": 5.0, "reverse_direction": True})
    )

    left = await _ok(await _hole(tools, kind="clearance", face_point=[8, 10, 10]))
    right = await _ok(await _hole(tools, kind="clearance", face_point=[32, 10, 10]))

    evidence("both halves of a split top", {"left": left, "right": right})
    assert left["data"]["position"]["fully_defined"] is True
    assert right["data"]["position"]["fully_defined"] is True


async def test_a_side_view_does_not_change_the_face(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)
    scratch.adapter.currentModel.ShowNamedView2("*Left", -1)

    reply = await _ok(await _hole(tools, kind="clearance"))

    evidence("a hole in the left view", reply)
    assert reply["data"]["face"]["normal"] == [0.0, 0.0, 1.0]
    assert _removed(reply) == pytest.approx(90.792, abs=0.01)


async def test_hole_refusals_change_nothing(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _block(scratch.adapter, tools)
    await _ok(await _hole(tools, kind="countersink"))
    count = await _count(tools)

    unknown = await _hole(tools, kind="clearance", size="M3.3")
    in_cone = await _hole(tools, kind="clearance", face_point=[22.5, 10, 10])
    wrong_side = await _hole(tools, kind="clearance", face_point=[5, 10, 10], side="-Z")
    modeled = await _hole(tools, kind="clearance", thread="modeled")

    evidence(
        "four refusals",
        {"unknown": unknown, "cone": in_cone, "side": wrong_side, "modeled": modeled},
    )
    assert "M3.3" in unknown["message"]
    assert in_cone["error_type"] == "invalid_input" and "not flat" in in_cone["message"]
    assert wrong_side["error_type"] == "invalid_input" and "+Z" in wrong_side["message"]
    assert modeled["error_type"] == "invalid_input"
    assert await _count(tools) == count


async def test_an_external_m10_thread(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _rod(scratch.adapter, tools)

    reply = await _ok(
        await tools["create_thread"](
            {"size": "M10x1.5", "edge_point": [5, 0, 40], "length_mm": 20}
        )
    )

    evidence("external M10x1.5, 20 mm", reply)
    assert reply["data"]["thread"]["minor_diameter_mm"] == pytest.approx(8.3762)
    assert _removed(reply) == pytest.approx(268, rel=0.005)


async def test_an_internal_m10_thread(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _drilled(scratch.adapter, tools, 8.376202)

    reply = await _ok(
        await tools["create_thread"](
            {
                "size": "M10x1.5",
                "edge_point": [15 + 4.188101, 15, 20],
                "length_mm": 12,
                "internal": True,
            }
        )
    )

    evidence("internal M10x1.5, 12 mm", reply)
    assert _removed(reply) == pytest.approx(120.4, rel=0.005)


async def test_thread_refusals_change_nothing(scratch) -> None:
    tools = await _tools(scratch.adapter)
    await _rod(scratch.adapter, tools)
    count = await _count(tools)

    def call(size: str, point: list[float], length: float = 20) -> Any:
        return tools["create_thread"](
            {"size": size, "edge_point": point, "length_mm": length}
        )

    garbage = await call("M10x1.3", [5, 0, 40])
    air = await call("M10x1.5", [30, 30, 40])
    turns = await call("M3x0.5", [5, 0, 40], 60)

    evidence("size, air and turns", {"size": garbage, "air": air, "turns": turns})
    assert garbage["error_type"] == "invalid_input" and "M10x1.5" in garbage["message"]
    assert air["error_type"] == "not_found"
    assert turns["error_type"] == "invalid_input" and "turns" in turns["message"]
    assert await _count(tools) == count


@pytest.mark.parametrize(("internal", "diameter"), [(False, 9.9), (True, 8.5)])
async def test_the_wrong_diameter_is_refused(scratch, internal, diameter) -> None:
    tools = await _tools(scratch.adapter)
    if internal:
        await _drilled(scratch.adapter, tools, diameter)
        point = [15 + diameter / 2, 15, 20]
    else:
        await _rod(scratch.adapter, tools, diameter)
        point = [diameter / 2, 0, 40]

    reply = await tools["create_thread"](
        {"size": "M10x1.5", "edge_point": point, "length_mm": 10, "internal": internal}
    )

    evidence(f"M10x1.5 on Ø{diameter}", reply)
    assert reply["error_type"] == "invalid_input"
    assert f"Ø{diameter:.3f}" in reply["message"]
    assert ("Ø8.376" if internal else "Ø10") in reply["message"]
