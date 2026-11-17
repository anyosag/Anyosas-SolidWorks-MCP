"""Probe the COM calls behind the r2-sketching tools ("The Dipper", round two).

The ``r2-sketching`` branch was written without SolidWorks (other sessions
had it). This runs every new COM sequence on scratch parts through
``PyWin32Adapter`` and the registered tools, and compares each result with
the number worked out beforehand. Each step says what it settles; the report
keeps the replies.

1. ``frames`` (an empty part, then a 40 x 10 x 20 block for a face): for the
   Front, Top and Right planes, a plane 10 mm off the Top Plane, one at 30
   degrees to the Top Plane about Axis1 (z), one on the block's top face at
   (20, 10, -10), and one through (20, 0, -10) square to (0.5, 0, 0.866):
   ``plane_axes`` (``IRefPlane.Transform``) against ``create_sketch``'s
   ``axes`` (``ModelToSketchTransform`` inverted). Expected: x, y and normal
   equal within 1e-6 and origins within 1e-4 mm. Settles whether a plane's
   transform is the frame a sketch on it takes. Each sketch is exited empty,
   so SolidWorks deletes it (pitfall #45): the feature count is unchanged.
2. ``model_points``:
   a. on the plane 10 mm off the Top Plane (the block's top), ``add_circle``
      with ``coordinates: "model"`` at (10, 10, -10) r 3, cut 4 deep: the
      block's 8000 mm3 less 9 pi x 4 = 113.10, 7886.90. A centre at
      (10, 11, -10) is refused "1 mm above the sketch plane".
   b. on the plane through (0, 0, 0) square to n = (sin 30, 0, cos 30) in an
      empty part, a circle r 2 at p = 5 x (cos 30, 0, -sin 30), extruded 4:
      16 pi = 50.27 mm3, its centre of mass at p + 2 n = (5.330, 0, -0.768).
      Settles that projected points land where the model says.
3. ``axis_plane`` (an empty part):
   a. ``create_axis`` through (20, 0, -10) along (0, 1, 0): ``read_back``
      ``on_line`` True. Settles ``Insert3DSketch``, ``CreateLine`` in model
      metres, the ``Line1@3DSketch1`` "EXTSKETCHSEGMENT" pick and
      ``InsertAxis2`` on a sketch line.
   b. a plane through (20, 0, -10) square to (1, 0, 0): ``normal_matches``
      True, ``axes.origin_mm`` on x = 20. Settles Perpendicular (mark 0) and
      Coincident with the line's start (mark 1) on ``InsertRefPlane``, and
      which way its normal points.
   c. a plane at 30 degrees to (b) about (a)'s axis: ``plane_axes`` normal
      with x component cos 30 = 0.866 (either sign).
4. ``union`` (empty parts):
   a. on the Front Plane two circles r 18, 30 apart, ``create_extrusion`` 5
      with no ``regions``: the first try fails, the retry with every region
      succeeds with a ``warning``: the union, 2 x 324 pi less the lens
      (81.03), times 5 = 9773.62 mm3.
   b. the same with a circle r 5 inside the first: no retry (the error says
      "Building every region was not tried"); with ``regions: "all"`` it
      builds 9773.62, the small circle filled.
   c. a 100 x 60 x 10 block on the Top Plane, the two circles again on a plane
      10 mm up, ``create_cut_extrude`` 4 deep with ``regions: "all"``:
      60000 less 4 x 1954.72 = 52181.13.
   Settles ``EnableContourSelection``, the sketch at mark 4 and each
   ``ISketchRegion.Select2`` at mark 4, and that SolidWorks unions them.
5. ``pattern`` (an empty part): a cylinder r 20, 20 tall along +Y; on the plane
   through (20, 10, 0) square to (1, 0, 0) a 10 x 4 rectangle by model points
   (y 5..15, z -2..2), cut 3 deep into it: v = 10 x the area between x = 17
   and the circle over z -2..2 (computed below). Then ``pattern_circular`` with
   ``axis_point`` (0, 0, 0), ``axis_direction`` (0, 1, 0), ``count: 6``: the
   cylinder's 8000 pi less 6 v. Settles a pattern about an axis it made.

It only touches the documents it creates, closes them without saving, and
keeps the report in ``tests/.generated/probe_r2_sketching/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_r2_sketching.py
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_r2_sketching"
#: Volumes agree within this much (mm3): SolidWorks' mass properties.
TOLERANCE = 0.05
#: Directions agree within this much, origins within this much (mm).
AXIS_TOLERANCE, ORIGIN_TOLERANCE = 1e-6, 1e-4

S30, C30 = math.sin(math.radians(30)), math.cos(math.radians(30))


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.bodies import register_bodies_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-r2-sketching")
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


def _same_axes(a: Any, b: Any) -> bool:
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    for key, tolerance in (
        ("x", AXIS_TOLERANCE),
        ("y", AXIS_TOLERANCE),
        ("normal", AXIS_TOLERANCE),
        ("origin_mm", ORIGIN_TOLERANCE),
    ):
        if any(abs(p - q) > tolerance for p, q in zip(a[key], b[key], strict=True)):
            return False
    return True


async def _count(adapter: PyWin32Adapter) -> Any:
    from solidworks_mcp.adapters.solidworks.features import _feature_count

    return _feature_count(adapter)


async def _block(
    tools: dict[str, Any], plane: str, corners: tuple, depth: float
) -> dict[str, Any]:
    x1, y1, x2, y2 = corners
    await tools["create_sketch"]({"plane": plane})
    await tools["add_rectangle"]({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await tools["exit_sketch"]()
    return await tools["create_extrusion"]({"depth": depth})


async def frames(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "r2_frames"})
    await tools["create_axis"]({"reference": "z"})
    offset = await tools["create_reference_plane"](
        {"reference": "Top Plane", "offset": 10}
    )
    angled = await tools["create_reference_plane"](
        {"reference": "Top Plane", "angle": 30, "axis": "Axis1"}
    )
    through = report.keep(
        "1.through",
        await tools["create_reference_plane"](
            {"point": [20, 0, -10], "normal": [0.5, 0, 0.866]}
        ),
    )
    await _block(tools, "Top", (0, 0, 40, 20), 10)
    face = await tools["create_reference_plane"]({"face_point": [20, 10, -10]})
    planes = ["Front", "Top", "Right"] + [
        (made.get("plane") or {}).get("name")
        for made in (offset, angled, through, face)
    ]
    for plane in planes:
        before = await _count(adapter)
        read = report.keep(
            f"1.plane_axes.{plane}", await tools["plane_axes"]({"plane": plane})
        )
        opened = report.keep(
            f"1.create_sketch.{plane}", await tools["create_sketch"]({"plane": plane})
        )
        await tools["exit_sketch"]()
        report.check(
            f"1.{plane}",
            "IRefPlane.Transform is the frame a sketch on the plane takes",
            True,
            _same_axes(read.get("axes"), (opened.get("sketch") or {}).get("axes")),
        )
        report.check(
            f"1.{plane}.count",
            "an empty sketch is deleted on exit",
            before,
            await _count(adapter),
        )


async def model_points(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "r2_points"})
    await _block(tools, "Top", (0, 0, 40, 20), 10)
    top = await tools["create_reference_plane"](
        {"reference": "Top Plane", "offset": 10}
    )
    await tools["create_sketch"]({"plane": top["plane"]["name"]})
    refused = report.keep(
        "2a.off_plane",
        await tools["add_circle"](
            {
                "center_x": 10,
                "center_y": 11,
                "center_z": -10,
                "radius": 3,
                "coordinates": "model",
            }
        ),
    )
    report.check(
        "2a.refused",
        "a point 1 mm off the plane is refused",
        True,
        "1 mm above the sketch plane" in str(refused.get("message")),
    )
    report.keep(
        "2a.circle",
        await tools["add_circle"](
            {
                "center_x": 10,
                "center_y": 10,
                "center_z": -10,
                "radius": 3,
                "coordinates": "model",
            }
        ),
    )
    await tools["exit_sketch"]()
    cut = report.keep("2a.cut", await tools["create_cut_extrude"]({"depth": 4}))
    report.check(
        "2a.volume",
        "a model centre on an offset plane",
        8000 - 36 * math.pi,
        _volume(cut),
    )

    await tools["create_part"]({"name": "r2_points_tilted"})
    normal = [S30, 0.0, C30]
    plane = report.keep(
        "2b.plane",
        await tools["create_reference_plane"]({"point": [0, 0, 0], "normal": normal}),
    )
    await tools["create_sketch"]({"plane": plane["plane"]["name"]})
    p = [5 * C30, 0.0, -5 * S30]
    report.keep(
        "2b.circle",
        await tools["add_circle"](
            {
                "center_x": p[0],
                "center_y": p[1],
                "center_z": p[2],
                "radius": 2,
                "coordinates": "model",
            }
        ),
    )
    await tools["exit_sketch"]()
    boss = report.keep("2b.boss", await tools["create_extrusion"]({"depth": 4}))
    report.check("2b.volume", "the projected circle", 16 * math.pi, _volume(boss))
    bodies = report.keep("2b.bodies", await tools["list_bodies"]())
    first = ((bodies.get("data") or {}).get("bodies") or [{}])[0]
    centre = first.get("center_of_mass_mm")
    side = 1 if (plane["plane"].get("normal_matches") is not False) else -1
    expected = [p[i] + side * 2 * normal[i] for i in range(3)]
    report.check(
        "2b.centre",
        "the boss stands at p + 2 n",
        [round(v, 3) for v in expected],
        [round(float(v), 3) for v in centre] if isinstance(centre, list) else centre,
    )


async def axis_plane(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "r2_axis_plane"})
    axis = report.keep(
        "3a.axis",
        await tools["create_axis"]({"point": [20, 0, -10], "direction": [0, 1, 0]}),
    )
    report.check(
        "3a.on_line",
        "Insert3DSketch, CreateLine, EXTSKETCHSEGMENT and InsertAxis2",
        True,
        ((axis.get("axis") or {}).get("read_back") or {}).get("on_line"),
    )
    plane = report.keep(
        "3b.plane",
        await tools["create_reference_plane"](
            {"point": [20, 0, -10], "normal": [1, 0, 0]}
        ),
    )
    data = plane.get("plane") or {}
    report.check(
        "3b.normal",
        "which way a Perpendicular plane faces",
        True,
        data.get("normal_matches"),
    )
    report.check(
        "3b.origin_x",
        "the plane passes through the point",
        20.0,
        ((data.get("axes") or {}).get("origin_mm") or [None])[0],
    )
    turned = report.keep(
        "3c.turned",
        await tools["create_reference_plane"](
            {
                "reference": data.get("name"),
                "angle": 30,
                "axis": (axis.get("axis") or {}).get("name"),
            }
        ),
    )
    read = await tools["plane_axes"]({"plane": (turned.get("plane") or {}).get("name")})
    report.check(
        "3c.normal_x",
        "a plane turned about an axis through a point",
        round(C30, 3),
        abs(round(((read.get("axes") or {}).get("normal") or [0])[0], 3)),
    )


def _union_area(r: float, d: float) -> float:
    lens = 2 * r * r * math.acos(d / (2 * r)) - (d / 2) * math.sqrt(4 * r * r - d * d)
    return 2 * math.pi * r * r - lens


async def union(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    area = _union_area(18, 30)
    rings = [
        {"center_x": 0, "center_y": 0, "radius": 18},
        {"center_x": 30, "center_y": 0, "radius": 18},
    ]

    await tools["create_part"]({"name": "r2_union"})
    await tools["create_sketch"]({"plane": "Front"})
    for ring in rings:
        await tools["add_circle"](ring)
    await tools["exit_sketch"]()
    auto = report.keep("4a.auto", await tools["create_extrusion"]({"depth": 5}))
    report.check(
        "4a.volume", "the automatic retry with every region", 5 * area, _volume(auto)
    )
    report.check(
        "4a.warned",
        "the retry says so",
        True,
        "every region" in str(auto.get("warning")),
    )

    await tools["create_part"]({"name": "r2_union_hole"})
    await tools["create_sketch"]({"plane": "Front"})
    for ring in [*rings, {"center_x": 0, "center_y": 0, "radius": 5}]:
        await tools["add_circle"](ring)
    sketch = (await tools["exit_sketch"]()).get("sketch", {}).get("name") or "Sketch1"
    refused = report.keep(
        "4b.refused",
        await tools["create_extrusion"]({"depth": 5, "sketch_name": sketch}),
    )
    report.check(
        "4b.not_tried",
        "a hole stops the automatic union",
        True,
        "not tried" in str(refused.get("message")),
    )
    forced = report.keep(
        "4b.forced",
        await tools["create_extrusion"](
            {"depth": 5, "sketch_name": sketch, "regions": "all"}
        ),
    )
    report.check("4b.volume", "regions 'all' fills the hole", 5 * area, _volume(forced))

    await tools["create_part"]({"name": "r2_union_cut"})
    await _block(tools, "Top", (-50, -30, 50, 30), 10)
    up = await tools["create_reference_plane"]({"reference": "Top Plane", "offset": 10})
    await tools["create_sketch"]({"plane": up["plane"]["name"]})
    for ring in rings:
        await tools["add_circle"](ring)
    await tools["exit_sketch"]()
    cut = report.keep(
        "4c.cut", await tools["create_cut_extrude"]({"depth": 4, "regions": "all"})
    )
    report.check("4c.volume", "a cut of every region", 60000 - 4 * area, _volume(cut))


def _rim_cut(r: float, depth: float, half_width: float, height: float) -> float:
    """The volume a flat-bottomed cut ``depth`` deep removes from a cylinder's rim."""
    steps = 20000
    dz = 2 * half_width / steps
    total = 0.0
    for i in range(steps):
        z = -half_width + (i + 0.5) * dz
        total += max(0.0, math.sqrt(r * r - z * z) - (r - depth)) * dz
    return total * height


async def pattern(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "r2_pattern"})
    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 20})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 20})
    plane = report.keep(
        "5.plane",
        await tools["create_reference_plane"](
            {"point": [20, 10, 0], "normal": [1, 0, 0]}
        ),
    )
    await tools["create_sketch"]({"plane": plane["plane"]["name"]})
    report.keep(
        "5.outline",
        await tools["add_outlines"](
            {
                "coordinates": "model",
                "outlines": [
                    {"points": [[20, 5, -2], [20, 15, -2], [20, 15, 2], [20, 5, 2]]}
                ],
            }
        ),
    )
    await tools["exit_sketch"]()
    cut = report.keep("5.cut", await tools["create_cut_extrude"]({"depth": 3}))
    one = _rim_cut(20, 3, 2, 10)
    report.check(
        "5.cut_volume", "the cut at the rim", 8000 * math.pi - one, _volume(cut)
    )
    patterned = report.keep(
        "5.pattern",
        await tools["pattern_circular"](
            {
                "features": [(cut.get("cut_extrude") or {}).get("name")],
                "axis_point": [0, 0, 0],
                "axis_direction": [0, 1, 0],
                "count": 6,
            }
        ),
    )
    report.check(
        "5.pattern_volume",
        "a pattern about the axis it made",
        8000 * math.pi - 6 * one,
        (patterned.get("pattern") or {}).get("volume_after"),
    )


STEPS = [
    ("frames", frames),
    ("model_points", model_points),
    ("axis_plane", axis_plane),
    ("union", union),
    ("pattern", pattern),
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
