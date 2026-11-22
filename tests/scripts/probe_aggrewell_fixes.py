"""Probe the Aggrewell fixes (branch ``aggrewell-fixes``) against real SolidWorks.

The fixes from the Aggrewell enclosure v2 session were written and tested on
fakes and the mock adapter only: patterns that leave out the seeds a listed
pattern repeats (``pattern_seeds.py``), an assembly's box from its
components' solids (``assembly_box.py``), a part and an assembly of one title
told apart, and ``check_interference``'s filter (``io.interferences_among``).
This probe runs each on scratch documents and records what SolidWorks did,
with the raw COM readings the fixes rest on, so a run's report settles them.

It is the speed probe's machinery, loaded from ``probe_speed_plan.py``: the
warm-up, the ``PyWin32Adapter`` connect and disconnect, the tools called in
the probe's own process, ``Probe`` and ``Outcome``, the report, the log, the
table, ``--only``, ``--dry-run`` and ``--keep-scratch``, with checks of its
own (:data:`AGGREWELL`).

Checks, in order (``--only`` takes these names, comma-separated):

``pattern_seeds``
    A 160 x 120 x 4 mm plate. ``Vent_Hole``, a Ø3 hole through it, and
    ``Vent_CS``, a 0.3 mm chamfer on its top edge; ``Vent_Ring``, their
    circular pattern of 6 about an axis normal to the plate and off its
    centre. Raw: Vent_Ring's ``GetParents`` (each name and ``GetTypeName2``)
    and ``GetPatternFeatureCount`` (expected the hole, the chamfer and the
    axis; 2). Then ``pattern_linear`` of the three along x (``patterned``
    Vent_Ring, both seeds in ``left_out``, a warning, 6 vents);
    ``mirror_feature`` of the three about the Right Plane, features only (6
    vents); that linear pattern listed with the three, along y (12 vents);
    the unsure path, the hole along a plate edge, whose pattern has the edge's
    feature for a parent too (parents and seed count raw), then listed with
    that pattern (``left_out`` the hole, and whether SolidWorks refused the
    first try); and the hole with its chamfer, which keeps both. A vent is
    the cut's and the chamfer's ``measured_change``. Each call's seed plan
    (sure, unsure and every try, so each ``FeatureLinearPattern5`` call) is
    recorded. Every copy lands on uncut plate, either way along the edge:
    :func:`layout_problems` checks that before anything runs.
``assembly_box``
    A 50 x 40 x 7.2 mm block with a ``create_axis`` line through it, 10 mm
    up from its base, in an assembly. ``verify_model``'s box
    (``component_extreme_points``, within 0.01 mm), beside ``GetBox(0)`` read
    raw; a second copy turned 90 degrees about Y and set apart (the union);
    that copy hidden, then suppressed (the first block alone). No tool
    suppresses a component (``set_component_state`` fixes and flexes only), so
    ``suppress_feature`` of its name is tried, then raw
    ``IComponent2.SetSuppression2``. Lightweight is skipped, with a note.
``same_title``
    ``AggrewellProbe_Tray.SLDPRT`` and ``AggrewellProbe_Tray.SLDASM`` (holding
    ``AggrewellProbe_TrayInsert``), both open. Raw: whether a saved part's
    ``GetTitle`` lacks its extension (if not, the names can't clash and the
    ambiguity expectation is skipped). ``activate_document`` of the bare name
    (``invalid_input`` naming both), of each file name (``verified``, its
    type); ``list_open_documents`` marking only the part active (its rows
    compare titles before paths, so both may read active). Raw, each
    answer recorded and the documents reopened after: does ``ActivateDoc3``
    take a full path; does ``CloseDoc`` with a full path, and with a file
    name, close only that one. Then ``close_model`` with ``document`` the
    assembly: ``closed`` names it alone, and the part stays open.
``interference_between``
    A tray and two nuts (blocks) placed with ``set_component_transform`` so
    each nut overlaps the tray and the nuts overlap each other.
    ``check_interference`` with no components (three pairs), [nut1, tray]
    (that pair), [tray] (its two), [nut1, nut2, tray] (all three), and each
    pair's volume against the blocks' overlap. The mock reports no counts.
``thread_pattern``
    A plate with four Ø5 posts (one would leave the other instances on no
    post, so a refusal could not be laid to the thread); ``create_thread``
    M5x0.8 on one post's top edge, 1 mm out, ``data.extent`` recorded; then
    ``pattern_circular`` of the Thread feature about the posts' centre, count
    4. Either refused, the message naming the Thread and the hint saying to
    use ``create_thread``, or made (recorded as patterned: the hint then never
    fires).

Safety, as the speed probe's. It attaches to the SolidWorks that is running
and never starts one; ``connect()`` falls back to ``Dispatch``, so it looks
again just before connecting. Every document it makes is saved at once under
the run folder as ``AggrewellProbe_<name>``; it opens no other file. After
each check, and at the end, it closes only its own documents (saved under the
run folder, or never saved with a title it made) and never one open before.
It changes no SolidWorks setting; its raw COM work (reads; ``ActivateDoc3``
and ``CloseDoc`` of its own two Tray documents; the suppression of its own
component) runs inside ``_handle_com_operation``, the document calls under the
adapter's own names for them, so they get its 1 s document settle pause
(pitfall #26). The SolidWorks files are deleted at the end unless
``--keep-scratch``.

Output, under ``<out>/<YYYYmmdd-HHMMSS>/`` (``tests/.generated/aggrewell_probe/``
by default): ``report.json`` and ``server.log``, as the speed probe's.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_aggrewell_fixes.py
    .venv\\Scripts\\python.exe tests/scripts/probe_aggrewell_fixes.py --only same_title
    .venv\\Scripts\\python.exe tests/scripts/probe_aggrewell_fixes.py --dry-run

``--dry-run`` never touches SolidWorks: it runs every check on
``MockSolidWorksAdapter`` with the raw COM parts skipped, and the mock's
volumes and boxes, which it makes up, are not judged. A full live run takes
about 7 minutes besides SolidWorks' own warm-up.
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
from solidworks_mcp.adapters.solidworks import (
    assembly_box,
    component_visibility,
    pattern_seeds,
)
from solidworks_mcp.adapters.solidworks import io as sw_io
from solidworks_mcp.adapters.solidworks.clearance import transform_point
from solidworks_mcp.adapters.solidworks.com_utils import as_list
from solidworks_mcp.adapters.solidworks.placement import rotation_rows_from_deg
from solidworks_mcp.tools.modeling import SetComponentStateInput


def _load_speed_probe() -> Any:
    """``probe_speed_plan.py``, loaded from its file beside this one.

    It goes into ``sys.modules`` before it runs: its dataclasses look their
    module up there (``load_readiness`` does the same for ``readiness.py``).
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

DEFAULT_OUT = speed.REPO / "tests" / ".generated" / "aggrewell_probe"
#: Every scratch file the probe writes starts with this.
PREFIX = "AggrewellProbe_"
TAG = "aggrewell-probe"

# ---- 1. pattern_seeds: the plate and its vents (mm) -----------------------------

#: The plate: a rectangle on Front, x -80..80 and y -60..60, extruded PLATE_T (+Z).
PLATE = (-80.0, -60.0, 80.0, 60.0)
PLATE_T = 4.0
#: The seed vent: a hole of VENT_R through the plate, its top edge chamfered.
VENT = (26.0, -40.0)
VENT_R = 1.5
CHAMFER = 0.3
#: Vent_Ring turns about an axis through RING_CENTRE, normal to the plate: off
#: the plate's centre, so the ring and its mirror about the Right Plane differ.
RING_CENTRE = (20.0, -40.0)
RING_COUNT = 6
#: Vent_Ring along +X, then it and that row along +Y.
ROW_X = 30.0
ROW_Y = 30.0
#: The unsure path: the seed hole along the plate's edge at x = 80, z = 4
#: (which way along it is SolidWorks' choice), then that pattern along -X.
EDGE_POINT = (PLATE[2], 0.0, PLATE_T)
EDGE_STEP = 15.0
EDGE_ROW = 60.0
#: The plain pattern: the hole and its chamfer along +Y.
PLAIN_STEP = 75.0

HOLE = math.pi * VENT_R**2 * PLATE_T
#: The chamfer's ring: its triangle (legs CHAMFER) turned about the hole's
#: axis, at its centroid's radius (Pappus).
CHAMFER_RING = 2 * math.pi * (VENT_R + CHAMFER / 3) * CHAMFER**2 / 2
VENT_VOLUME = HOLE + CHAMFER_RING
#: A pattern's or mirror's volume against the vents it should cut.
VOLUME_SHARE = 0.002

# ---- 2. assembly_box (mm) -----------------------------------------------------

#: The block: on Front, x 0..50, y 0..40, extruded 7.2 (+Z); its box, low then high.
BLOCK = (0.0, 0.0, 50.0, 40.0)
BLOCK_T = 7.2
BLOCK_BOX = (0.0, 0.0, 0.0, 50.0, 40.0, BLOCK_T)
#: The axis line create_axis draws (10 mm long, in a 3D sketch): up from the
#: block's base, 2.8 mm past its top, as on the Aggrewell enclosure.
BLOCK_AXIS = {"point": [25.0, 20.0, 0.0], "direction": [0.0, 0.0, 1.0]}
#: The second copy: turned 90 degrees about Y (the X axis to -Z), set apart.
COPY_ROTATION = (0.0, 90.0, 0.0)
COPY_ORIGIN = (100.0, 0.0, 0.0)
BOX_TOLERANCE = 0.01
#: ``swComponentSuppressionState_e``: suppressed, fully resolved.
SUPPRESSED = 0
RESOLVED = 2

# ---- 4. interference_between (mm) -----------------------------------------------

#: The tray and nut blocks (x1, y1, x2, y2 on Front, then the depth).
TRAY = (0.0, 0.0, 60.0, 40.0, 10.0)
NUT = (0.0, 0.0, 12.0, 12.0, 8.0)
#: Where each nut's origin goes: each overlaps the tray, and the two overlap.
NUT_ORIGINS = {"nut1": (5.0, 5.0, 6.0), "nut2": (11.0, 11.0, 8.0)}
OVERLAP_SHARE = 0.01

# ---- 5. thread_pattern (mm) -------------------------------------------------------

THREAD_PLATE = (-25.0, -25.0, 25.0, 25.0)
THREAD_PLATE_T = 4.0
#: Four Ø5 posts round the plate's centre, extruded 12 from Front (8 above it).
POSTS = [[15.0, 0.0, 2.5], [0.0, 15.0, 2.5], [-15.0, 0.0, 2.5], [0.0, -15.0, 2.5]]
POST_HEIGHT = 12.0
#: M5x0.8 on the first post's top edge, started 1 mm out: 4 mm into the post
#: with the start offset added to the length, as create_thread's docs say.
THREAD = {
    "size": "M5x0.8",
    "edge_point": [POSTS[0][0] + POSTS[0][2], POSTS[0][1], POST_HEIGHT],
    "length_mm": 5.0,
    "start_offset_mm": 1.0,
}


# ---- helpers -------------------------------------------------------------------------


def _live(p: Any, ok: bool) -> bool | None:
    """``ok`` live; None on the mock, whose volumes and boxes are made up."""
    return ok if p.live else None


def _metres(point: tuple[float, ...]) -> str:
    """An edge hint, ``"x,y,z"`` in metres, from a point in mm."""
    return ",".join(f"{c / 1000.0:.6g}" for c in point)


def _delta(reply: dict[str, Any]) -> float | None:
    """A feature reply's ``measured_change.volume_delta_mm3``."""
    value = (reply.get("measured_change") or {}).get("volume_delta_mm3")
    return float(value) if speed._number(value) else None


def _same_path(a: Any, b: Any) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(
        os.path.normpath(str(b))
    )


def _try(adapter: Any, fn: Any) -> tuple[Any, str | None]:
    """``fn()`` and None, or None and what it raised."""
    value, error = adapter._attempt_with_error(fn)
    return value, (None if error is None else f"{type(error).__name__}: {error}")


def raw_read(p: Any, r: Any, label: str, fn: Any) -> Any:
    """A raw COM read as one operation (``Probe.raw``, live only); None when it fails.

    The failure is recorded: a read that fails is a finding, not a reason to
    stop the check.
    """
    try:
        return p.raw(label, fn)
    except CheckFailed as exc:
        r.expect(f"the raw read {label} ran", False, str(exc))
        return None


def document_op(p: Any, operation: str, fn: Any) -> Any:
    """Raw COM work that activates or closes a document, live only.

    It runs under the adapter's own name for such work (``activate_document``
    or ``close_leftover_documents``, as the speed probe's ``close_new`` closes
    its documents), so it gets the document settle pause (pitfall #26) and the
    active-document check lets it through, as it does any document choice.
    """
    p.require_live(operation)
    result = p.adapter._handle_com_operation(operation, fn)
    if not result.is_success:
        raise CheckFailed(f"{operation}: {result.error}")
    return result.data


class _Tries(list):
    """A seed plan's feature lists, noting each one the adapter takes.

    The live adapter iterates them, one ``FeatureLinearPattern5`` (or
    circular pattern, or mirror) call each, stopping at the first that makes
    a feature; the mock takes the first by index.
    """

    def __init__(self, attempts: list[list[str]], tried: list[list[str]]) -> None:
        super().__init__(attempts)
        self._tried = tried

    def __iter__(self) -> Iterator[list[str]]:
        for attempt in super().__iter__():
            self._tried.append(list(attempt))
            yield attempt

    def __getitem__(self, index: Any) -> Any:
        item = super().__getitem__(index)
        if isinstance(index, int):
            self._tried.append(list(item))
        return item


@contextmanager
def seed_plans() -> Iterator[list[dict[str, Any]]]:
    """Each seed plan a pattern or mirror call makes, and each try it gets.

    ``pattern_seeds.SeedPlan.attempts`` is wrapped for the call (no COM): the
    plan's ``sure`` and ``unsure`` seeds, and in ``tries`` every feature list
    the adapter called SolidWorks with.
    """
    original = pattern_seeds.SeedPlan.attempts
    seen: list[dict[str, Any]] = []

    def attempts(plan: Any) -> list[list[str]]:
        entry: dict[str, Any] = {
            "listed": list(plan.listed),
            "sure": dict(plan.sure),
            "unsure": dict(plan.unsure),
            "patterns": list(plan.patterns),
            "threads": list(plan.threads),
            "tries": [],
        }
        seen.append(entry)
        return _Tries(original(plan), entry["tries"])

    pattern_seeds.SeedPlan.attempts = attempts
    try:
        yield seen
    finally:
        pattern_seeds.SeedPlan.attempts = original


async def watched(
    p: Any, tool: str, args: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """A pattern or mirror call's reply, and the seed plan it made."""
    with seed_plans() as plans:
        reply = await p.call(tool, args)
    return reply, (plans[-1] if plans else None)


def _left_out(data: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(item.get("feature")), str(item.get("patterned_by")))
        for item in data.get("left_out") or []
        if isinstance(item, dict)
    }


def _warned_left_out(reply: dict[str, Any]) -> bool:
    return "Left out" in str(reply.get("warning") or "")


async def open_rows(p: Any) -> list[dict[str, Any]]:
    """``list_open_documents``' rows (read-only), as a list: two may share a title."""
    listed = await p.adapter.list_open_documents()
    if not listed.is_success:
        return []
    return [d for d in listed.data or [] if isinstance(d, dict)]


def _is_open(rows: list[dict[str, Any]], path: str) -> bool:
    return any(_same_path(row.get("path"), path) for row in rows)


# ---- raw COM (live only) ----------------------------------------------------------


def read_pattern(adapter: Any, name: str) -> dict[str, Any]:
    """A feature's ``GetTypeName2``, its parents and its seed count, read raw.

    ``GetParents`` (each parent's ``Name`` and ``GetTypeName2``) and
    ``GetDefinition().GetPatternFeatureCount()``, as ``pattern_seeds`` reads
    them, but every parent kept, reference geometry too. Changes nothing.
    """
    model = adapter.currentModel
    sw_type_info.flag_members(model, "FeatureByName")
    feature, error = _try(adapter, lambda: model.FeatureByName(name))
    if feature is None:
        return {"feature": name, "found": False, "error": error}
    sw_type_info.flag_members(feature, "GetTypeName2", "GetParents", "GetDefinition")
    kind, _ = _try(adapter, lambda: feature.GetTypeName2())
    raw_parents, parents_error = _try(adapter, lambda: feature.GetParents())
    parents = []
    for parent in as_list(raw_parents):
        sw_type_info.flag_members(parent, "GetTypeName2")
        parent_name, _ = _try(adapter, lambda q=parent: q.Name)
        parent_type, _ = _try(adapter, lambda q=parent: q.GetTypeName2())
        parents.append({"name": parent_name, "type": parent_type})
    definition, definition_error = _try(adapter, lambda: feature.GetDefinition())
    count, count_error = None, definition_error
    if definition is not None:
        sw_type_info.flag_members(definition, "GetPatternFeatureCount")
        count, count_error = _try(adapter, lambda: definition.GetPatternFeatureCount())
    return {
        "feature": name,
        "found": True,
        "type": kind,
        "parents": parents,
        "parents_error": parents_error,
        "seed_count": count,
        "seed_count_error": count_error,
    }


def read_assembly_box(adapter: Any) -> dict[str, Any]:
    """``IAssemblyDoc.GetBox(0)`` in mm: the box the summary used before the fix."""
    assembly = sw_type_info.flagged(adapter.currentModel, "IAssemblyDoc")
    box, error = _try(adapter, lambda: assembly.GetBox(0))
    if not isinstance(box, (list, tuple)) or len(box) < 6:
        return {"raw": box, "error": error}
    low = [round(float(v) * 1000.0, 4) for v in box[:3]]
    high = [round(float(v) * 1000.0, 4) for v in box[3:6]]
    return {
        "min_mm": low,
        "max_mm": high,
        "size_mm": [round(h - lo, 4) for lo, h in zip(low, high, strict=True)],
    }


def set_suppression(adapter: Any, name: str, state: int) -> dict[str, Any]:
    """Set one component's ``SetSuppression2`` state, rebuild, and read it back."""
    model = adapter.currentModel
    component = component_visibility.find(adapter, model, [name])[name]
    sw_type_info.flag_members(component, "SetSuppression2", "GetSuppression2")
    before, _ = _try(adapter, lambda: component.GetSuppression2())
    returned, error = _try(adapter, lambda: component.SetSuppression2(state))
    adapter._attempt(lambda: model.EditRebuild3())
    after, _ = _try(adapter, lambda: component.GetSuppression2())
    return {"before": before, "returned": returned, "error": error, "after": after}


def read_title(adapter: Any) -> dict[str, Any]:
    """The active document's ``GetTitle`` and ``GetPathName``."""
    model = adapter.currentModel
    return {
        "title": adapter._get_attr_or_call(model, "GetTitle"),
        "path": adapter._get_attr_or_call(model, "GetPathName"),
    }


def _active_path(adapter: Any) -> str | None:
    active = adapter._attempt(lambda: adapter.swApp.ActiveDoc)
    if active is None:
        return None
    path = adapter._attempt(lambda: adapter._get_attr_or_call(active, "GetPathName"))
    return str(path) if path else None


def activate_by_path(adapter: Any, path: str) -> dict[str, Any]:
    """``ActivateDoc3(<full path>)``, no rebuild: what it returned and what is active."""
    before = _active_path(adapter)
    returned, error = _try(
        adapter,
        lambda: adapter.swApp.ActivateDoc3(path, False, 1, sw_io._byref_int()),
    )
    after = _active_path(adapter)
    adapter.currentModel = None  # the next tool call finds the active one
    return {
        "active_before": before,
        "returned_a_document": returned is not None,
        "error": error,
        "active_after": after,
        "took": _same_path(after, path),
    }


def close_doc(adapter: Any, name: str) -> dict[str, Any]:
    """``CloseDoc(name)``: what it returned."""
    returned, error = _try(adapter, lambda: adapter.swApp.CloseDoc(name))
    adapter.currentModel = None
    return {"name": name, "returned": returned, "error": error}


# ---- 1. pattern_seeds --------------------------------------------------------------


def _ring() -> list[tuple[float, float]]:
    """Vent_Ring's six vent centres, the seed first."""
    radius = math.dist(VENT, RING_CENTRE)
    start = math.atan2(VENT[1] - RING_CENTRE[1], VENT[0] - RING_CENTRE[0])
    return [
        (
            RING_CENTRE[0] + radius * math.cos(start + 2 * math.pi * k / RING_COUNT),
            RING_CENTRE[1] + radius * math.sin(start + 2 * math.pi * k / RING_COUNT),
        )
        for k in range(RING_COUNT)
    ]


def vent_layout(edge_sense: int) -> dict[str, list[tuple[float, float, float]]]:
    """The holes each step of ``pattern_seeds`` adds: (x, y, radius at the top) in mm.

    A vent's radius at the top face is its hole's plus the chamfer.

    Args:
        edge_sense: +1 or -1: which way along the plate's edge (+Y or -Y) the
            edge's pattern runs. SolidWorks picks it, so both must fit.
    """
    vent = VENT_R + CHAMFER
    ring = _ring()
    row = ring + [(x + ROW_X, y) for x, y in ring]
    edge = (VENT[0], VENT[1] + edge_sense * EDGE_STEP)
    return {
        "seed": [(*VENT, vent)],
        "ring": [(x, y, vent) for x, y in ring[1:]],
        "row_x": [(x + ROW_X, y, vent) for x, y in ring],
        "mirror": [(-x, y, vent) for x, y in ring],
        "rows_y": [(x, y + ROW_Y, vent) for x, y in row],
        "edge": [(*edge, VENT_R)],
        "edge_row": [
            (VENT[0] - EDGE_ROW, VENT[1], VENT_R),
            (edge[0] - EDGE_ROW, edge[1], VENT_R),
        ],
        "plain": [(VENT[0], VENT[1] + PLAIN_STEP, vent)],
    }


def layout_problems(clearance: float = 1.0) -> list[str]:
    """Holes off the plate, or within ``clearance`` mm of another, either way along the edge.

    The volume expectations count whole vents: a copy that overlapped
    another, or ran off the plate, would cut less.
    """
    x1, y1, x2, y2 = PLATE
    problems = []
    if not (
        EDGE_POINT[0] == x2 and y1 < EDGE_POINT[1] < y2 and EDGE_POINT[2] == PLATE_T
    ):
        problems.append(
            f"EDGE_POINT {EDGE_POINT} is not on the plate's x = {x2:g} top edge"
        )
    for sense in (1, -1):
        holes = [
            (step, x, y, r)
            for step, rows in vent_layout(sense).items()
            for x, y, r in rows
        ]
        for step, x, y, r in holes:
            inside = (
                x1 + r + clearance <= x <= x2 - r - clearance
                and y1 + r + clearance <= y <= y2 - r - clearance
            )
            if not inside:
                problems.append(f"{step}: ({x:.2f}, {y:.2f}) runs off the plate")
        for index, (one, ax, ay, ar) in enumerate(holes):
            for two, bx, by, br in holes[index + 1 :]:
                if math.dist((ax, ay), (bx, by)) < ar + br + clearance:
                    problems.append(
                        f"{one} ({ax:.2f}, {ay:.2f}) and {two} ({bx:.2f}, {by:.2f}) "
                        f"overlap (edge sense {sense:+d})"
                    )
    return sorted(set(problems))


def _cuts(step: str, vent: float, hole: float) -> float:
    """The volume step ``step`` should remove: its vents and plain holes, mm³."""
    return sum(vent if r > VENT_R else hole for _x, _y, r in vent_layout(1)[step])


async def check_pattern_seeds(p: Any, r: Any) -> None:
    await p.new_part("seeds")
    x1, y1, x2, y2 = PLATE
    await speed.block(p, x1, y1, x2, y2, PLATE_T)
    cut = await speed.circle_cut(p, VENT[0], VENT[1], VENT_R)
    cut_name = str((cut.get("cut_extrude") or {}).get("name"))
    await p.ok("rename_feature", {"old_name": cut_name, "new_name": "Vent_Hole"})
    rim = (VENT[0] + VENT_R, VENT[1], PLATE_T)
    chamfer = await p.ok(
        "add_chamfer", {"distance": CHAMFER, "edge_names": [_metres(rim)]}
    )
    chamfer_name = str((chamfer.get("chamfer") or {}).get("name"))
    await p.ok("rename_feature", {"old_name": chamfer_name, "new_name": "Vent_CS"})

    hole_cut, chamfer_cut = _delta(cut), _delta(chamfer)
    measured = hole_cut is not None and chamfer_cut is not None
    hole = -hole_cut if hole_cut is not None else HOLE
    vent = hole + (-chamfer_cut if chamfer_cut is not None else CHAMFER_RING)
    r.measure(
        "vent_mm3",
        {
            "hole_measured": None if hole_cut is None else -hole_cut,
            "chamfer_measured": None if chamfer_cut is None else -chamfer_cut,
            "hole_geometry": round(HOLE, 4),
            "chamfer_geometry": round(CHAMFER_RING, 4),
            "used": {"hole": round(hole, 4), "vent": round(vent, 4)},
        },
    )
    if not measured:
        r.note("a vent's volume didn't read from measured_change; its geometry is used")
    r.expect(
        f"a vent is the {2 * VENT_R:g} mm hole and its {CHAMFER:g} mm chamfer",
        _live(p, measured and speed._relative(vent, VENT_VOLUME, VOLUME_SHARE)),
        round(vent, 4),
        round(VENT_VOLUME, 4),
    )

    axis = await p.ok(
        "create_axis",
        {"point": [*RING_CENTRE, 0.0], "direction": [0.0, 0.0, 1.0]},
    )
    axis_name = str((axis.get("axis") or {}).get("name"))
    reply, plan = await watched(
        p,
        "pattern_circular",
        {"features": ["Vent_Hole", "Vent_CS"], "axis": axis_name, "count": RING_COUNT},
    )
    if reply.get("status") != "success":
        raise CheckFailed(
            f"pattern_circular of the seed vent failed ({reply.get('error_type')}): "
            f"{reply.get('message')}"
        )
    ring = reply.get("pattern") or {}
    r.measure("ring.seed_plan", plan)
    await p.ok(
        "rename_feature", {"old_name": str(ring.get("name")), "new_name": "Vent_Ring"}
    )
    r.expect(
        "Vent_Ring patterns the hole and its chamfer",
        ring.get("patterned") == ["Vent_Hole", "Vent_CS"] and not ring.get("left_out"),
        ring.get("patterned"),
    )
    r.expect(
        f"Vent_Ring cuts {RING_COUNT - 1} more vents",
        _live(
            p,
            speed._relative(
                ring.get("volume_change"), -_cuts("ring", vent, hole), VOLUME_SHARE
            ),
        ),
        ring.get("volume_change"),
        round(-_cuts("ring", vent, hole), 4),
    )
    r.time("ring.server_s", reply.get("server_time_s"))

    if p.live:
        read = raw_read(
            p, r, "read_vent_ring", lambda: read_pattern(p.adapter, "Vent_Ring")
        )
        r.measure("ring.raw", read)
        names = {
            str(parent.get("name")) for parent in (read or {}).get("parents") or []
        }
        r.expect(
            "Vent_Ring's GetParents: Vent_Hole, Vent_CS and the axis",
            names == {"Vent_Hole", "Vent_CS", axis_name},
            (read or {}).get("parents"),
            sorted({"Vent_Hole", "Vent_CS", axis_name}),
        )
        r.expect(
            "Vent_Ring's GetPatternFeatureCount is 2",
            (read or {}).get("seed_count") == 2,
            (read or {}).get("seed_count"),
            2,
        )
    else:
        r.note("Vent_Ring's GetParents and seed count: raw COM, live only")

    state: dict[str, Any] = {"vent": vent, "hole": hole}

    async def _row_along_x(p: Any, r: Any) -> None:
        """The three listed: only Vent_Ring patterned, its seeds left out."""
        reply, plan = await watched(
            p,
            "pattern_linear",
            {
                "features": ["Vent_Hole", "Vent_CS", "Vent_Ring"],
                "direction": "x",
                "count": 2,
                "spacing": ROW_X,
            },
        )
        r.measure("row_x.seed_plan", plan)
        r.measure(
            "row_x.reply", {k: reply.get(k) for k in ("status", "message", "warning")}
        )
        r.expect(
            "row_x: success", reply.get("status") == "success", reply.get("message")
        )
        data = reply.get("pattern") or {}
        state["row"] = data.get("name")
        r.expect(
            "row_x: patterned is [Vent_Ring]",
            data.get("patterned") == ["Vent_Ring"],
            data.get("patterned"),
        )
        r.expect(
            "row_x: left_out names both seeds, patterned_by Vent_Ring",
            _left_out(data) == {("Vent_Hole", "Vent_Ring"), ("Vent_CS", "Vent_Ring")},
            data.get("left_out"),
        )
        r.expect(
            "row_x: a warning says so", _warned_left_out(reply), reply.get("warning")
        )
        want = -_cuts("row_x", state["vent"], state["hole"])
        r.expect(
            "row_x: 6 vents",
            _live(p, speed._relative(_delta(reply), want, VOLUME_SHARE)),
            _delta(reply),
            round(want, 4),
        )
        r.measure("row_x.tries", len((plan or {}).get("tries") or []))
        r.time("row_x.server_s", reply.get("server_time_s"))

    async def _mirror(p: Any, r: Any) -> None:
        """The three mirrored as features: only Vent_Ring mirrored."""
        reply, plan = await watched(
            p,
            "mirror_feature",
            {
                "features": ["Vent_Hole", "Vent_CS", "Vent_Ring"],
                "mirror_plane": "Right Plane",
                "mirror_bodies": False,
            },
        )
        r.measure("mirror.seed_plan", plan)
        r.expect(
            "mirror: success", reply.get("status") == "success", reply.get("message")
        )
        data = reply.get("mirror") or {}
        r.expect(
            "mirror: mirrored is [Vent_Ring]",
            data.get("mirrored") == ["Vent_Ring"],
            data.get("mirrored"),
        )
        r.expect(
            "mirror: left_out names both seeds",
            _left_out(data) == {("Vent_Hole", "Vent_Ring"), ("Vent_CS", "Vent_Ring")},
            data.get("left_out"),
        )
        want = -_cuts("mirror", state["vent"], state["hole"])
        r.expect(
            "mirror: 6 vents",
            _live(p, speed._relative(data.get("volume_change"), want, VOLUME_SHARE)),
            data.get("volume_change"),
            round(want, 4),
        )
        r.time("mirror.server_s", reply.get("server_time_s"))

    async def _nested(p: Any, r: Any) -> None:
        """Row_x listed with the three it repeats: only row_x patterned."""
        row = state.get("row")
        if not row:
            raise CheckFailed("row_x made no pattern to list")
        listed = ["Vent_Hole", "Vent_CS", "Vent_Ring", str(row)]
        reply, plan = await watched(
            p,
            "pattern_linear",
            {"features": listed, "direction": "y", "count": 2, "spacing": ROW_Y},
        )
        r.measure("nested.seed_plan", plan)
        r.expect(
            "nested: success", reply.get("status") == "success", reply.get("message")
        )
        data = reply.get("pattern") or {}
        r.expect(
            f"nested: patterned is [{row}]",
            data.get("patterned") == [row],
            data.get("patterned"),
        )
        r.expect(
            "nested: the other three are left out",
            {feature for feature, _by in _left_out(data)}
            == {"Vent_Hole", "Vent_CS", "Vent_Ring"},
            data.get("left_out"),
        )
        want = -_cuts("rows_y", state["vent"], state["hole"])
        r.expect(
            "nested: 12 vents",
            _live(p, speed._relative(_delta(reply), want, VOLUME_SHARE)),
            _delta(reply),
            round(want, 4),
        )
        r.time("nested.server_s", reply.get("server_time_s"))

    async def _unsure_edge(p: Any, r: Any) -> None:
        """A pattern along an edge, then listed with its seed: the unsure path."""
        edge = await p.ok(
            "pattern_linear",
            {
                "features": ["Vent_Hole"],
                "direction": ",".join(f"{c:g}" for c in EDGE_POINT),
                "count": 2,
                "spacing": EDGE_STEP,
            },
        )
        data = edge.get("pattern") or {}
        name = str(data.get("name"))
        r.measure("edge.direction", data.get("direction"))
        want = -_cuts("edge", state["vent"], state["hole"])
        r.expect(
            "edge: one hole",
            _live(p, speed._relative(_delta(edge), want, VOLUME_SHARE)),
            _delta(edge),
            round(want, 4),
        )
        read: dict[str, Any] = {}
        if p.live:
            read = (
                raw_read(
                    p, r, "read_edge_pattern", lambda: read_pattern(p.adapter, name)
                )
                or {}
            )
            r.measure("edge.raw", read)
        else:
            r.note(f"{name}'s GetParents and seed count: raw COM, live only")
        reply, plan = await watched(
            p,
            "pattern_linear",
            {
                "features": ["Vent_Hole", name],
                "direction": "-x",
                "count": 2,
                "spacing": EDGE_ROW,
            },
        )
        tries = (plan or {}).get("tries") or []
        r.measure("edge_row.seed_plan", plan)
        r.measure("edge_row.feature_calls", len(tries))
        refused = len(tries) > 1
        r.measure("edge_row.solidworks_refused_the_first_try", refused)
        if read:
            seeds = [
                parent
                for parent in read.get("parents") or []
                if parent.get("type") not in pattern_seeds.NOT_SEEDS
            ]
            unsure = not (
                isinstance(read.get("seed_count"), int | float)
                and len(seeds) <= read["seed_count"]
            )
            r.measure("edge.unsure_by_raw_reading", unsure)
            planned = (plan or {}).get("unsure") or {}
            r.expect(
                "the seed plan reads the edge pattern as the raw reading does",
                ("Vent_Hole" in planned) == unsure,
                {"plan_unsure": planned, "raw_unsure": unsure},
            )
            r.note(
                f"{name}: {len(seeds)} parent(s) that may be seeds, seed count "
                f"{read.get('seed_count')!r}: the hole was "
                + ("unsure" if unsure else "sure")
                + (
                    f"; {len(tries)} FeatureLinearPattern5 call(s)"
                    + (" (SolidWorks refused the first)" if refused else "")
                )
            )
        r.expect(
            "edge_row: success", reply.get("status") == "success", reply.get("message")
        )
        row = reply.get("pattern") or {}
        r.expect(
            f"edge_row: left_out [Vent_Hole], patterned_by {name}",
            _left_out(row) == {("Vent_Hole", name)},
            row.get("left_out"),
        )
        r.expect(
            f"edge_row: patterned is [{name}]",
            row.get("patterned") == [name],
            row.get("patterned"),
        )
        want = -_cuts("edge_row", state["vent"], state["hole"])
        r.expect(
            "edge_row: two holes",
            _live(p, speed._relative(_delta(reply), want, VOLUME_SHARE)),
            _delta(reply),
            round(want, 4),
        )
        r.time("edge_row.server_s", reply.get("server_time_s"))

    async def _plain(p: Any, r: Any) -> None:
        """A hole and its chamfer, no pattern listed: both patterned."""
        reply, plan = await watched(
            p,
            "pattern_linear",
            {
                "features": ["Vent_Hole", "Vent_CS"],
                "direction": "y",
                "count": 2,
                "spacing": PLAIN_STEP,
            },
        )
        r.measure("plain.seed_plan", plan)
        r.expect(
            "plain: success", reply.get("status") == "success", reply.get("message")
        )
        data = reply.get("pattern") or {}
        r.expect(
            "plain: patterned is [Vent_Hole, Vent_CS], nothing left out",
            data.get("patterned") == ["Vent_Hole", "Vent_CS"]
            and "left_out" not in data
            and not _warned_left_out(reply),
            {"patterned": data.get("patterned"), "left_out": data.get("left_out")},
        )
        want = -_cuts("plain", state["vent"], state["hole"])
        r.expect(
            "plain: one vent",
            _live(p, speed._relative(_delta(reply), want, VOLUME_SHARE)),
            _delta(reply),
            round(want, 4),
        )

    await speed.run_parts(p, r, _row_along_x, _mirror, _nested, _unsure_edge, _plain)


# ---- 2. assembly_box -----------------------------------------------------------------


def placed_box(
    box: tuple[float, ...], rows: list[list[float]], origin: tuple[float, ...]
) -> tuple[list[float], list[float]]:
    """A part box (low then high corner, mm) placed by ``rows`` and ``origin``: its low, high."""
    corners = [
        (x, y, z)
        for x in (box[0], box[3])
        for y in (box[1], box[4])
        for z in (box[2], box[5])
    ]
    placed = [transform_point(corner, rows, origin) for corner in corners]
    return (
        [min(point[axis] for point in placed) for axis in range(3)],
        [max(point[axis] for point in placed) for axis in range(3)],
    )


def union(*boxes: tuple[list[float], list[float]]) -> tuple[list[float], list[float]]:
    return (
        [min(low[axis] for low, _high in boxes) for axis in range(3)],
        [max(high[axis] for _low, high in boxes) for axis in range(3)],
    )


async def _verify_box(
    p: Any, r: Any, label: str, want: tuple[list[float], list[float]]
) -> dict[str, Any]:
    """``verify_model`` with the size ``want`` gives; the box and method checked."""
    low, high = want
    size = [round(h - lo, 6) for lo, h in zip(low, high, strict=True)]
    reply = await p.ok(
        "verify_model",
        {"bounding_box_mm": size, "length_tolerance_mm": BOX_TOLERANCE},
    )
    measured = reply.get("measured") or {}
    box = measured.get("bounding_box_mm") or {}
    method = measured.get("bounding_box_method")
    r.measure(f"{label}.measured", {"box": box, "method": method})
    r.expect(
        f"{label}: bounding_box_method is {assembly_box.METHOD}",
        _live(p, method == assembly_box.METHOD),
        method,
    )
    corners = (box.get("min"), box.get("max"))
    tight = all(
        isinstance(got, list)
        and len(got) == 3
        and all(
            speed._close(a, b, BOX_TOLERANCE) for a, b in zip(got, wanted, strict=True)
        )
        for got, wanted in zip(corners, (low, high), strict=True)
    )
    r.expect(
        f"{label}: the box is {size} mm, within {BOX_TOLERANCE} mm",
        _live(p, tight and reply.get("verdict") == "pass"),
        {"min": box.get("min"), "max": box.get("max"), "verdict": reply.get("verdict")},
        {"min": [round(v, 4) for v in low], "max": [round(v, 4) for v in high]},
    )
    if p.live:
        r.measure(
            f"{label}.get_box_0",
            raw_read(p, r, "get_box", lambda: read_assembly_box(p.adapter)),
        )
    r.time(f"{label}.server_s", reply.get("server_time_s"))
    return reply


async def check_assembly_box(p: Any, r: Any) -> None:
    block_path = await p.new_part("box_block")
    x1, y1, x2, y2 = BLOCK
    await speed.block(p, x1, y1, x2, y2, BLOCK_T)
    axis = await p.ok("create_axis", BLOCK_AXIS)
    r.measure(
        "axis", {k: (axis.get("axis") or {}).get(k) for k in ("name", "read_back")}
    )
    await p.save_in_place()
    await p.ok("close_model", {})
    await p.new_assembly("box")
    first = await p.ok("insert_component", {"file_path": str(block_path)})
    first_name = str((first.get("component") or {}).get("component"))
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    alone = placed_box(BLOCK_BOX, identity, (0.0, 0.0, 0.0))
    reply = await _verify_box(p, r, "one", alone)
    if p.live:
        solid = ((reply.get("measured") or {}).get("bounding_box_mm") or {}).get("size")
        old = (r.measurements.get("one.get_box_0") or {}).get("size_mm")
        looser = (
            isinstance(old, list)
            and isinstance(solid, list)
            and any(
                not speed._close(a, b, BOX_TOLERANCE)
                for a, b in zip(old, solid, strict=False)
            )
        )
        r.measure("one.get_box_0_looser", looser)
        r.note(
            f"GetBox(0) reads {old} mm where the solids are {solid} mm"
            + (": the fix was needed here" if looser else "")
        )

    second = await p.ok(
        "insert_component",
        {
            "file_path": str(block_path),
            "x": COPY_ORIGIN[0],
            "y": COPY_ORIGIN[1],
            "z": COPY_ORIGIN[2],
        },
    )
    copy = str((second.get("component") or {}).get("component"))
    placed = await p.ok(
        "set_component_transform",
        {
            "component": copy,
            "origin_mm": list(COPY_ORIGIN),
            "rotation_deg": list(COPY_ROTATION),
        },
    )
    r.measure("copy.transform", placed.get("transform"))
    r.measure("components", [first_name, copy])
    rows = rotation_rows_from_deg(*COPY_ROTATION)
    both = union(alone, placed_box(BLOCK_BOX, rows, COPY_ORIGIN))
    await _verify_box(p, r, "union", both)

    await p.ok("set_component_visibility", {"components": [copy], "visible": False})
    await _verify_box(p, r, "copy_hidden", alone)
    await p.ok("set_component_visibility", {"components": [copy], "visible": True})
    await _verify_box(p, r, "copy_shown", both)

    await speed.run_parts(p, r, _suppressed(copy, alone))
    fields = set(SetComponentStateInput.model_fields)
    r.note(
        "lightweight: skipped; set_component_state takes "
        + ", ".join(sorted(fields))
        + " and no tool makes a component lightweight, so the GetBox(0) fallback "
        "(assembly_box) isn't reached (raw IComponent2.SetSuppression2(1) would)"
    )


def _suppressed(copy: str, alone: tuple[list[float], list[float]]) -> Any:
    async def _copy_suppressed(p: Any, r: Any) -> None:
        """The copy suppressed: by suppress_feature of its name, else raw."""
        tried = await p.call("suppress_feature", {"name": copy})
        r.measure(
            "suppress.tool_reply",
            {k: tried.get(k) for k in ("status", "error_type", "message", "data")},
        )
        how = "suppress_feature"
        if tried.get("status") != "success":
            r.note(
                f"suppress_feature {copy!r} failed ({tried.get('error_type')}): "
                f"{tried.get('message')}"
            )
            if not p.live:
                r.note("suppressing a component raw: live only")
                return
            how = "IComponent2.SetSuppression2(0)"
            r.measure(
                "suppress.raw",
                p.raw("suppress", lambda: set_suppression(p.adapter, copy, SUPPRESSED)),
            )
        r.measure("suppress.how", how)
        await _verify_box(p, r, "copy_suppressed", alone)
        if how == "suppress_feature":
            await p.ok("suppress_feature", {"name": copy, "suppress": False})
        else:
            p.raw("resolve", lambda: set_suppression(p.adapter, copy, RESOLVED))

    return _copy_suppressed


# ---- 3. same_title -------------------------------------------------------------------


async def check_same_title(p: Any, r: Any) -> None:
    insert = await p.new_part("TrayInsert")
    await speed.block(p, 0, 0, 20, 20, 5)
    await p.save_in_place()
    await p.ok("close_model", {})
    part_file = await p.new_part("Tray")
    await speed.block(p, 0, 0, 30, 30, 6)
    await p.save_in_place()
    # Read while the part is the only Tray: its title as saved.
    read = (
        raw_read(p, r, "read_title", lambda: read_title(p.adapter)) if p.live else None
    )
    asm_file = await p.new_assembly("Tray")
    await p.ok("insert_component", {"file_path": str(insert)})
    await p.save_in_place()
    if part_file.stem != asm_file.stem:
        raise CheckFailed(
            f"the part and the assembly got two names: {part_file.name}, {asm_file.name}"
        )
    paths = {"part": str(part_file), "asm": str(asm_file)}
    hid = p.adapter.__dict__.get("hide_extensions")
    try:
        if not p.live:
            # The mock's save keeps its own title, and its file-name match
            # splits on "/": open both as SolidWorks titles them with
            # extensions hidden (its test hook), at forward-slash paths.
            p.adapter.hide_extensions = True
            paths = {key: Path(path).as_posix() for key, path in paths.items()}
            for path in paths.values():
                opened = await p.adapter.open_model(path)
                if not opened.is_success:
                    raise CheckFailed(f"the mock didn't open {path}: {opened.error}")
            rows = await open_rows(p)
            read = next(
                (row for row in rows if _same_path(row.get("path"), paths["part"])), {}
            )
        parts = _same_title_parts(paths, part_file.stem, read or {})
        await speed.run_parts(p, r, *parts)
    finally:
        if not p.live:
            p.adapter.hide_extensions = bool(hid)


async def _reopen(p: Any, paths: dict[str, str]) -> list[str]:
    """Open again whichever of ``paths`` is closed; the ones opened."""
    opened = []
    for path in paths.values():
        if not _is_open(await open_rows(p), path):
            await p.ok("open_model", {"file_path": path})
            opened.append(Path(path).name)
    return opened


def _same_title_parts(
    paths: dict[str, str], stem: str, read: dict[str, Any]
) -> tuple[Any, ...]:
    """The steps of ``same_title``, each a part: one that fails leaves the next to run."""
    part, asm = paths["part"], paths["asm"]
    part_name, asm_name = Path(part).name, Path(asm).name

    async def _bare_name(p: Any, r: Any) -> None:
        """The name both documents answer to is refused."""
        rows = await open_rows(p)
        title = str(read.get("title") or "")
        hidden = bool(title) and not title.lower().endswith(Path(part).suffix.lower())
        r.measure("part_title", read)
        r.measure("extension_hidden", hidden)
        r.measure(
            "open", [{k: row.get(k) for k in ("title", "path", "type")} for row in rows]
        )
        if not hidden:
            r.note(
                f"the saved part is titled {title!r}, with its extension: the part "
                "and the assembly can't share a title here, so the ambiguity isn't "
                "checked"
            )
            return
        bare = await p.call("activate_document", {"title_or_path": stem})
        message = str(bare.get("message") or "")
        r.expect(
            f"activate_document {stem!r}: invalid_input",
            bare.get("error_type") == "invalid_input",
            (bare.get("status"), bare.get("error_type")),
        )
        r.expect(
            "the refusal names both files",
            part_name in message and asm_name in message,
            message,
        )

    async def _file_names(p: Any, r: Any) -> None:
        """Each file name, with its extension, activates its own document."""
        for label, name, path, kind in (
            ("assembly", asm_name, asm, "Assembly"),
            ("part", part_name, part, "Part"),
        ):
            activated = await p.call("activate_document", {"title_or_path": name})
            data = activated.get("data") or {}
            r.expect(
                f"activate_document {name!r}: verified",
                activated.get("status") == "success" and data.get("verified") is True,
                {
                    "status": activated.get("status"),
                    "data": data,
                    "message": activated.get("message"),
                },
            )
            info = await p.ok("get_model_info")
            model = info.get("model_info") or {}
            r.expect(
                f"get_model_info then reports the {label}",
                model.get("type") == kind and _same_path(model.get("path"), path),
                {"type": model.get("type"), "path": model.get("path")},
            )
        rows = await open_rows(p)
        active = [row.get("path") for row in rows if row.get("is_active")]
        r.expect(
            "list_open_documents marks only the part active",
            len(active) == 1 and _same_path(active[0], part),
            active,
        )

    async def _raw_names(p: Any, r: Any) -> None:
        """Does ActivateDoc3 take a path; does CloseDoc with a path, or a file name, close only it.

        Each answer is recorded, and whatever a call closed is opened again,
        so the next starts with both documents open.
        """
        if not p.live:
            r.note(
                "ActivateDoc3 and CloseDoc with a path or file name: raw COM, live only"
            )
            return
        answers: dict[str, Any] = {}
        try:
            await p.ok("activate_document", {"title_or_path": part_name})
            answers["ActivateDoc3(path)"] = document_op(
                p, "activate_document", lambda: activate_by_path(p.adapter, asm)
            )
            for label, name, target in (
                ("CloseDoc(path)", asm, "asm"),
                ("CloseDoc(file name)", part_name, "part"),
            ):
                answer = document_op(
                    p,
                    "close_leftover_documents",
                    lambda n=name: close_doc(p.adapter, n),
                )
                rows = await open_rows(p)
                still = {key: _is_open(rows, path) for key, path in paths.items()}
                answer["still_open"] = still
                answer["closed_it"] = not still[target]
                answer["closed_only_it"] = not still[target] and all(
                    still[key] for key in paths if key != target
                )
                answers[label] = answer
                answer["reopened"] = await _reopen(p, paths)
        finally:
            r.measure("raw_names", answers)
        for label, answer in answers.items():
            took = answer.get("took", answer.get("closed_only_it"))
            r.note(f"{label}: {'yes' if took else 'no'}")

    async def _close_the_assembly(p: Any, r: Any) -> None:
        """close_model with document the assembly closes it alone."""
        r.measure("reopened_before_close", await _reopen(p, paths))
        closed = await p.call("close_model", {}, document=asm_name)
        r.expect(
            f"close_model {asm_name!r}: closed is [{asm_name}]",
            closed.get("status") == "success" and closed.get("closed") == [asm_name],
            {
                "closed": closed.get("closed"),
                "warning": closed.get("warning"),
                "message": closed.get("message"),
            },
        )
        rows = await open_rows(p)
        r.expect(
            "the part is still open, the assembly isn't",
            _is_open(rows, part) and not _is_open(rows, asm),
            [row.get("path") for row in rows],
        )

    return (_bare_name, _file_names, _raw_names, _close_the_assembly)


# ---- 4. interference_between ----------------------------------------------------------


def _box_of(block: tuple[float, ...], origin: tuple[float, ...]) -> tuple[float, ...]:
    x1, y1, x2, y2, depth = block
    return (
        x1 + origin[0],
        y1 + origin[1],
        origin[2],
        x2 + origin[0],
        y2 + origin[1],
        depth + origin[2],
    )


def overlap(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """The volume two boxes (low then high corner) share, mm³."""
    volume = 1.0
    for axis in range(3):
        volume *= max(0.0, min(a[axis + 3], b[axis + 3]) - max(a[axis], b[axis]))
    return volume


def _pairs(data: dict[str, Any]) -> dict[frozenset[str], float]:
    """Each pair of components the interferences name, with their volume, mm³."""
    found: dict[frozenset[str], float] = {}
    for item in data.get("interferences") or []:
        names = frozenset(str(n) for n in item.get("components") or [])
        volume = item.get("volume_mm3")
        found[names] = found.get(names, 0.0) + (
            float(volume) if speed._number(volume) else 0.0
        )
    return found


async def check_interference_between(p: Any, r: Any) -> None:
    tray_path = await p.new_part("ifr_tray")
    await speed.block(p, *TRAY)
    await p.save_in_place()
    await p.ok("close_model", {})
    nut_path = await p.new_part("ifr_nut")
    await speed.block(p, *NUT)
    await p.save_in_place()
    await p.ok("close_model", {})
    await p.new_assembly("interference")
    names: dict[str, str] = {}
    for key, path, at in (
        ("tray", tray_path, (0.0, 0.0, 0.0)),
        ("nut1", nut_path, (100.0, 0.0, 0.0)),
        ("nut2", nut_path, (130.0, 0.0, 0.0)),
    ):
        placed = await p.ok(
            "insert_component",
            {"file_path": str(path), "x": at[0], "y": at[1], "z": at[2]},
        )
        names[key] = str((placed.get("component") or {}).get("component"))
    for key, origin in NUT_ORIGINS.items():
        moved = await p.ok(
            "set_component_transform",
            {"component": names[key], "origin_mm": list(origin)},
        )
        r.measure(f"{key}.transform", moved.get("transform"))
    await p.save_in_place()
    r.measure("components", names)
    boxes = {
        "tray": _box_of(TRAY, (0.0, 0.0, 0.0)),
        **{key: _box_of(NUT, origin) for key, origin in NUT_ORIGINS.items()},
    }
    want = {
        frozenset({names[a], names[b]}): overlap(boxes[a], boxes[b])
        for a, b in (("tray", "nut1"), ("tray", "nut2"), ("nut1", "nut2"))
    }
    r.measure(
        "overlaps_mm3",
        {" & ".join(sorted(pair)): volume for pair, volume in want.items()},
    )
    tray, nut1, nut2 = names["tray"], names["nut1"], names["nut2"]
    for label, components, pairs in (
        ("none", [], set(want)),
        ("nut1_tray", [nut1, tray], {frozenset({nut1, tray})}),
        ("tray", [tray], {frozenset({tray, nut1}), frozenset({tray, nut2})}),
        ("all_three", [nut1, nut2, tray], set(want)),
    ):
        reply = await p.ok(
            "check_interference", {"components": components} if components else {}
        )
        data = reply.get("data") or {}
        found = _pairs(data)
        r.measure(
            f"{label}.reply",
            {
                k: data.get(k)
                for k in (
                    "interference_found",
                    "interference_count",
                    "scope",
                    "interferences",
                )
            },
        )
        r.time(f"{label}.server_s", reply.get("server_time_s"))
        r.expect(
            f"{label}: measured in the kernel, without SolidWorks' UI",
            data.get("method") == "body_intersection",
            data.get("method"),
            "body_intersection",
        )
        if not p.live:
            # The mock answers from its components' boxes, not the blocks'.
            r.expect(
                f"{label}: the mock gives a count",
                isinstance(data.get("interference_count"), int),
                data.get("interference_count"),
            )
            continue
        r.expect(
            f"{label}: the pairs are {len(pairs)}",
            set(found) == pairs,
            sorted(" & ".join(sorted(pair)) for pair in found),
            sorted(" & ".join(sorted(pair)) for pair in pairs),
        )
        r.expect(
            f"{label}: each pair's volume is the blocks' overlap",
            all(
                speed._relative(found.get(pair), want[pair], OVERLAP_SHARE)
                for pair in pairs
            ),
            {" & ".join(sorted(pair)): found.get(pair) for pair in pairs},
            {" & ".join(sorted(pair)): want[pair] for pair in pairs},
        )
    if not p.live:
        r.note("pairs and volumes: live only; the mock's boxes aren't the blocks'")


# ---- 5. thread_pattern -----------------------------------------------------------------


async def check_thread_pattern(p: Any, r: Any) -> None:
    await p.new_part("thread")
    x1, y1, x2, y2 = THREAD_PLATE
    await speed.block(p, x1, y1, x2, y2, THREAD_PLATE_T)
    posts = await p.ok("create_sketch", {"plane": "Front"})
    await p.ok("add_sketch_geometry", {"circles": POSTS})
    await p.ok("exit_sketch")
    await p.ok(
        "create_extrusion",
        {"depth": POST_HEIGHT, "sketch_name": posts["sketch"]["name"]},
    )
    made = await p.ok("create_thread", THREAD)
    data = made.get("data") or {}
    thread = str(data.get("feature"))
    r.measure(
        "thread",
        {
            k: data.get(k)
            for k in ("feature", "thread", "edge", "extent", "measured_change", "note")
        },
    )
    r.time("thread.server_s", made.get("server_time_s"))
    axis = await p.ok(
        "create_axis", {"point": [0.0, 0.0, 0.0], "direction": [0.0, 0.0, 1.0]}
    )
    axis_name = str((axis.get("axis") or {}).get("name"))
    reply, plan = await watched(
        p,
        "pattern_circular",
        {"features": [thread], "axis": axis_name, "count": len(POSTS)},
    )
    r.measure("pattern.seed_plan", plan)
    r.measure(
        "pattern.reply",
        {
            k: reply.get(k)
            for k in ("status", "error_type", "message", "hint", "warning")
        },
    )
    r.time("pattern.server_s", reply.get("server_time_s"))
    if reply.get("status") == "success":
        r.measure("outcome", "SolidWorks patterned the thread")
        r.note("SolidWorks patterned the thread: the hint never fires")
        pattern = reply.get("pattern") or {}
        r.measure("pattern.volume_change", pattern.get("volume_change"))
        r.expect(
            "the pattern made: SolidWorks patterned the thread",
            True,
            pattern.get("patterned"),
        )
        return
    message, hint = str(reply.get("message") or ""), str(reply.get("hint") or "")
    empty = "produced no geometry" in message
    r.measure(
        "outcome",
        "SolidWorks made an empty pattern"
        if empty
        else "SolidWorks refused the pattern",
    )
    if empty:
        r.note(
            "FeatureCircularPattern5 returned a pattern that cut nothing: the Thread "
            "hint, which comes with a call that returns nothing, doesn't apply"
        )
    r.expect(
        "refused: operation_failed",
        reply.get("error_type") == "operation_failed",
        reply.get("error_type"),
    )
    r.expect(f"the message names {thread}", thread in message, message)
    r.expect(
        "the hint says to cut each thread with its own create_thread",
        "create_thread" in hint,
        hint,
    )


# ---- the plan --------------------------------------------------------------------------

_BUILD = (
    "create_part",
    "create_sketch",
    "add_rectangle",
    "exit_sketch",
    "create_extrusion",
)

CHECKS: dict[str, Any] = {
    check.name: check
    for check in (
        Check(
            "pattern_seeds",
            check_pattern_seeds,
            "patterns and a mirror leave out the seeds a listed pattern repeats",
            (
                *_BUILD,
                "add_circle",
                "create_cut_extrude",
                "rename_feature",
                "add_chamfer",
                "create_axis",
                "pattern_circular",
                "pattern_linear",
                "mirror_feature",
            ),
            ("AggrewellProbe_seeds",),
            2.0,
            raw_com="GetParents, GetTypeName2, GetPatternFeatureCount of two patterns",
        ),
        Check(
            "assembly_box",
            check_assembly_box,
            "an assembly's box is its shown, unsuppressed components' solids",
            (
                *_BUILD,
                "create_axis",
                "close_model",
                "create_assembly",
                "insert_component",
                "set_component_transform",
                "set_component_visibility",
                "suppress_feature",
                "verify_model",
            ),
            ("AggrewellProbe_box_block", "AggrewellProbe_box"),
            1.5,
            raw_com="IAssemblyDoc.GetBox(0); IComponent2.SetSuppression2 if suppress_feature can't",
        ),
        Check(
            "same_title",
            check_same_title,
            "a part and an assembly of one title are told apart",
            (
                *_BUILD,
                "close_model",
                "create_assembly",
                "insert_component",
                "activate_document",
                "get_model_info",
                "open_model",
            ),
            (
                "AggrewellProbe_TrayInsert",
                "AggrewellProbe_Tray.SLDPRT",
                "AggrewellProbe_Tray.SLDASM",
            ),
            1.5,
            raw_com="GetTitle; ActivateDoc3(path), CloseDoc(path), CloseDoc(file name)",
        ),
        Check(
            "interference_between",
            check_interference_between,
            "check_interference with components gives those among them",
            (
                *_BUILD,
                "close_model",
                "create_assembly",
                "insert_component",
                "set_component_transform",
                "check_interference",
            ),
            (
                "AggrewellProbe_ifr_tray",
                "AggrewellProbe_ifr_nut",
                "AggrewellProbe_interference",
            ),
            1.0,
        ),
        Check(
            "thread_pattern",
            check_thread_pattern,
            "a refused pattern of a Thread feature names it, and the hint says create_thread",
            (
                *_BUILD,
                "add_sketch_geometry",
                "create_thread",
                "create_axis",
                "pattern_circular",
            ),
            ("AggrewellProbe_thread",),
            1.0,
        ),
    )
}

#: The adapter and module members the checks reach past the tools.
MEMBERS = (
    (PyWin32Adapter, "_handle_com_operation"),
    (PyWin32Adapter, "_attempt"),
    (PyWin32Adapter, "_attempt_with_error"),
    (PyWin32Adapter, "_get_attr_or_call"),
    (PyWin32Adapter, "list_open_documents"),
    (PyWin32Adapter, "open_model"),
    (PyWin32Adapter, "save_file"),
    (pattern_seeds.SeedPlan, "attempts"),
    (pattern_seeds, "NOT_SEEDS"),
    (assembly_box, "METHOD"),
    (component_visibility, "find"),
    (sw_io, "_byref_int"),
    (speed.Probe, "require_live"),
    (speed, "block"),
    (speed, "circle_cut"),
    (speed, "run_parts"),
)
#: COM methods the raw parts call, checked in the type library wrapper.
COM_MEMBERS = {
    "IPartDoc": ("FeatureByName",),
    "IModelDoc2": ("GetTitle", "GetPathName", "EditRebuild3"),
    "IFeature": ("GetParents", "GetTypeName2", "GetDefinition"),
    "ICircularPatternFeatureData": ("GetPatternFeatureCount",),
    "ILinearPatternFeatureData": ("GetPatternFeatureCount",),
    "ISldWorks": ("ActivateDoc3", "CloseDoc"),
    "IAssemblyDoc": ("GetBox",),
    "IComponent2": ("SetSuppression2", "GetSuppression2"),
}


async def _record_layout(p: Any, report: dict[str, Any]) -> None:
    """The vents' layout and its check, in the report."""
    report["vent_layout"] = {
        f"edge {'+' if sense > 0 else '-'}y": {
            step: [[round(c, 3) for c in hole] for hole in holes]
            for step, holes in vent_layout(sense).items()
        }
        for sense in (1, -1)
    }
    report["layout_problems"] = layout_problems()


AGGREWELL = speed.ProbeKind(
    tag=TAG,
    prefix=PREFIX,
    default_out=DEFAULT_OUT,
    about="the Aggrewell fixes",
    checks=CHECKS,
    members=MEMBERS,
    com_members=COM_MEMBERS,
    prepare=_record_layout,
)


def main(argv: list[str] | None = None) -> int:
    problems = layout_problems()
    if problems:
        for problem in problems:
            print(f"[{TAG}] LAYOUT: {problem}")
        print(f"[{TAG}] the vents overlap or leave the plate; nothing was run")
        return 2
    return speed.main(argv, AGGREWELL)


if __name__ == "__main__":
    sys.exit(main())
