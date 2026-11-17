"""Probe the r2-query branch's COM paths: query_geometry and a coarse measure_clearance.

Written without SolidWorks (other sessions had it). Each step builds scratch
documents through ``PyWin32Adapter`` and the registered tools, runs the new
COM sequences raw first (to settle what the code assumes) and then through
the tools, times each call, and compares it with ``select_edges`` on the
same part. It closes only the documents it opened, without saving, and
deletes its scratch files. The report goes to
``tests/.generated/probe_query_geometry/<stamp>/report.json``.

Steps (``--steps``, in order):

1. ``picks`` - the pocket plate of ``probe_dipper_edges`` (``--grid``, 20x12
   by default: 2,904 edges, the top face carrying every rim). Settles:

   * ``IModelDocExtension.SelectByRay`` from a pocket's middle along each
     axis, for faces (2) and edges (1), with the ray radius: what it returns,
     what ``GetSelectedObject6(1, -1)`` then holds, and whether ``Append``
     false replaced the selection (the count stays 1);
   * ``SelectByID2("", "FACE", ...)`` at a point in the air (expected False)
     and on the floor;
   * ``ClearSelection2`` leaving the count at 0, and ``GetUpdateStamp``
     unchanged by the picks;
   * ``query_geometry`` at three pockets: picks (``complete: false``), then
     ``read_all``, then again at another pocket and at the same point, each
     timed with its ``read``; against ``select_edges`` with an
     ``inside_box`` around the same pocket (fresh index, then kept);
   * that a selection made before (``select_edges``) is unchanged after a
     query (no picks then: ``method: index``).

2. ``surfaces`` - a block with a 5 mm hole, a cone and a 2 mm fillet round a
   boss's top (a torus), and a revolved half-disc (a sphere). Settles the
   order of ``CylinderParams``, ``ConeParams``, ``SphereParams`` and
   ``TorusParams`` (centre/origin, axis, radii, half angle in radians),
   ``IFace2.Normal`` on a plane, ``IEdge.GetCurveParams2`` ([6] and [7] the
   start and end parameters) and ``ICurve.GetLength3`` on an arc, and what
   ``query_geometry`` reports for each against the modelled sizes.

3. ``clearance`` - an assembly of two copies of the pocket plate, 0.4 mm
   apart face to face. Settles how long ``measure_clearance`` takes full,
   with ``coarse`` and with ``timeout_s: 1``, and that each coarse or timed
   reply's bounds hold the full answer (``at_least_mm`` <= full <=
   ``clearance_mm``).

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_query_geometry.py [--grid 20x12]
    [--steps picks,surfaces,clearance]
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.base import ExtrusionParameters, RevolveParameters
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import edge_index
from solidworks_mcp.adapters.solidworks.features import _null_callout

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_query_geometry"

PITCH, POCKET, MARGIN, DEPTH = 8.0, 4.0, 6.0, 3.0
AXES = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]


# ---- building ----------------------------------------------------------------------


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.analysis import register_analysis_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools

    mcp = FastMCP("probe-query-geometry")
    await register_modeling_tools(mcp, adapter, {})
    await register_analysis_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _invoke(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    """Call a tool; one with no input model takes keywords only."""
    fn = tools[name]
    positional = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return await (fn(**args) if not positional else fn(args))


async def _timed(call: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = await call()
    return result, round(time.perf_counter() - started, 3)


def _ok(result: Any, what: str) -> Any:
    assert result.is_success, f"{what}: {result.error}"
    return result.data


async def _sketch(adapter: PyWin32Adapter, plane: str, rects=(), circles=()) -> None:
    _ok(await adapter.create_sketch(plane), f"create_sketch {plane}")
    for x1, y1, x2, y2 in rects:
        _ok(await adapter.add_rectangle(x1, y1, x2, y2), "add_rectangle")
    for x, y, r in circles:
        _ok(await adapter.add_circle(x, y, r), "add_circle")
    _ok(await adapter.exit_sketch(), "exit_sketch")


def _z_sign(adapter: PyWin32Adapter) -> float:
    """-1 when a Top sketch's y runs along model -Z (the default), else +1."""
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetBodies2")
    body = list(model.GetBodies2(0, False) or [])[0]
    sw_type_info.flag_members(body, "GetBodyBox")
    return -1.0 if float(body.GetBodyBox()[2]) < -1e-3 else 1.0


async def _pocket_plate(adapter: PyWin32Adapter, grid: tuple[int, int]) -> float:
    _ok(await adapter.create_part(), "create_part")
    width = MARGIN * 2 + PITCH * grid[0]
    depth = MARGIN * 2 + PITCH * grid[1]
    await _sketch(adapter, "Top", [(0, 0, width, depth)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=10)), "plate")
    plane = _ok(await adapter.create_reference_plane("Top Plane", offset=10), "plane")
    rects = [
        (MARGIN + PITCH * i, MARGIN + PITCH * j)
        for i in range(grid[0])
        for j in range(grid[1])
    ]
    await _sketch(
        adapter, plane["name"], [(x, y, x + POCKET, y + POCKET) for x, y in rects]
    )
    _ok(await adapter.create_cut_extrude(ExtrusionParameters(depth=DEPTH)), "pockets")
    return _z_sign(adapter)


def _pocket_middle(i: int, j: int, z_sign: float) -> list[float]:
    """The middle of pocket (i, j), halfway down, model mm."""
    x = MARGIN + PITCH * i + POCKET / 2
    y = MARGIN + PITCH * j + POCKET / 2
    return [x, 10 - DEPTH / 2, z_sign * y]


def _pocket_box(i: int, j: int, z_sign: float) -> list[list[float]]:
    x, y, z = _pocket_middle(i, j, z_sign)
    half = POCKET / 2 + 1
    return [[x - half, 10 - DEPTH - 1, z - half], [x + half, 11, z + half]]


# ---- 1. picks ----------------------------------------------------------------------


def _count(adapter: PyWin32Adapter) -> Any:
    manager = adapter.currentModel.SelectionManager
    sw_type_info.flag_members(manager, "GetSelectedObjectCount2")
    return adapter._attempt(lambda: manager.GetSelectedObjectCount2(-1))


def _raw_picks(adapter: PyWin32Adapter, point_mm: list[float]) -> list[dict[str, Any]]:
    """Settles what SelectByRay and SelectByID2 do, one pick at a time."""
    model = adapter.currentModel
    extension = model.Extension
    manager = model.SelectionManager
    sw_type_info.flag_members(manager, "GetSelectedObject6", "GetSelectedObjectType3")
    x, y, z = (c / 1000 for c in point_mm)
    rows = []
    tries: list[tuple[str, Callable[[], Any]]] = [
        (
            f"SelectByRay {kind} {d}",
            lambda d=d, t=t: extension.SelectByRay(x, y, z, *d, 0.003, t, False, 0, 0),
        )
        for kind, t in (("FACE", 2), ("EDGE", 1))
        for d in AXES
    ]
    tries += [
        (
            "SelectByID2 FACE in the air",
            lambda: extension.SelectByID2(
                "", "FACE", x, y, z, False, 0, _null_callout(), 0
            ),
        ),
        (
            "SelectByID2 FACE on the floor",
            lambda: extension.SelectByID2(
                "", "FACE", x, y - DEPTH / 2000, z, False, 0, _null_callout(), 0
            ),
        ),
    ]
    for name, pick in tries:
        try:
            returned: Any = pick()
        except Exception as exc:  # noqa: BLE001 - a probe records every failure
            returned = f"raised {exc!r}"
        entity = adapter._attempt(lambda: manager.GetSelectedObject6(1, -1))
        row: dict[str, Any] = {
            "pick": name,
            "returned": repr(returned),
            "count": _count(adapter),
            "type": adapter._attempt(lambda: manager.GetSelectedObjectType3(1, -1)),
        }
        if entity is not None:
            sw_type_info.flag_members(entity, "GetClosestPointOn")
            closest = adapter._attempt(
                lambda e=entity: list(e.GetClosestPointOn(x, y, z))
            )
            if closest:
                row["distance_mm"] = round(math.dist(closest[:3], (x, y, z)) * 1000, 4)
        rows.append(row)
    adapter._attempt(lambda: model.ClearSelection2(True))
    rows.append({"after ClearSelection2": _count(adapter)})
    return rows


def _summary(reply: dict[str, Any]) -> dict[str, Any]:
    data = reply.get("data") or {}
    return {
        "status": reply.get("status"),
        "message": reply.get("message"),
        "error": reply.get("error") or reply.get("message")
        if reply.get("status") != "success"
        else None,
        "method": data.get("method"),
        "complete": data.get("complete"),
        "faces_found": data.get("faces_found"),
        "edges_found": data.get("edges_found"),
        "read": data.get("read"),
        "faces": data.get("faces"),
        "edges": data.get("edges"),
    }


async def step_picks(
    adapter: PyWin32Adapter, tools: dict[str, Any], grid: tuple[int, int]
) -> dict[str, Any]:
    """Settles the picks, and query_geometry's cost against select_edges."""
    z_sign = await _pocket_plate(adapter, grid)
    out: dict[str, Any] = {"z_sign": z_sign}
    middle = _pocket_middle(1, 1, z_sign)
    stamp = edge_index.update_stamp(adapter, adapter.currentModel)
    out["raw"] = _raw_picks(adapter, middle)
    out["stamp_unchanged_by_picks"] = (
        edge_index.update_stamp(adapter, adapter.currentModel) == stamp
    )
    runs = []
    for label, point, extra in (
        ("picks", middle, {}),
        ("read_all", middle, {"read_all": True}),
        ("another pocket", _pocket_middle(grid[0] // 2, grid[1] // 2, z_sign), {}),
        ("same point again", _pocket_middle(grid[0] // 2, grid[1] // 2, z_sign), {}),
    ):
        reply, seconds = await _timed(
            lambda p=point, e=extra: _invoke(
                tools, "query_geometry", {"point_mm": p, "radius_mm": 2.6, **e}
            )
        )
        runs.append({"run": label, "seconds": seconds, **_summary(reply)})
    out["query_geometry"] = runs
    edge_index.forget(adapter)
    compare = []
    for label in ("select_edges, fresh index", "select_edges, kept index"):
        reply, seconds = await _timed(
            lambda: _invoke(
                tools, "select_edges", {"inside_box": _pocket_box(1, 1, z_sign)}
            )
        )
        data = reply.get("data") or {}
        compare.append(
            {
                "run": label,
                "seconds": seconds,
                "selected": data.get("selected_edges"),
                "read": data.get("read"),
            }
        )
    out["select_edges"] = compare
    before = _count(adapter)
    reply, seconds = await _timed(
        lambda: _invoke(tools, "query_geometry", {"point_mm": middle, "radius_mm": 2.6})
    )
    out["with_a_selection"] = {
        "count_before": before,
        "count_after": _count(adapter),
        "seconds": seconds,
        "method": (reply.get("data") or {}).get("method"),
    }
    return out


# ---- 2. surfaces -------------------------------------------------------------------


async def _revolved(
    adapter: PyWin32Adapter, segments: list[tuple[str, tuple[float, ...]]]
) -> None:
    _ok(await adapter.create_part(), "create_part")
    _ok(await adapter.create_sketch("Front"), "create_sketch")
    for kind, args in segments:
        if kind == "line":
            _ok(await adapter.add_line(*args), "add_line")
        else:
            _ok(await adapter.add_arc(*args), "add_arc")
    _ok(await adapter.add_centerline(0, -20, 0, 20), "add_centerline")
    _ok(await adapter.exit_sketch(), "exit_sketch")
    _ok(await adapter.create_revolve(RevolveParameters(angle=360)), "revolve")


def _raw_surfaces(adapter: PyWin32Adapter) -> list[dict[str, Any]]:
    """Every face's surface kind and raw parameters, and each edge's curve params."""
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetBodies2")
    rows = []
    for body in list(model.GetBodies2(0, False) or []):
        sw_type_info.flag_members(body, "GetFaces")
        for face in list(body.GetFaces() or []):
            sw_type_info.flag_members(face, "GetSurface", "GetEdges")
            surface = face.GetSurface()
            names = ("IsPlane", "IsCylinder", "IsCone", "IsSphere", "IsTorus")
            sw_type_info.flag_members(surface, *names, "IsParametric")
            row: dict[str, Any] = {
                n: adapter._attempt(lambda n=n, s=surface: getattr(s, n)())
                for n in (*names, "IsParametric")
            }
            row["normal"] = adapter._attempt(lambda f=face: list(f.Normal))
            for prop in (
                "PlaneParams",
                "CylinderParams",
                "ConeParams",
                "SphereParams",
                "TorusParams",
            ):
                value = adapter._attempt(lambda p=prop, s=surface: getattr(s, p))
                if value is not None:
                    row[prop] = list(value) if isinstance(value, tuple) else repr(value)
            edges = []
            for edge in list(face.GetEdges() or []):
                sw_type_info.flag_members(edge, "GetCurveParams2", "GetCurve")
                params = adapter._attempt(lambda e=edge: list(e.GetCurveParams2()))
                curve = edge.GetCurve()
                sw_type_info.flag_members(curve, "GetLength3")
                length = None
                if params and len(params) >= 8:
                    length = adapter._attempt(
                        lambda c=curve, p=params: c.GetLength3(p[6], p[7])
                    )
                edges.append({"curve_params2": params, "length3_m": length})
            row["edges"] = edges
            rows.append(row)
    return rows


async def step_surfaces(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """Settles the surface parameters' order, and what query_geometry reports."""
    out: dict[str, Any] = {}
    # A 40 x 30 x 20 block, a 5 mm hole (r 2.5) through it at sketch (10, 10),
    # a r 4 boss on top at (30, 15) with a 1 mm fillet round its top (a torus).
    _ok(await adapter.create_part(), "create_part")
    await _sketch(adapter, "Top", [(0, 0, 40, 30)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=20)), "block")
    z_sign = _z_sign(adapter)
    plane = _ok(await adapter.create_reference_plane("Top Plane", offset=20), "plane")
    await _sketch(adapter, plane["name"], circles=[(10, 10, 2.5)])
    _ok(await adapter.create_cut_extrude(ExtrusionParameters(depth=20)), "hole")
    await _sketch(adapter, plane["name"], circles=[(30, 15, 4)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=5)), "boss")
    fillet = await adapter.add_fillet(1.0, [f"{0.034},{0.025},{z_sign * 0.015}"])
    out["fillet"] = fillet.is_success or fillet.error
    out["raw_block"] = _raw_surfaces(adapter)
    for label, point, radius in (
        (
            "hole wall, expect cylinder r 2.5 axis Y centre (10, 10, sketch y 10)",
            [10.0, 10.0, z_sign * 10.0],
            2.6,
        ),
        ("boss top round, expect torus R 3 r 1", [33.5, 24.5, z_sign * 15.0], 1.2),
        ("block top corner, expect 3 planes and 3 lines", [40.0, 20.0, 0.0], 0.5),
    ):
        reply, seconds = await _timed(
            lambda p=point, r=radius: _invoke(
                tools,
                "query_geometry",
                {"point_mm": p, "radius_mm": r, "read_all": True},
            )
        )
        out[label] = {"seconds": seconds, **_summary(reply)}
    # A cylinder r 10 with a cone to r 5 on it, and a half disc of r 8: a sphere.
    await _revolved(
        adapter,
        [
            ("line", (0, 0, 10, 0)),
            ("line", (10, 0, 10, 10)),
            ("line", (10, 10, 5, 15)),
            ("line", (5, 15, 0, 15)),
            ("line", (0, 15, 0, 0)),
        ],
    )
    out["raw_cone"] = _raw_surfaces(adapter)
    reply = await _invoke(
        tools, "query_geometry", {"point_mm": [7.5, 12.5, 0], "radius_mm": 0.5}
    )
    out["cone, expect half angle 45"] = _summary(reply)
    await _revolved(adapter, [("arc", (0, 0, 0, -8, 0, 8)), ("line", (0, 8, 0, -8))])
    out["raw_sphere"] = _raw_surfaces(adapter)
    reply = await _invoke(
        tools, "query_geometry", {"point_mm": [8, 0, 0], "radius_mm": 0.5}
    )
    out["sphere, expect r 8 at the origin"] = _summary(reply)
    return out


# ---- 3. clearance ------------------------------------------------------------------


async def step_clearance(
    adapter: PyWin32Adapter, tools: dict[str, Any], grid: tuple[int, int], scratch: Path
) -> dict[str, Any]:
    """Settles how long the full, coarse and timed searches take, and their bounds."""
    await _pocket_plate(adapter, grid)
    path = scratch / "pq_plate.SLDPRT"
    _ok(await adapter.save_file(str(path)), "save plate")
    width = MARGIN * 2 + PITCH * grid[0]
    _ok(await adapter.create_assembly(), "create_assembly")
    for x in (0.0, width + 0.4):
        _ok(await adapter.insert_component(str(path), x, 0, 0), "insert_component")
    listed = _ok(await adapter.list_components(), "list_components")
    names = [c["name"] if isinstance(c, dict) else str(c) for c in listed]
    out: dict[str, Any] = {"components": names}
    runs = {}
    for label, extra in (
        ("full", {}),
        ("coarse", {"coarse": True}),
        ("timeout_s 1", {"timeout_s": 1}),
    ):
        reply, seconds = await _timed(
            lambda e=extra: _invoke(
                tools,
                "measure_clearance",
                {"component_a": names[0], "component_b": names[1], **e},
            )
        )
        data = reply.get("data") or {}
        runs[label] = {
            "seconds": seconds,
            "message": reply.get("message"),
            **{
                k: data.get(k)
                for k in (
                    "clearance_mm",
                    "at_least_mm",
                    "complete",
                    "stopped_by",
                    "pairs_checked",
                    "pairs_total",
                )
            },
        }
    full = runs["full"].get("clearance_mm")
    for label in ("coarse", "timeout_s 1"):
        run = runs[label]
        if None not in (full, run.get("clearance_mm"), run.get("at_least_mm")):
            run["bounds_hold"] = (
                run["at_least_mm"] - 1e-6 <= full <= run["clearance_mm"] + 1e-6
            )
    out["runs"] = runs
    out["expected_mm"] = 0.4
    return out


# ---- the run -----------------------------------------------------------------------


def _titles(adapter: PyWin32Adapter) -> set[str]:
    return {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }


async def run(grid: tuple[int, int], steps: list[str]) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp, "grid": grid}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = _titles(adapter)
    try:
        tools = await _tools(adapter)
        table: dict[str, tuple[Callable[..., Any], tuple[Any, ...]]] = {
            "picks": (step_picks, (tools, grid)),
            "surfaces": (step_surfaces, (tools,)),
            "clearance": (step_clearance, (tools, grid, scratch)),
        }
        for key in steps:
            step, args = table[key]
            print(f"[probe] {key}: {step.__doc__}")
            started = time.perf_counter()
            try:
                report[key] = await step(adapter, *args)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report[key] = {"error": repr(exc)}
            report[key]["probe_seconds"] = round(time.perf_counter() - started, 1)
    finally:
        for title in _titles(adapter) - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        for path in scratch.glob("*"):
            try:
                path.unlink()
            except OSError:
                pass
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key, value in report.items():
            if isinstance(value, dict) and "error" in value:
                print(f"[probe] {key} FAILED: {value['error']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grid", default="20x12", help="pockets across x down")
    parser.add_argument(
        "--steps",
        default="picks,surfaces,clearance",
        help="comma-separated: picks, surfaces, clearance",
    )
    args = parser.parse_args()
    across, down = (int(n) for n in args.grid.lower().split("x"))
    return asyncio.run(run((across, down), args.steps.split(",")))


if __name__ == "__main__":
    sys.exit(main())
