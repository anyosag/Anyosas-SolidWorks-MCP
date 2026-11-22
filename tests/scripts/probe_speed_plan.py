"""Probe the speed plan's changes against real SolidWorks (integration branch 28f6ff4).

The speed changes (``server_time_s`` and the per-call ``Timing`` log line,
``add_sketch_geometry``, sketch runs batched by ``execute_workflow``, the
``export_image`` picture, the faster ``measure_clearance``, ``delete_feature``
and ``select_edges``, the eight-argument STL ``SaveAs2``, and the replies that
carry the next read) were written and unit-tested without SolidWorks. This
probe runs each one on scratch documents and records what it measured, with
the times, so a run's report settles them. The two ``experiment_`` checks
only measure, for server changes not made yet.

Checks, in order (``--only`` takes these names, comma-separated):

``timing``
    Sketch, rectangle, extrude and fillet through the tools: each reply's
    ``server_time_s`` beside ``execution_time``, the ``Timing`` line the log
    gets for each call (``tool_boundary._finish``), and what each phase took
    in all (pace, pre_checks, closure, post_checks, other).
``sketch_geometry``
    40 circles in one ``add_sketch_geometry`` against 40 ``add_circle`` calls
    in a second sketch on the same plane: ids, the circles ``get_sketch``
    reads back, COM operations and times; a cut through the 40 (its volume);
    one call with every row kind and ``construction``; a rectangle on a plane
    15 mm off Front.
``workflow_runs``
    ``execute_workflow``: a sketch, three circles (one construction), two
    construction lines, ``add_sketch_dimension`` on
    ``$steps.c1.circle.id.center``, exit, extrude. The five entity steps must
    be one ``add_sketch_entities`` operation; ids against ``get_sketch``, the
    volume. Then a run whose third step SolidWorks refuses (a zero-length
    line; an oversized fillet if SolidWorks draws that line) with
    ``rollback_on_error``: ``failed_step``, and the model as it was.
``summary_reuse``
    Only when a change that reuses geometry summaries between steps is on the
    branch (found in the source, or by counting reads on the mock adapter);
    skipped, touching nothing, otherwise. A sketch-cut-sketch-cut workflow:
    ``measured_change`` against the same steps as separate calls, and the
    summary reads each took.
``export_image``
    ``export_image`` over FastMCP's in-memory client, as
    ``tests/solidworks_mcp/tools/test_export_image_inline.py`` calls it: the
    picture as image content (PNG and JPG), the file, its size against the
    inline cap.
``clearance``
    An assembly of two 40 x 40 x 5 mm plates 2.0 mm apart, and a 9.6 mm pin
    inside a 10 mm hole (0.2 mm radial play): ``measure_clearance`` by default
    and with ``faces: true``: ``clearance_mm``, ``method`` and the times.
``delete``
    A sketch with a boss and a fillet on it: deleting the sketch takes both;
    ``removed_features`` against the tree listed before and after; no
    ``list_features`` inside the delete. Then a cut with ``delete_absorbed``.
``edge_scan``
    A 200 x 100 x 4 mm plate with 40 square and 100 round holes (692 edges):
    ``select_edges`` with no source is refused as ``edge_scan_too_large`` in
    under a second, and so is a loose ``inside_box``; ``scan_whole_part:
    true`` reads them all. ``SOLIDWORKS_MCP_EDGE_SCAN_LIMIT`` is unset for it.
``stl``
    ``export_stl`` of a part and of an assembly of two copies of it: no "Type
    mismatch" and no ``SaveAs3`` fallback in the log, ``SaveAs2`` watched call
    by call; the part's triangles against a ``SaveAs3`` export of it; what
    ``GetExportFileData(2)`` returns.
``next_reads``
    ``open_documents`` in the replies of ``create_part``, ``save_as``,
    ``save_body_as_part``, ``close_model``, ``open_model`` and
    ``activate_document`` against ``list_open_documents``; ``open_model``'s
    ``summary`` against ``list_features`` and ``list_bodies``, and the time it
    adds; ``expect``
    passing and failing (``expectation_failed``, the feature kept) on an
    extrusion and on a loft; ``add_mate`` with ``check_interference``.
``experiment_edge_reads``
    Measurement only. On the 692-edge plate, 200 edges described by
    ``face_selection.describe_edge`` against ``IEdge.GetCurve`` with
    ``GetCurveParams2``: times, the end points compared, and what
    ``GetCurveParams2`` returns under late binding.
``experiment_speed_switches``
    Measurement only. On copies of a part with 60 features: 20 planes and 10
    cuts as the tree is, with ``FeatureManager.EnableFeatureTree`` off and
    with ``EnableFeatureTreeWindow`` off, and whether the FeatureManager tree
    is current afterwards; 200 edge selections with
    ``IModelView.EnableGraphicsUpdate`` on and off; 40 circles with
    ``SketchManager.DisplayWhenAdded`` on and off. Every switch is read first
    and put back in a ``finally``.

Safety. It attaches to the SolidWorks that is running and never starts one
(with none running it stops, as the live suite does), waits until a freshly
started SolidWorks is ready (``tests/live/readiness.py``, as
``conftest._warm_up``), and connects a ``PyWin32Adapter`` as the other probes
do. Connecting turns off SolidWorks' dialog and dimension-input toggles, as
the server does, and leaves them so. Every document it makes is a new part
or assembly, saved at once under the run folder as ``SpeedProbe_<name>``; it
opens no other file. After each check, and at the end whatever happened, it
closes the documents that check opened (new since the check began, and saved
under the run folder or never saved) and leaves every other document alone,
so don't make documents by hand while it runs. Raw COM work runs inside
``_handle_com_operation``, so the settle gap between operations stays
(pitfall #26); nothing calls ``CommandInProgress``. The SolidWorks files are
deleted at the end unless ``--keep-scratch``; the report, the log, the STL
and picture files stay.

Output, under ``<out>/<YYYYmmdd-HHMMSS>/``: ``report.json`` (rewritten after
every check) and ``server.log``, every line the tools logged, in the server's
format. The MCP server writes the same lines to its ``config.log_file``
(``~/.solidworks_mcp/logs/server.log`` unless ``SOLIDWORKS_MCP_LOG_FILE`` or
``SOLIDWORKS_MCP_DATA_DIR`` says otherwise); the probe runs the tools in its
own process, so they come here. A table of the checks is printed at the end.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_speed_plan.py
    .venv\\Scripts\\python.exe tests/scripts/probe_speed_plan.py --only timing,stl
    .venv\\Scripts\\python.exe tests/scripts/probe_speed_plan.py --dry-run

``--dry-run`` never touches SolidWorks: it builds the plan, imports
everything, checks the tools and adapter members the checks use, and runs
every check against ``MockSolidWorksAdapter`` with the raw COM parts skipped
(the mock's numbers are made up, so only an ``error`` there means something).
A full live run takes about 15 to 25 minutes; ``--pacing live`` uses the live
suite's slower settle gaps instead of the server's.

The machinery is shared: a sibling probe (``probe_aggrewell_fixes.py``) loads
this file and runs its own checks through :func:`main` with a
:class:`ProbeKind`, which names its checks, its scratch prefix, its log tag and
its output folder. :data:`SPEED` is this probe's.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import importlib
import importlib.util
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.tools import ToolResult

from solidworks_mcp.adapters import pywin32_adapter, sw_type_info
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import edge_index
from solidworks_mcp.adapters.solidworks import io as sw_io
from solidworks_mcp.adapters.solidworks.com_utils import as_list, selection_count
from solidworks_mcp.adapters.solidworks.face_selection import describe_edge
from solidworks_mcp.tools import export as export_tools
from solidworks_mcp.tools.analysis import register_analysis_tools
from solidworks_mcp.tools.automation import register_automation_tools
from solidworks_mcp.tools.bodies import register_bodies_tools
from solidworks_mcp.tools.export import register_export_tools
from solidworks_mcp.tools.file_management import register_file_management_tools
from solidworks_mcp.tools.modeling import register_modeling_tools
from solidworks_mcp.tools.sketching import register_sketching_tools
from solidworks_mcp.tools.tool_boundary import takes_input
from solidworks_mcp.utils import stl_mesh

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "tests" / ".generated" / "speed_probe"
#: Every scratch file the probe writes starts with this.
PREFIX = "SpeedProbe_"
#: What the probe's lines start with (``[speed-probe]``), and its FastMCP's name.
TAG = "speed-probe"
#: The SolidWorks files deleted at the end (unless ``--keep-scratch``).
SOLIDWORKS_SUFFIXES = (".sldprt", ".sldasm")

#: The live suite's pacing (``tests/live/conftest.py``), for ``--pacing live``.
LIVE_PACING = {
    "SOLIDWORKS_MCP_COM_SETTLE_SECONDS": "1",
    "SOLIDWORKS_MCP_DOC_SETTLE_SECONDS": "2.5",
    "SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS": "2",
    "SOLIDWORKS_MCP_BUSY_WAIT_SECONDS": "5",
}
#: The settings a report records (and the probe's own: ``SOLIDWORKS_MCP_TIMING``).
RECORDED_SETTINGS = (
    *LIVE_PACING,
    "SOLIDWORKS_MCP_SETTLE_MODE",
    "SOLIDWORKS_MCP_TIMING",
    "SOLIDWORKS_MCP_EDGE_SCAN_LIMIT",
    "SOLIDWORKS_MCP_KEEP_MINIMIZED",
    "SOLIDWORKS_MCP_HEADLESS",
)
#: ``tests/live/conftest.py``'s upper bound on the warm-up wait, seconds.
DEFAULT_WARMUP_SECONDS = 480
_TRUTHY = {"1", "true", "yes", "on"}

# ---- geometry the checks build (mm) ------------------------------------------

#: The 40 circles of ``sketch_geometry``: an 8 x 5 grid, r 2, inside a
#: 90 x 50 x 5 block.
GRID_CIRCLES = [[10.0 + 10 * i, 5.0 + 10 * j, 2.0] for j in range(5) for i in range(8)]
#: The 692-edge plate: 200 x 100 x 4 with 40 square holes (12 edges each)
#: and 100 round ones (2 each), plus the plate's own 12.
EDGE_PLATE = (0.0, 0.0, 200.0, 100.0, 4.0)
EDGE_SQUARES = [
    [5.0 + 10 * i, 5.0 + 10 * j, 9.0 + 10 * i, 9.0 + 10 * j]
    for j in range(5)
    for i in range(8)
]
EDGE_HOLES = [[105.0 + 9 * i, 6.0 + 9.5 * j, 1.5] for j in range(10) for i in range(10)]
EDGE_COUNT = 12 + 12 * len(EDGE_SQUARES) + 2 * len(EDGE_HOLES)
#: The fillet ``timing`` and ``delete`` put on a 40 x 20 x 10 block from
#: Front: its vertical edge at x 40, y 20 (a coordinate hint in metres).
BLOCK_EDGE = "0.04,0.02,0.005"
FILLET_REMOVES = (4 - math.pi) * 10.0  # (r^2 - pi r^2 / 4) x length, r 2
#: A square frustum's volume: 20 x 20 to 10 x 10 over 20 mm.
FRUSTUM = 20.0 / 3 * (400 + 100 + math.sqrt(400 * 100))


class Skip(Exception):
    """The check doesn't apply here; it did nothing."""


class CheckFailed(Exception):
    """A step the check depends on failed; the check stops there."""


# ---- results ----------------------------------------------------------------


@dataclass
class Outcome:
    """What one check expected and got, what it measured and how long it took."""

    name: str
    quiet: bool = False
    status: str = "pass"
    expectations: list[dict[str, Any]] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0
    closed: list[str] = field(default_factory=list)
    tag: str = TAG

    def expect(
        self, what: str, ok: bool | None, got: Any = None, want: Any = None
    ) -> bool:
        """Record one expectation; ``ok`` None means it couldn't be judged."""
        entry = {"what": what, "ok": ok, "got": got}
        if want is not None:
            entry["want"] = want
        self.expectations.append(entry)
        if not self.quiet:
            mark = {True: "ok ", False: "BAD", None: " ? "}[ok]
            wanted = f" (want {want!r})" if want is not None else ""
            print(f"[{self.tag}]   {mark} {what}: {_short(got)}{wanted}", flush=True)
        return bool(ok)

    def measure(self, key: str, value: Any) -> Any:
        self.measurements[key] = value
        return value

    def time(self, key: str, seconds: Any) -> Any:
        self.timings[key] = round(seconds, 4) if isinstance(seconds, float) else seconds
        return seconds

    def note(self, text: str) -> None:
        self.notes.append(text)
        if not self.quiet:
            print(f"[{self.tag}]   note: {text}", flush=True)

    def settle(self) -> None:
        """The status from what happened: error, skip, fail or pass."""
        if self.error is not None:
            self.status = "error"
        elif self.status != "skip" and any(e["ok"] is False for e in self.expectations):
            self.status = "fail"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "expectations": self.expectations,
            "measurements": self.measurements,
            "timings": self.timings,
            "notes": self.notes,
            "error": self.error,
            "duration_s": round(self.duration_s, 2),
            "documents_closed": self.closed,
        }


def _short(value: Any, limit: int = 160) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _close(a: Any, b: Any, tolerance: float) -> bool:
    return _number(a) and _number(b) and abs(float(a) - float(b)) <= tolerance


def _relative(a: Any, b: Any, share: float) -> bool:
    return _number(a) and _number(b) and abs(a - b) <= share * max(abs(b), 1e-9)


def _operations(reply: dict[str, Any] | None) -> dict[str, int]:
    """The COM operations a reply's ``timing`` counts (``SOLIDWORKS_MCP_TIMING=1``)."""
    rows = ((reply or {}).get("timing") or {}).get("operations") or {}
    return {
        str(name): int(row.get("count", 0))
        for name, row in rows.items()
        if isinstance(row, dict)
    }


# ---- the log ----------------------------------------------------------------

#: One ``CallTiming.summary`` line: ``Timing export_stl: 2.913 s; pace 0.750,
#: pre_checks 0.004, closure 0.812, post_checks 0.010, other 1.337 s; 4 COM
#: operations: export_file x1 0.940 s, ...``.
TIMING_LINE = re.compile(
    r"^Timing (?P<tool>\S+?)(?: \(in (?P<parent>\S+)\))?: (?P<server>\d+\.\d+) s; "
    r"pace (?P<pace>\d+\.\d+), pre_checks (?P<pre_checks>\d+\.\d+), "
    r"closure (?P<closure>\d+\.\d+), post_checks (?P<post_checks>\d+\.\d+), "
    r"other (?P<other>\d+\.\d+) s; (?P<count>\d+) COM operations"
    r"(?:: (?P<operations>.*))?$"
)
TIMING_OPERATION = re.compile(r"(?P<name>\S+) x(?P<count>\d+) (?P<seconds>\d+\.\d+) s")
PHASES = ("pace", "pre_checks", "closure", "post_checks", "other")


def parse_timing(message: str) -> dict[str, Any] | None:
    """A ``Timing`` log line as numbers, or None for any other line."""
    match = TIMING_LINE.match(message)
    if match is None:
        return None
    operations = {
        m["name"]: {"count": int(m["count"]), "seconds": float(m["seconds"])}
        for m in TIMING_OPERATION.finditer(match["operations"] or "")
    }
    return {
        "tool": match["tool"],
        "parent": match["parent"],
        "server_s": float(match["server"]),
        **{phase: float(match[phase]) for phase in PHASES},
        "com_operations": int(match["count"]),
        "operations": operations,
    }


def server_log_path() -> Path:
    """Where the MCP server writes these lines (``config.py``'s ``log_file``)."""
    explicit = os.environ.get("SOLIDWORKS_MCP_LOG_FILE", "").strip()
    if explicit:
        return Path(explicit)
    data = os.environ.get("SOLIDWORKS_MCP_DATA_DIR", "").strip()
    base = Path(data) if data else Path.home() / ".solidworks_mcp"
    return base / "logs" / "server.log"


class LogCapture:
    """The tools' log lines, kept in memory and written to ``server.log``.

    The format is ``utils/logging.py``'s file format, with milliseconds. The
    default stderr handler goes; warnings still reach the console.
    """

    FORMAT = (
        "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
        "{name}:{function}:{line} | {message}"
    )

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []
        self.path: Path | None = None
        self._ids: list[int] = []

    def start(self, path: Path) -> None:
        from loguru import logger

        self.path = path
        logger.remove()
        self._ids = [
            logger.add(self._keep, level="DEBUG", format="{message}"),
            logger.add(path, level="DEBUG", format=self.FORMAT, encoding="utf-8"),
            logger.add(
                sys.stderr, level="WARNING", format="[server] {level}: {message}"
            ),
        ]

    def stop(self) -> None:
        from loguru import logger

        for sink in self._ids:
            try:
                logger.remove(sink)
            except ValueError:
                pass
        self._ids = []

    def _keep(self, message: Any) -> None:
        record = message.record
        self.lines.append(
            {"level": record["level"].name, "message": str(record["message"])}
        )

    def mark(self) -> int:
        return len(self.lines)

    def since(self, mark: int) -> list[str]:
        return [line["message"] for line in self.lines[mark:]]

    def timing(self, mark: int, nested: bool = False) -> list[dict[str, Any]]:
        """The ``Timing`` lines logged since ``mark``; nested ones only if asked."""
        parsed = [parse_timing(message) for message in self.since(mark)]
        return [p for p in parsed if p is not None and (nested or p["parent"] is None)]

    def timing_in_file(self) -> list[dict[str, Any]]:
        """The top-level ``Timing`` lines ``server.log`` holds, read back from disk."""
        if self.path is None or not self.path.exists():
            return []
        found = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            parts = line.split(" | ", 3)
            parsed = parse_timing(parts[-1]) if len(parts) == 4 else None
            if parsed is not None and parsed["parent"] is None:
                found.append(parsed)
        return found


# ---- SolidWorks: attach, never launch (as tests/live/conftest.py) -----------


def solidworks_pid() -> int | None:
    """PID of the running ``SLDWORKS.exe``; no COM call, never launches it."""
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq SLDWORKS.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    for row in csv.reader(out.splitlines()):
        if row and row[0].lower() == "sldworks.exe":
            return int(row[1])
    return None


def solidworks_age_seconds(pid: int) -> float | None:
    """Seconds since process ``pid`` started (``GetProcessTimes``)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, ignored = wintypes.FILETIME(), wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(ignored),
            ctypes.byref(ignored),
            ctypes.byref(ignored),
        ):
            return None
        now = wintypes.FILETIME()
        kernel32.GetSystemTimeAsFileTime(ctypes.byref(now))

        def ticks(ft: Any) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        return (ticks(now) - ticks(created)) / 10_000_000
    finally:
        kernel32.CloseHandle(handle)


def load_readiness() -> Any:
    """``tests/live/readiness.py``, loaded from its file (no sys.path change).

    The module goes into ``sys.modules`` before it runs: its dataclasses look
    their module up there, and the first live run died without it
    (``'NoneType' object has no attribute '__dict__'``).
    """
    name = "speed_probe_readiness"
    path = REPO / "tests" / "live" / "readiness.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Can't load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def com_answers() -> bool:
    """One cheap COM read on the running SolidWorks (``GetActiveObject`` only)."""
    if solidworks_pid() is None:
        return False
    try:
        import pythoncom
        import win32com.client
        from win32com.client import dynamic

        pythoncom.CoInitialize()
        app = dynamic.Dispatch(win32com.client.GetActiveObject("SldWorks.Application"))
        revision = app.RevisionNumber
        return bool(revision() if callable(revision) else revision)
    except Exception:  # noqa: BLE001 - not answering yet, whatever it raised
        return False


def _env_seconds(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def warm_up(pid: int, tag: str = TAG) -> dict[str, Any]:
    """Wait for a freshly started SolidWorks, as ``conftest._warm_up`` does."""
    readiness = load_readiness()
    warmup = _env_seconds("SW_TEST_WARMUP_SECONDS", DEFAULT_WARMUP_SECONDS)
    age = solidworks_age_seconds(pid) if warmup > 0 else None
    waited = readiness.warm_up(
        age,
        warmup,
        lambda: readiness.SolidWorksReadiness(pid, com_answers),
        fixed=os.getenv("SW_TEST_WARMUP_FIXED", "").strip().lower() in _TRUTHY,
        settle=_env_seconds(
            "SW_TEST_WARMUP_SETTLE_SECONDS", readiness.DEFAULT_SETTLE_SECONDS
        ),
        log=lambda line: print(line.replace("[live]", f"[{tag}]"), flush=True),
    )
    return {
        "age_s": None if age is None else round(age),
        "waited_s": None if waited is None else round(waited.waited, 1),
        "ready": None if waited is None else waited.ready,
        "reason": None if waited is None else waited.reason,
    }


# ---- the session --------------------------------------------------------------


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except (OSError, ValueError):
        return False
    return True


class Probe:
    """The adapter, the tools and the probe's own documents.

    ``live`` False is the dry run on ``MockSolidWorksAdapter``: raw COM work
    is skipped and nothing is closed. ``prefix`` starts every scratch file's
    name and ``tag`` every line printed (:class:`ProbeKind`).
    """

    def __init__(
        self,
        adapter: Any,
        mcp: FastMCP,
        tools: dict[str, Any],
        run_dir: Path,
        log: LogCapture,
        live: bool,
        prefix: str = PREFIX,
        tag: str = TAG,
    ) -> None:
        self.adapter = adapter
        self.mcp = mcp
        self.tools = tools
        self.run_dir = run_dir
        self.log = log
        self.live = live
        self.prefix = prefix
        self.tag = tag
        self.outcome: Outcome | None = None
        self.calls: list[dict[str, Any]] = []
        self.baseline: set[str] = set()
        #: Titles of the documents the probe made (for the report).
        self.created: set[str] = set()
        self.cache: dict[str, Any] = {}
        self.pending: dict[str, tuple[Callable[[Any], Any], Any]] = {}
        self.last_wall = 0.0

    # -- tools

    async def call(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        document: str | None = None,
    ) -> dict[str, Any]:
        """Call a tool as a script does (a dict for its input); its reply.

        ``document`` is the tool's own ``document`` argument (the tool
        boundary makes it the active document first).
        """
        fn = self.tools[tool]
        chosen = {"document": document} if document else {}
        started = time.perf_counter()
        if takes_input(fn):
            reply = await fn(dict(args or {}), **chosen)
        else:
            reply = await fn(**chosen)
        self.last_wall = time.perf_counter() - started
        if isinstance(reply, ToolResult):  # content beside the reply
            reply = dict(reply.structured_content or {})
        self.calls.append(
            {
                "check": self.outcome.name if self.outcome else None,
                "tool": tool,
                "status": reply.get("status"),
                "error_type": reply.get("error_type"),
                "server_time_s": reply.get("server_time_s"),
                "execution_time": reply.get("execution_time"),
                "wall_s": round(self.last_wall, 4),
            }
        )
        return reply

    async def ok(
        self,
        tool: str,
        args: dict[str, Any] | None = None,
        document: str | None = None,
    ) -> dict[str, Any]:
        """:meth:`call`, which must succeed."""
        reply = await self.call(tool, args, document)
        if reply.get("status") != "success":
            raise CheckFailed(
                f"{tool} failed ({reply.get('error_type')}): {reply.get('message')}"
            )
        return reply

    # -- raw COM

    def require_live(self, what: str) -> None:
        if not self.live:
            raise Skip(f"{what}: raw COM, live only")

    def raw(self, name: str, fn: Callable[[], Any]) -> Any:
        """Raw COM work as one adapter operation: the settle gap before it, the watchdog."""
        self.require_live(name)
        result = self.adapter._handle_com_operation(f"probe_{name}", fn)
        if not result.is_success:
            raise CheckFailed(f"{name}: {result.error}")
        return result.data

    def switch(
        self,
        label: str,
        read: Callable[[], Any],
        write: Callable[[Any], Any],
        value: Any,
    ) -> Any:
        """Set a SolidWorks switch, keeping what it was to put back; the old value."""
        original = self.raw(f"read_{label}", read)
        self.pending[label] = (write, original)
        self.raw(f"set_{label}", lambda: write(value))
        return original

    def restore(self, label: str) -> dict[str, Any]:
        """Put a switch back; how long that took."""
        write, original = self.pending[label]

        def put_back() -> float:
            started = time.perf_counter()
            write(original)
            return time.perf_counter() - started

        seconds = self.raw(f"restore_{label}", put_back)
        del self.pending[label]
        return {"restored_to": original, "seconds": round(float(seconds), 4)}

    def restore_all(self) -> list[str]:
        """Put back every switch still set; the ones that failed."""
        failed = []
        for label in reversed(list(self.pending)):
            try:
                self.restore(label)
            except Exception as exc:  # noqa: BLE001 - reported, the rest still tried
                failed.append(f"{label}: {exc}")
        return failed

    # -- documents

    def file(self, label: str, suffix: str) -> Path:
        """A path in the run folder that isn't taken yet."""
        path = self.run_dir / f"{self.prefix}{label}{suffix}"
        number = 2
        while path.exists():
            path = self.run_dir / f"{self.prefix}{label}_{number}{suffix}"
            number += 1
        return path

    async def documents(self) -> dict[str, dict[str, Any]]:
        """The open documents by title (``list_open_documents``, read-only)."""
        listed = await self.adapter.list_open_documents()
        if not listed.is_success:
            return {}
        return {
            str(doc.get("title")): doc
            for doc in listed.data or []
            if isinstance(doc, dict) and doc.get("title")
        }

    async def active_title(self) -> str | None:
        docs = await self.documents()
        return next((t for t, d in docs.items() if d.get("is_active")), None)

    async def _own_new_document(self, reply: dict[str, Any], name: Any) -> None:
        """Stop unless the document just made is the active one."""
        if name:
            self.created.add(str(name))
        active = next(
            (
                d.get("title")
                for d in reply.get("open_documents") or []
                if isinstance(d, dict) and d.get("active")
            ),
            None,
        )
        if active is None:
            active = await self.active_title()
        if active is None or active in self.baseline:
            raise CheckFailed(
                f"the new document {name!r} is not the active one ({active!r}); "
                "stopped before changing anything"
            )

    async def _save_new(self, label: str, suffix: str) -> Path:
        path = self.file(label, suffix)
        saved = await self.adapter.save_file(str(path))
        if not saved.is_success:
            raise CheckFailed(f"saving {path.name} failed: {saved.error}")
        title = await self.active_title()
        if title:
            self.created.add(title)
        return path

    async def new_part(self, label: str) -> Path:
        """A new part, saved at once as ``<prefix><label>`` in the run folder."""
        made = await self.ok("create_part", {})
        await self._own_new_document(made, (made.get("part") or {}).get("name"))
        return await self._save_new(label, ".SLDPRT")

    async def new_assembly(self, label: str) -> Path:
        """A new assembly, saved at once as ``<prefix><label>`` in the run folder."""
        made = await self.ok("create_assembly", {})
        await self._own_new_document(made, (made.get("assembly") or {}).get("name"))
        return await self._save_new(label, ".SLDASM")

    async def save_in_place(self) -> None:
        saved = await self.adapter.save_file()
        if not saved.is_success:
            raise CheckFailed(f"saving the active document failed: {saved.error}")

    async def volume(self) -> float | None:
        summary = await self.adapter.get_model_geometry_summary()
        if not summary.is_success:
            return None
        return (summary.data or {}).get("volume_mm3")

    async def feature_rows(self) -> list[dict[str, Any]]:
        listed = await self.adapter.list_features(include_suppressed=True)
        if not listed.is_success:
            raise CheckFailed(f"list_features failed: {listed.error}")
        return [
            row
            for row in listed.data or []
            if isinstance(row, dict) and not row.get("component")
        ]

    async def feature_names(self) -> list[str]:
        return [str(row.get("name")) for row in await self.feature_rows()]

    def _mine(self, doc: dict[str, Any]) -> bool:
        """The probe's own: saved under the run folder, or unsaved with a title it made.

        Never any other unsaved document: another session sharing SolidWorks
        may create one during a check, and closing it would lose its work.
        """
        path = str(doc.get("path") or "")
        if path:
            return _inside(Path(path), self.run_dir)
        return str(doc.get("title")) in self.created

    async def close_new(self, keep: Collection[str]) -> list[str]:
        """Close the probe's documents: not in ``keep``, under the run folder or unsaved.

        Assemblies go first, then what they had loaded; a few passes catch
        the parts an assembly kept loaded without a window.
        """
        if not self.live:
            return []
        closed: list[str] = []
        for _ in range(3):
            docs = await self.documents()
            mine = sorted(
                (
                    title
                    for title, doc in docs.items()
                    if title not in keep
                    and title not in self.baseline
                    and self._mine(doc)
                ),
                key=lambda title: docs[title].get("type") != "Assembly",
            )
            if not mine:
                break
            for title in mine:
                result = self.adapter._handle_com_operation(
                    "close_leftover_documents",
                    lambda t=title: self.adapter.swApp.CloseDoc(t),
                )
                if result.is_success:
                    closed.append(title)
            self.adapter.currentModel = None
        return closed


async def register_tools(
    adapter: Any, name: str = TAG
) -> tuple[FastMCP, dict[str, Any]]:
    """Register the tool groups the checks use on one FastMCP, as the server does."""
    mcp = FastMCP(name)
    for register in (
        register_sketching_tools,
        register_modeling_tools,
        register_file_management_tools,
        register_export_tools,
        register_analysis_tools,
        register_bodies_tools,
        register_automation_tools,
    ):
        await register(mcp, adapter, {})
    return mcp, {tool.name: tool.fn for tool in await mcp.list_tools()}


# ---- builders -----------------------------------------------------------------


async def block(
    p: Probe, x1: float, y1: float, x2: float, y2: float, depth: float
) -> dict[str, Any]:
    """A rectangle on Front extruded ``depth`` mm (+Z); the extrusion's reply."""
    await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await p.ok("exit_sketch")
    return await p.ok("create_extrusion", {"depth": depth})


async def circle_cut(p: Probe, x: float, y: float, radius: float) -> dict[str, Any]:
    """A circle on Front cut through all; the cut's reply, with ``sketch``."""
    sketch = await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_circle", {"center_x": x, "center_y": y, "radius": radius})
    await p.ok("exit_sketch")
    name = sketch["sketch"]["name"]
    reply = await p.ok(
        "create_cut_extrude",
        {"depth": 1.0, "end_condition": "ThroughAll", "sketch_name": name},
    )
    reply["sketch"] = name
    return reply


async def clearance_parts(p: Probe) -> dict[str, Path]:
    """The plate, pin and holed block, built and saved once a run, then closed."""
    parts = p.cache.get("clearance_parts")
    if parts and all(path.exists() for path in parts.values()):
        return parts
    parts = {}
    for label, build in (
        ("plate", lambda: block(p, 0, 0, 40, 40, 5)),
        ("pin", lambda: _cylinder(p, 0, 0, 4.8, 8)),
        ("holed", lambda: _holed_block(p)),
    ):
        parts[label] = await p.new_part(label)
        await build()
        await p.save_in_place()
        await p.ok("close_model", {})
    p.cache["clearance_parts"] = parts
    return parts


async def _cylinder(p: Probe, x: float, y: float, radius: float, depth: float) -> None:
    await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_circle", {"center_x": x, "center_y": y, "radius": radius})
    await p.ok("exit_sketch")
    await p.ok("create_extrusion", {"depth": depth})


async def _holed_block(p: Probe) -> None:
    """30 x 30 x 10 with a 10 mm hole through it at (15, 15)."""
    await block(p, 0, 0, 30, 30, 10)
    await circle_cut(p, 15, 15, 5.0)


async def edges_part(p: Probe) -> int | None:
    """Make the 692-edge plate the active document (built once a run); its edges."""
    path = p.cache.get("edges_part")
    if path is not None and path.exists():
        await p.ok("open_model", {"file_path": str(path)})
    else:
        path = await p.new_part("edges")
        x1, y1, x2, y2, depth = EDGE_PLATE
        await block(p, x1, y1, x2, y2, depth)
        sketch = await p.ok("create_sketch", {"plane": "Front"})
        await p.ok(
            "add_sketch_geometry", {"rectangles": EDGE_SQUARES, "circles": EDGE_HOLES}
        )
        await p.ok("exit_sketch")
        await p.ok(
            "create_cut_extrude",
            {
                "depth": 1.0,
                "end_condition": "ThroughAll",
                "sketch_name": sketch["sketch"]["name"],
            },
        )
        await p.save_in_place()
        p.cache["edges_part"] = path
    if not p.live:
        return None
    return int(p.raw("edge_count", lambda: _edge_count(p.adapter)))


def _bodies(adapter: Any) -> list[Any]:
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetBodies2")
    return as_list(model.GetBodies2(0, False))


def _edge_count(adapter: Any) -> int:
    total = 0
    for body in _bodies(adapter):
        sw_type_info.flag_members(body, "GetEdgeCount")
        total += int(body.GetEdgeCount())
    return total


def _edges(adapter: Any) -> list[Any]:
    found: list[Any] = []
    for body in _bodies(adapter):
        sw_type_info.flag_members(body, "GetEdges")
        found.extend(as_list(body.GetEdges()))
    return found


def _sample(items: list[Any], count: int) -> list[Any]:
    """``count`` items spread over the list (the plate's edges mix lines and circles)."""
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


# ---- reading sketches back --------------------------------------------------------


async def read_sketch(p: Probe, name: str) -> list[dict[str, Any]]:
    reply = await p.ok("get_sketch", {"name": name, "limit": 1000})
    return list((reply.get("sketch") or {}).get("segments") or [])


def _circles(segments: list[dict[str, Any]]) -> list[tuple[float, float, float]]:
    out = []
    for segment in segments:
        center, radius = segment.get("center_mm"), segment.get("radius_mm")
        if segment.get("type") == "circle" and center and _number(radius):
            out.append((float(center[0]), float(center[1]), float(radius)))
    return out


def _same_circles(
    read: list[tuple[float, float, float]], rows: list[list[float]], tol: float = 1e-3
) -> bool:
    if len(read) != len(rows):
        return False
    left = sorted(read)
    right = sorted((float(x), float(y), float(r)) for x, y, r in rows)
    return all(
        all(abs(a - b) <= tol for a, b in zip(one, two, strict=True))
        for one, two in zip(left, right, strict=True)
    )


def _line_ends(segment: dict[str, Any]) -> tuple[tuple[float, float], ...] | None:
    start, end = segment.get("start_mm"), segment.get("end_mm")
    if not start or not end:
        return None
    return (
        (round(float(start[0]), 3), round(float(start[1]), 3)),
        (round(float(end[0]), 3), round(float(end[1]), 3)),
    )


async def run_parts(
    p: Probe, r: Outcome, *parts: Callable[[Probe, Outcome], Awaitable[None]]
) -> None:
    """Run a check's independent parts: one that fails is recorded, the next still runs.

    After a failed part any sketch it left open is closed (``exit_sketch``
    toggles only when SolidWorks has one open).
    """
    for part in parts:
        try:
            await part(p, r)
        except CheckFailed as exc:
            r.expect(f"{part.__name__.strip('_')} ran to its end", False, str(exc))
            try:
                await p.adapter.exit_sketch()
            except Exception as left:  # noqa: BLE001 - noted, the next part still runs
                r.note(f"closing a sketch left open failed: {left}")


# ---- 1. timing ------------------------------------------------------------------


async def check_timing(p: Probe, r: Outcome) -> None:
    r.measure("server_log_path", str(server_log_path()))
    r.measure("probe_log_path", str(p.log.path))
    await p.new_part("timing")
    mark = p.log.mark()
    rows = []
    for tool, args in (
        ("create_sketch", {"plane": "Front"}),
        ("add_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20}),
        ("exit_sketch", None),
        ("create_extrusion", {"depth": 10.0}),
        ("add_fillet", {"radius": 2.0, "edge_names": [BLOCK_EDGE]}),
    ):
        reply = await p.ok(tool, args)
        rows.append(
            {
                "tool": tool,
                "server_time_s": reply.get("server_time_s"),
                "execution_time": reply.get("execution_time"),
                "wall_s": round(p.last_wall, 4),
                "timing": reply.get("timing"),
                "measured_change": reply.get("measured_change"),
            }
        )
    lines = p.log.timing(mark)
    r.measure("calls", rows)
    r.measure("timing_lines", lines)
    r.expect(
        "every reply has server_time_s",
        all(_number(row["server_time_s"]) for row in rows),
        [row["server_time_s"] for row in rows],
    )
    r.expect(
        "server_time_s is at least execution_time",
        all(
            row["server_time_s"] + 1e-3 >= row["execution_time"]
            for row in rows
            if _number(row["server_time_s"]) and _number(row["execution_time"])
        ),
        [(row["tool"], row["server_time_s"], row["execution_time"]) for row in rows],
    )
    r.expect(
        "server_time_s is within the call's wall time",
        all(
            row["server_time_s"] <= row["wall_s"] + 5e-3
            for row in rows
            if _number(row["server_time_s"])
        ),
        [(row["tool"], row["server_time_s"], row["wall_s"]) for row in rows],
    )
    r.expect(
        "one Timing line per call, in order",
        [line["tool"] for line in lines] == [row["tool"] for row in rows],
        [line["tool"] for line in lines],
    )
    paired = list(zip(rows, lines, strict=False))
    r.expect(
        "each Timing line's seconds are the reply's server_time_s",
        bool(paired)
        and all(
            _close(line["server_s"], row["server_time_s"], 2e-3) for row, line in paired
        ),
        [(row["server_time_s"], line["server_s"]) for row, line in paired],
    )
    r.expect(
        "each reply's timing breakdown matches its line (SOLIDWORKS_MCP_TIMING=1)",
        bool(paired)
        and all(
            row["timing"] is not None
            and all(
                _close((row["timing"] or {}).get(f"{phase}_s"), line[phase], 2e-3)
                for phase in PHASES
            )
            for row, line in paired
        ),
        [
            {
                phase: ((row["timing"] or {}).get(f"{phase}_s"), line[phase])
                for phase in PHASES
            }
            for row, line in paired
        ],
    )
    in_file = p.log.timing_in_file()[-len(lines) :] if lines else []
    r.expect(
        "server.log on disk holds the same Timing lines",
        [(x["tool"], x["server_s"]) for x in in_file]
        == [(x["tool"], x["server_s"]) for x in lines],
        [(x["tool"], x["server_s"]) for x in in_file],
    )
    totals = {phase: round(sum(line[phase] for line in lines), 4) for phase in PHASES}
    totals["server_s"] = round(sum(line["server_s"] for line in lines), 4)
    totals["com_operations"] = sum(line["com_operations"] for line in lines)
    r.measure("phase_totals", totals)
    for phase, seconds in totals.items():
        r.time(f"total.{phase}", seconds)
    removed = -((rows[-1]["measured_change"] or {}).get("volume_delta_mm3") or 0.0)
    r.expect(
        "the fillet removed (4 - pi) x 10 mm3",
        _relative(removed, FILLET_REMOVES, 0.03),
        round(removed, 4),
        round(FILLET_REMOVES, 4),
    )


# ---- 2. sketch_geometry ---------------------------------------------------------------


async def check_sketch_geometry(p: Probe, r: Outcome) -> None:
    await p.new_part("sketch_geometry")
    await block(p, 0, 0, 90, 50, 5)
    await run_parts(
        p, r, _batch_against_singles, _mixed_construction, _offset_plane_rectangle
    )


async def _batch_against_singles(p: Probe, r: Outcome) -> None:
    """40 circles in one call against 40 calls, both read back; a cut through them."""
    first = await p.ok("create_sketch", {"plane": "Front"})
    batch = await p.ok("add_sketch_geometry", {"circles": GRID_CIRCLES})
    batch_wall = p.last_wall
    await p.ok("exit_sketch")
    second = await p.ok("create_sketch", {"plane": "Front"})
    singles = []
    singles_wall = 0.0
    for x, y, radius in GRID_CIRCLES:
        singles.append(
            await p.ok("add_circle", {"center_x": x, "center_y": y, "radius": radius})
        )
        singles_wall += p.last_wall
    await p.ok("exit_sketch")

    batch_ids = list(batch.get("circles") or [])
    single_ids = [(s.get("circle") or {}).get("id") for s in singles]
    r.expect(
        "add_sketch_geometry gives 40 ids", len(batch_ids) == 40, len(batch_ids), 40
    )
    r.expect(
        "the same ids as 40 add_circle calls in a new sketch",
        batch_ids == single_ids,
        {"batch": batch_ids[:3] + ["..."], "singles": single_ids[:3] + ["..."]},
    )
    batch_ops = _operations(batch)
    r.expect(
        "one add_sketch_entities operation draws the 40",
        batch_ops.get("add_sketch_entities") == 1 and "add_circle" not in batch_ops,
        batch_ops,
    )
    single_server = sum(s.get("server_time_s") or 0.0 for s in singles)
    r.time("batch.server_s", batch.get("server_time_s"))
    r.time("batch.wall_s", batch_wall)
    r.time("singles.server_s", round(single_server, 4))
    r.time("singles.wall_s", singles_wall)
    if _number(batch.get("server_time_s")) and batch["server_time_s"] > 0:
        r.measure("speedup_server", round(single_server / batch["server_time_s"], 2))

    first_name, second_name = first["sketch"]["name"], second["sketch"]["name"]
    batch_read = _circles(await read_sketch(p, first_name))
    single_read = _circles(await read_sketch(p, second_name))
    r.expect(
        "get_sketch reads the batch's circles where they were asked",
        _same_circles(batch_read, GRID_CIRCLES),
        batch_read[:3],
    )
    r.expect(
        "get_sketch reads the single calls' circles there too",
        _same_circles(single_read, GRID_CIRCLES),
        single_read[:3],
    )
    cut = await p.ok(
        "create_cut_extrude",
        {"depth": 1.0, "end_condition": "ThroughAll", "sketch_name": first_name},
    )
    removed = -((cut.get("measured_change") or {}).get("volume_delta_mm3") or 0.0)
    want = 40 * math.pi * 2.0**2 * 5.0
    r.expect(
        "the cut through the batch's sketch removes 40 holes",
        _relative(removed, want, 0.005),
        round(removed, 3),
        round(want, 3),
    )


async def _mixed_construction(p: Probe, r: Outcome) -> None:
    """Every row kind in one call, all construction geometry."""
    mixed_sketch = await p.ok("create_sketch", {"plane": "Front"})
    mixed = await p.ok(
        "add_sketch_geometry",
        {
            "circles": [[20, 70, 3]],
            "lines": [[0, 80, 30, 80]],
            "rectangles": [[40, 65, 60, 75]],
            "centerlines": [[0, 90, 30, 90]],
            "construction": True,
        },
    )
    await p.ok("exit_sketch")
    segments = await read_sketch(p, mixed_sketch["sketch"]["name"])
    kinds = ("circles", "lines", "rectangles", "centerlines")
    r.expect(
        "a mixed call gives one id per row",
        all(len(mixed.get(kind) or []) == 1 for kind in kinds),
        {kind: mixed.get(kind) for kind in kinds},
    )
    r.expect(
        "seven segments: a circle, a line, a rectangle's four lines, a centreline",
        len(segments) == 7,
        Counter(str(s.get("type")) for s in segments),
    )
    r.expect(
        "every one is construction geometry",
        bool(segments) and all(s.get("construction") is True for s in segments),
        [s.get("construction") for s in segments],
    )


async def _offset_plane_rectangle(p: Probe, r: Outcome) -> None:
    """A rectangle row on a plane 15 mm off Front, read back corner by corner."""
    plane = await p.ok(
        "create_reference_plane", {"reference": "Front Plane", "offset": 15}
    )
    on_plane = await p.ok("create_sketch", {"plane": plane["plane"]["name"]})
    rectangle = await p.ok("add_sketch_geometry", {"rectangles": [[5, 5, 35, 25]]})
    await p.ok("exit_sketch")
    segments = await read_sketch(p, on_plane["sketch"]["name"])
    corners = {end for s in segments if (ends := _line_ends(s)) for end in ends}
    want_corners = {(5.0, 5.0), (35.0, 5.0), (35.0, 25.0), (5.0, 25.0)}
    r.expect(
        "a rectangle on a plane 15 mm off Front: its corners where asked",
        len(segments) == 4 and corners == want_corners,
        sorted(corners),
        sorted(want_corners),
    )
    r.measure("offset_plane_rectangle", rectangle.get("rectangles"))


# ---- 3. workflow_runs --------------------------------------------------------------

#: Sketch, three circles (c3 construction), two construction lines (an open
#: line would leave the profile open), a dimension on c1's centre, exit,
#: extrude. c1 moves from x 10 to x 12.
RUN_STEPS: list[dict[str, Any]] = [
    {"tool": "create_sketch", "id": "sk", "arguments": {"plane": "Front"}},
    {
        "tool": "add_circle",
        "id": "c1",
        "arguments": {"center_x": 10, "center_y": 0, "radius": 3},
    },
    {
        "tool": "add_circle",
        "id": "c2",
        "arguments": {"center_x": -10, "center_y": 0, "radius": 3},
    },
    {
        "tool": "add_circle",
        "id": "c3",
        "arguments": {"center_x": 0, "center_y": 0, "radius": 15, "construction": True},
    },
    {
        "tool": "add_line",
        "id": "l1",
        "arguments": {"x1": -20, "y1": 10, "x2": 20, "y2": 10, "construction": True},
    },
    {
        "tool": "add_line",
        "id": "l2",
        "arguments": {"x1": -20, "y1": -10, "x2": 20, "y2": -10, "construction": True},
    },
    {
        "tool": "add_sketch_dimension",
        "id": "dim",
        "arguments": {
            "entity1": "$steps.c1.circle.id.center",
            "entity2": "Origin",
            "dimension_type": "horizontal",
            "value": 12,
        },
    },
    {"tool": "exit_sketch", "id": "exit"},
    {"tool": "create_extrusion", "id": "boss", "arguments": {"depth": 5}},
]
#: What get_sketch should read for steps 1-5: type, centre or ends, radius,
#: construction.
RUN_SEGMENTS = [
    ("circle", (12.0, 0.0), 3.0, False),
    ("circle", (-10.0, 0.0), 3.0, False),
    ("circle", (0.0, 0.0), 15.0, True),
    ("line", ((-20.0, 10.0), (20.0, 10.0)), None, True),
    ("line", ((-20.0, -10.0), (20.0, -10.0)), None, True),
]
#: A run whose step 2, a zero-length line, SolidWorks refuses in the middle
#: of a batch of entity steps.
REFUSED_LINE_STEPS: list[dict[str, Any]] = [
    {"tool": "create_sketch", "arguments": {"plane": "Front"}},
    {"tool": "add_circle", "arguments": {"center_x": 30, "center_y": 30, "radius": 2}},
    {"tool": "add_line", "arguments": {"x1": 5, "y1": 5, "x2": 5, "y2": 5}},
    {"tool": "add_circle", "arguments": {"center_x": 40, "center_y": 40, "radius": 2}},
    {"tool": "exit_sketch"},
    {"tool": "create_extrusion", "arguments": {"depth": 3}},
]
#: The same with step 4 a fillet too big for its 4 mm boss, if SolidWorks
#: draws a zero-length line.
REFUSED_FILLET_STEPS: list[dict[str, Any]] = [
    {"tool": "create_sketch", "arguments": {"plane": "Front"}},
    {"tool": "add_circle", "arguments": {"center_x": 30, "center_y": 30, "radius": 2}},
    {"tool": "exit_sketch"},
    {"tool": "create_extrusion", "arguments": {"depth": 3}},
    {
        "tool": "add_fillet",
        "arguments": {"radius": 5, "edge_names": ["0.032,0.03,0.003"]},
    },
]


def _segment_matches(segment: dict[str, Any], want: tuple[Any, ...]) -> bool:
    kind, where, radius, construction = want
    if segment.get("type") != kind or segment.get("construction") is not construction:
        return False
    if kind == "circle":
        center = segment.get("center_mm") or [None, None]
        return (
            _close(center[0], where[0], 1e-3)
            and _close(center[1], where[1], 1e-3)
            and _close(segment.get("radius_mm"), radius, 1e-3)
        )
    ends = _line_ends(segment)
    return ends is not None and set(ends) == set(where)


async def check_workflow_runs(p: Probe, r: Outcome) -> None:
    await p.new_part("workflow_runs")
    await run_parts(p, r, _one_run, _refused_runs)


async def _one_run(p: Probe, r: Outcome) -> None:
    """The nine steps: one batch of entity steps, the point reference, the boss."""
    mark = p.log.mark()
    reply = await p.ok(
        "execute_workflow",
        {"workflow_name": "speed probe run", "steps": RUN_STEPS, "detail": "full"},
    )
    r.time("workflow.server_s", reply.get("server_time_s"))
    r.time("workflow.execution_time", reply.get("execution_time"))
    ops = _operations(reply)
    r.measure("operations", ops)
    singles = {"add_circle", "add_line", "set_sketch_construction"}
    r.expect(
        "the five entity steps are one add_sketch_entities operation",
        ops.get("add_sketch_entities") == 1 and not singles & set(ops),
        ops,
    )
    lines = p.log.timing(mark, nested=True)
    outer = [line for line in lines if line["tool"] == "execute_workflow"]
    nested_tools = [
        line["tool"] for line in lines if line["parent"] == "execute_workflow"
    ]
    r.expect(
        "the log agrees: one add_sketch_entities, no add_circle or add_line call",
        bool(outer)
        and outer[-1]["operations"].get("add_sketch_entities", {}).get("count") == 1
        and not {"add_circle", "add_line"} & set(nested_tools),
        {
            "nested_calls": nested_tools,
            "operations": outer[-1]["operations"] if outer else None,
        },
    )
    entries = list(reply.get("steps") or [])
    r.expect(
        "all nine steps ran",
        reply.get("completed") == len(RUN_STEPS),
        reply.get("completed"),
    )
    ids = []
    for index, key in (
        (1, "circle"),
        (2, "circle"),
        (3, "circle"),
        (4, "line"),
        (5, "line"),
    ):
        result = entries[index].get("result") if index < len(entries) else None
        ids.append(((result or {}).get(key) or {}).get("id"))
    r.expect(
        "one id per entity step, in drawing order",
        ids == ["Circle_1", "Circle_2", "Circle_3", "Line_4", "Line_5"],
        ids,
    )
    dimension = (entries[6].get("result") or {}) if len(entries) > 6 else {}
    r.expect(
        "$steps.c1.circle.id.center reached the dimension as '<id>.center'",
        (dimension.get("dimension") or {}).get("entity1") == f"{ids[0]}.center",
        (dimension.get("dimension") or {}).get("entity1"),
    )
    sketch_name = ((entries[0].get("result") or {}).get("sketch") or {}).get("name")
    segments = await read_sketch(p, str(sketch_name))
    r.measure("segments", segments)
    r.expect(
        "get_sketch reads the five entities as the steps drew them (c1 at x 12)",
        len(segments) == len(RUN_SEGMENTS)
        and all(
            _segment_matches(s, w) for s, w in zip(segments, RUN_SEGMENTS, strict=True)
        ),
        [
            {
                k: s.get(k)
                for k in (
                    "type",
                    "center_mm",
                    "radius_mm",
                    "start_mm",
                    "end_mm",
                    "construction",
                )
            }
            for s in segments
        ],
    )
    boss = (entries[-1].get("result") or {}) if entries else {}
    added = (boss.get("measured_change") or {}).get("volume_delta_mm3")
    want = 2 * math.pi * 3.0**2 * 5.0
    r.expect(
        "the extrusion's volume: two r 3 circles, 5 mm",
        _relative(added, want, 0.01),
        added,
        round(want, 3),
    )


async def _refused_runs(p: Probe, r: Outcome) -> None:
    """A step SolidWorks refuses mid-run, with rollback_on_error: where, and the model."""
    for label, steps, failing in (
        ("zero_length_line", REFUSED_LINE_STEPS, 2),
        ("oversized_fillet", REFUSED_FILLET_STEPS, 4),
    ):
        names, volume = await p.feature_names(), await p.volume()
        refused = await p.call(
            "execute_workflow",
            {
                "workflow_name": f"speed probe {label}",
                "steps": steps,
                "rollback_on_error": True,
            },
        )
        data = refused.get("data") or {}
        after_names, after_volume = await p.feature_names(), await p.volume()
        r.measure(
            f"{label}.reply",
            {k: refused.get(k) for k in ("status", "error_type", "message")}
            | {
                "failed_step": data.get("failed_step"),
                "rolled_back": data.get("rolled_back"),
            },
        )
        if label == "zero_length_line" and refused.get("status") == "success":
            r.note(
                "SolidWorks drew the zero-length line; trying an oversized fillet instead"
            )
            continue
        r.expect(
            f"{label}: the workflow stops with an error",
            refused.get("status") == "error",
            refused.get("status"),
        )
        r.expect(
            f"{label}: failed_step is {failing}",
            data.get("failed_step") == failing,
            data.get("failed_step"),
            failing,
        )
        if label == "zero_length_line":
            r.expect(
                f"{label}: refused inside the batch (one add_sketch_entities, no add_line)",
                _operations(refused).get("add_sketch_entities") == 1
                and "add_line" not in _operations(refused),
                _operations(refused),
            )
        r.expect(
            f"{label}: rolled back",
            (data.get("rolled_back") or {}).get("done") is True,
            data.get("rolled_back"),
        )
        r.expect(
            f"{label}: the tree is as before",
            after_names == names,
            {"before": names[-4:], "after": after_names[-4:]},
        )
        r.expect(
            f"{label}: the volume is as before",
            (after_volume is None and volume is None)
            or _close(after_volume, volume, 1e-3),
            after_volume,
            volume,
        )
        break


# ---- 4. summary_reuse ----------------------------------------------------------------

#: Where a change that reuses geometry summaries between steps would be.
REUSE_MODULES = (
    "solidworks_mcp.tools.modeling",
    "solidworks_mcp.tools.automation",
    "solidworks_mcp.tools.tool_boundary",
    "solidworks_mcp.adapters.pywin32_adapter",
    "solidworks_mcp.adapters.solidworks.io",
)
REUSE_WORDS = r"(?:reus\w*|cach\w*|carr(?:y|ies|ied)\w*|remember\w*)"
REUSE_MARKER = re.compile(
    rf"summar\w*\W+(?:\w+\W+){{0,6}}?{REUSE_WORDS}|{REUSE_WORDS}\W+(?:\w+\W+){{0,6}}?summar",
    re.IGNORECASE,
)
#: Sketch, circle, exit, cut; twice.
SKETCH_CUT_TWICE: list[dict[str, Any]] = [
    step
    for x in (10, 30)
    for step in (
        {"tool": "create_sketch", "arguments": {"plane": "Front"}},
        {
            "tool": "add_circle",
            "arguments": {"center_x": x, "center_y": 10, "radius": 2},
        },
        {"tool": "exit_sketch"},
        {
            "tool": "create_cut_extrude",
            "arguments": {"depth": 1, "end_condition": "ThroughAll"},
        },
    )
]


def reuse_markers() -> list[str]:
    """Lines of the source that speak of reusing a summary."""
    found = []
    for name in REUSE_MODULES:
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        for number, line in enumerate(source.splitlines(), 1):
            if REUSE_MARKER.search(line):
                found.append(f"{name}:{number}: {line.strip()[:120]}")
    return found


@contextmanager
def counted_summaries(adapter: Any) -> Iterator[list[int]]:
    """Count ``get_model_geometry_summary`` calls on the adapter."""
    count = [0]
    original = adapter.get_model_geometry_summary

    async def counting() -> Any:
        count[0] += 1
        return await original()

    adapter.get_model_geometry_summary = counting
    try:
        yield count
    finally:
        del adapter.get_model_geometry_summary


async def _sketch_cut_twice(p: Probe, as_workflow: bool) -> tuple[list[Any], int, int]:
    """The two cuts' measured_change, the summary reads and COM summary operations."""
    with counted_summaries(p.adapter) as reads:
        if as_workflow:
            reply = await p.ok(
                "execute_workflow", {"steps": SKETCH_CUT_TWICE, "detail": "full"}
            )
            changes = [
                (e.get("result") or {}).get("measured_change")
                for e in reply.get("steps") or []
                if e.get("tool") == "create_cut_extrude"
            ]
            operations = _operations(reply).get("get_model_geometry_summary", 0)
        else:
            changes, operations = [], 0
            for step in SKETCH_CUT_TWICE:
                reply = await p.ok(step["tool"], step.get("arguments"))
                operations += _operations(reply).get("get_model_geometry_summary", 0)
                if step["tool"] == "create_cut_extrude":
                    changes.append(reply.get("measured_change"))
    return changes, reads[0], operations


async def detect_summary_reuse() -> dict[str, Any]:
    """Whether a summary-reuse change is on the branch: the source, then the mock.

    On the mock adapter the same sketch-cut-sketch-cut runs as separate calls
    and as one workflow; fewer summary reads in the workflow mean the steps
    share them. No SolidWorks is touched.
    """
    found: dict[str, Any] = {"source_markers": reuse_markers()}
    adapter = MockSolidWorksAdapter(_MOCK_CONFIG)
    await adapter.connect()
    fill_mock_gaps(adapter)
    try:
        mcp, tools = await register_tools(adapter)
        p = Probe(adapter, mcp, tools, Path("."), LogCapture(), live=False)
        reads = {}
        for label, as_workflow in (("separate", False), ("workflow", True)):
            await p.ok("create_part", {})
            await block(p, 0, 0, 40, 20, 10)
            _changes, reads[label], _operations_ = await _sketch_cut_twice(
                p, as_workflow
            )
        found["mock_reads"] = reads
    except Exception as exc:  # noqa: BLE001 - reported; the source still decides
        found["mock_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        await adapter.disconnect()
    reads = found.get("mock_reads") or {}
    fewer = bool(reads) and reads.get("workflow", 0) < reads.get("separate", 0)
    found["present"] = bool(found["source_markers"]) or fewer
    return found


async def check_summary_reuse(p: Probe, r: Outcome) -> None:
    found = p.cache.get("summary_reuse") or await detect_summary_reuse()
    r.measure("detection", found)
    if not found.get("present"):
        raise Skip(
            "no geometry-summary reuse on this branch (no marker in the source, and "
            "the mock reads as many summaries in a workflow as in separate calls)"
        )
    results = {}
    for label, as_workflow in (("separate", False), ("workflow", True)):
        await p.new_part(f"summary_reuse_{label}")
        await block(p, 0, 0, 40, 20, 10)
        changes, reads, operations = await _sketch_cut_twice(p, as_workflow)
        results[label] = {"changes": changes, "reads": reads, "com_reads": operations}
    r.measure("results", results)
    keys = ("volume_before_mm3", "volume_after_mm3", "volume_delta_mm3")
    same = (
        all(
            _close((a or {}).get(k), (b or {}).get(k), 1e-3)
            for a, b in zip(
                results["workflow"]["changes"],
                results["separate"]["changes"],
                strict=False,
            )
            for k in keys
        )
        and len(results["workflow"]["changes"]) == 2
    )
    r.expect(
        "the workflow's measured_change equals the separate calls'",
        same,
        {
            label: [(c or {}).get("volume_delta_mm3") for c in res["changes"]]
            for label, res in results.items()
        },
    )
    r.expect(
        "the workflow reads fewer summaries",
        results["workflow"]["reads"] < results["separate"]["reads"],
        {label: res["reads"] for label, res in results.items()},
    )


# ---- 5. export_image -------------------------------------------------------------------


async def check_export_image(p: Probe, r: Outcome) -> None:
    await p.new_part("export_image")
    await block(p, 0, 0, 40, 20, 10)
    r.measure("inline_cap_bytes", export_tools.INLINE_IMAGE_MAX_BYTES)
    r.measure("inline_cap_pixels", export_tools.INLINE_IMAGE_MAX_PIXELS)
    async with Client(p.mcp) as client:
        for kind, mime in (("png", "image/png"), ("jpg", "image/jpeg")):
            path = p.file(f"image_{kind}", f".{kind}")
            started = time.perf_counter()
            result = await client.call_tool(
                "export_image", {"input_data": {"file_path": str(path)}}
            )
            wall = time.perf_counter() - started
            blocks = [getattr(b, "type", None) for b in result.content]
            images = [b for b in result.content if getattr(b, "type", None) == "image"]
            reply = dict(result.structured_content or {})
            r.expect(
                f"{kind}: the reply succeeded",
                reply.get("status") == "success",
                reply.get("message"),
            )
            r.expect(
                f"{kind}: the picture comes as image content",
                blocks == ["text", "image"],
                blocks,
            )
            r.expect(
                f"{kind}: as {mime}",
                bool(images) and getattr(images[0], "mimeType", None) == mime,
                [getattr(b, "mimeType", None) for b in images],
            )
            r.expect(f"{kind}: the file is written", path.exists(), str(path))
            size = path.stat().st_size if path.exists() else None
            sent = len(base64.b64decode(images[0].data)) if images else None
            r.expect(
                f"{kind}: the picture is the file as written",
                sent == size,
                (sent, size),
            )
            check = reply.get("file_check") or {}
            r.measure(
                kind,
                {
                    "size_bytes": size,
                    "share_of_cap": round(size / export_tools.INLINE_IMAGE_MAX_BYTES, 4)
                    if size
                    else None,
                    "width": check.get("width"),
                    "height": check.get("height"),
                    "warning": reply.get("warning"),
                },
            )
            r.time(f"{kind}.server_s", reply.get("server_time_s"))
            r.time(f"{kind}.wall_s", wall)


# ---- 6. clearance --------------------------------------------------------------------


async def check_clearance(p: Probe, r: Outcome) -> None:
    parts = await clearance_parts(p)
    await p.new_assembly("clearance")
    names = {}
    for key, part, at in (
        ("plate_a", "plate", (0, 0, 0)),
        ("plate_b", "plate", (0, 0, 7)),
        ("holed", "holed", (100, 0, 0)),
        ("pin", "pin", (115, 15, 1)),
    ):
        placed = await p.ok(
            "insert_component",
            {"file_path": str(parts[part]), "x": at[0], "y": at[1], "z": at[2]},
        )
        names[key] = (placed.get("component") or {}).get("component")
    await p.save_in_place()
    r.measure("components", names)
    for label, a, b, want in (
        ("plates", "plate_a", "plate_b", 2.0),
        ("pin_in_hole", "pin", "holed", 0.2),
    ):
        pair = {"component_a": names[a], "component_b": names[b]}
        whole = await p.ok("measure_clearance", pair)
        faces = await p.ok("measure_clearance", {**pair, "faces": True})
        one, two = whole.get("data") or {}, faces.get("data") or {}
        r.expect(
            f"{label}: {want} mm measured whole",
            _close(one.get("clearance_mm"), want, 5e-3),
            one.get("clearance_mm"),
            want,
        )
        r.expect(
            f"{label}: the same with faces: true",
            _close(one.get("clearance_mm"), two.get("clearance_mm"), 1e-3),
            (one.get("clearance_mm"), two.get("clearance_mm")),
        )
        r.expect(
            f"{label}: method components by default, face_pairs with faces",
            one.get("method") == "components" and two.get("method") == "face_pairs",
            (one.get("method"), two.get("method")),
        )
        r.time(f"{label}.whole_s", whole.get("server_time_s"))
        r.time(f"{label}.faces_s", faces.get("server_time_s"))
        r.measure(
            label,
            {
                "whole": {
                    k: one.get(k)
                    for k in (
                        "clearance_mm",
                        "method",
                        "complete",
                        "touching",
                        "pairs_checked",
                    )
                },
                "faces": {
                    k: two.get(k)
                    for k in (
                        "clearance_mm",
                        "method",
                        "complete",
                        "touching",
                        "pairs_checked",
                        "pairs_total",
                        "face_a",
                        "face_b",
                    )
                },
            },
        )


# ---- 7. delete ------------------------------------------------------------------------


async def _delete_and_compare(
    p: Probe, r: Outcome, label: str, args: dict[str, Any], must_go: set[str]
) -> None:
    before = await p.feature_names()
    mark = p.log.mark()
    reply = await p.ok("delete_feature", args)
    after = await p.feature_names()
    data = reply.get("data") or {}
    removed = list(data.get("removed_features") or [])
    listed = [name for name in before if name not in after]
    r.expect(
        f"{label}: removed_features is what the tree lost (listed before and after)",
        sorted(removed) == sorted(listed),
        {"removed_features": removed, "tree": listed},
    )
    r.expect(
        f"{label}: {', '.join(sorted(must_go))} went", must_go <= set(listed), listed
    )
    ops = _operations(reply)
    lines = [line for line in p.log.timing(mark) if line["tool"] == "delete_feature"]
    walked = "list_features" in ops or any(
        "list_features" in line["operations"] for line in lines
    )
    r.expect(f"{label}: no list_features inside the delete", not walked, ops)
    r.time(f"{label}.server_s", reply.get("server_time_s"))
    r.measure(
        label,
        {
            k: data.get(k)
            for k in (
                "features_before",
                "features_after",
                "features_removed",
                "removed_unnamed",
                "absorbed_sketches",
                "absorbed_sketches_left",
            )
        },
    )


async def check_delete(p: Probe, r: Outcome) -> None:
    await p.new_part("delete")
    await run_parts(p, r, _delete_a_sketch, _delete_a_cut_absorbed)


async def _delete_a_sketch(p: Probe, r: Outcome) -> None:
    """A sketch with a boss and a fillet built on it: all three go."""
    sketch = await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    await p.ok("exit_sketch")
    boss = await p.ok("create_extrusion", {"depth": 10.0})
    fillet = await p.ok("add_fillet", {"radius": 2.0, "edge_names": [BLOCK_EDGE]})
    gone = {
        str(sketch["sketch"]["name"]),
        str((boss.get("extrusion") or {}).get("name")),
        str((fillet.get("fillet") or {}).get("name")),
    }
    await _delete_and_compare(p, r, "sketch", {"name": sketch["sketch"]["name"]}, gone)


async def _delete_a_cut_absorbed(p: Probe, r: Outcome) -> None:
    """A cut deleted with delete_absorbed: the cut and its sketch go."""
    await block(p, 50, 0, 90, 20, 10)
    cut = await circle_cut(p, 70, 10, 3)
    name = str((cut.get("cut_extrude") or {}).get("name"))
    await _delete_and_compare(
        p,
        r,
        "cut_absorbed",
        {"name": name, "delete_absorbed": True},
        {name, str(cut["sketch"])},
    )


# ---- 8. edge_scan ---------------------------------------------------------------------------


async def check_edge_scan(p: Probe, r: Outcome) -> None:
    saved = os.environ.pop("SOLIDWORKS_MCP_EDGE_SCAN_LIMIT", None)
    try:
        r.measure("scan_limit", edge_index.scan_limit())
        count = await edges_part(p)
        r.measure("edges", count)
        r.expect(
            f"the plate has more than 600 edges ({EDGE_COUNT} expected)",
            None if count is None else count > 600,
            count,
            EDGE_COUNT,
        )
        refused = await p.call("select_edges", {})
        wall = p.last_wall
        r.expect(
            "no source: refused as edge_scan_too_large",
            refused.get("error_type") == "edge_scan_too_large",
            refused.get("error_type"),
        )
        r.expect("refused in under 1 s", wall < 1.0, round(wall, 3))
        r.time("unscoped_refusal_s", wall)
        r.measure("unscoped_data", refused.get("data"))
        loose = await p.call(
            "select_edges", {"inside_box": [[-1, -1, -1], [201, 101, 5]]}
        )
        r.expect(
            "a loose inside_box: refused too",
            loose.get("error_type") == "edge_scan_too_large"
            and (loose.get("data") or {}).get("inside_box") is True,
            (loose.get("error_type"), (loose.get("data") or {}).get("inside_box")),
        )
        r.time("loose_box_refusal_s", p.last_wall)
        r.measure("loose_box_data", loose.get("data"))
        whole = await p.call("select_edges", {"scan_whole_part": True})
        data = whole.get("data") or {}
        r.expect(
            "scan_whole_part: true reads them",
            whole.get("status") == "success",
            whole.get("message"),
        )
        r.expect(
            "it selects every edge",
            count is None or data.get("selected_edges") == count,
            data.get("selected_edges"),
            count,
        )
        r.time("scan_whole_part_s", p.last_wall)
        if _number(count) and count:
            r.measure("seconds_per_edge", round(p.last_wall / count, 4))
    finally:
        if saved is not None:
            os.environ["SOLIDWORKS_MCP_EDGE_SCAN_LIMIT"] = saved


# ---- 9. stl -------------------------------------------------------------------------------

#: Log lines from the old STL path: SaveAs2's type mismatch and the fallback.
STL_FALLBACK = (
    "Type mismatch",
    "falling back to SaveAs3",
    "Extension.SaveAs2 failed",
    "SaveAs3 also failed",
)


@contextmanager
def watch_stl(adapter: Any) -> Iterator[dict[str, Any]]:
    """Record how the live adapter's STL export went: the export data, SaveAs2, the fallback.

    Wraps ``_prepare_stl_export_data``, ``_save_stl_with_extension`` and
    ``_save_stl_with_fallback`` on the adapter, and counts the null export
    data ``pywin32_adapter._null_callout`` hands ``SaveAs2``; all put back.
    """
    seen: dict[str, Any] = {
        "export_data": [],
        "saveas2": [],
        "fallback": 0,
        "null_export_data": 0,
    }
    if not isinstance(adapter, PyWin32Adapter):
        yield seen
        return
    prepare = adapter._prepare_stl_export_data
    save = adapter._save_stl_with_extension
    fallback = adapter._save_stl_with_fallback
    null = pywin32_adapter._null_callout

    def prepared() -> Any:
        data = prepare()
        seen["export_data"].append(None if data is None else type(data).__name__)
        return data

    def saved(ext: Any, data: Any, path: str) -> bool:
        wrote = save(ext, data, path)
        seen["saveas2"].append({"export_data": data is not None, "wrote": wrote})
        return wrote

    def fell_back(doc: Any, path: str) -> None:
        seen["fallback"] += 1
        fallback(doc, path)

    def counted_null() -> Any:
        seen["null_export_data"] += 1
        return null()

    adapter._prepare_stl_export_data = prepared
    adapter._save_stl_with_extension = saved
    adapter._save_stl_with_fallback = fell_back
    pywin32_adapter._null_callout = counted_null
    try:
        yield seen
    finally:
        for name in (
            "_prepare_stl_export_data",
            "_save_stl_with_extension",
            "_save_stl_with_fallback",
        ):
            adapter.__dict__.pop(name, None)
        pywin32_adapter._null_callout = null


def _saveas3(adapter: Any, path: Path) -> bool:
    """The old export: ``SaveAs3`` with the export options, nothing selected."""
    doc = adapter.swApp.ActiveDoc or adapter.currentModel
    doc.ClearSelection2(True)
    doc.SaveAs3(str(path), 0, pywin32_adapter._EXPORT_OPTIONS)
    return path.exists()


def _export_data(adapter: Any) -> dict[str, Any]:
    """What ``GetExportFileData(2)`` hands back, read and not changed."""
    data = adapter.swApp.GetExportFileData(2)
    found: dict[str, Any] = {"type": None if data is None else type(data).__name__}
    if data is not None:
        found["repr"] = repr(data)[:200]
        for name in ("Merge", "ExportBodiesAs", "ExportAsOneFile"):
            try:
                found[name] = getattr(data, name)
            except Exception as exc:  # noqa: BLE001 - recorded
                found[name] = f"raised {type(exc).__name__}: {exc}"
    return found


async def _export_stl(p: Probe, r: Outcome, label: str, path: Path) -> dict[str, Any]:
    mark = p.log.mark()
    with watch_stl(p.adapter) as seen:
        reply = await p.ok("export_stl", {"file_path": str(path)})
    lines = [m for m in p.log.since(mark) if any(s in m for s in STL_FALLBACK)]
    r.expect(
        f"{label}: no Type mismatch or SaveAs3 fallback in the log", not lines, lines
    )
    if p.live:
        r.expect(
            f"{label}: SaveAs2 wrote the file, no fallback",
            seen["fallback"] == 0
            and bool(seen["saveas2"])
            and seen["saveas2"][-1]["wrote"],
            seen,
        )
    r.measure(f"{label}.export", seen)
    mesh = (reply.get("export") or {}).get("mesh_check") or {}
    r.measure(f"{label}.mesh_check", mesh)
    r.time(f"{label}.server_s", reply.get("server_time_s"))
    return mesh


async def check_stl(p: Probe, r: Outcome) -> None:
    part = await p.new_part("stl")
    await block(p, 0, 0, 40, 20, 10)
    await circle_cut(p, 20, 10, 3)
    await p.save_in_place()
    mesh = await _export_stl(p, r, "part", p.file("stl_part", ".stl"))
    if p.live:
        r.measure(
            "GetExportFileData(2)",
            p.raw("export_data", lambda: _export_data(p.adapter)),
        )
        legacy = p.file("stl_part_saveas3", ".stl")
        p.raw("export_saveas3", lambda: _saveas3(p.adapter, legacy))
        old = stl_mesh.check_stl(legacy, mesh.get("units"))
        r.measure("saveas3.mesh_check", old)
        r.expect(
            "part: the same triangles as a SaveAs3 export",
            _number(mesh.get("triangles"))
            and mesh.get("triangles") == old.get("triangles"),
            (mesh.get("triangles"), old.get("triangles")),
        )
    await p.new_assembly("stl_assembly")
    for x in (0, 60):
        await p.ok("insert_component", {"file_path": str(part), "x": x, "y": 0, "z": 0})
    await p.save_in_place()
    both = await _export_stl(p, r, "assembly", p.file("stl_assembly", ".stl"))
    r.expect(
        "assembly: twice the part's triangles",
        _number(mesh.get("triangles"))
        and _relative(both.get("triangles"), 2 * mesh["triangles"], 0.05),
        (both.get("triangles"), mesh.get("triangles")),
    )


# ---- 10. next_reads -----------------------------------------------------------------------


async def _compare_documents(
    p: Probe, r: Outcome, label: str, reply: dict[str, Any]
) -> None:
    """A reply's ``open_documents`` against ``list_open_documents`` right after it."""
    docs = reply.get("open_documents")
    listed = await p.ok("list_open_documents", {"check_hidden": True})
    every = list(listed.get("documents") or [])
    shown = [d for d in every if d.get("visible") is not False or d.get("is_active")]
    got = sorted(str(d.get("title")) for d in docs or [])
    want = sorted(str(d.get("title")) for d in shown)
    active_got = next((d.get("title") for d in docs or [] if d.get("active")), None)
    active_want = next((d.get("title") for d in every if d.get("is_active")), None)
    modified = {str(d.get("title")): d.get("modified") for d in shown}
    differ = [
        d.get("title")
        for d in docs or []
        if isinstance(d.get("modified"), bool)
        and isinstance(modified.get(str(d.get("title"))), bool)
        and d.get("modified") != modified.get(str(d.get("title")))
    ]
    r.expect(
        f"{label}: the reply has open_documents",
        isinstance(docs, list),
        type(docs).__name__,
    )
    r.expect(
        f"{label}: the documents with a window, as listed",
        got == want,
        {"reply": got, "listed": want},
    )
    r.expect(
        f"{label}: the same active document",
        active_got == active_want,
        (active_got, active_want),
    )
    r.expect(f"{label}: the same modified flags", not differ, differ)
    r.time(f"{label}.server_s", reply.get("server_time_s"))


async def _expect_cases(p: Probe, r: Outcome) -> None:
    """``expect`` passing and failing on an extrusion and on a loft."""
    await p.new_part("expect")
    await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_rectangle", {"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    await p.ok("exit_sketch")
    good = await p.ok(
        "create_extrusion", {"depth": 10.0, "expect": {"volume_mm3": 8000}}
    )
    r.expect(
        "extrusion: a met expect gives verification pass",
        (good.get("verification") or {}).get("verdict") == "pass",
        good.get("verification"),
    )
    await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_rectangle", {"x1": 50, "y1": 0, "x2": 60, "y2": 10})
    await p.ok("exit_sketch")
    bad = await p.call(
        "create_extrusion",
        {"depth": 10.0, "merge_result": False, "expect": {"volume_mm3": 12345}},
    )
    made = ((bad.get("data") or {}).get("extrusion") or {}).get("name")
    r.expect(
        "extrusion: a missed expect is expectation_failed",
        bad.get("error_type") == "expectation_failed",
        bad.get("error_type"),
    )
    r.expect(
        "extrusion: the feature stays in the model",
        bool(made) and made in await p.feature_names(),
        made,
    )

    for label, x, expect in (
        ("loft", -50.0, None),
        ("loft_missed", 80.0, {"solid_body_count": 99}),
    ):
        first = await p.ok("create_sketch", {"plane": "Front"})
        await p.ok("add_rectangle", {"x1": x - 10, "y1": -10, "x2": x + 10, "y2": 10})
        await p.ok("exit_sketch")
        plane = await p.ok(
            "create_reference_plane", {"reference": "Front Plane", "offset": 20}
        )
        second = await p.ok("create_sketch", {"plane": plane["plane"]["name"]})
        await p.ok("add_rectangle", {"x1": x - 5, "y1": -5, "x2": x + 5, "y2": 5})
        await p.ok("exit_sketch")
        if expect is None:
            volume = await p.volume()
            expect = {"volume_mm3": (volume or 0.0) + FRUSTUM}
        profiles = [first["sketch"]["name"], second["sketch"]["name"]]
        reply = await p.call("create_loft", {"profiles": profiles, "expect": expect})
        r.time(f"{label}.server_s", reply.get("server_time_s"))
        if label == "loft":
            r.expect(
                "loft: a met expect gives verification pass",
                reply.get("status") == "success"
                and (reply.get("verification") or {}).get("verdict") == "pass",
                (reply.get("message"), reply.get("verification")),
            )
        else:
            name = ((reply.get("data") or {}).get("loft") or {}).get("name")
            r.expect(
                "loft: a missed expect is expectation_failed",
                reply.get("error_type") == "expectation_failed",
                reply.get("error_type"),
            )
            r.expect(
                "loft: the feature stays in the model",
                bool(name) and name in await p.feature_names(),
                name,
            )


async def check_next_reads(p: Probe, r: Outcome) -> None:
    await run_parts(p, r, _document_replies, _expect_cases, _mate_interference)


async def _document_replies(p: Probe, r: Outcome) -> None:
    """open_documents in six document tools' replies; open_model's summary."""
    made = await p.ok("create_part", {})
    await p._own_new_document(made, (made.get("part") or {}).get("name"))
    await _compare_documents(p, r, "create_part", made)
    await block(p, 0, 0, 20, 20, 10)
    await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_rectangle", {"x1": 30, "y1": 0, "x2": 50, "y2": 20})
    await p.ok("exit_sketch")
    await p.ok("create_extrusion", {"depth": 10.0, "merge_result": False})
    path = p.file("next_reads", ".SLDPRT")
    saved = await p.ok("save_as", {"file_path": str(path)})
    await _compare_documents(p, r, "save_as", saved)
    bodies = await p.ok("list_bodies", {})
    names = [b.get("name") for b in (bodies.get("data") or {}).get("bodies") or []]
    r.measure("bodies", names)
    if not names:
        raise CheckFailed("list_bodies named no body to save as a part")
    split = await p.ok(
        "save_body_as_part",
        {
            "body_name": str(names[0]),
            "file_path": str(p.file("next_reads_body", ".SLDPRT")),
        },
    )
    await _compare_documents(p, r, "save_body_as_part", split)
    closed = await p.ok("close_model", {})
    await _compare_documents(p, r, "close_model", closed)

    opened = await p.ok("open_model", {"file_path": str(path)})
    await _compare_documents(p, r, "open_model", opened)
    summary = opened.get("summary") or {}
    listed = await p.ok(
        "list_features",
        {"include_standard": False, "include_suppressed": True, "limit": 1000},
    )
    rows = list(listed.get("features") or [])
    tree = [str(row.get("name")) for row in rows]
    r.measure("summary", summary)
    r.expect(
        "summary.features is list_features' count",
        summary.get("features") == len(rows),
        (summary.get("features"), len(rows)),
    )
    r.expect(
        "summary.by_type is list_features' types",
        summary.get("by_type")
        == dict(Counter(str(row.get("type") or "Unknown") for row in rows)),
        summary.get("by_type"),
    )
    r.expect(
        "summary.last is list_features' last 5",
        summary.get("last") == tree[-5:],
        (summary.get("last"), tree[-5:]),
    )
    listed_bodies = await p.ok("list_bodies", {})
    body_names = [
        str(b.get("name"))
        for b in (listed_bodies.get("data") or {}).get("bodies") or []
    ]
    r.expect(
        "summary.bodies names list_bodies' bodies",
        (summary.get("bodies") or {}).get("count") == len(body_names)
        and (summary.get("bodies") or {}).get("names") == body_names[:10],
        {"summary": summary.get("bodies"), "list_bodies": body_names},
    )
    r.time("open_model.with_summary_s", opened.get("execution_time"))
    await p.ok("close_model", {})
    bare = await p.adapter.open_model(str(path), list_documents=True)
    if not bare.is_success:
        raise CheckFailed(
            f"opening {path.name} without the summary failed: {bare.error}"
        )
    r.time("open_model.without_summary_s", bare.execution_time)
    if p.live:

        def contents() -> float:
            started = time.perf_counter()
            sw_io.model_contents(p.adapter, p.adapter.currentModel)
            return time.perf_counter() - started

        r.time("model_contents_s", p.raw("model_contents", contents))

    await p.new_part("next_reads_other")
    activated = await p.ok("activate_document", {"title_or_path": str(path)})
    await _compare_documents(p, r, "activate_document", activated)


async def _mate_interference(p: Probe, r: Outcome) -> None:
    """add_mate with check_interference: two plates mated plane to plane overlap."""
    parts = await clearance_parts(p)
    await p.new_assembly("mate")
    placed = []
    for z in (0, 7):
        reply = await p.ok(
            "insert_component",
            {"file_path": str(parts["plate"]), "x": 0, "y": 0, "z": z},
        )
        placed.append((reply.get("component") or {}).get("component"))
    mate = await p.ok(
        "add_mate",
        {
            "component_a": placed[0],
            "component_b": placed[1],
            "mate_type": "coincident",
            "check_interference": True,
        },
    )
    interference = mate.get("interference") or {}
    r.measure("interference", interference)
    r.measure("add_mate.operations", _operations(mate))
    r.expect(
        "add_mate carries check_interference's summary",
        "interference_found" in interference,
        interference,
    )
    r.expect(
        "the plates overlap once their Front planes are mated (40 x 40 x 5 mm)",
        interference.get("interference_found") is True
        and _relative(interference.get("total_volume_mm3"), 8000.0, 0.02),
        (interference.get("interference_found"), interference.get("total_volume_mm3")),
    )
    r.time("add_mate.server_s", mate.get("server_time_s"))


# ---- 11. experiment_edge_reads --------------------------------------------------------------------


def _curve_params_reads(
    adapter: Any, edges: list[Any], full: bool
) -> tuple[list[dict[str, Any]], float]:
    """Each edge through ``GetCurve`` and ``GetCurveParams2``; the reads and seconds.

    ``full`` also reads what ``describe_edge`` gives: the curve's kind and a
    circle's parameters.
    """
    started = time.perf_counter()
    out = []
    for edge in edges:
        entry: dict[str, Any] = {}
        try:
            sw_type_info.flag_members(edge, "GetCurve", "GetCurveParams2")
            if full:
                curve = edge.GetCurve()
                if curve is not None:
                    sw_type_info.flag_members(curve, "IsCircle", "IsLine")
                    entry["circle"] = bool(curve.IsCircle())
                    if entry["circle"]:
                        entry["circle_params"] = list(curve.CircleParams)
                    else:
                        entry["line"] = bool(curve.IsLine())
            params = edge.GetCurveParams2()
            entry["params_type"] = type(params).__name__
            entry["params"] = (
                list(params) if isinstance(params, list | tuple) else repr(params)
            )
        except Exception as exc:  # noqa: BLE001 - one edge's failure is a finding
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out.append(entry)
    return out, time.perf_counter() - started


def _describe_reads(
    adapter: Any, edges: list[Any]
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    out = [describe_edge(adapter, edge) for edge in edges]
    return out, time.perf_counter() - started


def _compare_ends(
    described: list[dict[str, Any]], read: list[dict[str, Any]]
) -> dict[str, Any]:
    """The largest gap between the end points the two reads give, mm."""
    worst, compared, circles = 0.0, 0, 0
    for one, two in zip(described, read, strict=True):
        params = two.get("params")
        if not isinstance(params, list) or len(params) < 6:
            continue
        start = [c * 1000 for c in params[0:3]]
        end = [c * 1000 for c in params[3:6]]
        if one.get("start_mm") and one.get("end_mm"):
            a, b = one["start_mm"], one["end_mm"]
            gap = min(
                max(math.dist(a, start), math.dist(b, end)),
                max(math.dist(a, end), math.dist(b, start)),
            )
            worst, compared = max(worst, gap), compared + 1
        elif one.get("center_mm") and _number(one.get("radius_mm")):
            gap = abs(math.dist(one["center_mm"], start) - one["radius_mm"])
            worst, circles = max(worst, gap), circles + 1
    return {
        "max_gap_mm": round(worst, 6),
        "lines_compared": compared,
        "circles_compared": circles,
    }


async def check_experiment_edge_reads(p: Probe, r: Outcome) -> None:
    p.require_live("IEdge reads")
    count = await edges_part(p)
    r.measure("edges", count)
    edges = _sample(p.raw("list_edges", lambda: _edges(p.adapter)), 200)
    r.measure("sampled", len(edges))
    runs: dict[str, list[float]] = {
        "describe_edge": [],
        "curve_params_full": [],
        "curve_params_ends": [],
    }
    described = read = None
    for _ in range(2):
        described, seconds = p.raw(
            "describe_edges", lambda: _describe_reads(p.adapter, edges)
        )
        runs["describe_edge"].append(seconds)
        read, seconds = p.raw(
            "curve_params", lambda: _curve_params_reads(p.adapter, edges, True)
        )
        runs["curve_params_full"].append(seconds)
        _ends, seconds = p.raw(
            "curve_params_ends", lambda: _curve_params_reads(p.adapter, edges, False)
        )
        runs["curve_params_ends"].append(seconds)
    for name, seconds in runs.items():
        r.time(f"{name}_200_s", min(seconds))
        r.measure(f"{name}_runs_s", [round(s, 4) for s in seconds])
    if min(runs["curve_params_full"]) > 0:
        r.measure(
            "speedup_full",
            round(min(runs["describe_edge"]) / min(runs["curve_params_full"]), 2),
        )
    first = (read or [{}])[0]
    r.measure(
        "GetCurveParams2",
        {"type": first.get("params_type"), "first": first.get("params")},
    )
    arrays = [
        isinstance(e.get("params"), list) and len(e["params"]) >= 8 for e in read or []
    ]
    r.expect(
        "GetCurveParams2 returns the array under late binding",
        bool(arrays) and all(arrays),
        Counter(e.get("params_type") for e in read or []),
    )
    gaps = _compare_ends(described or [], read or [])
    r.measure("end_points", gaps)
    r.expect(
        "its end points agree with describe_edge's", gaps["max_gap_mm"] < 1e-3, gaps
    )
    kinds = Counter(d.get("curve") for d in described or [])
    r.measure("kinds", dict(kinds))


# ---- 12. experiment_speed_switches -----------------------------------------------------------

#: The tree switches compared, by variant: (variant, IFeatureManager property).
TREE_VARIANTS = (
    ("tree_as_is", None),
    ("feature_tree_off", "EnableFeatureTree"),
    ("feature_tree_window_off", "EnableFeatureTreeWindow"),
)
#: ``swFeatMgrPane_e``: the pane the tree is in.
PANE_BOTTOM, PANE_TOP = 1, 0
#: Feature types :func:`_tree_current` looks for in the FeatureManager.
TREE_TYPES = frozenset({"RefPlane", "Cut", "Extrusion", "ICE"})


async def tree_part(p: Probe) -> Path:
    """A part with 60 features (a block, 8 cuts, 42 planes), saved and closed."""
    path = p.cache.get("tree_part")
    if path is not None and path.exists():
        return path
    path = await p.new_part("tree")
    await block(p, 0, 0, 100, 20, 10)
    for i in range(8):
        await circle_cut(p, 6 + 12 * i, 10, 1.5)
    for i in range(42):
        await p.ok(
            "create_reference_plane", {"reference": "Front Plane", "offset": 20 + i}
        )
    await p.save_in_place()
    await p.ok("close_model", {})
    p.cache["tree_part"] = path
    return path


def _tree_current(adapter: Any, wanted: list[str]) -> dict[str, Any]:
    """Whether the FeatureManager tree shows every feature in ``wanted``."""
    model = adapter.currentModel
    manager = sw_type_info.flagged(model.FeatureManager, "IFeatureManager")
    root = manager.GetFeatureTreeRootItem2(
        PANE_BOTTOM
    ) or manager.GetFeatureTreeRootItem2(PANE_TOP)
    if root is None:
        return {"current": None, "reason": "GetFeatureTreeRootItem2 gave nothing"}
    texts = []
    sw_type_info.flag_members(root, "GetFirstChild")
    node = root.GetFirstChild()
    while node is not None and len(texts) < 5000:
        sw_type_info.flag_members(node, "GetNext")
        texts.append(str(node.Text))
        node = node.GetNext()
    shown = set(texts)
    missing = [
        name
        for name in wanted
        if name not in shown and not any(name in t for t in texts)
    ]
    return {
        "current": not missing,
        "tree_items": len(texts),
        "features": len(wanted),
        "missing": missing[:20],
        "last_items": texts[-3:],
    }


async def _tree_state(p: Probe) -> dict[str, Any]:
    """:func:`_tree_current` for the active part; ``current`` None when unreadable."""
    try:
        rows = await p.feature_rows()
        wanted = [str(row.get("name")) for row in rows if row.get("type") in TREE_TYPES]
        return p.raw("tree_current", lambda: _tree_current(p.adapter, wanted))
    except CheckFailed as exc:
        return {"current": None, "reason": str(exc)}


async def _twenty_planes_ten_cuts(p: Probe) -> dict[str, Any]:
    planes, cuts, sequences = [], [], []
    for i in range(20):
        reply = await p.ok(
            "create_reference_plane", {"reference": "Front Plane", "offset": 100 + i}
        )
        planes.append(reply.get("server_time_s") or 0.0)
    for i in range(10):
        started = time.perf_counter()
        cut = await circle_cut(p, 8 + 9 * i, 4, 0.8)
        sequences.append(time.perf_counter() - started)
        cuts.append(cut.get("server_time_s") or 0.0)

    def stats(values: list[float]) -> dict[str, float]:
        ordered = sorted(values)
        return {
            "total_s": round(sum(values), 4),
            "mean_s": round(sum(values) / len(values), 4),
            "median_s": round(ordered[len(ordered) // 2], 4),
        }

    return {
        "planes": stats(planes),
        "cuts": stats(cuts),
        "cut_sequences": stats(sequences),
    }


async def _tree_variants(p: Probe, r: Outcome) -> None:
    tree = await tree_part(p)
    for variant, switch in TREE_VARIANTS:
        copy = p.file(f"tree_{variant}", ".SLDPRT")
        shutil.copyfile(tree, copy)
        await p.ok("open_model", {"file_path": str(copy)})
        manager = p.raw(
            "feature_manager",
            lambda: sw_type_info.flagged(
                p.adapter.currentModel.FeatureManager, "IFeatureManager"
            ),
        )
        row: dict[str, Any] = {"switch": switch}
        if switch is not None:
            row["was"] = p.switch(
                variant,
                lambda s=switch, m=manager: getattr(m, s),
                lambda value, s=switch, m=manager: setattr(m, s, value),
                False,
            )
        try:
            row["times"] = await _twenty_planes_ten_cuts(p)
            if switch is not None:
                row["tree_while_off"] = await _tree_state(p)
        finally:
            if switch is not None:
                row["restore"] = p.restore(variant)
        if switch is not None:
            row["after_restore"] = p.raw(
                f"read_{variant}_again", lambda s=switch, m=manager: getattr(m, s)
            )
            r.expect(
                f"{variant}: {switch} is back as it was",
                row["after_restore"] == row["was"],
                row["after_restore"],
                row["was"],
            )
        state = await _tree_state(p)
        row["tree_after"] = state
        r.expect(
            f"{variant}: the FeatureManager tree is current afterwards",
            state.get("current"),
            state,
        )
        r.measure(variant, row)
        r.time(f"{variant}.planes_total_s", row["times"]["planes"]["total_s"])
        r.time(f"{variant}.cuts_total_s", row["times"]["cuts"]["total_s"])
        await p.ok("close_model", {})


def _select_loop(adapter: Any, edges: list[Any]) -> dict[str, Any]:
    """Select each edge, appending, then clear; the seconds and the count."""
    model = adapter.currentModel
    manager = model.SelectionManager
    sw_type_info.flag_members(manager, "CreateSelectData")
    model.ClearSelection2(True)
    data = manager.CreateSelectData()
    started = time.perf_counter()
    for edge in edges:
        sw_type_info.flag_members(edge, "Select4")
        edge.Select4(True, data)
    seconds = time.perf_counter() - started
    count = selection_count(adapter, model)
    model.ClearSelection2(True)
    return {"seconds": round(seconds, 4), "selected": count}


async def _graphics_variants(p: Probe, r: Outcome) -> None:
    await edges_part(p)
    edges = _sample(p.raw("list_edges", lambda: _edges(p.adapter)), 200)
    view = p.raw("active_view", lambda: p.adapter.currentModel.ActiveView)
    if view is None:
        r.note("no active view: the graphics switch wasn't measured")
        return
    runs: dict[str, list[dict[str, Any]]] = {"graphics_on": [], "graphics_off": []}
    for _ in range(2):
        runs["graphics_on"].append(
            p.raw("select_loop", lambda: _select_loop(p.adapter, edges))
        )
        p.switch(
            "graphics",
            lambda: view.EnableGraphicsUpdate,
            lambda value: setattr(view, "EnableGraphicsUpdate", value),
            False,
        )
        try:
            runs["graphics_off"].append(
                p.raw("select_loop_no_graphics", lambda: _select_loop(p.adapter, edges))
            )
        finally:
            restored = p.restore("graphics")
    r.measure("selection_loops", runs)
    r.measure("graphics_restore", restored)
    r.expect(
        "every loop selects the 200 edges",
        all(run["selected"] == len(edges) for loops in runs.values() for run in loops),
        {k: [run["selected"] for run in v] for k, v in runs.items()},
    )
    back = p.raw("graphics_read", lambda: view.EnableGraphicsUpdate)
    r.expect(
        "EnableGraphicsUpdate is back as it was",
        back == restored["restored_to"],
        back,
        restored["restored_to"],
    )
    for name, loops in runs.items():
        r.time(f"select_200.{name}_s", min(run["seconds"] for run in loops))


async def _display_variants(p: Probe, r: Outcome) -> None:
    await p.new_part("display_when_added")
    items = [
        {"tool": "add_circle", "center_x": x, "center_y": y, "radius": radius}
        for x, y, radius in GRID_CIRCLES
    ]
    runs: dict[str, list[Any]] = {"display_on": [], "display_off": []}
    for display in (True, False, True, False):
        sketch = await p.ok("create_sketch", {"plane": "Front"})
        name = "display_on" if display else "display_off"
        manager = p.adapter.currentSketchManager
        if not display:
            p.switch(
                "display_when_added",
                lambda m=manager: m.DisplayWhenAdded,
                lambda value, m=manager: setattr(m, "DisplayWhenAdded", value),
                False,
            )
        try:
            drawn = await p.adapter.add_sketch_entities(items)
        finally:
            if not display:
                restored = p.restore("display_when_added")
        if not display:
            back = p.raw(
                "read_display_when_added", lambda m=manager: m.DisplayWhenAdded
            )
            r.expect(
                "DisplayWhenAdded is back as it was",
                back == restored["restored_to"],
                back,
                restored["restored_to"],
            )
        await p.ok("exit_sketch")
        if not drawn.is_success:
            raise CheckFailed(f"40 circles ({name}) failed: {drawn.error}")
        circles = _circles(await read_sketch(p, sketch["sketch"]["name"]))
        runs[name].append({"seconds": drawn.execution_time, "circles": len(circles)})
    r.measure("circle_runs", runs)
    r.expect(
        "every sketch holds its 40 circles",
        all(run["circles"] == 40 for loops in runs.values() for run in loops),
        {k: [run["circles"] for run in v] for k, v in runs.items()},
    )
    for name, loops in runs.items():
        r.time(f"circles_40.{name}_s", min(run["seconds"] or 0.0 for run in loops))


async def check_experiment_speed_switches(p: Probe, r: Outcome) -> None:
    p.require_live("speed switches on SolidWorks' own objects")
    for part in (_tree_variants, _graphics_variants, _display_variants):
        try:
            await part(p, r)
        except Exception as exc:  # noqa: BLE001 - one part's failure, the others still run
            r.expect(
                f"{part.__name__.strip('_')} ran", False, f"{type(exc).__name__}: {exc}"
            )
            if not isinstance(exc, CheckFailed):
                r.measure(
                    f"{part.__name__.strip('_')}.traceback", traceback.format_exc()
                )


# ---- the plan ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One check: what it settles, the tools it calls, what it makes, about how long."""

    name: str
    run: Callable[[Probe, Outcome], Awaitable[None]]
    settles: str
    tools: tuple[str, ...]
    documents: tuple[str, ...]
    minutes: float
    raw_com: str = ""


_BUILD = (
    "create_part",
    "create_sketch",
    "add_rectangle",
    "exit_sketch",
    "create_extrusion",
)
_CUT = ("add_circle", "create_cut_extrude")

CHECKS: dict[str, Check] = {
    check.name: check
    for check in (
        Check(
            "timing",
            check_timing,
            "server_time_s beside execution_time; one Timing log line per call; phase totals",
            (*_BUILD, "add_fillet"),
            ("SpeedProbe_timing",),
            0.5,
        ),
        Check(
            "sketch_geometry",
            check_sketch_geometry,
            "add_sketch_geometry: 40 circles in one operation against 40 add_circle calls",
            (
                *_BUILD,
                *_CUT,
                "add_sketch_geometry",
                "get_sketch",
                "create_reference_plane",
            ),
            ("SpeedProbe_sketch_geometry",),
            1.0,
        ),
        Check(
            "workflow_runs",
            check_workflow_runs,
            "execute_workflow draws a run of entity steps in one operation; point references; rollback",
            (
                *_BUILD,
                "execute_workflow",
                "add_circle",
                "add_line",
                "add_sketch_dimension",
                "get_sketch",
                "add_fillet",
            ),
            ("SpeedProbe_workflow_runs",),
            0.8,
        ),
        Check(
            "summary_reuse",
            check_summary_reuse,
            "geometry summaries reused between workflow steps (skipped when not on the branch)",
            (*_BUILD, *_CUT, "execute_workflow"),
            ("SpeedProbe_summary_reuse_separate", "SpeedProbe_summary_reuse_workflow"),
            1.0,
        ),
        Check(
            "export_image",
            check_export_image,
            "export_image sends the picture as image content over MCP",
            (*_BUILD, "export_image"),
            (
                "SpeedProbe_export_image",
                "SpeedProbe_image_png.png",
                "SpeedProbe_image_jpg.jpg",
            ),
            0.5,
        ),
        Check(
            "clearance",
            check_clearance,
            "measure_clearance whole-component first; faces: true gives the same gap",
            (
                *_BUILD,
                *_CUT,
                "close_model",
                "create_assembly",
                "insert_component",
                "measure_clearance",
            ),
            (
                "SpeedProbe_plate",
                "SpeedProbe_pin",
                "SpeedProbe_holed",
                "SpeedProbe_clearance",
            ),
            2.0,
        ),
        Check(
            "delete",
            check_delete,
            "delete_feature names what went without listing the tree twice",
            (*_BUILD, *_CUT, "add_fillet", "delete_feature"),
            ("SpeedProbe_delete",),
            0.8,
        ),
        Check(
            "edge_scan",
            check_edge_scan,
            "select_edges refuses an unscoped scan of 692 edges at once; scan_whole_part reads them",
            (
                *_BUILD,
                "add_sketch_geometry",
                "create_cut_extrude",
                "select_edges",
                "open_model",
            ),
            ("SpeedProbe_edges",),
            2.5,
            raw_com="the edge count (IBody2.GetEdgeCount)",
        ),
        Check(
            "stl",
            check_stl,
            "export_stl through SaveAs2's eight arguments, no SaveAs3 fallback",
            (*_BUILD, *_CUT, "create_assembly", "insert_component", "export_stl"),
            ("SpeedProbe_stl", "SpeedProbe_stl_assembly", "SpeedProbe_stl_*.stl"),
            1.0,
            raw_com="SaveAs3 export, GetExportFileData(2)",
        ),
        Check(
            "next_reads",
            check_next_reads,
            "open_documents, open_model's summary, expect, add_mate's check_interference",
            (
                *_BUILD,
                "save_as",
                "list_bodies",
                "save_body_as_part",
                "close_model",
                "open_model",
                "list_open_documents",
                "list_features",
                "activate_document",
                "create_reference_plane",
                "create_loft",
                "create_assembly",
                "insert_component",
                "add_mate",
            ),
            (
                "SpeedProbe_next_reads",
                "SpeedProbe_next_reads_body",
                "SpeedProbe_next_reads_other",
                "SpeedProbe_expect",
                "SpeedProbe_mate",
            ),
            2.5,
            raw_com="model_contents timing",
        ),
        Check(
            "experiment_edge_reads",
            check_experiment_edge_reads,
            "measurement: describe_edge against IEdge.GetCurve + GetCurveParams2 on 200 edges",
            ("open_model",),
            ("SpeedProbe_edges",),
            1.5,
            raw_com="all of it",
        ),
        Check(
            "experiment_speed_switches",
            check_experiment_speed_switches,
            "measurement: EnableFeatureTree, EnableFeatureTreeWindow, EnableGraphicsUpdate, DisplayWhenAdded",
            (
                *_BUILD,
                *_CUT,
                "create_reference_plane",
                "close_model",
                "open_model",
                "get_sketch",
            ),
            (
                "SpeedProbe_tree",
                "SpeedProbe_tree_<variant>",
                "SpeedProbe_display_when_added",
            ),
            6.0,
            raw_com="all of it",
        ),
    )
}


def build_plan(only: str | None, checks: dict[str, Check] = CHECKS) -> list[Check]:
    if not only:
        return list(checks.values())
    names = [name.strip() for name in only.split(",") if name.strip()]
    unknown = [name for name in names if name not in checks]
    if unknown:
        raise SystemExit(
            f"Unknown check(s): {', '.join(unknown)}. The checks: {', '.join(checks)}"
        )
    return [checks[name] for name in checks if name in names]


#: The adapter and module members the checks reach past the tools.
MEMBERS = (
    (PyWin32Adapter, "_handle_com_operation"),
    (PyWin32Adapter, "_prepare_stl_export_data"),
    (PyWin32Adapter, "_save_stl_with_extension"),
    (PyWin32Adapter, "_save_stl_with_fallback"),
    (PyWin32Adapter, "add_sketch_entities"),
    (PyWin32Adapter, "exit_sketch"),
    (PyWin32Adapter, "get_application_info"),
    (PyWin32Adapter, "get_model_geometry_summary"),
    (PyWin32Adapter, "list_features"),
    (PyWin32Adapter, "list_open_documents"),
    (PyWin32Adapter, "open_model"),
    (PyWin32Adapter, "save_file"),
    (pywin32_adapter, "_null_callout"),
    (pywin32_adapter, "_EXPORT_OPTIONS"),
    (sw_io, "model_contents"),
    (edge_index, "scan_limit"),
    (export_tools, "INLINE_IMAGE_MAX_BYTES"),
    (export_tools, "INLINE_IMAGE_MAX_PIXELS"),
    (stl_mesh, "check_stl"),
)
#: COM members the raw parts call, checked in the type library wrapper when it is there.
COM_MEMBERS = {
    "IEdge": ("GetCurve", "GetCurveParams2"),
    "IEntity": ("Select4",),
    "IFeatureManager": ("GetFeatureTreeRootItem2", "InsertRefPlane"),
    "ITreeControlItem": ("GetFirstChild", "GetNext"),
    "ISldWorks": ("GetExportFileData",),
    "IBody2": ("GetEdgeCount", "GetEdges"),
}
_MOCK_CONFIG = {
    "mock_connect_delay": 0,
    "mock_model_delay": 0,
    "mock_feature_delay": 0,
    "mock_sketch_delay": 0,
}


def fill_mock_gaps(adapter: Any) -> list[str]:
    """Dry run only: stand-ins for what ``MockSolidWorksAdapter`` doesn't do.

    So the checks go on past them and their own code runs: a cut is the
    mock's extrusion, a sketch dimension says yes. The numbers stay the
    mock's; the live adapter is never touched.
    """
    from solidworks_mcp.adapters.base import (
        AdapterResult,
        AdapterResultStatus,
        SolidWorksAdapter,
    )

    filled = []
    if type(adapter).create_cut_extrude is SolidWorksAdapter.create_cut_extrude:

        async def create_cut_extrude(params: Any) -> Any:
            return await adapter.create_extrusion(params)

        adapter.create_cut_extrude = create_cut_extrude
        filled.append("create_cut_extrude")
    if type(adapter).add_sketch_dimension is SolidWorksAdapter.add_sketch_dimension:

        async def add_sketch_dimension(*_args: Any) -> Any:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data={"id": "Dimension_1", "name": "D1@Sketch"},
                execution_time=0.0,
            )

        adapter.add_sketch_dimension = add_sketch_dimension
        filled.append("add_sketch_dimension")
    return filled


def static_problems(
    plan: list[Check],
    tools: dict[str, Any],
    members: tuple[tuple[Any, str], ...] = MEMBERS,
) -> list[str]:
    problems = []
    for check in plan:
        missing = sorted(set(check.tools) - set(tools))
        if missing:
            problems.append(f"{check.name}: tools not registered: {', '.join(missing)}")
    for owner, name in members:
        if not hasattr(owner, name):
            problems.append(f"{getattr(owner, '__name__', owner)} has no {name}")
    return problems


def com_member_report(
    com_members: dict[str, tuple[str, ...]] = COM_MEMBERS,
) -> dict[str, Any]:
    """The raw parts' COM methods in the gen_py wrapper (none needed to run, only read)."""
    found: dict[str, Any] = {}
    for interface, names in com_members.items():
        try:
            known = sw_type_info.interface_method_names(interface)
        except Exception as exc:  # noqa: BLE001 - no wrapper here: reported
            return {"wrapper": f"not read: {type(exc).__name__}: {exc}"}
        found[interface] = (
            {name: name in known for name in names} if known else "not in wrapper"
        )
    return found


async def _find_summary_reuse(p: Probe, report: dict[str, Any]) -> None:
    """Whether summaries are reused, found once on the mock, for ``summary_reuse``."""
    if p.live:
        if "summary_reuse" in report["plan"]:
            p.cache["summary_reuse"] = await detect_summary_reuse()
        return
    report["summary_reuse"] = await detect_summary_reuse()
    print(f"[{p.tag}] summary reuse on this branch: {report['summary_reuse']}")
    p.cache["summary_reuse"] = report["summary_reuse"]


@dataclass(frozen=True)
class ProbeKind:
    """A probe script: its checks and what it calls its things.

    Attributes:
        tag: What its printed lines start with (``[speed-probe]``).
        prefix: What every scratch file's name starts with.
        default_out: The folder its run folders go in.
        about: What it checks, for ``--help`` ("the speed plan's changes").
        checks: Its checks by name, in order.
        members: The adapter and module members it reaches past the tools.
        com_members: The COM methods its raw parts call, by interface.
        fill_mock: The dry run's stand-ins for what the mock doesn't do.
        prepare: Run once before the checks, with the probe and the report.
    """

    tag: str
    prefix: str
    default_out: Path
    about: str
    checks: dict[str, Check]
    members: tuple[tuple[Any, str], ...] = ()
    com_members: dict[str, tuple[str, ...]] = field(default_factory=dict)
    fill_mock: Callable[[Any], list[str]] = fill_mock_gaps
    prepare: Callable[[Probe, dict[str, Any]], Awaitable[None]] | None = None


SPEED = ProbeKind(
    tag=TAG,
    prefix=PREFIX,
    default_out=DEFAULT_OUT,
    about="the speed plan's changes",
    checks=CHECKS,
    members=MEMBERS,
    com_members=COM_MEMBERS,
    prepare=_find_summary_reuse,
)


# ---- running ---------------------------------------------------------------------------------


def _git_head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return out.stdout.strip() or None


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


async def run_checks(
    p: Probe, plan: list[Check], report: dict[str, Any], path: Path
) -> None:
    for check in plan:
        outcome = Outcome(check.name, quiet=not p.live, tag=p.tag)
        if p.live and solidworks_pid() is None:
            outcome.error = "SolidWorks is not running (crashed?); the run stopped here"
            outcome.settle()
            report["checks"][check.name] = outcome.as_dict()
            continue
        p.outcome = outcome
        print(f"[{p.tag}] {check.name}: {check.settles}", flush=True)
        keep = set(await p.documents()) if p.live else set()
        started = time.perf_counter()
        try:
            await check.run(p, outcome)
        except Skip as exc:
            outcome.status = "skip"
            outcome.note(str(exc))
        except CheckFailed as exc:
            outcome.expect("the check ran to its end", False, str(exc))
        except Exception as exc:  # noqa: BLE001 - a probe records every failure
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.measure("traceback", traceback.format_exc())
        finally:
            for failed in p.restore_all():
                outcome.note(f"a switch could not be put back: {failed}")
            try:
                outcome.closed = await p.close_new(keep)
            except Exception as exc:  # noqa: BLE001 - reported, the run goes on
                outcome.note(f"closing its documents failed: {exc}")
            outcome.duration_s = time.perf_counter() - started
            outcome.settle()
            report["checks"][check.name] = outcome.as_dict()
            report["calls"] = p.calls
            write_report(path, report)
            print(f"[{p.tag}] {check.name}: {outcome.status}", flush=True)
        p.outcome = None


def print_table(report: dict[str, Any]) -> None:
    rows = []
    for name, result in report.get("checks", {}).items():
        expectations = result.get("expectations") or []
        passed = sum(1 for e in expectations if e.get("ok") is True)
        failed = [e["what"] for e in expectations if e.get("ok") is False]
        note = result.get("error") or (failed[0] if failed else "")
        if result.get("status") == "skip" and result.get("notes"):
            note = result["notes"][-1]
        rows.append(
            (
                name,
                result.get("status", "?"),
                f"{passed}/{len(expectations)}",
                f"{result.get('duration_s', 0):.1f} s",
                note,
            )
        )
    header = ("check", "status", "expected", "time", "first problem / note")
    widths = [max(len(str(row[i])) for row in [header, *rows]) for i in range(4)]
    print()
    print(
        "  ".join(str(header[i]).ljust(widths[i]) for i in range(4)) + "  " + header[4]
    )
    for row in rows:
        cells = "  ".join(str(row[i]).ljust(widths[i]) for i in range(4))
        print(f"{cells}  {_short(row[4], 90) if row[4] else ''}".rstrip())
    print()


def _settings() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in RECORDED_SETTINGS}


def _run_dir(out: Path, prefix: str) -> Path:
    run = out / f"{prefix}{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run.mkdir(parents=True, exist_ok=True)
    return run


def _delete_scratch(run_dir: Path, prefix: str = PREFIX) -> list[str]:
    """The probe's SolidWorks files in the run folder, deleted; those that wouldn't go."""
    left = []
    for path in run_dir.glob(f"{prefix}*"):
        if path.suffix.lower() in SOLIDWORKS_SUFFIXES:
            try:
                path.unlink()
            except OSError:
                left.append(path.name)
    return left


async def live_run(args: argparse.Namespace, kind: ProbeKind = SPEED) -> int:
    plan = build_plan(args.only, kind.checks)
    pid = solidworks_pid()
    if pid is None:
        print(
            f"[{kind.tag}] SolidWorks is not running. Start it (the probe never "
            "launches it), let it finish starting, and run again.",
            flush=True,
        )
        return 3
    if args.pacing == "live":
        for name, value in LIVE_PACING.items():
            os.environ.setdefault(name, value)
    os.environ["SOLIDWORKS_MCP_TIMING"] = "1"
    waited = warm_up(pid, kind.tag)
    run_dir = _run_dir(Path(args.out), "")
    log = LogCapture()
    log.start(run_dir / "server.log")
    path = run_dir / "report.json"
    report: dict[str, Any] = {
        "mode": "live",
        "started": datetime.now().isoformat(timespec="seconds"),
        "head": _git_head(),
        "plan": [check.name for check in plan],
        "settings": _settings(),
        "solidworks": {"pid": pid, "warm_up": waited},
        "server_log_path": str(server_log_path()),
        "probe_log_path": str(log.path),
        "checks": {},
    }
    print(f"[{kind.tag}] run folder: {run_dir}", flush=True)
    adapter = PyWin32Adapter({})
    p: Probe | None = None
    try:
        # connect() falls back to Dispatch, which would start a SolidWorks;
        # the warm-up can take minutes, so look again just before it.
        if solidworks_pid() is None:
            raise SystemExit("SolidWorks stopped during the warm-up; nothing was run.")
        await adapter.connect()
        info = await adapter.get_application_info()
        report["solidworks"]["application"] = (
            info.data if info.is_success else info.error
        )
        mcp, tools = await register_tools(adapter, kind.tag)
        problems = static_problems(plan, tools, kind.members)
        if problems:
            report["problems"] = problems
            raise SystemExit("Plan problems: " + "; ".join(problems))
        p = Probe(
            adapter, mcp, tools, run_dir, log, True, prefix=kind.prefix, tag=kind.tag
        )
        p.baseline = set(await p.documents())
        report["solidworks"]["documents_open_before"] = len(p.baseline)
        if kind.prepare is not None:
            await kind.prepare(p, report)
        await run_checks(p, plan, report, path)
    finally:
        if p is not None:
            report["switches_not_restored"] = p.restore_all()
            report["documents_made"] = sorted(p.created)
            try:
                report["closed_at_end"] = await p.close_new(keep=())
                still = set(await p.documents())
                report["solidworks"]["baseline_still_open"] = len(p.baseline & still)
            except Exception as exc:  # noqa: BLE001 - reported
                report["closed_at_end"] = f"failed: {exc}"
        await adapter.disconnect()
        if not args.keep_scratch:
            report["scratch_not_deleted"] = _delete_scratch(run_dir, kind.prefix)
        report["finished"] = datetime.now().isoformat(timespec="seconds")
        write_report(path, report)
        log.stop()
        print_table(report)
        print(f"[{kind.tag}] report: {path}", flush=True)
    failed = [
        n for n, c in report["checks"].items() if c["status"] in ("fail", "error")
    ]
    return 1 if failed else 0


async def dry_run(args: argparse.Namespace, kind: ProbeKind = SPEED) -> int:
    """The plan, the imports and every check on the mock adapter; no SolidWorks."""
    plan = build_plan(args.only, kind.checks)
    os.environ["SOLIDWORKS_MCP_TIMING"] = "1"
    run_dir = _run_dir(Path(args.out), "dryrun-")
    log = LogCapture()
    log.start(run_dir / "server.log")
    print(f"[{kind.tag}] dry run, no SolidWorks; folder: {run_dir}")
    total = 0.0
    for check in plan:
        total += check.minutes
        print(f"\n  {check.name} (about {check.minutes:g} min): {check.settles}")
        print(f"    tools: {', '.join(dict.fromkeys(check.tools))}")
        print(f"    makes: {', '.join(check.documents)}")
        if check.raw_com:
            print(f"    raw COM (live only): {check.raw_com}")
    print(f"\n  about {total:g} min live, besides SolidWorks' own warm-up\n")
    # The warm-up's parts that need no SolidWorks: the first live run died
    # loading readiness.py, which no dry run had done.
    readiness = load_readiness()
    readiness.SolidWorksReadiness(os.getpid(), lambda: False)
    age = solidworks_age_seconds(os.getpid())
    print(f"  warm-up helpers load; this process' age reads {age!r} s\n")
    adapter = MockSolidWorksAdapter(_MOCK_CONFIG)
    await adapter.connect()
    path = run_dir / "report.json"
    report: dict[str, Any] = {
        "mode": "dry-run",
        "started": datetime.now().isoformat(timespec="seconds"),
        "head": _git_head(),
        "plan": [check.name for check in plan],
        "settings": _settings(),
        "server_log_path": str(server_log_path()),
        "mock_stand_ins": kind.fill_mock(adapter),
        "checks": {},
    }
    try:
        mcp, tools = await register_tools(adapter, kind.tag)
        report["problems"] = static_problems(plan, tools, kind.members)
        report["com_members"] = com_member_report(kind.com_members)
        for problem in report["problems"]:
            print(f"[{kind.tag}] PROBLEM: {problem}")
        print(f"[{kind.tag}] COM members in the type library: {report['com_members']}")
        p = Probe(
            adapter, mcp, tools, run_dir, log, False, prefix=kind.prefix, tag=kind.tag
        )
        if kind.prepare is not None:
            await kind.prepare(p, report)
        await run_checks(p, plan, report, path)
    finally:
        await adapter.disconnect()
        write_report(path, report)
        log.stop()
    print_table(report)
    print(
        f"[{kind.tag}] on the mock only 'error' means a fault in the probe; fail is the mock's numbers"
    )
    print(f"[{kind.tag}] report: {path}")
    errors = [n for n, c in report["checks"].items() if c["status"] == "error"]
    return 1 if errors or report["problems"] else 0


def parse_args(
    argv: list[str] | None = None, kind: ProbeKind = SPEED
) -> argparse.Namespace:
    try:
        shown = f"{kind.default_out.relative_to(REPO).as_posix()}/"
    except ValueError:
        shown = str(kind.default_out)
    parser = argparse.ArgumentParser(
        description=(
            f"Check {kind.about} against the running SolidWorks, on "
            "scratch documents only. Needs the user's OK and nothing else "
            "driving SolidWorks; --dry-run touches no SolidWorks."
        )
    )
    parser.add_argument(
        "--out",
        default=str(kind.default_out),
        help=f"Folder for the run's folder (default {shown})",
    )
    parser.add_argument(
        "--only",
        default=None,
        help=f"Comma-separated checks to run: {', '.join(kind.checks)}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the plan, import everything and run the checks on the mock adapter",
    )
    parser.add_argument(
        "--pacing",
        choices=("server", "live"),
        default="server",
        help="server: the server's settle gaps (default); live: the live suite's slower ones",
    )
    parser.add_argument(
        "--keep-scratch",
        action="store_true",
        help=f"Keep the {kind.prefix} parts and assemblies after the run",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, kind: ProbeKind = SPEED) -> int:
    args = parse_args(argv, kind)
    return asyncio.run(dry_run(args, kind) if args.dry_run else live_run(args, kind))


if __name__ == "__main__":
    sys.exit(main())
