"""Probe the quiet trials: cuts that pick their side without SolidWorks' error sound.

Written without SolidWorks; run it on scratch parts, with the user's OK. It
can't hear the sound, so it reads the toggle that makes it,
``swEnableSoundsForSolidWorksEvents`` (515), before, during and after each
cut (pitfall #61). "During" is read at every ``_create_cut_extrude_impl``
call, the build of one side.

1. A cut from a face: a 40 x 20 x 10 mm block on Front (z = 0 to 10), a plane
   on its front face (``face_point`` [0, 0, 10]), a 3 mm circle, a 2 mm blind
   cut with no ``reverse_direction``. Expected: ``direction_choice``
   ``from_geometry``, one side built, ``cut_toward`` -Z, 56.5 mm³ removed,
   and toggle 515 never changed.
2. A cut from a plane inside the block: Front offset 5 mm (z = 5), the same
   circle and cut. Expected: the trial (``more_material`` or
   ``equal_material``), three sides built, toggle 515 off at each (when the
   user has the sounds on) and back to the user's value after.
3. The same as 2 with ``SOLIDWORKS_MCP_QUIET_TRIALS=0``: the toggle stays as
   the user has it throughout.

It only touches the documents it creates, closes them without saving, and
writes the report under ``tests/.generated/probe_quiet_trials/``. If it stops
part-way, check Tools > Options > System Options > General > "Enable sounds
for SolidWorks events": the report's ``before`` says what it was.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_quiet_trials.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import features, quiet

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_quiet_trials"
#: The circle's radius and the cut's depth, mm.
RADIUS = 3.0
DEPTH = 2.0


class Report:
    """What each step tried and got."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def record(self, step: str, settles: str, got: Any, ok: bool | None = None) -> Any:
        self.rows.append({"step": step, "settles": settles, "got": got, "ok": ok})
        mark = {True: "ok ", False: "BAD", None: "   "}[ok]
        print(f"[probe] {mark} {step}: {got!r}")
        return got


def _sounds(adapter: Any) -> Any:
    """Toggle 515 as SolidWorks reads it now."""
    try:
        return adapter.swApp.GetUserPreferenceToggle(quiet.EVENT_SOUNDS)
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        return f"raised {type(exc).__name__}: {exc}"


class _Watch:
    """Read toggle 515 at every side the cut builds."""

    def __init__(self, adapter: Any) -> None:
        self.adapter = adapter
        self.during: list[Any] = []
        self._impl = features._create_cut_extrude_impl

    def __enter__(self) -> _Watch:
        def impl(*args: Any, **kwargs: Any) -> Any:
            self.during.append(_sounds(self.adapter))
            return self._impl(*args, **kwargs)

        features._create_cut_extrude_impl = impl
        return self

    def __exit__(self, *_exc: Any) -> None:
        features._create_cut_extrude_impl = self._impl


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-quiet-trials")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _block(adapter: Any, tools: dict[str, Any]) -> None:
    """A 40 x 20 x 10 mm block on Front, from z = 0 to z = 10."""
    await adapter.create_part()
    await tools["set_units"]({"unit_system": "mm"})
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_rectangle"]({"x1": -20, "y1": -10, "x2": 20, "y2": 10})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 10.0})


async def _cut_on(
    adapter: Any, tools: dict[str, Any], report: Report, step: str, plane: str
) -> dict[str, Any]:
    """A circle on ``plane`` cut ``DEPTH`` mm with no direction; what 515 read."""
    sketch = await tools["create_sketch"]({"plane": plane})
    await tools["add_circle"]({"center_x": 0.0, "center_y": 0.0, "radius": RADIUS})
    await tools["exit_sketch"]()
    name = (sketch.get("sketch") or {}).get("name")
    before = _sounds(adapter)
    with _Watch(adapter) as watch:
        reply = await tools["create_cut_extrude"]({"depth": DEPTH, "sketch_name": name})
    after = _sounds(adapter)
    got = {
        "before": before,
        "during": watch.during,
        "after": after,
        "reply": reply,
    }
    report.record(step, "toggle 515 around the cut, and the reply", got)
    return got


def _parameters(got: dict[str, Any]) -> dict[str, Any]:
    reply = got.get("reply") or {}
    return {
        "cut_extrude": reply.get("cut_extrude"),
        "removed_mm3": -(
            (reply.get("measured_change") or {}).get("volume_delta_mm3") or 0
        ),
        "message": reply.get("message"),
    }


async def from_a_face(adapter: Any, tools: dict[str, Any], report: Report) -> None:
    await _block(adapter, tools)
    made = await adapter.create_reference_plane(
        "", 0.0, 0.0, False, None, [0.0, 0.0, 10.0]
    )
    plane = str((made.data or {}).get("name"))
    got = await _cut_on(adapter, tools, report, "1.cut", plane)
    shown = _parameters(got)
    expected = math.pi * RADIUS**2 * DEPTH
    report.record(
        "1.one_side",
        "one side built, toggle 515 never changed",
        {"sides": len(got["during"]), **shown},
        ok=len(got["during"]) == 1
        and got["during"] == [got["before"]]
        and got["after"] == got["before"]
        and abs(shown["removed_mm3"] - expected) < 0.05 * expected,
    )
    report.record(
        "1.message",
        "the message (from_geometry, cut toward -Z)",
        shown["message"],
        ok="-Z" in str(shown["message"]),
    )


async def inside_the_block(
    adapter: Any, tools: dict[str, Any], report: Report, step: str = "2"
) -> None:
    await _block(adapter, tools)
    made = await adapter.create_reference_plane("Front Plane", 5.0)
    plane = str((made.data or {}).get("name"))
    got = await _cut_on(adapter, tools, report, f"{step}.cut", plane)
    quiet_on = quiet.enabled()
    muted = all(value is False for value in got["during"])
    report.record(
        f"{step}.trial",
        "three sides built; 515 off during each (quiet on, sounds on) and back after",
        {"sides": len(got["during"]), "quiet": quiet_on, **_parameters(got)},
        ok=len(got["during"]) >= 2
        and got["after"] == got["before"]
        and (
            muted
            if quiet_on and got["before"] is True
            else all(v == got["before"] for v in got["during"])
        ),
    )


async def run() -> int:
    out = OUT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    report = Report()
    adapter = PyWin32Adapter({})
    await adapter.connect()
    report.record("0.before", "the user's toggle 515", _sounds(adapter))
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    was = os.environ.get(quiet.QUIET_TRIALS_ENV)
    try:
        tools = await _tools(adapter)
        for name, step in (
            ("1", from_a_face),
            ("2", inside_the_block),
        ):
            try:
                await step(adapter, tools, report)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report.record(name, "raised", f"{type(exc).__name__}: {exc}", ok=False)
        os.environ[quiet.QUIET_TRIALS_ENV] = "0"
        try:
            await inside_the_block(adapter, tools, report, step="3")
        except Exception as exc:  # noqa: BLE001 - a probe records every failure
            report.record("3", "raised", f"{type(exc).__name__}: {exc}", ok=False)
    finally:
        if was is None:
            os.environ.pop(quiet.QUIET_TRIALS_ENV, None)
        else:
            os.environ[quiet.QUIET_TRIALS_ENV] = was
        report.record("9.after", "the user's toggle 515 at the end", _sounds(adapter))
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
