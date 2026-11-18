"""Probe the COM calls behind the MMPLA parameter tools (a well-plate stamp mold).

The ``mmpla-params`` branch was written without SolidWorks (other sessions
had it). This runs every new COM sequence on scratch parts through
``PyWin32Adapter`` and the registered tools, and compares each result with
the number worked out beforehand. Each step says what it settles; the report
keeps the replies.

1. ``circular`` (an empty part, a sketch on Top): a seed circle r 1.5 at
   (30, 0) with a 3 mm diameter dimension, then ``sketch_circular_pattern``
   with ``count: 8`` about ``center_x: 10``. Expected: ``pattern.radius`` 20;
   new dimensions with roles radius (20), angle and count (8); after
   ``exit_sketch``, 8 full circles in ``get_sketch``, every centre 20 mm from
   (10, 0). Then ``set_dimension``: the count to 12 gives 12 circles; the
   radius to 25 puts every centre (the seed's too) 25 mm from (10, 0); the
   seed's diameter to 4 gives every circle r 2. Settles that
   ``RadiusDim``/``AngleDim``/``CreateNumOfInstancesDim`` make a parametric
   pattern, that ``ArcRadius``/``ArcAngle`` from the seed toward the centre
   put the centre where asked, and that the sketch feature's display
   dimensions name the new dimensions. The angle dimension's value (360, or
   the 45 between instances) is recorded, not checked.
2. ``linear`` (an empty part): a seed circle r 1 at (0, -20) with a 2 mm
   diameter dimension, then ``sketch_linear_pattern`` ``spacing: 4.5``,
   ``count: 12``. Expected: spacing (4.5) and count (12) dimensions; 12
   circles, the last centre at x 49.5. The count to 6 gives 6 circles, the
   spacing to 9 puts the last centre at x 45. Settles ``XSpacingDim`` and
   ``CreateNumOfInstancesDimInXDir``.
3. ``equations`` (step 2's part): ``set_global_variable`` "well_d" 1.2 mm;
   ``add_equation`` seed diameter = "well_d" (reads 1.2) and spacing =
   "well_d" * 5 (reads 6). "well_d" to 1.5: ``driven`` reads 1.5 and 7.5,
   ``get_sketch`` circles r 0.75 and the last centre at x 37.5.
   ``list_equations`` counts 3. ``"nope" * 2`` is ``invalid_input`` and
   leaves 3 equations and the spacing at 7.5. Deleting "well_d" is refused
   while used; deleting the spacing's equation keeps 7.5; deleting the
   other two leaves 0. Settles ``GetEquationMgr``, ``Add2`` (one
   configuration), the indexed ``Equation``/``Value``/``GlobalVariable``
   reads through ``IDispatch.Invoke``, ``Status`` after a bad expression,
   ``Delete`` and the rebuild that updates the dimensions.
4. ``session`` (step 2's part, no restart): with ``currentModel`` dropped,
   ``adopt_active_document`` gives the part back by title, and ``save_as``
   with ``currentModel`` dropped saves the active part (the file exists).
   Settles the reconnect's document adoption and ``save_file``'s fallback
   on a real ``ActiveDoc``; the reconnect itself needs SolidWorks restarted
   and is left to the unit tests.

It only touches the documents it creates, closes them without saving
(step 4 writes one file, under ``tests/.generated/probe_mmpla_params/``),
and keeps the report there.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_mmpla_params.py
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

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_mmpla_params"
#: Lengths agree within this much (mm).
TOLERANCE = 1e-3


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.engineering_data import register_engineering_data_tools
    from solidworks_mcp.tools.file_management import register_file_management_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-mmpla-params")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_engineering_data_tools(mcp, adapter, {})
    await register_file_management_tools(mcp, adapter, {})
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


def _circles(reply: dict[str, Any]) -> list[tuple[list[float], float]]:
    """Each full circle of a ``get_sketch`` reply: its centre and radius (mm)."""
    out = []
    for seg in (reply.get("sketch") or {}).get("segments") or []:
        start, end, centre = (
            seg.get("start_mm"),
            seg.get("end_mm"),
            seg.get("center_mm"),
        )
        if not (start and end and centre) or seg.get("construction"):
            continue
        if math.dist(start, end) > TOLERANCE:
            continue  # an arc, not a circle
        out.append((centre, math.dist(start, centre)))
    return out


def _role(reply: dict[str, Any], role: str) -> dict[str, Any]:
    dims = (reply.get("pattern") or {}).get("dimensions") or []
    return next((d for d in dims if d.get("role") == role), {})


async def _seed(
    tools: dict[str, Any], report: Report, step: str, x: float, y: float, r: float
) -> tuple[str, str]:
    """A sketch on Top with a circle and its diameter dimension: the names."""
    sketch = (await tools["create_sketch"]({"plane": "Top"}))["sketch"]["name"]
    circle = (await tools["add_circle"]({"center_x": x, "center_y": y, "radius": r}))[
        "circle"
    ]["id"]
    report.keep(
        f"{step}.diameter",
        await tools["add_sketch_dimension"](
            {"entity1": circle, "dimension_type": "diameter", "value": 2 * r}
        ),
    )
    return sketch, circle


async def _seed_dimension(
    tools: dict[str, Any], sketch: str, value: float, skip: set[str]
) -> str | None:
    listed = await tools["list_model_dimensions"]({"feature_name": sketch})
    return next(
        (
            d["full_name"]
            for d in listed.get("data") or []
            if d["full_name"] not in skip and abs(float(d["value"] or 0) - value) < 1e-6
        ),
        None,
    )


async def circular(tools: dict[str, Any], adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "mmpla_circular"})
    sketch, circle = await _seed(tools, report, "1", 30, 0, 1.5)
    made = report.keep(
        "1.pattern",
        await tools["sketch_circular_pattern"](
            {"entities": [circle], "count": 8, "center_x": 10, "center_y": 0}
        ),
    )
    settles = "ArcRadius/ArcAngle from the seed toward the centre"
    report.check("1.radius", settles, 20.0, (made.get("pattern") or {}).get("radius"))
    roles = sorted(d.get("role") for d in made["pattern"].get("dimensions") or [])
    report.check(
        "1.roles", "the pattern's own dimensions", ["angle", "count", "radius"], roles
    )
    report.check(
        "1.count_dim", "count dimension", 8.0, _role(made, "count").get("value")
    )
    report.check(
        "1.radius_dim", "radius dimension", 20.0, _role(made, "radius").get("value")
    )
    report.keep("1.angle_dim", _role(made, "angle"))
    await tools["exit_sketch"]()

    async def ring(label: str, count: int, radius: float, r: float) -> None:
        read = report.keep(label, await tools["get_sketch"]({"name": sketch}))
        found = _circles(read)
        report.check(f"{label}.count", "instances follow the count", count, len(found))
        far = [math.dist(c, [10.0, 0.0]) for c, _ in found]
        report.check(
            f"{label}.on_ring",
            "every centre, the seed's too, on the ring",
            True,
            bool(far) and all(abs(d - radius) <= TOLERANCE for d in far),
        )
        report.check(
            f"{label}.radii",
            "copies follow the seed's size",
            True,
            bool(found) and all(abs(s - r) <= TOLERANCE for _, s in found),
        )

    await ring("1.sketch", 8, 20.0, 1.5)
    pattern_names = {d["name"] for d in made["pattern"].get("dimensions") or []}
    report.keep(
        "1.set_count",
        await tools["set_dimension"](
            {"name": _role(made, "count").get("name"), "value": 12}
        ),
    )
    await ring("1.count12", 12, 20.0, 1.5)
    report.keep(
        "1.set_radius",
        await tools["set_dimension"](
            {"name": _role(made, "radius").get("name"), "value": 25}
        ),
    )
    await ring("1.radius25", 12, 25.0, 1.5)
    seed_dim = await _seed_dimension(tools, sketch, 3.0, pattern_names)
    report.keep("1.seed_dimension", seed_dim)
    report.keep(
        "1.set_seed",
        await tools["set_dimension"]({"name": seed_dim, "value": 4}),
    )
    await ring("1.seed4", 12, 25.0, 2.0)


async def linear(tools: dict[str, Any], adapter: Any, report: Report) -> dict[str, Any]:
    await tools["create_part"]({"name": "mmpla_linear"})
    sketch, circle = await _seed(tools, report, "2", 0, -20, 1)
    made = report.keep(
        "2.pattern",
        await tools["sketch_linear_pattern"](
            {"entities": [circle], "spacing": 4.5, "count": 12}
        ),
    )
    report.check(
        "2.spacing_dim", "spacing dimension", 4.5, _role(made, "spacing").get("value")
    )
    report.check(
        "2.count_dim", "count dimension", 12.0, _role(made, "count").get("value")
    )
    await tools["exit_sketch"]()

    async def row(label: str, count: int, last_x: float) -> None:
        found = _circles(
            report.keep(label, await tools["get_sketch"]({"name": sketch}))
        )
        report.check(f"{label}.count", "instances follow the count", count, len(found))
        report.check(
            f"{label}.last_x",
            "the row follows the spacing",
            last_x,
            max((c[0] for c, _ in found), default=None),
        )

    await row("2.sketch", 12, 49.5)
    await tools["set_dimension"]({"name": _role(made, "count").get("name"), "value": 6})
    await row("2.count6", 6, 22.5)
    await tools["set_dimension"](
        {"name": _role(made, "spacing").get("name"), "value": 9}
    )
    await row("2.spacing9", 6, 45.0)
    names = {d["name"] for d in made["pattern"].get("dimensions") or []}
    return {
        "sketch": sketch,
        "spacing": _role(made, "spacing").get("name"),
        "seed": await _seed_dimension(tools, sketch, 2.0, names),
    }


async def equations(
    tools: dict[str, Any], adapter: Any, report: Report, names: dict[str, Any]
) -> None:
    seed, spacing, sketch = names["seed"], names["spacing"], names["sketch"]
    added = report.keep(
        "3.variable",
        await tools["set_global_variable"](
            {"name": "well_d", "value": 1.2, "units": "mm"}
        ),
    )
    report.check("3.added", "Add2 a global variable", "added", added["data"]["action"])
    first = report.keep(
        "3.seed",
        await tools["add_equation"]({"dimension": seed, "expression": "well_d"}),
    )
    report.check(
        "3.seed_value",
        "an equation drives a dimension",
        1.2,
        (first.get("data") or {}).get("dimension", {}).get("value"),
    )
    second = report.keep(
        "3.spacing",
        await tools["add_equation"](
            {"dimension": spacing, "expression": '"well_d" * 5'}
        ),
    )
    report.check(
        "3.spacing_value",
        "an expression of the variable",
        6.0,
        (second.get("data") or {}).get("dimension", {}).get("value"),
    )
    changed = report.keep(
        "3.change",
        await tools["set_global_variable"]({"name": "well_d", "value": 1.5}),
    )
    report.check(
        "3.driven",
        "one change moves both dimensions",
        [1.5, 7.5],
        [
            (d.get("dimension") or {}).get("value")
            for d in (changed.get("data") or {}).get("driven") or []
        ],
    )
    found = _circles(
        report.keep("3.sketch", await tools["get_sketch"]({"name": sketch}))
    )
    report.check(
        "3.radii",
        "the seed's copies follow the variable",
        True,
        bool(found) and all(abs(s - 0.75) <= TOLERANCE for _, s in found),
    )
    report.check(
        "3.last_x",
        "the spacing follows the variable",
        37.5,
        max((c[0] for c, _ in found), default=None),
    )
    listed = report.keep("3.list", await tools["list_equations"]({}))
    report.check(
        "3.count", "the indexed reads", 3, (listed.get("data") or {}).get("count")
    )
    bad = report.keep(
        "3.bad",
        await tools["add_equation"]({"dimension": spacing, "expression": '"nope" * 2'}),
    )
    report.check(
        "3.bad_type",
        "Status -1 is invalid_input",
        "invalid_input",
        bad.get("error_type"),
    )
    after = await tools["list_equations"]({})
    report.check(
        "3.bad_count", "a refused equation is undone", 3, after["data"]["count"]
    )
    kept = await tools["get_dimension"]({"name": spacing})
    report.check(
        "3.bad_value",
        "and the dimension kept",
        7.5,
        (kept.get("dimension") or {}).get("value"),
    )
    refused = report.keep(
        "3.delete_used", await tools["delete_equation"]({"name": "well_d"})
    )
    report.check(
        "3.delete_used",
        "a variable in use is kept",
        "invalid_input",
        refused.get("error_type"),
    )
    report.keep("3.delete_spacing", await tools["delete_equation"]({"name": spacing}))
    freed = await tools["get_dimension"]({"name": spacing})
    report.check(
        "3.freed",
        "a freed dimension keeps its value",
        7.5,
        (freed.get("dimension") or {}).get("value"),
    )
    await tools["delete_equation"]({"name": seed})
    last = report.keep(
        "3.delete_variable", await tools["delete_equation"]({"name": "well_d"})
    )
    report.check("3.empty", "Delete", 0, (last.get("data") or {}).get("count"))


async def session(
    tools: dict[str, Any], adapter: Any, report: Report, out: Path
) -> None:
    title = str(adapter._get_attr_or_call(adapter.swApp.ActiveDoc, "GetTitle"))
    adapter.currentModel = None
    adopted = adapter._session_coordinator.adopt_active_document()
    report.check(
        "4.adopt",
        "adopt_active_document on a real ActiveDoc",
        title,
        str(adapter._get_attr_or_call(adopted, "GetTitle")) if adopted else None,
    )
    adapter.currentModel = None
    target = out / "mmpla_saved.SLDPRT"
    saved = report.keep("4.save_as", await tools["save_as"]({"file_path": str(target)}))
    report.check(
        "4.save_as", "save_file falls back to ActiveDoc", "success", saved.get("status")
    )
    report.check("4.file", "the file was written", True, target.exists())


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

        async def later_steps() -> None:
            # Steps 3 and 4 work on step 2's part.
            names = await linear(tools, adapter, report)
            await equations(tools, adapter, report, names)
            await session(tools, adapter, report, out)

        for name, step in (
            ("circular", lambda: circular(tools, adapter, report)),
            ("linear, equations, session", later_steps),
        ):
            try:
                await step()
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                raised = f"{type(exc).__name__}: {exc}"
                report.rows.append({"step": name, "raised": raised})
                print(f"[probe] {name} raised {raised}")
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
