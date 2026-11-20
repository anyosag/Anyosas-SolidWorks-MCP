"""Probe the COM calls behind fully defining a sketch (the "MMPLA" session).

The ``mmpla-sketch`` branch was written without SolidWorks (other sessions
had it). This runs every new COM sequence on scratch parts through
``PyWin32Adapter`` and the registered tools, and compares each result with
the value worked out beforehand. Each step says what it settles; the report
keeps the replies.

1. ``points`` (part ``mmpla_points``, a sketch on Front):
   a. a circle at (8, 3) r 0.3 and a centreline along x, (-5, 0) to (20, 0),
      fixed. ``add_sketch_constraint`` coincident(circle, centreline):
      ``entities_used`` [circle.center, centreline], ``verified_by``
      ``relation_count``. Settles ``AddRelation`` with the circle's
      ``GetCenterPoint2`` point and a line, and ``GetRelationsCount(0)``.
   b. a circle at (15, 4) r 1, coincident with ``"Origin"``: success. Settles
      the ``OriginProfileFeature`` sketch point as an ``AddRelation`` entity.
   c. the same coincident again: an error ("did not take"), or success with
      ``verified_by`` ``relation_count`` if SolidWorks adds a second one.
      Settles what a repeated relation reads as.
   d. ``check_sketch_fully_defined``: ``under_defined``, raw 2 (``x`` of the
      first circle and both radii are free). Settles
      ``ISketch.GetConstrainedStatus`` on the open sketch.
   e. horizontal dimension ``<circle>.center`` to ``Origin``, 8 mm:
      ``measured`` 8, ``name`` ``D1@Sketch1``. Settles
      ``AddHorizontalDimension2`` with two points selected (``Select4`` /
      ``Select2`` on ``ISketchPoint``), one of them the origin.
   f. diameters 0.6 and 2: then ``check_sketch_fully_defined``
      ``fully_defined``, raw 3.
   g. after ``exit_sketch``, ``get_sketch`` ``summary``: the first circle at
      (8, 0) r 0.3, the second at (0, 0) r 1; ``definition_state``
      ``fully_defined``; ``relations`` holds a ``coincident`` (point and
      line), one with the origin and a ``fix``; ``dimensions`` 3 values 8,
      0.6 and 2. Settles ``GetRelations``, ``GetEntities``,
      ``GetEntitiesType`` and ``GetID`` on a closed sketch.
2. ``distances`` (part ``mmpla_distances``, a sketch on Front): circles A at
   (10, 10) r 2 and B at (30, 12) r 2, a vertical centreline x = 0 from
   (0, -5) to (0, 40), fixed, and a line (0, -10) to (40, -10).
   a. linear A to B, 25: ``measured`` 25. Settles ``AddDimension2`` with
      both centres selected.
   b. linear A to the centreline, 12: ``measured`` 12. Settles a centre to
      a line.
   c. vertical ``A.center`` to ``Origin``, 7: ``measured`` 7. Settles
      ``AddVerticalDimension2``.
   d. horizontal on the line alone, 40: ``measured`` 40.
   e. after ``exit_sketch``, ``get_sketch`` ``summary``: A at (12, 7) and
      |AB| = 25 within 1e-4 mm.
   f. last, in a reopened sketch (``edit_sketch``): a horizontal ``A`` to
      ``Origin`` of 20, which over-defines it: an error, either the read
      back ("reads 12 mm, not 20 mm") or ``solidworks_dialog`` if
      SolidWorks asks to make it driven (the watchdog cancels that).
      Settles that a dimension that didn't take is not a success; the ids
      ``edit_sketch`` gives circles start with ``Circle_``.
3. ``summary`` (part ``mmpla_summary``, a sketch on Front): 30 circles r 1 on
   a 6 x 5 grid 5 mm apart, the first fixed. After ``exit_sketch``:
   a. ``get_sketch`` ``full``: 30 segments, every one ``circle`` with
      ``radius_mm`` 1 and no ``start_mm``.
   b. ``get_sketch`` ``summary``: ``segment_counts`` {"circle": 30}, 30
      ``circles``, a ``fix`` relation on segment 0, ``under_defined``; and
      faster than the full read (expected True; both times kept).
   c. ``check_sketch_fully_defined`` by name: ``under_defined``. Settles
      ``GetConstrainedStatus`` on a sketch that isn't open.

It only touches the documents it creates, closes them without saving, and
keeps the report in ``tests/.generated/probe_mmpla_sketch/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_mmpla_sketch.py
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_mmpla_sketch"
#: Lengths agree within this much (mm).
TOLERANCE = 1e-4


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-mmpla-sketch")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
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


def _id(reply: dict[str, Any], key: str) -> str:
    return str((reply.get(key) or {}).get("id"))


async def _circle(tools: dict[str, Any], x: float, y: float, r: float) -> str:
    reply = await tools["add_circle"]({"center_x": x, "center_y": y, "radius": r})
    return _id(reply, "circle")


async def _centreline(tools: dict[str, Any], x1, y1, x2, y2) -> str:
    reply = await tools["add_centerline"]({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return _id(reply, "centerline")


async def _relate(tools, report, name, e1, e2, kind) -> dict[str, Any]:
    return report.keep(
        name,
        await tools["add_sketch_constraint"](
            {"entity1": e1, "entity2": e2, "relation_type": kind}
        ),
    )


async def _dimension(tools, report, name, e1, e2, kind, value) -> dict[str, Any]:
    return report.keep(
        name,
        await tools["add_sketch_dimension"](
            {"entity1": e1, "entity2": e2, "dimension_type": kind, "value": value}
        ),
    )


def _measured(reply: dict[str, Any]) -> Any:
    return (reply.get("dimension") or {}).get("measured")


async def _state(tools: dict[str, Any], sketch: str | None = None) -> tuple:
    reply = await tools["check_sketch_fully_defined"](
        input_data={"sketch_name": sketch} if sketch else None
    )
    found = reply.get("sketch") or {}
    return found.get("definition_state"), found.get("raw_status")


async def _summary(tools: dict[str, Any], name: str) -> dict[str, Any]:
    reply = await tools["get_sketch"]({"name": name, "detail": "summary"})
    return reply.get("sketch") or {}


def _centre(summary: dict[str, Any], index: int) -> Any:
    for circle in summary.get("circles") or []:
        if circle.get("index") == index:
            return circle.get("center_mm"), circle.get("radius_mm")
    return None, None


async def points(tools: dict[str, Any], _adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "mmpla_points"})
    await tools["create_sketch"]({"plane": "Front"})
    first = await _circle(tools, 8, 3, 0.3)
    axis = await _centreline(tools, -5, 0, 20, 0)
    await _relate(tools, report, "1a.fix", axis, None, "fix")
    tied = await _relate(tools, report, "1a.coincident", first, axis, "coincident")
    report.check(
        "1a.entities",
        "a circle coincident with a line ties its centre",
        [f"{first}.center", axis],
        (tied.get("constraint") or {}).get("entities_used"),
    )
    report.check(
        "1a.verified",
        "GetRelationsCount rises",
        "relation_count",
        (tied.get("constraint") or {}).get("verified_by"),
    )
    second = await _circle(tools, 15, 4, 1)
    origin = await _relate(tools, report, "1b.origin", second, "Origin", "coincident")
    report.check(
        "1b.origin", "the origin is an AddRelation entity", "success", origin["status"]
    )
    again = await _relate(tools, report, "1c.again", second, "Origin", "coincident")
    report.check(
        "1c.again",
        "a repeated relation reads as not taken (or a second relation)",
        True,
        again["status"] == "error"
        or (again.get("constraint") or {}).get("verified_by") == "relation_count",
    )
    report.check(
        "1d.state",
        "GetConstrainedStatus on the open sketch",
        ("under_defined", 2),
        await _state(tools),
    )
    horizontal = await _dimension(
        tools, report, "1e.horizontal", f"{first}.center", "Origin", "horizontal", 8
    )
    report.check(
        "1e.measured",
        "AddHorizontalDimension2 on two points",
        8.0,
        _measured(horizontal),
    )
    report.check(
        "1e.name",
        "the dimension's name for set_dimension",
        "D1@Sketch1",
        (horizontal.get("dimension") or {}).get("name"),
    )
    await _dimension(tools, report, "1f.d1", first, None, "diameter", 0.6)
    await _dimension(tools, report, "1f.d2", second, None, "diameter", 2)
    report.check(
        "1f.state",
        "fully defined once placed and sized",
        ("fully_defined", 3),
        await _state(tools),
    )
    exited = report.keep("1g.exit", await tools["exit_sketch"]())
    name = (exited.get("sketch") or {}).get("name") or "Sketch1"
    summary = report.keep("1g.summary", await _summary(tools, name))
    report.check(
        "1g.first",
        "the centre moved onto the line",
        ([8.0, 0.0], 0.3),
        _centre(summary, 0),
    )
    report.check(
        # Index 1, not 2: run 20261119-232612 read GetSketchSegments as the
        # two circles, then the centreline, not in the order they were drawn.
        "1g.second",
        "the centre on the origin",
        ([0.0, 0.0], 1.0),
        _centre(summary, 1),
    )
    report.check(
        "1g.state",
        "read on a closed sketch",
        "fully_defined",
        summary.get("definition_state"),
    )
    kinds = sorted(r.get("type") for r in summary.get("relations") or [])
    report.check(
        "1g.relations",
        "GetRelations lists the relations added",
        True,
        {"coincident", "fix"} <= set(kinds),
    )
    values = sorted(d.get("value") for d in summary.get("dimensions") or [])
    report.check(
        "1g.dimensions", "the sketch's dimensions by name", [0.6, 2.0, 8.0], values
    )


async def distances(tools: dict[str, Any], _adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "mmpla_distances"})
    await tools["create_sketch"]({"plane": "Front"})
    a = await _circle(tools, 10, 10, 2)
    b = await _circle(tools, 30, 12, 2)
    axis = await _centreline(tools, 0, -5, 0, 40)
    await _relate(tools, report, "2.fix", axis, None, "fix")
    line = _id(
        await tools["add_line"]({"x1": 0, "y1": -10, "x2": 40, "y2": -10}), "line"
    )
    centres = await _dimension(tools, report, "2a.centres", a, b, "linear", 25)
    report.check(
        "2a.measured", "AddDimension2 centre to centre", 25.0, _measured(centres)
    )
    to_line = await _dimension(tools, report, "2b.to_line", a, axis, "linear", 12)
    report.check("2b.measured", "a centre to a line", 12.0, _measured(to_line))
    vertical = await _dimension(
        tools, report, "2c.vertical", f"{a}.center", "Origin", "vertical", 7
    )
    report.check("2c.measured", "AddVerticalDimension2", 7.0, _measured(vertical))
    run = await _dimension(tools, report, "2d.run", line, None, "horizontal", 40)
    report.check("2d.measured", "a line's run", 40.0, _measured(run))
    exited = await tools["exit_sketch"]()
    name = (exited.get("sketch") or {}).get("name") or "Sketch1"
    summary = report.keep("2e.summary", await _summary(tools, name))
    (ax, ay), _r = _centre(summary, 0)
    (bx, by), _r = _centre(summary, 1)
    report.check(
        "2e.a", "A placed by the dimensions", [12.0, 7.0], [round(ax, 4), round(ay, 4)]
    )
    report.check("2e.ab", "|AB| as dimensioned", 25.0, math.dist((ax, ay), (bx, by)))
    opened = report.keep("2f.edit", await tools["edit_sketch"]({"name": name}))
    ids = [s.get("id") for s in (opened.get("sketch") or {}).get("segments") or []]
    report.check(
        "2f.ids", "edit_sketch names circles Circle_", True, ids[:1] == ["Circle_1"]
    )
    driven = await _dimension(
        tools, report, "2f.driven", "Circle_1.center", "Origin", "horizontal", 20
    )
    report.check(
        "2f.driven",
        "a dimension that didn't take is an error",
        True,
        driven["status"] == "error"
        and (
            "not 20 mm" in str(driven.get("message"))
            or driven.get("error_type") == "solidworks_dialog"
        ),
    )
    await tools["exit_sketch"]()


async def summary(tools: dict[str, Any], _adapter: Any, report: Report) -> None:
    await tools["create_part"]({"name": "mmpla_summary"})
    await tools["create_sketch"]({"plane": "Front"})
    first = None
    for row in range(5):
        for column in range(6):
            made = await _circle(tools, 5 * column, 5 * row, 1)
            first = first or made
    await _relate(tools, report, "3.fix", first, None, "fix")
    exited = await tools["exit_sketch"]()
    name = (exited.get("sketch") or {}).get("name") or "Sketch1"
    started = time.perf_counter()
    full = report.keep("3a.full", await tools["get_sketch"]({"name": name}))
    full_s = time.perf_counter() - started
    segments = (full.get("sketch") or {}).get("segments") or []
    report.check(
        "3a.circles",
        "IsCircle and GetRadius in the full read",
        (30, True),
        (
            len(segments),
            all(
                s.get("type") == "circle"
                and abs((s.get("radius_mm") or 0) - 1.0) <= TOLERANCE
                and "start_mm" not in s
                for s in segments
            ),
        ),
    )
    started = time.perf_counter()
    brief = report.keep("3b.summary", await _summary(tools, name))
    summary_s = time.perf_counter() - started
    report.keep("3b.seconds", {"full": full_s, "summary": summary_s})
    report.check(
        "3b.counts", "counted by type", {"circle": 30}, brief.get("segment_counts")
    )
    report.check(
        "3b.circles",
        "circles as centre and radius",
        30,
        len(brief.get("circles") or []),
    )
    fixes = [
        r
        for r in brief.get("relations") or []
        if r.get("type") == "fix"
        and any(e.get("segment") == 0 for e in r.get("entities") or [])
    ]
    report.check("3b.fix", "a relation names its segment by GetID", 1, len(fixes))
    report.check(
        "3b.state",
        "GetConstrainedStatus closed",
        "under_defined",
        brief.get("definition_state"),
    )
    report.check("3b.faster", "the summary is the fast path", True, summary_s < full_s)
    report.check(
        "3c.state",
        "check_sketch_fully_defined by name",
        "under_defined",
        (await _state(tools, name))[0],
    )


STEPS = [("points", points), ("distances", distances), ("summary", summary)]


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
