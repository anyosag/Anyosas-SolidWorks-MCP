"""Probe the COM calls behind the Dipper build's new modeling features.

The ``dipper-modeling`` branch was written without SolidWorks (another
session had it). This runs every new COM sequence on scratch parts through
``PyWin32Adapter`` and the registered tools, and compares each result with
the volume (mm3) or size worked out beforehand. Each step says what it
settles; the report keeps the replies.

1. ``offsets`` (a 40 x 10 x 20 block, x 0..40, y 0..10, z -20..0: 8000):
   a. a 10 x 10 boss sketched on the Top Plane with ``start_offset_mm: 12``,
      5 deep: a body from y 12 to 17 (+500, box y up to 17). Settles
      ``T0 = swStartOffset`` on ``FeatureExtrusion3``, and that
      ``FlipStartOffset`` False offsets along the sketch normal.
   b. the same with ``start_offset_mm: -4``, 2 deep: a body from y -4 to -2
      (+200, box y down to -4). Settles ``FlipStartOffset`` True offsets the
      other way while the boss still grows along the normal.
   c. a r 3 hole sketched on the Top Plane, cut 4 deep with
      ``start_offset_mm: 3``, side left to the tool: y 3..7 inside the block
      (-113.10). Settles ``StartOffset`` on ``FeatureCut4``, and, from
      ``volume_removed_mm3``, whether flipping ``Dir`` keeps the offset
      (against the normal it cuts y 3..0, 84.82; 0 if the offset flips too).
   d. a 10 x 6 rectangle on a plane 20 mm off the Right Plane (the middle of
      the block), cut ``end_condition: "MidPlane"`` 6 deep: x 17..23
      (-360). Settles ``T1 = swEndCondMidPlane`` with ``Sd`` True on a cut.
2. ``drafts``: two 40 x 20 x 10 blocks drafted 3 deg, inward (``Ddir1``
   False, pitfall #53: 7689.2) and with ``draft_outward`` (8318.2). Then a
   10 x 10 x 4 cut in each top, drafted 5 deg: inward 372.65, outward
   428.65. Settles ``Ddir1`` on both calls (the API help says True is
   inward; #53 measured False as inward) and what "inward" is for a cut
   (narrower with depth, as the tool says).
3. ``pattern`` (a 110 x 10 x 40 plate: 44000):
   a. a 4 x 30 slot through it (-1200), then ``pattern_linear`` along "x",
      8 instances 12 mm apart (-8400 more: 34400). Settles the Right Plane's
      normal at mark 1 as Direction 1 (+X), the seed at mark 4, and
      ``FeatureLinearPattern5``'s argument list.
   b. the slot again along "-x": off the part, so the tool fails, deletes
      the empty pattern and the volume stays 34400.
   c. a r 1.5 hole through at (102, -3), then a grid: "-z" 4 x 10 mm and
      the plate's front edge (55, 0, 0) 2 x 4 mm: 8 holes (-565.49 in
      all: 33834.51). Settles an edge at mark 2 as Direction 2, and "-z"
      (FlipDir1) going -Z.
   d. a 5 mm cube kept a separate body, patterned as a body along "y" 3 x 10
      (+250 over the cube's 125). Settles mark 256 for bodies.
4. ``text`` (a 60 x 5 x 30 slab: 9000):
   a. ``emboss_text`` "MAX" 6 high, 0.6 up, on a plane 5 mm off the Top
      Plane (its top face): the volume grows, the box to y 5.6, no flip.
      Settles ``InsertSketchText`` at a sketch point in metres, the
      ``ITextFormat`` height, and that a text sketch extrudes.
   b. ``emboss_text`` "V2" on the face at (30, 5, -20): settles the plane
      made on a face, which way its normal points (``direction_flipped``),
      and where x, y from ``face_point`` land.
   c. ``emboss_text`` "MIN" engraved 0.4 at (10, 5, -25): the volume drops.
   d. step by step on the Front Plane (the slab's front face):
      ``add_sketch_text`` "OPEN FLAT" in Arial 3 bold, ``exit_sketch``,
      ``create_cut_extrude`` 0.5 deep. Settles a text sketch cut, and
      ``get_sketch`` reading the font and height back.
5. ``angle`` (empty parts): "LONGTEXT" 5 high on the Front Plane at (10, 20),
   extruded 1, at 0 deg and at 90 deg. Settles where (x, y) is (the box's
   lower left at 0 deg?) and that the construction line turns the text: at
   90 deg the box is tall (y extent about 30) and narrow (x about 5).
6. ``groove``: a r 10 x 30 cylinder along z, and on the Right Plane a
   triangle (z 13..17 at r 10, apex at r 8, z 15) revolved about Axis1 (z)
   with ``create_cut_revolve``: -234.57 (Pappus: 2 pi x 9.333 x 4).
   Settles ``FeatureRevolve2`` with ``IsCut`` True and the axis at mark 4.

It only touches the documents it creates, closes them without saving, and
keeps the report in ``tests/.generated/probe_dipper/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_dipper_modeling.py
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_dipper"
#: Volumes agree within this much (mm3): SolidWorks' mass properties.
TOLERANCE = 0.05


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.bodies import register_bodies_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-dipper")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_bodies_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


class Report:
    """What each step expected and got."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.replies: dict[str, Any] = {}

    def keep(self, name: str, reply: Any) -> Any:
        self.replies[name] = reply
        return reply

    def check(self, step: str, settles: str, expected: Any, got: Any) -> None:
        if isinstance(expected, int | float) and isinstance(got, int | float):
            ok = abs(float(expected) - float(got)) <= TOLERANCE
        else:
            ok = expected == got
        self.rows.append(
            {
                "step": step,
                "settles": settles,
                "expected": expected,
                "got": got,
                "ok": ok,
            }
        )
        print(
            f"[probe] {'ok ' if ok else 'BAD'} {step}: expected {expected}, got {got}"
        )


def _volume(reply: dict[str, Any]) -> Any:
    return (reply.get("measured_change") or {}).get("volume_after_mm3")


def _box(reply: dict[str, Any]) -> Any:
    return (reply.get("measured_change") or {}).get("bounding_box_after_mm")


async def _summary(adapter: PyWin32Adapter) -> dict[str, Any]:
    result = await adapter.get_model_geometry_summary()
    return result.data if result.is_success and result.data else {}


async def _block(
    tools: dict[str, Any], plane: str, corners: tuple, depth: float, **extra: Any
) -> dict[str, Any]:
    """A rectangle on ``plane`` extruded ``depth`` mm."""
    x1, y1, x2, y2 = corners
    await tools["create_sketch"]({"plane": plane})
    await tools["add_rectangle"]({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await tools["exit_sketch"]()
    return await tools["create_extrusion"]({"depth": depth, **extra})


async def _sketch(tools: dict[str, Any], plane: str, draw: dict[str, Any]) -> str:
    """One closed sketch on ``plane``; returns its name."""
    opened = await tools["create_sketch"]({"plane": plane})
    name = opened["sketch"]["name"]
    tool, arguments = next(iter(draw.items()))
    await tools[tool](arguments)
    await tools["exit_sketch"]()
    return str(name)


async def offsets(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "pd_offsets"})
    block = report.keep("1.block", await _block(tools, "Top", (0, 0, 40, 20), 10))
    report.check("1.block", "the block", 8000.0, _volume(block))

    # Sketch (5..15, 5..15) on Top is x 5..15, z -5..-15.
    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_rectangle"]({"x1": 5, "y1": 5, "x2": 15, "y2": 15})
    await tools["exit_sketch"]()
    up = report.keep(
        "1a.boss_up",
        await tools["create_extrusion"]({"depth": 5, "start_offset_mm": 12}),
    )
    report.check("1a.volume", "swStartOffset on a boss", 8500.0, _volume(up))
    report.check(
        "1a.box_y",
        "FlipStartOffset False is along the normal",
        17.0,
        (_box(up) or [0, 0])[1],
    )

    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_rectangle"]({"x1": 5, "y1": 5, "x2": 15, "y2": 15})
    await tools["exit_sketch"]()
    down = report.keep(
        "1b.boss_down",
        await tools["create_extrusion"]({"depth": 2, "start_offset_mm": -4}),
    )
    report.check(
        "1b.volume", "FlipStartOffset True offsets the other way", 8700.0, _volume(down)
    )
    report.check(
        "1b.box_y", "the box now runs from y -4 to 17", 21.0, (_box(down) or [0, 0])[1]
    )

    hole = await _sketch(
        tools, "Top", {"add_circle": {"center_x": 30, "center_y": 10, "radius": 3}}
    )
    # Through the adapter: its parameters keep both sides' volume_removed_mm3.
    cut = await adapter.create_cut_extrude(
        ExtrusionParameters(depth=4.0, sketch_name=hole, start_offset=3.0)
    )
    applied = (cut.data.parameters or {}) if cut.is_success and cut.data else {}
    report.keep("1c.cut_offset", applied if cut.is_success else cut.error)
    removed = 9 * math.pi * 4
    report.check(
        "1c.volume",
        "StartOffset on FeatureCut4",
        8700.0 - removed,
        (await _summary(adapter)).get("volume_mm3"),
    )
    report.check(
        "1c.against",
        "flipping Dir keeps the offset (0 if it flips too)",
        9 * math.pi * 3,
        (applied.get("volume_removed_mm3") or {}).get("against_normal"),
    )

    plane = report.keep(
        "1d.plane",
        await tools["create_reference_plane"](
            {"reference": "Right Plane", "offset": 20}
        ),
    )
    # On a Right-parallel plane sketch x is -Z and sketch y is +Y.
    middle = await _sketch(
        tools,
        plane["plane"]["name"],
        {"add_rectangle": {"x1": 5, "y1": 2, "x2": 15, "y2": 8}},
    )
    mid = report.keep(
        "1d.mid_plane",
        await tools["create_cut_extrude"](
            {"sketch_name": middle, "depth": 6, "end_condition": "MidPlane"}
        ),
    )
    report.check(
        "1d.volume",
        "swEndCondMidPlane, Sd True, on a cut",
        8700.0 - removed - 360.0,
        _volume(mid),
    )


def _prismatoid(
    bottom: tuple[float, float], top: tuple[float, float], h: float
) -> float:
    middle = ((bottom[0] + top[0]) / 2, (bottom[1] + top[1]) / 2)
    return h / 6 * (bottom[0] * bottom[1] + 4 * middle[0] * middle[1] + top[0] * top[1])


async def drafts(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "pd_drafts"})
    grow = 2 * 10 * math.tan(math.radians(3))
    inward = _prismatoid((40, 20), (40 - grow, 20 - grow), 10)
    outward = _prismatoid((40, 20), (40 + grow, 20 + grow), 10)
    a = report.keep(
        "2.inward", await _block(tools, "Top", (0, 0, 40, 20), 10, draft_angle=3)
    )
    report.check(
        "2.inward", "Ddir1 False drafts a boss inward (#53)", inward, _volume(a)
    )
    b = report.keep(
        "2.outward",
        await _block(
            tools, "Top", (60, 0, 100, 20), 10, draft_angle=3, draft_outward=True
        ),
    )
    report.check(
        "2.outward", "Ddir1 True drafts a boss outward", inward + outward, _volume(b)
    )

    top = await tools["create_reference_plane"](
        {"reference": "Top Plane", "offset": 10}
    )
    shrink = 2 * 4 * math.tan(math.radians(5))
    cut_in = _prismatoid((10, 10), (10 - shrink, 10 - shrink), 4)
    cut_out = _prismatoid((10, 10), (10 + shrink, 10 + shrink), 4)
    for label, corners, outward_cut, removed, before in (
        ("2.cut_inward", (15, 5, 25, 15), False, cut_in, inward + outward),
        ("2.cut_outward", (75, 5, 85, 15), True, cut_out, inward + outward - cut_in),
    ):
        x1, y1, x2, y2 = corners
        name = await _sketch(
            tools,
            top["plane"]["name"],
            {"add_rectangle": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}},
        )
        cut = report.keep(
            label,
            await tools["create_cut_extrude"](
                {
                    "sketch_name": name,
                    "depth": 4,
                    "draft_angle": 5,
                    "draft_outward": outward_cut,
                }
            ),
        )
        report.check(
            label,
            "Ddir1 on FeatureCut4: inward is narrower with depth",
            before - removed,
            _volume(cut),
        )


async def pattern(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "pd_pattern"})
    plate = await _block(tools, "Top", (0, 0, 110, 40), 10)
    report.check("3.plate", "the plate", 44000.0, _volume(plate))
    slot_sketch = await _sketch(
        tools, "Top", {"add_rectangle": {"x1": 5, "y1": 5, "x2": 9, "y2": 35}}
    )
    slot = await tools["create_cut_extrude"](
        {"sketch_name": slot_sketch, "depth": 10, "end_condition": "ThroughAll"}
    )
    slot_name = slot["cut_extrude"]["name"]
    report.check("3a.slot", "one slot", 42800.0, _volume(slot))
    row = report.keep(
        "3a.row",
        await tools["pattern_linear"](
            {"features": [slot_name], "direction": "x", "count": 8, "spacing": 12}
        ),
    )
    report.check("3a.row", "x is the Right Plane's normal, +X", 34400.0, _volume(row))

    off = report.keep(
        "3b.off_the_part",
        await tools["pattern_linear"](
            {"features": [slot_name], "direction": "-x", "count": 3, "spacing": 12}
        ),
    )
    report.check("3b.status", "an empty pattern fails", "error", off.get("status"))
    report.check(
        "3b.volume",
        "and is deleted",
        34400.0,
        (await _summary(adapter)).get("volume_mm3"),
    )

    hole_sketch = await _sketch(
        tools, "Top", {"add_circle": {"center_x": 102, "center_y": 3, "radius": 1.5}}
    )
    hole = await tools["create_cut_extrude"](
        {"sketch_name": hole_sketch, "depth": 10, "end_condition": "ThroughAll"}
    )
    one = math.pi * 1.5**2 * 10
    report.check("3c.hole", "one hole", 34400.0 - one, _volume(hole))
    grid = report.keep(
        "3c.grid",
        await tools["pattern_linear"](
            {
                "features": [hole["cut_extrude"]["name"]],
                "direction": "-z",
                "count": 4,
                "spacing": 10,
                "direction2": "55,0,0",
                "count2": 2,
                "spacing2": 4,
            }
        ),
    )
    report.check(
        "3c.grid", "an edge at mark 2; -z flips", 34400.0 - 8 * one, _volume(grid)
    )

    cube = await _block(tools, "Top", (130, 0, 135, 5), 5, merge_result=False)
    report.check("3d.cube", "a separate cube", 34400.0 - 8 * one + 125.0, _volume(cube))
    bodies = (await tools["list_bodies"]()).get("data") or {}
    cube_body = min(
        bodies.get("bodies") or [{"name": "", "volume_mm3": 0}],
        key=lambda b: b.get("volume_mm3") or 0.0,
    )["name"]
    stack = report.keep(
        "3d.bodies",
        await tools["pattern_linear"](
            {
                "features": [cube_body],
                "direction": "y",
                "count": 3,
                "spacing": 10,
                "bodies": True,
            }
        ),
    )
    report.check(
        "3d.bodies", "mark 256 for bodies", 34400.0 - 8 * one + 375.0, _volume(stack)
    )


async def text(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "pd_text"})
    slab = await _block(tools, "Top", (0, 0, 60, 30), 5)
    report.check("4.slab", "the slab", 9000.0, _volume(slab))
    top = await tools["create_reference_plane"]({"reference": "Top Plane", "offset": 5})
    raised = report.keep(
        "4a.emboss",
        await tools["emboss_text"](
            {
                "text": "MAX",
                "plane": top["plane"]["name"],
                "x": 5,
                "y": 5,
                "height": 6,
                "depth": 0.6,
            }
        ),
    )
    report.check(
        "4a.box_y", "a text sketch extrudes 0.6 up", 5.6, (_box(raised) or [0, 0])[1]
    )
    report.check(
        "4a.height",
        "ITextFormat.CharHeight",
        6.0,
        (raised.get("text") or {}).get("height_mm"),
    )
    report.check(
        "4a.flipped",
        "an offset plane's normal is +Y",
        False,
        (raised.get("feature") or {}).get("direction_flipped"),
    )
    face = report.keep(
        "4b.face",
        await tools["emboss_text"](
            {"text": "V2", "face_point": [30, 5, -20], "height": 5, "depth": 0.6}
        ),
    )
    report.check(
        "4b.added",
        "an emboss on a face adds volume",
        True,
        ((face.get("measured_change") or {}).get("volume_delta_mm3") or 0) > 0,
    )
    engraved = report.keep(
        "4c.engrave",
        await tools["emboss_text"](
            {
                "text": "MIN",
                "face_point": [10, 5, -25],
                "height": 4,
                "depth": 0.4,
                "mode": "engrave",
            }
        ),
    )
    report.check(
        "4c.removed",
        "an engrave removes volume",
        True,
        ((engraved.get("measured_change") or {}).get("volume_delta_mm3") or 0) < 0,
    )

    opened = await tools["create_sketch"]({"plane": "Front"})
    written = report.keep(
        "4d.add_sketch_text",
        await tools["add_sketch_text"](
            {
                "text": "OPEN FLAT",
                "x": 5,
                "y": 1,
                "height": 3,
                "font": "Arial",
                "bold": True,
            }
        ),
    )
    await tools["exit_sketch"]()
    sunk = report.keep(
        "4d.cut",
        await tools["create_cut_extrude"](
            {"sketch_name": opened["sketch"]["name"], "depth": 0.5}
        ),
    )
    report.check(
        "4d.removed",
        "a text sketch cuts",
        True,
        ((sunk.get("measured_change") or {}).get("volume_delta_mm3") or 0) < 0,
    )
    report.check(
        "4d.font",
        "TypeFaceName read back",
        "Arial",
        (written.get("text") or {}).get("font"),
    )
    read = report.keep(
        "4d.get_sketch", await tools["get_sketch"]({"name": opened["sketch"]["name"]})
    )
    report.keep("4d.text_read_back", read.get("sketch") or read)


async def angle(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    for degrees in (0, 90):
        await tools["create_part"]({"name": f"pd_angle_{degrees}"})
        opened = await tools["create_sketch"]({"plane": "Front"})
        await tools["add_sketch_text"](
            {"text": "LONGTEXT", "x": 10, "y": 20, "height": 5, "angle": degrees}
        )
        await tools["exit_sketch"]()
        boss = report.keep(
            f"5.angle_{degrees}",
            await tools["create_extrusion"](
                {"sketch_name": opened["sketch"]["name"], "depth": 1}
            ),
        )
        box = (boss.get("measured_change") or {}).get("bounding_box_after_mm") or [0, 0]
        tall = box[1] > box[0]
        report.check(
            f"5.angle_{degrees}",
            "the construction line turns the text"
            if degrees
            else "x reads along sketch x",
            degrees == 90,
            tall,
        )
        report.keep(f"5.angle_{degrees}.summary", await _box_corners(tools))


async def _box_corners(tools: dict[str, Any]) -> Any:
    """Where (x, y) put the text: the box's corners, from list_bodies."""
    bodies = await tools["list_bodies"]()
    return [b.get("box_mm") for b in (bodies.get("data") or {}).get("bodies", [])]


async def groove(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "pd_groove"})
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 10})
    await tools["exit_sketch"]()
    rod = await tools["create_extrusion"]({"depth": 30})
    rod_volume = math.pi * 100 * 30
    report.check("6.rod", "the rod", rod_volume, _volume(rod))
    axis = report.keep("6.axis", await tools["create_axis"]({"reference": "z"}))
    # On the Right Plane sketch x is -Z, sketch y is +Y.
    profile = await _sketch(
        tools,
        "Right",
        {"add_outlines": {"outlines": [{"points": [[-13, 10], [-17, 10], [-15, 8]]}]}},
    )
    cut = report.keep(
        "6.cut_revolve",
        await tools["create_cut_revolve"](
            {
                "sketch_name": profile,
                "axis_entity": (axis.get("axis") or {}).get("name"),
            }
        ),
    )
    groove_volume = 2 * math.pi * ((10 + 10 + 8) / 3) * 4
    report.check(
        "6.volume",
        "IsCut on FeatureRevolve2, axis at mark 4",
        rod_volume - groove_volume,
        _volume(cut),
    )


STEPS = [
    ("offsets", offsets),
    ("drafts", drafts),
    ("pattern", pattern),
    ("text", text),
    ("angle", angle),
    ("groove", groove),
]


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    report = Report()
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    try:
        tools = await _tools(adapter)
        for name, step in STEPS:
            try:
                await step(tools, adapter, report)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report.rows.append(
                    {"step": name, "raised": f"{type(exc).__name__}: {exc}"}
                )
                print(f"[probe] {name} raised {type(exc).__name__}: {exc}")
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(
            json.dumps(
                {"checks": report.rows, "replies": report.replies},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        bad = [r for r in report.rows if not r.get("ok")]
        print(
            f"[probe] {len(report.rows) - len(bad)} of {len(report.rows)} as expected"
        )
        print(f"[probe] report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
