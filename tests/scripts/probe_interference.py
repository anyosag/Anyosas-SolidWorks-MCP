"""Probe ``check_interference`` in the kernel, against SolidWorks' own detection.

Written without SolidWorks; run it on scratch documents, with the user's OK.
It builds a 40 x 10 x 10 mm bar (x 0 to 40, y -5 to 5, z 0 to 10) and four
assemblies of it. The window is left as the user has it: the user's Stage
Manager parks SolidWorks without minimizing it, and a minimized frame would
fight it.

1. ``pair``: two bars 30 mm apart along x, sharing 10 x 10 x 10 = 1000 mm3;
2. ``turned``: the second one turned 90 degrees about z at x = 30
   (``set_component_transform``): it spans x 25 to 35 and y 0 to 40, and
   shares 10 x 5 x 10 = 500 mm3 (the bodies are placed by ``Transform2``);
3. ``touching``: two bars 40 mm apart, face to face: no interference;
4. ``row``: ten bars 30 mm apart, each sharing 1000 mm3 with the next: nine.

For each, it records:

* the tool's ``check_interference`` (the kernel): ``method``, the count, the
  volumes and the time, and the window state and whether SolidWorks has the
  foreground before and after it (unchanged, and not brought to the front).
  Whether a PropertyManager showed can only be seen: watch the screen;
* SolidWorks' own detection on the same assembly, as the fallback runs it
  (``io._manager_interferences``): the count, the volumes and the time;
* that the two agree: the same count, volumes within 0.1 %.

Then ``check_interference`` with ``coincident`` on the touching pair:
``method: interference_detection`` and a ``note``.

It only touches the documents it creates, closes them without saving, and
deletes its files. The report is under ``tests/.generated/probe_interference/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_interference.py --live

Without ``--live`` it only prints this and exits: once, ``--help`` ran it on
SolidWorks without the user's OK, and the cut pipe killed it before its
clean-up, leaving its documents open and SolidWorks minimized.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import io

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_interference"
#: The assemblies: the bars' x positions (mm), and what they share.
CASES = {
    "pair": ([0.0, 30.0], [1000.0]),
    "turned": ([0.0, 30.0], [500.0]),
    "touching": ([0.0, 40.0], []),
    "row": ([30.0 * k for k in range(10)], [1000.0] * 9),
}


class Report:
    """What each step tried and got."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, step: str, settles: str, got: Any, ok: bool | None = None) -> Any:
        self.rows.append({"step": step, "settles": settles, "got": got, "ok": ok})
        mark = {True: "ok ", False: "BAD", None: "   "}[ok]
        print(f"[probe] {mark} {step}: {got!r}")
        return got


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools import register_tools

    mcp = FastMCP("probe-interference")
    await register_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _bar(adapter: Any, tools: dict[str, Any], folder: Path) -> str:
    """The 40 x 10 x 10 mm bar, saved; its path."""
    await adapter.create_part()
    await tools["set_units"]({"unit_system": "mm"})
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_rectangle"]({"x1": 0, "y1": -5, "x2": 40, "y2": 5})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 10.0})
    path = str(folder / "probe_interference_bar.SLDPRT")
    await tools["save_as"]({"file_path": path})
    return path


async def _assembly(
    adapter: Any, tools: dict[str, Any], bar: str, xs: list[float]
) -> list[str]:
    """A new assembly of bars at ``xs``; the components' names."""
    await adapter.create_assembly()
    names = []
    for x in xs:
        reply = await tools["insert_component"](
            {"file_path": bar, "x": x, "y": 0.0, "z": 0.0}
        )
        names.append(str((reply.get("component") or {}).get("component")))
    return names


def _window(adapter: Any) -> dict[str, Any]:
    window = adapter._window
    return {
        "state": adapter._window_state(),
        "solidworks_in_front": bool(window and window.foreground_is_solidworks()),
    }


def _volumes(data: dict[str, Any]) -> list[float]:
    return sorted(float(i.get("volume_mm3") or 0.0) for i in data["interferences"])


def _close(found: list[float], expected: list[float]) -> bool:
    return len(found) == len(expected) and all(
        abs(a - b) <= 1e-3 * max(1.0, b)
        for a, b in zip(found, sorted(expected), strict=True)
    )


async def _kernel(adapter: Any, tools: dict[str, Any]) -> dict[str, Any]:
    window_before = _window(adapter)
    started = time.perf_counter()
    reply = await tools["check_interference"]({})
    seconds = time.perf_counter() - started
    data = reply.get("data") or {}
    return {
        "status": reply.get("status"),
        "message": reply.get("message"),
        "method": data.get("method"),
        "note": data.get("note"),
        "count": data.get("interference_count"),
        "volumes": _volumes(data) if "interferences" in data else None,
        "seconds": round(seconds, 3),
        "execution_time": reply.get("execution_time"),
        "window_before": window_before,
        "window_after": _window(adapter),
    }


def _manager(adapter: Any) -> dict[str, Any]:
    def detect() -> dict[str, Any]:
        assembly = sw_type_info.flagged(adapter.currentModel, "IAssemblyDoc")
        return io._manager_interferences(adapter, assembly, False, True, True, set())

    started = time.perf_counter()
    result = adapter._handle_com_operation("check_interference", detect)
    seconds = time.perf_counter() - started
    data = result.data if result.is_success else None
    return {
        "error": result.error,
        "count": None if data is None else data.get("interference_count"),
        "volumes": None if data is None else _volumes(data),
        "seconds": round(seconds, 3),
    }


async def case(
    adapter: Any, tools: dict[str, Any], report: Report, bar: str, name: str
) -> None:
    xs, expected = CASES[name]
    components = await _assembly(adapter, tools, bar, xs)
    if name == "turned":
        report.record(
            f"{name}.turn",
            "the second bar turned 90 degrees about z at x = 30",
            await tools["set_component_transform"](
                {
                    "component": components[1],
                    "origin_mm": [30.0, 0.0, 0.0],
                    "rotation_deg": [0.0, 0.0, 90.0],
                }
            ),
        )
    kernel = await _kernel(adapter, tools)
    was, now = kernel["window_before"], kernel["window_after"]
    unmoved = now["state"] == was["state"] and (
        was["solidworks_in_front"] or not now["solidworks_in_front"]
    )
    report.record(
        f"{name}.kernel",
        f"method body_intersection, volumes {expected}, the window as it was, "
        "not brought to the front",
        kernel,
        ok=kernel["method"] == "body_intersection"
        and _close(kernel["volumes"] or [], expected)
        and unmoved,
    )
    manager = _manager(adapter)
    report.record(
        f"{name}.manager",
        "SolidWorks' own detection agrees",
        manager,
        ok=manager["volumes"] is not None and _close(manager["volumes"], expected),
    )
    if name == "touching":
        reply = await tools["check_interference"]({})
        coincident = await adapter.check_interference({"coincident": True})
        data = coincident.data or {}
        report.record(
            f"{name}.coincident",
            "coincident goes to SolidWorks' detection, with a note",
            {
                "plain": (reply.get("data") or {}).get("interference_count"),
                "method": data.get("method"),
                "note": data.get("note"),
                "count": data.get("interference_count"),
            },
            ok=data.get("method") == "interference_detection"
            and bool(data.get("note")),
        )


async def run() -> int:
    out = OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="probe_interference_"))
    report = Report()
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    window_before = adapter._window_state()
    report.record("0.window", "the window as the user had it", window_before)
    try:
        tools = await _tools(adapter)
        bar = await _bar(adapter, tools, folder)
        for name in CASES:
            try:
                await case(adapter, tools, report, bar, name)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report.record(name, "raised", f"{type(exc).__name__}: {exc}", ok=False)
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        report.record("9.window", "the window at the end", adapter._window_state())
        await adapter.disconnect()
        shutil.rmtree(folder, ignore_errors=True)
        path = out / "report.json"
        path.write_text(
            json.dumps(report.rows, indent=2, default=str), encoding="utf-8"
        )
        print(f"[probe] report: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run on SolidWorks only with ``--live``; otherwise print the usage."""
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").split("\n\n", 1)[0],
        epilog="Without --live nothing touches SolidWorks.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="connect to the running SolidWorks and run the probe (the user's OK first)",
    )
    if not parser.parse_args(argv).live:
        parser.print_help()
        return 2
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
