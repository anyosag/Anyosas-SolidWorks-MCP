"""Probe the Dish Rack truss fixes (branch ``dishrack-truss-fixes``) on real SolidWorks.

Two fixes from the Dish Rack truss build were tested on fakes and the mock
adapter only:

- ``create_reference_plane`` with ``point`` and ``normal`` picked the start of
  its 3D sketch line by coordinates (``SelectByID2``), which takes the first
  sketch point along the view through them. After a plane through
  X = +28.4, one asked through X = -28.4 with normal -X was made through
  +28.4 and kept with only a warning; one with normal +X couldn't select its
  start. The start point is now selected as itself (``GetStartPoint2``, then
  ``Select4``), and a plane made off its point is deleted with its 3D sketch
  and refused (``reference_geometry.plane_through``).
- ``close_model`` dropped ``{"title": X}`` and closed the active document. Its
  input is strict now: a document named in it is refused, in a workflow
  before any step runs (``CloseModelInput``).

It is the speed probe's machinery, loaded from ``probe_speed_plan.py`` (the
warm-up, the ``PyWin32Adapter`` connect and disconnect, the tools called in
the probe's own process, the report, the log, the table, ``--only``,
``--dry-run`` and ``--keep-scratch``), with checks of its own (:data:`TRUSS`).

Checks, in order (``--only`` takes these names, comma-separated):

``planes``
    A scratch part seen from the right (``ShowNamedView2``, swRightView), so
    the points line up along the view, as they must have on the truss. The
    Dish Rack sequence: through [28.4, 0, 0] with normal +X, [-28.4, 0, 0]
    with -X, [-28.4, 0, 0] with +X; then +Y, -Y and the [1, 0, +-1] normals
    the session used next. Each plane: made, ``through_point`` and
    ``normal_matches`` true, ``plane_axes`` within 0.01 mm of its point, and
    its start selected as itself (``Select4``), the coordinate pick never
    reached.
``old_pick``
    The first three on a second part in the same view, the start picked the
    old way, by coordinates (patched in this process only). Where SolidWorks
    put each is recorded, not judged; a plane kept must go through its point,
    and one refused must leave the tree as it was.
``off_point``
    A plane whose start pick takes the line's far end instead (``Select4`` on
    ``GetEndPoint2``, patched), so SolidWorks makes it 10 mm off its point:
    refused, the message saying so, and the plane and its 3D sketch gone.
    Then the same plane unpatched, through its point.
``close_by_title``
    Two scratch parts, the second active. ``close_model`` with ``title`` (the
    first's), then ``path``: ``invalid_input`` pointing to ``document``, both
    still open. A workflow whose second step is that close: refused before
    its first step runs. Then ``close_model`` with ``document`` the first's
    file name: it alone closes.

Safety, as the speed probe's. It attaches to the SolidWorks that is running
and never starts one. Every document it makes is saved at once under the run
folder as ``TrussProbe_<name>``; it opens no other file. After each check,
and at the end, it closes only its own documents and never one open before.
It changes no SolidWorks setting; its raw COM work (the view of its own
parts) runs inside ``_handle_com_operation``. The SolidWorks files are
deleted at the end unless ``--keep-scratch``. It drives SolidWorks only with
``--live``; without ``--live`` or ``--dry-run`` it says so and stops.

Output, under ``<out>/<YYYYmmdd-HHMMSS>/`` (``tests/.generated/truss_probe/``
by default): ``report.json`` and ``server.log``, as the speed probe's.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_truss_fixes.py --live
    .venv\\Scripts\\python.exe tests/scripts/probe_truss_fixes.py --live --only planes
    .venv\\Scripts\\python.exe tests/scripts/probe_truss_fixes.py --dry-run

``--dry-run`` never touches SolidWorks: it runs every check on
``MockSolidWorksAdapter``, which makes its planes without picking, so the
picks, the old pick and the off-point refusal are live only. A live run takes
about two minutes besides SolidWorks' own warm-up.
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import reference_geometry
from solidworks_mcp.adapters.solidworks.plane_frame import OFF_PLANE_TOLERANCE_MM


def _load_speed_probe() -> Any:
    """``probe_speed_plan.py``, loaded from its file beside this one.

    It goes into ``sys.modules`` before it runs: its dataclasses look their
    module up there.
    """
    name = "probe_speed_plan"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().with_name("probe_speed_plan.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Can't load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


speed = _load_speed_probe()
Check = speed.Check
CheckFailed = speed.CheckFailed

DEFAULT_OUT = speed.REPO / "tests" / ".generated" / "truss_probe"
#: Every scratch file the probe writes starts with this. Never ``DishRack``:
#: those are the user's files.
PREFIX = "TrussProbe_"
TAG = "truss-probe"

#: ``swStandardViews_e.swRightView``: the view along X, where the truss's
#: points at X = +28.4 and -28.4 line up.
RIGHT_VIEW = 4
#: The Dish Rack truss sequence: (label, point in mm, normal).
DISH_RACK = (
    ("plus_x", [28.4, 0.0, 0.0], [1.0, 0.0, 0.0]),
    ("minus_x", [-28.4, 0.0, 0.0], [-1.0, 0.0, 0.0]),
    ("minus_x_normal_plus_x", [-28.4, 0.0, 0.0], [1.0, 0.0, 0.0]),
)
#: Either way along Y, and the two normals the session used next.
OTHERS = (
    ("plus_y", [0.0, 20.0, 0.0], [0.0, 1.0, 0.0]),
    ("minus_y", [0.0, -20.0, 0.0], [0.0, -1.0, 0.0]),
    ("x_plus_z", [10.0, 0.0, 10.0], [1.0, 0.0, 1.0]),
    ("x_minus_z", [10.0, 0.0, -10.0], [1.0, 0.0, -1.0]),
)
#: The off-point plane: its line's far end lies LINE_MM along the normal.
OFF_POINT = ([0.0, 0.0, 5.0], [0.0, 0.0, 1.0])


# ---- helpers -------------------------------------------------------------------------


def _live(p: Any, ok: bool) -> bool | None:
    """``ok`` live; None on the mock, which makes its planes without picking."""
    return ok if p.live else None


def _unit(vector: list[float]) -> list[float]:
    size = math.sqrt(sum(c * c for c in vector))
    return [c / size for c in vector]


def off_plane(axes: Any, point: list[float]) -> float | None:
    """How far ``point`` lies off the plane ``axes`` describes (``plane_axes``), mm."""
    if not isinstance(axes, dict):
        return None
    try:
        origin, normal = axes["origin_mm"], axes["normal"]
        return round(
            sum(
                (float(a) - float(o)) * float(n)
                for a, o, n in zip(point, origin, normal, strict=True)
            ),
            6,
        )
    except (KeyError, TypeError, ValueError):
        return None


def along(axes: Any, normal: list[float]) -> float | None:
    """The read-back normal's dot with the one asked: 1 when they agree."""
    if not isinstance(axes, dict):
        return None
    try:
        return round(
            sum(
                float(a) * b for a, b in zip(axes["normal"], _unit(normal), strict=True)
            ),
            9,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _same_path(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(
        os.path.normpath(str(b))
    )


async def open_rows(p: Any) -> list[dict[str, Any]]:
    """The open documents' paths and titles (``list_open_documents``, read-only)."""
    listed = await p.adapter.list_open_documents()
    if not listed.is_success:
        return []
    return [
        {"path": doc.get("path"), "title": doc.get("title")}
        for doc in listed.data or []
        if isinstance(doc, dict)
    ]


def _is_open(p: Any, rows: list[dict[str, Any]], part: tuple[Path, Any]) -> bool:
    """Whether ``part`` (its file, its title) is open: by path, on the mock by title.

    The mock's save doesn't move a document to its file: its rows keep their
    ``Mock://`` paths.
    """
    path, title = part
    if p.live:
        return any(_same_path(row.get("path"), path) for row in rows)
    return any(row.get("title") == title for row in rows)


def show_view(adapter: Any, view: int) -> dict[str, Any]:
    """``ShowNamedView2`` of a standard view on the active part; what it raised."""
    model = adapter.currentModel
    sw_type_info.flag_members(model, "ShowNamedView2")
    _value, error = adapter._attempt_with_error(
        lambda: model.ShowNamedView2("*Right", view)
    )
    return {
        "view": view,
        "error": None if error is None else f"{type(error).__name__}: {error}",
    }


def _sketch_open(adapter: Any) -> bool:
    """Whether the adapter has a sketch open (the live one's or the mock's)."""
    return any(
        getattr(adapter, name, None) is not None
        for name in ("currentSketch", "_current_sketch")
    )


# ---- the picks -----------------------------------------------------------------------


@contextmanager
def start_picks() -> Iterator[list[dict[str, Any]]]:
    """How each plane's start point is selected: ``Select4`` on it, or by coordinates.

    ``reference_geometry._select_object`` and ``_select`` are wrapped for the
    block (no COM of their own). Only point picks are kept: ``select_line``
    picks the line itself the same way.
    """
    original_object = reference_geometry._select_object
    original_select = reference_geometry._select
    picks: list[dict[str, Any]] = []

    def select_object(adapter: Any, entity: Any, mark: int, append: bool) -> bool:
        ok = original_object(adapter, entity, mark, append)
        picks.append({"how": "Select4", "mark": mark, "ok": ok})
        return ok

    def select(
        adapter: Any, name: str, kind: str, at_m: Any, append: bool, mark: int
    ) -> bool:
        ok = original_select(adapter, name, kind, at_m, append, mark)
        if kind == "EXTSKETCHPOINT":
            picks.append(
                {
                    "how": "SelectByID2",
                    "at_mm": [round(c * 1000.0, 6) for c in at_m],
                    "mark": mark,
                    "ok": ok,
                }
            )
        return ok

    reference_geometry._select_object = select_object
    reference_geometry._select = select
    try:
        yield picks
    finally:
        reference_geometry._select_object = original_object
        reference_geometry._select = original_select


@contextmanager
def old_start_pick() -> Iterator[None]:
    """``select_line_start`` as it was before the fix: by coordinates only."""
    original = reference_geometry.select_line_start

    def by_position(adapter: Any, line: Any, mark: int, append: bool) -> bool:
        return reference_geometry._select(
            adapter, "", "EXTSKETCHPOINT", line.start_m(), append, mark
        )

    reference_geometry.select_line_start = by_position
    try:
        yield
    finally:
        reference_geometry.select_line_start = original


@contextmanager
def far_end_pick() -> Iterator[None]:
    """``select_line_start`` taking the line's far end: a plane LINE_MM off its point."""
    original = reference_geometry.select_line_start

    def far_end(adapter: Any, line: Any, mark: int, append: bool) -> bool:
        reference_geometry._flag(adapter, line.segment, "GetEndPoint2")
        point = reference_geometry._attempt(
            adapter, lambda: line.segment.GetEndPoint2()
        )
        return point is not None and reference_geometry._select_object(
            adapter, point, mark, append
        )

    reference_geometry.select_line_start = far_end
    try:
        yield
    finally:
        reference_geometry.select_line_start = original


async def make_plane(
    p: Any, r: Any, label: str, point: list[float], normal: list[float]
) -> dict[str, Any]:
    """``create_reference_plane`` through ``point`` square to ``normal``, recorded.

    Returns:
        dict[str, Any]: ``reply``, its ``plane`` as ``data``, and ``picks``.
    """
    with start_picks() as picks:
        reply = await p.call(
            "create_reference_plane", {"point": point, "normal": normal}
        )
    data = reply.get("plane") or {}
    r.measure(
        f"{label}.reply",
        {
            "status": reply.get("status"),
            "error_type": reply.get("error_type"),
            "message": reply.get("message"),
            "hint": reply.get("hint"),
            "warning": reply.get("warning"),
            **{
                key: data.get(key)
                for key in (
                    "name",
                    "sketch",
                    "normal_matches",
                    "through_point",
                    "features_before",
                    "features_after",
                )
            },
        },
    )
    r.measure(f"{label}.picks", picks)
    r.time(f"{label}.server_s", reply.get("server_time_s"))
    return {"reply": reply, "data": data, "picks": picks}


async def read_back(
    p: Any, r: Any, label: str, name: Any, point: list[float], normal: list[float]
) -> float | None:
    """``plane_axes`` of the plane ``name``: how far ``point`` lies off it (recorded)."""
    axes = (await p.ok("plane_axes", {"plane": str(name)})).get("axes") or {}
    off = off_plane(axes, point)
    r.measure(
        f"{label}.read_back",
        {
            "origin_mm": axes.get("origin_mm"),
            "normal": axes.get("normal"),
            "off_mm": off,
            "along_asked": along(axes, normal),
        },
    )
    return off


def _through(off: float | None) -> bool:
    return off is not None and abs(off) <= OFF_PLANE_TOLERANCE_MM


def _only_select4(picks: list[dict[str, Any]]) -> bool:
    return [pick["how"] for pick in picks] == ["Select4"] and picks[0]["ok"] is True


# ---- 1. planes -----------------------------------------------------------------------


async def check_planes(p: Any, r: Any) -> None:
    await p.new_part("planes")
    if p.live:
        r.measure(
            "view", p.raw("show_right_view", lambda: show_view(p.adapter, RIGHT_VIEW))
        )
    else:
        r.note("the view along X: raw COM, live only")
    for label, point, normal in (*DISH_RACK, *OTHERS):
        made = await make_plane(p, r, label, point, normal)
        reply, data, picks = made["reply"], made["data"], made["picks"]
        if not r.expect(
            f"{label}: made", reply.get("status") == "success", reply.get("message")
        ):
            continue
        r.expect(
            f"{label}: through_point and normal_matches",
            data.get("through_point") is True and data.get("normal_matches") is True,
            {
                "through_point": data.get("through_point"),
                "normal_matches": data.get("normal_matches"),
            },
        )
        off = await read_back(p, r, label, data.get("name"), point, normal)
        r.expect(
            f"{label}: plane_axes puts its point on it "
            f"(within {OFF_PLANE_TOLERANCE_MM:g} mm)",
            _through(off),
            off,
        )
        r.expect(
            f"{label}: its start selected as itself (Select4), never by coordinates",
            _live(p, _only_select4(picks)),
            picks,
        )


# ---- 2. old_pick ---------------------------------------------------------------------


async def check_old_pick(p: Any, r: Any) -> None:
    await p.new_part("old_pick")
    if p.live:
        r.measure(
            "view", p.raw("show_right_view", lambda: show_view(p.adapter, RIGHT_VIEW))
        )
    else:
        r.note("the old pick by coordinates: live only (the mock doesn't pick)")
    missed = []
    for label, point, normal in DISH_RACK:
        before = await p.feature_names()
        with old_start_pick():
            made = await make_plane(p, r, label, point, normal)
        after = await p.feature_names()
        reply, data = made["reply"], made["data"]
        message = str(reply.get("message") or "")
        if reply.get("status") == "success":
            off = await read_back(p, r, label, data.get("name"), point, normal)
            r.expect(
                f"old pick, {label}: the plane kept goes through its point",
                _through(off),
                off,
            )
            outcome = f"made, {off} mm off its point"
        else:
            r.expect(
                f"old pick, {label}: refused, the tree as it was",
                after == before,
                {"before": before, "after": after},
            )
            if "not through it" in message:
                missed.append(label)
                outcome = "made off its point, deleted and refused"
            else:
                outcome = f"refused: {message}"
        r.measure(f"{label}.outcome", outcome)
        r.note(f"old pick, {label}: {outcome}")
    if p.live:
        r.measure("missed", missed)
        r.note(
            "the old pick missed: "
            + (", ".join(missed) if missed else "none here (this view didn't do it)")
        )


# ---- 3. off_point --------------------------------------------------------------------


async def check_off_point(p: Any, r: Any) -> None:
    await p.new_part("off_point")
    point, normal = OFF_POINT
    before = await p.feature_names()
    with far_end_pick():
        made = await make_plane(p, r, "far_end", point, normal)
    after = await p.feature_names()
    reply = made["reply"]
    message = str(reply.get("message") or "")
    r.measure("tree", {"before": before, "after": after})
    if p.live:
        off = f"{reference_geometry.LINE_MM:g} mm from point"
        r.expect(
            "far end: refused", reply.get("status") == "error", reply.get("status")
        )
        r.expect(
            f"far end: the message says it was made {off} and deleted",
            off in message and "not through it, so it was deleted" in message,
            message,
        )
        r.expect(
            "far end: the plane and its 3D sketch are gone, the tree as before",
            after == before,
            {"before": before, "after": after},
        )
        r.expect(
            "far end: picked with Select4",
            [pick["how"] for pick in made["picks"]] == ["Select4"],
            made["picks"],
        )
    else:
        r.note("the far-end pick: live only (the mock doesn't pick)")
    again = await make_plane(p, r, "again", point, normal)
    data = again["data"]
    r.expect(
        "again, unpatched: made through its point",
        again["reply"].get("status") == "success" and data.get("through_point") is True,
        {"status": again["reply"].get("status"), "through": data.get("through_point")},
    )
    r.expect(
        "again: its start selected as itself (Select4)",
        _live(p, _only_select4(again["picks"])),
        again["picks"],
    )


# ---- 4. close_by_title ---------------------------------------------------------------


async def check_close_by_title(p: Any, r: Any) -> None:
    first_file = await p.new_part("close_first")
    first = (first_file, await p.active_title())
    second_file = await p.new_part("close_second")
    second = (second_file, await p.active_title())
    title = first_file.stem
    r.measure("parts", {"first": first, "second": second})
    for key, value in (("title", title), ("path", str(first_file))):
        reply = await p.call("close_model", {key: value})
        message = str(reply.get("message") or "")
        r.measure(
            f"{key}.reply",
            {k: reply.get(k) for k in ("status", "error_type", "message", "hint")},
        )
        r.expect(
            f"close_model {{{key!r}}}: invalid_input",
            reply.get("error_type") == "invalid_input",
            (reply.get("status"), reply.get("error_type")),
        )
        r.expect(
            f"close_model {{{key!r}}}: it points to `document`",
            f"`{key}` isn't an input of close_model" in message
            and "with `document`" in message,
            message,
        )
        rows = await open_rows(p)
        r.expect(
            f"close_model {{{key!r}}}: both parts still open",
            _is_open(p, rows, first) and _is_open(p, rows, second),
            rows,
        )

    features = await p.feature_names()
    steps = [{"create_sketch": {"plane": "Top"}}, {"close_model": {"title": title}}]
    reply = await p.call("execute_workflow", {"steps": steps})
    message = str(reply.get("message") or "")
    r.measure(
        "workflow.reply",
        {k: reply.get(k) for k in ("status", "error_type", "message", "hint")},
    )
    r.expect(
        "workflow: invalid_input naming steps.1 (close_model)",
        reply.get("error_type") == "invalid_input"
        and "steps.1 (close_model)" in message
        and "`title` isn't an input of close_model" in message,
        message,
    )
    rows = await open_rows(p)
    r.expect(
        "workflow: both parts still open",
        _is_open(p, rows, first) and _is_open(p, rows, second),
        rows,
    )
    now = await p.feature_names()
    r.expect(
        "workflow: no step ran (no sketch open, the tree as before)",
        now == features and not _sketch_open(p.adapter),
        {"before": features, "after": now, "sketch_open": _sketch_open(p.adapter)},
    )

    # Live by its file name; the mock knows it by its own title.
    document = first_file.name if p.live else str(first[1])
    closed = await p.call("close_model", {}, document=document)
    r.measure(
        "close.reply",
        {
            k: closed.get(k)
            for k in ("status", "error_type", "message", "closed", "warning")
        },
    )
    r.expect(
        f"close_model, document {document}: success",
        closed.get("status") == "success",
        closed.get("message"),
    )
    r.expect(
        f"close_model, document {document}: closed is [{document}]",
        _live(p, closed.get("closed") == [document]),
        closed.get("closed"),
    )
    rows = await open_rows(p)
    r.expect(
        "the first part is closed, the second still open",
        not _is_open(p, rows, first) and _is_open(p, rows, second),
        rows,
    )


# ---- the plan ------------------------------------------------------------------------

CHECKS: dict[str, Any] = {
    check.name: check
    for check in (
        Check(
            "planes",
            check_planes,
            "planes through a point go through it, the truss's sequence first",
            ("create_part", "create_reference_plane", "plane_axes"),
            ("TrussProbe_planes",),
            0.5,
            raw_com="ShowNamedView2 (the view along X)",
        ),
        Check(
            "old_pick",
            check_old_pick,
            "where the old pick by coordinates puts the truss's planes",
            ("create_part", "create_reference_plane", "plane_axes"),
            ("TrussProbe_old_pick",),
            0.5,
            raw_com="ShowNamedView2 (the view along X)",
        ),
        Check(
            "off_point",
            check_off_point,
            "a plane made off its point is deleted with its 3D sketch and refused",
            ("create_part", "create_reference_plane"),
            ("TrussProbe_off_point",),
            0.3,
        ),
        Check(
            "close_by_title",
            check_close_by_title,
            "close_model refuses a document named in its input, a workflow first",
            ("create_part", "close_model", "execute_workflow", "create_sketch"),
            ("TrussProbe_close_first", "TrussProbe_close_second"),
            0.5,
        ),
    )
}

#: The adapter and module members the checks reach past the tools.
MEMBERS = (
    (PyWin32Adapter, "_handle_com_operation"),
    (PyWin32Adapter, "_attempt_with_error"),
    (PyWin32Adapter, "list_open_documents"),
    (PyWin32Adapter, "list_features"),
    (PyWin32Adapter, "save_file"),
    (reference_geometry, "_select_object"),
    (reference_geometry, "_select"),
    (reference_geometry, "select_line_start"),
    (reference_geometry, "_flag"),
    (reference_geometry, "_attempt"),
    (reference_geometry, "LINE_MM"),
    (speed.Probe, "feature_names"),
)
#: COM methods the raw parts and the patched picks call.
COM_MEMBERS = {
    "IModelDoc2": ("ShowNamedView2",),
    "ISketchLine": ("GetStartPoint2", "GetEndPoint2"),
    "ISketchPoint": ("Select4",),
    "ISelectionMgr": ("CreateSelectData",),
    "IFeatureManager": ("InsertRefPlane",),
}

TRUSS = speed.ProbeKind(
    tag=TAG,
    prefix=PREFIX,
    default_out=DEFAULT_OUT,
    about="the Dish Rack truss fixes",
    checks=CHECKS,
    members=MEMBERS,
    com_members=COM_MEMBERS,
)


def main(argv: list[str] | None = None) -> int:
    """Run the probe: live only with ``--live``, on the mock with ``--dry-run``."""
    args = list(sys.argv[1:] if argv is None else argv)
    live = "--live" in args
    args = [arg for arg in args if arg != "--live"]
    if not live and "--dry-run" not in args and not {"-h", "--help"} & set(args):
        print(
            f"[{TAG}] this drives the running SolidWorks: pass --live (with the "
            "user's OK and nothing else driving it), or --dry-run for the mock"
        )
        return 2
    return speed.main(args, TRUSS)


if __name__ == "__main__":
    sys.exit(main())
