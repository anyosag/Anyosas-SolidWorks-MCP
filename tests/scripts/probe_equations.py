"""Probe ``IEquationMgr`` through late binding (``parametric-equations`` task group 1).

Written without SolidWorks; run it on scratch parts, with the user's OK, to
settle the decisions ``design.md`` leaves to the probes. Each step records
what it got; the report keeps everything.

1.1 The manager (a new part, a 40 x 20 x 10 block):
    ``GetEquationMgr()``, ``GetCount()``; ``Equation(0)``, ``Value(0)`` and
    ``GlobalVariable(0)`` read three ways: a plain call, after
    ``flagged(eqmgr, "IEquationMgr")``, and ``_oleobj_.InvokeTypes`` with
    gen_py's DISPIDs (8, 7, 22). ``Add3`` with ``swAllConfiguration`` (2) on
    a part with two configurations: the equation reads in both. Settles D4's
    add call and D5.
1.2 Units: on an mm part and an inch part, ``"D1@Sketch1" = 25``, ``= 25mm``
    and ``= 1in``; each one's ``Value(i)`` and the dimension's
    ``SystemValue``. Settles D2: suffixes accepted, and what a bare number
    means.
1.3 Refusals: ``Add3`` with ``"D1@NoSuchSketch" = 5`` (the index and
    ``Status``); ``SetEquation`` with a bad expression (is the old text
    kept?); ``Delete`` out of range. Settles D4's refusal rules.
1.4 Broken: ``"D1@Boss-Extrude1" = 2 * "<circle diameter>@Sketch2"``, then
    ``delete_feature`` on Sketch2: ``Parameter`` of the gone name is null and
    the equation survives; ``list_equations`` marks it ``broken``. Settles D6.
1.5 The tools end to end: ``set_equations`` on the block (9000 mm³),
    replacing (10000 mm³), a refused middle equation (list unchanged),
    ``driven_by`` and the ``set_dimension`` refusal.

It only touches the documents it creates, closes them without saving, and
writes the report under ``tests/.generated/probe_equations/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_equations.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_equations"
#: gen_py's DISPIDs for the indexed properties (sldworks.tlb, SOLIDWORKS 2026).
DISPIDS = {"Equation": (8, 8), "Value": (7, 5), "GlobalVariable": (22, 11)}
LCID = 0
PROPERTY_GET = 2


class Report:
    """What each step tried and got."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, step: str, settles: str, got: Any, ok: bool | None = None) -> Any:
        self.rows.append({"step": step, "settles": settles, "got": got, "ok": ok})
        mark = {True: "ok ", False: "BAD", None: "   "}[ok]
        print(f"[probe] {mark} {step}: {got!r}")
        return got


def _try(call: Callable[[], Any]) -> Any:
    try:
        return {"value": call()}
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return {"raised": f"{type(exc).__name__}: {exc}"}


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.engineering_data import register_engineering_data_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-equations")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_engineering_data_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _block(adapter: Any, tools: dict[str, Any], units: str = "mm") -> dict:
    """A 40 x 20 x 10 mm block; the width dimension's name and the boss's."""
    await adapter.create_part()
    if units != "mm":
        await tools["set_units"]({"unit_system": units})
    sketch = await tools["create_sketch"]({"plane": "Front"})
    corners = [(-20, -10), (20, -10), (20, 10), (-20, 10)]
    lines = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1], strict=True):
        line = await tools["add_line"]({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
        lines.append(line["line"]["id"])
    width = await tools["add_sketch_dimension"]({"entity1": lines[0], "value": 40})
    await tools["exit_sketch"]()
    boss = await tools["create_extrusion"]({"depth": 10.0})
    return {
        "width": (width.get("dimension") or {}).get("name"),
        "sketch": (sketch.get("sketch") or {}).get("name"),
        "boss": (boss.get("extrusion") or {}).get("name"),
    }


def _manager(adapter: Any) -> Any:
    from solidworks_mcp.adapters.solidworks import equations as eq

    return eq.manager(adapter, adapter.currentModel)


async def manager_calls(adapter: Any, tools: dict[str, Any], report: Report) -> None:
    from solidworks_mcp.adapters import sw_type_info
    from solidworks_mcp.adapters.solidworks.ref_plane_edit import get_indexed

    block = await _block(adapter, tools)
    model = adapter.currentModel
    mgr = _manager(adapter)
    report.record("1.1.count_empty", "GetCount", _try(lambda: mgr.GetCount()))
    added = _try(lambda: mgr.Add3(0, '"W" = 45mm', False, 2, None))
    report.record("1.1.add3", "Add3 with swAllConfiguration", added)
    if "raised" in added:
        report.record(
            "1.1.add2", "Add2 fallback", _try(lambda: mgr.Add2(0, '"W" = 45mm', False))
        )
    for name, (dispid, vt) in DISPIDS.items():
        report.record(
            f"1.1.{name}.plain", "a plain call", _try(lambda n=name: getattr(mgr, n)(0))
        )
        report.record(
            f"1.1.{name}.invoke",
            "IDispatch.Invoke (get_indexed, what the adapter uses)",
            _try(lambda n=name: get_indexed(mgr, n, 0)),
        )
        report.record(
            f"1.1.{name}.invoketypes",
            "InvokeTypes with gen_py's DISPID",
            _try(
                lambda d=dispid, v=vt: mgr._oleobj_.InvokeTypes(
                    d, LCID, PROPERTY_GET, (v, 0), ((3, 1),), 0
                )
            ),
        )
    flagged = _try(lambda: sw_type_info.flagged(mgr, "IEquationMgr"))
    report.record("1.1.flagged", "flagged(eqmgr, IEquationMgr)", flagged)
    if "value" in flagged:
        report.record(
            "1.1.Equation.flagged",
            "Equation(0) after flagging",
            _try(lambda: flagged["value"].Equation(0)),
        )
    report.record(
        "1.1.solve_order", "AutomaticSolveOrder", _try(lambda: mgr.AutomaticSolveOrder)
    )
    report.record("1.1.status", "Status", _try(lambda: mgr.Status))
    # Two configurations: does swAllConfiguration reach the second?
    report.record(
        "1.1.add_config",
        "a second configuration",
        _try(lambda: model.AddConfiguration3("Second", "", "", 0)),
    )
    report.record(
        "1.1.second_config",
        "the equation in the second configuration",
        _try(lambda: get_indexed(_manager(adapter), "Equation", 0)),
    )
    report.record("1.1.block", "the block's names", block)


async def units(adapter: Any, tools: dict[str, Any], report: Report) -> None:
    from solidworks_mcp.adapters.solidworks.ref_plane_edit import get_indexed

    for unit in ("mm", "in"):
        block = await _block(adapter, tools, units=unit)
        mgr = _manager(adapter)
        name = block["width"]
        for right in ("25", "25mm", "1in"):
            text = f'"{name}" = {right}'
            count = mgr.GetCount()
            index = _try(lambda m=mgr, t=text, c=count: m.Add3(c, t, True, 2, None))
            adapter.currentModel.ForceRebuild3(False)
            report.record(
                f"1.2.{unit}.{right}",
                "a unit suffix, and what a bare number means",
                {
                    "added": index,
                    "value": _try(lambda m=mgr, c=count: get_indexed(m, "Value", c)),
                    "system_value": _try(
                        lambda n=name: adapter.currentModel.Parameter(n).SystemValue
                    ),
                },
            )
            if isinstance(index.get("value"), int) and index["value"] >= 0:
                mgr.Delete(index["value"])


async def refusals(adapter: Any, tools: dict[str, Any], report: Report) -> None:
    from solidworks_mcp.adapters.solidworks.ref_plane_edit import (
        get_indexed,
        put_indexed,
    )

    await _block(adapter, tools)
    mgr = _manager(adapter)
    report.record(
        "1.3.add_missing",
        "Add3 of a missing dimension: index and Status",
        {
            "index": _try(lambda: mgr.Add3(0, '"D1@NoSuchSketch" = 5', False, 2, None)),
            "status": _try(lambda: mgr.Status),
            "count": _try(lambda: mgr.GetCount()),
        },
    )
    count = mgr.GetCount()
    mgr.Add3(count, '"H" = 5', False, 2, None)
    put = _try(lambda: put_indexed(mgr, "Equation", count, '"H" = 6 +'))
    report.record(
        "1.3.bad_set",
        "SetEquation with a bad expression keeps the old text",
        {"put": put, "reads": _try(lambda: get_indexed(mgr, "Equation", count))},
    )
    report.record(
        "1.3.delete_range", "Delete out of range", _try(lambda: mgr.Delete(99))
    )


async def broken(adapter: Any, tools: dict[str, Any], report: Report) -> None:
    block = await _block(adapter, tools)
    second = await tools["create_sketch"]({"plane": "Top"})
    circle = await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 3})
    diameter = await tools["add_sketch_dimension"](
        {"entity1": circle["circle"]["id"], "dimension_type": "diameter", "value": 6}
    )
    await tools["exit_sketch"]()
    gone = (diameter.get("dimension") or {}).get("name")
    set_reply = await tools["set_equations"](
        {"equations": [f'"D1@{block["boss"]}" = 2 * "{gone}"']}
    )
    report.record("1.4.set", "the equation", set_reply)
    deleted = await tools["delete_feature"]({"name": second["sketch"]["name"]})
    report.record("1.4.delete_feature", "delete_feature on Sketch2", deleted)
    report.record(
        "1.4.parameter",
        "Parameter of the gone name is null",
        _try(lambda: adapter.currentModel.Parameter(gone) is None),
    )
    listed = await tools["list_equations"]({})
    entries = (listed.get("data") or {}).get("equations") or []
    report.record(
        "1.4.broken",
        "list_equations marks it broken",
        entries,
        ok=any(e["broken"] for e in entries),
    )


async def tools_end_to_end(adapter: Any, tools: dict[str, Any], report: Report) -> None:
    block = await _block(adapter, tools)
    width = block["width"]
    added = await tools["set_equations"](
        {"equations": ['"W" = 45', f'"{width}" = "W"']}
    )
    delta = ((added.get("data") or {}).get("measured_change") or {}).get(
        "volume_delta_mm3"
    )
    report.record(
        "1.5.added",
        "9000 mm³",
        added,
        ok=delta is not None and abs(delta - 1000) < 1e-3,
    )
    replaced = await tools["set_equations"]({"equations": ['"W" = 50']})
    report.record(
        "1.5.replaced",
        "replaced in place",
        replaced,
        ok=((replaced.get("data") or {}).get("equations") or [{}])[0].get("replaced")
        is True,
    )
    refused = await tools["set_equations"](
        {"equations": ['"W" = 55', '"D1@NoSuchSketch" = "W"', '"H" = 5']}
    )
    listed = await tools["list_equations"]({})
    report.record(
        "1.5.refused",
        "a refused middle equation restores the list",
        {"refused": refused, "list": listed},
        ok=refused.get("error_type") == "invalid_input"
        and (listed.get("data") or {}).get("count") == 2,
    )
    dims = await tools["list_model_dimensions"]({})
    driven = {d["full_name"]: d.get("driven_by") for d in dims.get("data") or []}
    report.record(
        "1.5.driven_by", "driven_by", driven, ok=driven.get(width) is not None
    )
    refusal = await tools["set_dimension"]({"name": width, "value": 30})
    report.record(
        "1.5.set_dimension",
        "set_dimension refuses a driven dimension",
        refusal,
        ok=refusal.get("error_type") == "invalid_input",
    )


async def run() -> int:
    out = OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
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
        for name, step in (
            ("1.1", manager_calls),
            ("1.2", units),
            ("1.3", refusals),
            ("1.4", broken),
            ("1.5", tools_end_to_end),
        ):
            try:
                await step(adapter, tools, report)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report.record(name, "raised", f"{type(exc).__name__}: {exc}", ok=False)
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(
            json.dumps(report.rows, indent=2, default=str), encoding="utf-8"
        )
        print(f"[probe] report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
