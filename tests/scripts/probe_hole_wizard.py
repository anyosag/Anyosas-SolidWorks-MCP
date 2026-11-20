"""Probe the Hole Wizard and the Thread feature (``hole-wizard-and-threads`` task group 1).

Written without SolidWorks (the code in ``adapters/solidworks/holes.py`` and
``threads.py`` was built on the API help and the type library alone). Run it
on scratch parts, with the user's OK, to settle what ``design.md`` leaves to
the probes. Each step records what it got; the report keeps everything, and
nothing here is settled until a run's report says so.

Scratch parts (each step builds its own): a 40 x 20 x 10 mm block on Front
(top face at z = 10), a Ø10 x 40 mm rod on Front (end edge at z = 40), and a
30 x 30 x 20 mm block with a hole through it at (15, 15).

1.1 ``HoleWizard5``: its argument count in the type library (27 expected);
    each kind through and blind with the D4 vectors, the volume removed and
    the sizes read back; then one slot at a time: ``Value1`` 0, -1 and the fit
    on a clearance hole; ``Value7``/``Value8`` 0 and -1 on a tapped hole.
1.2 Picking the face: ``SelectByRay`` from 0.01 mm above the point, the
    selection count with and without ``ClearSelection2`` first (pitfall #59);
    in the left view (``ShowNamedView2``), on a top split by a slot, and over
    an earlier countersink.
1.3 Where the point lands: a hole at [20, 14, 10], its edge's centre (hjbaard
    saw about 0.04 mm off); the position sketch (sub-features, its points and
    segments, ``ModelToSketchTransform``); ``pin_position`` and the sketch's
    ``GetConstrainedStatus``; ``set_dimension`` on the x dimension moves the
    hole; whether ``HoleWizard5`` takes pre-selected sketch points as well.
1.4 The sizes: every ``IWizardHoleFeatureData2`` property the code reads, for
    each kind and fit of M3 (to confirm 3.2 / 3.4 / 3.6, Ø6.5 x 3.4, Ø6.72 at
    90°, tap drill 2.5 and thread depth 6.0), and M4, M5, M30; ``M3.3``.
1.5 The thread definition and its libraries: ``CreateDefinition(87)``,
    ``InitializeThreadData``, ``Type`` set to ``Metric Tap`` and ``Metric Die``
    and read back; ``GetConfigurationNames`` for M3, M4, M10 and M30 and how
    they are spelled (``M10x1.0`` or ``M10x1``); every property the code sets,
    read back after setting it (late binding call forms); whether ``Edge``
    takes the edge as a property or needs ``SelectByRay`` with Mark 1.
1.6 Thread diameters: external M10x1.5 on Ø10, Ø9.9 and Ø10.1 rods; internal
    on Ø8.376, Ø8.5 and Ø8.2 holes; the volumes against the threads spec
    (268 and 120.4 mm³); which way the thread runs from the edge.
1.7 Versions: ``RevisionNumber``, what ``CreateDefinition(87)`` and
    ``HoleWizard5`` do; the API help says the Thread feature data is
    "SOLIDWORKS 2018 FCS, Revision Number 26.0" and ``HoleWizard5`` 2014.
1.8 Rebuild time: ``create_thread`` and the next ``ForceRebuild3`` for
    M3x0.5 at 10, 50 and 100 mm and M10x1.5 at 20 and 100 mm (sets
    ``MAX_THREAD_TURNS``, now a provisional 100).
1.9 A modeled tapped hole: M3 blind and M4 through; the start edge, the
    length (``ThreadDepth`` or the through length), whether the cosmetic
    thread stays beside the modeled one, the volume at the plate's ends.
1.10 Failure and cleanup: ``HoleWizard5`` for ``M3.3``; a ``CreateFeature``
    with no material behind the edge (what the tree holds after); deleting a
    hole removes its sketches and thread, and the count and volume return.
1.11 Drawing import (pitfall #18): a drawing of the pinned hole, dimensions
    imported with ``include_hole_wizard``; do the position dimensions arrive,
    once each?
chamber The imaging-chamber threads, on scratch copies of the geometry only
    (never the user's ``Chamber_*`` files): is ``M30x1``/``M30x1.0`` in both
    libraries; an external M30x1 on a Ø30.0 shaft at y = 1.77, 5.5 mm toward
    +Y (Y-up axis); the same on a Ø29.7 shaft with ``DiameterOverride`` and
    ``Diameter`` 29.7 mm, and with ``Offset``/``OffsetDistance`` 0.15 mm, set
    by hand on the definition (the spec doesn't take them; this measures what
    an extension would do); an internal M30x1 in a Ø28.92 bore 6 mm deep.

It only touches the documents it creates, closes them without saving, and
writes the report under ``tests/.generated/probe_hole_wizard/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    .venv\\Scripts\\python.exe tests/scripts/probe_hole_wizard.py
    .venv\\Scripts\\python.exe tests/scripts/probe_hole_wizard.py 1.5 chamber
"""

from __future__ import annotations

import asyncio
import json
import math
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_hole_wizard"
GEN_PY = "83A33D31-27C5-11CE-BFD4-00400513BB57x0x34x0"


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

    from solidworks_mcp.tools.drawing import register_drawing_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-hole-wizard")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_drawing_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _volume(adapter: Any) -> float | None:
    result = await adapter.get_model_geometry_summary()
    return (result.data or {}).get("volume_mm3") if result.is_success else None


async def _part(adapter: Any, tools: dict[str, Any]) -> None:
    await adapter.create_part()
    await tools["set_units"]({"unit_system": "mm"})


async def block(adapter: Any, tools: dict[str, Any]) -> None:
    """A 40 x 20 x 10 block on Front, (0, 0) to (40, 20), top face at z = 10."""
    await _part(adapter, tools)
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 40, "y2": 20})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 10.0})


async def rod(adapter: Any, tools: dict[str, Any], diameter: float = 10.0) -> None:
    """A rod on Front, centred on the origin, its end edge at z = 40."""
    await _part(adapter, tools)
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": diameter / 2})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 40.0})


async def drilled_block(adapter: Any, tools: dict[str, Any], hole: float) -> None:
    """A 30 x 30 x 20 block with a Ø``hole`` hole through it at (15, 15)."""
    await _part(adapter, tools)
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_rectangle"]({"x1": 0, "y1": 0, "x2": 30, "y2": 30})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 20.0})
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_circle"]({"center_x": 15, "center_y": 15, "radius": hole / 2})
    await tools["exit_sketch"]()
    await tools["create_cut_extrude"]({"depth": 20.0, "end_condition": "ThroughAll"})


def _model(adapter: Any) -> Any:
    return adapter.currentModel


def _thread_definition(adapter: Any) -> Any:
    from solidworks_mcp.adapters.solidworks.bodies import feature_manager
    from solidworks_mcp.adapters.solidworks.com_utils import flag

    manager = feature_manager(adapter, _model(adapter), "CreateDefinition")
    data = manager.CreateDefinition(87)
    flag(adapter, data, "InitializeThreadData")
    return data


async def hole_wizard_signature(adapter, tools, report: Report) -> None:
    """1.1: the signature, and each kind's volume against its sizes."""
    import importlib

    module = _try(lambda: importlib.import_module(f"win32com.gen_py.{GEN_PY}"))
    if "value" in module:
        import inspect

        method = module["value"].IFeatureManager.HoleWizard5
        count = len(inspect.signature(method).parameters) - 1
        report.record("1.1.arguments", "HoleWizard5 takes 27", count, ok=count == 27)
    for kind, fit, depth in [
        ("clearance", "close", None),
        ("clearance", "normal", None),
        ("clearance", "loose", None),
        ("clearance", "normal", 6.0),
        ("counterbore", "normal", None),
        ("countersink", "normal", None),
        ("tapped", None, 8.0),
        ("tapped", None, None),
    ]:
        await block(adapter, tools)
        before = await _volume(adapter)
        reply = await tools["create_hole_wizard_hole"](
            {"kind": kind, "size": "M3", "face_point": [20, 10, 10], "depth_mm": depth}
            | ({"fit": fit} if fit else {})
        )
        after = await _volume(adapter)
        report.record(
            f"1.1.{kind}.{fit}.{depth or 'through'}",
            "volume removed vs the sizes read back (D4 vectors)",
            {
                "removed": (before or 0) - (after or 0),
                "hole": (reply.get("data") or {}).get("hole"),
                "status": reply.get("status"),
                "message": reply.get("message"),
            },
        )
    await _vector_slots(adapter, tools, report)


async def _vector_slots(adapter, tools, report: Report) -> None:
    """1.1: one value slot at a time through ``wizard_hole`` (a wrong one cuts silently)."""
    from solidworks_mcp.adapters.solidworks import holes

    for kind, slot, value in [
        ("clearance", 0, 0.0),
        ("clearance", 0, -1.0),
        ("clearance", 1, 0.0),
        ("tapped", 6, 0.0),
        ("tapped", 6, -1.0),
        ("tapped", 7, 0.0),
    ]:
        await block(adapter, tools)
        request = holes.check_hole_input(kind, "M3", 8.0)
        values = holes.wizard_values(kind, request["fit"], False)
        values[slot] = value
        before = await _volume(adapter)
        model = _model(adapter)
        face = holes.find_face(adapter, [20, 10, 10], None)
        holes.pick_face(adapter, model, [20, 10, 10], face.normal)
        original = holes.wizard_values
        holes.wizard_values = lambda *_a, v=values: list(v)  # type: ignore[assignment]
        try:
            feature = _try(
                lambda r=request, m=model: holes.wizard_hole(adapter, m, r, None)
            )
        finally:
            holes.wizard_values = original  # type: ignore[assignment]
        report.record(
            f"1.1.slot.{kind}.Value{slot + 1}={value:g}",
            "what one slot does",
            {
                "feature": bool(feature.get("value")),
                "removed": (before or 0) - (await _volume(adapter) or 0),
            },
        )


async def face_pick(adapter, tools, report: Report) -> None:
    """1.2: the ray pick, cleared and not, in a side view, on a split top."""
    from solidworks_mcp.adapters.solidworks.com_utils import selection_count

    await block(adapter, tools)
    model = _model(adapter)
    extension = model.Extension
    for cleared in (True, False):
        if cleared:
            model.ClearSelection2(True)
        _try(
            lambda: extension.SelectByRay(
                0.02, 0.01, 0.01001, 0, 0, -1, 1e-4, 2, False, 0, 0
            )
        )
        report.record(
            f"1.2.count.cleared={cleared}",
            "SelectByRay with Append false: one entity? (pitfall #59)",
            selection_count(adapter, model),
        )
    model.ClearSelection2(True)
    _try(lambda: model.ShowNamedView2("*Left", -1))
    reply = await tools["create_hole_wizard_hole"](
        {"kind": "clearance", "size": "M3", "face_point": [20, 10, 10]}
    )
    report.record(
        "1.2.left_view", "the hole lands in the face holding the point", reply
    )
    await block(adapter, tools)
    await tools["create_sketch"]({"plane": "Front"})
    await tools["add_rectangle"]({"x1": 18, "y1": -1, "x2": 22, "y2": 21})
    await tools["exit_sketch"]()
    await tools["create_cut_extrude"]({"depth": 5.0, "reverse_direction": True})
    for point in ([8, 10, 10], [32, 10, 10]):
        reply = await tools["create_hole_wizard_hole"](
            {"kind": "clearance", "size": "M3", "face_point": point}
        )
        report.record(f"1.2.split.{point[0]}", "either top face", reply)
    await block(adapter, tools)
    await tools["create_hole_wizard_hole"](
        {"kind": "countersink", "size": "M3", "face_point": [20, 10, 10]}
    )
    second = await tools["create_hole_wizard_hole"](
        {"kind": "clearance", "size": "M3", "face_point": [22.5, 10, 10]}
    )
    report.record(
        "1.2.in_countersink",
        "a point in the cone is refused (not flat)",
        second,
        ok=second.get("error_type") == "invalid_input",
    )


async def landing(adapter, tools, report: Report) -> None:
    """1.3: the point, the position sketch, the pin and set_dimension."""
    from solidworks_mcp.adapters.solidworks import holes
    from solidworks_mcp.adapters.solidworks.inspection import sketch_axes

    await block(adapter, tools)
    model = _model(adapter)
    face = holes.find_face(adapter, [20, 14, 10], None)
    holes.pick_face(adapter, model, [20, 14, 10], face.normal)
    request = holes.check_hole_input("clearance", "M3")
    feature = holes.wizard_hole(adapter, model, request, None)
    model.ForceRebuild3(False)
    circles = holes._circles_near_axis(adapter, [20, 14, 10], (0, 0, 1), 3.4)
    report.record("1.3.unpinned_centre", "offset from [20, 14, 10] (0.04 mm?)", circles)
    subs = [
        (s.Name, _try(lambda s=s: s.GetTypeName2()))
        for s in holes._sub_features(adapter, feature)
    ]
    report.record("1.3.sub_features", "the position sketch's place", subs)
    sub, sketch = holes.position_sketch(adapter, feature)
    report.record(
        "1.3.axes", "ModelToSketchTransform", _try(lambda: sketch_axes(adapter, sketch))
    )
    pinned = _try(lambda: holes.pin_position(adapter, model, feature, [20, 14, 10]))
    report.record("1.3.pin", "dimensioned from the origin, fully defined", pinned)
    circles = holes._circles_near_axis(adapter, [20, 14, 10], (0, 0, 1), 3.4)
    report.record("1.3.pinned_centre", "within 0.01 mm of the point", circles)
    names = (pinned.get("value") or {}).get("dimensions") or {}
    if names.get("x"):
        moved = await tools["set_dimension"]({"name": names["x"], "value": 30})
        circles = holes._circles_near_axis(adapter, [30, 14, 10], (0, 0, 1), 3.4)
        report.record(
            "1.3.moved",
            "set_dimension moves the hole to x = 30",
            {"reply": moved, "circles": circles},
        )
    report.record(
        "1.3.preselected_points",
        "does HoleWizard5 take a face plus pre-selected sketch points? Not scripted: "
        "try it by hand on a block with a sketch point at (20, 14) on the top face",
        None,
    )


async def wizard_sizes(adapter, tools, report: Report) -> None:
    """1.4: every size property for each kind, and an unknown size."""
    from solidworks_mcp.adapters.solidworks import holes

    for size in ("M3", "M4", "M5", "M30"):
        for kind in holes.KINDS:
            await block(adapter, tools)
            model = _model(adapter)
            face = holes.find_face(adapter, [20, 10, 10], None)
            holes.pick_face(adapter, model, [20, 10, 10], face.normal)
            request = holes.check_hole_input(kind, size, 8.0)
            feature = holes.wizard_hole(adapter, model, request, None)
            raw = holes.read_sizes(adapter, feature) if feature is not None else None
            report.record(f"1.4.{size}.{kind}", "IWizardHoleFeatureData2 (m, rad)", raw)
    await block(adapter, tools)
    unknown = await tools["create_hole_wizard_hole"](
        {"kind": "clearance", "size": "M3.3", "face_point": [20, 10, 10]}
    )
    report.record("1.4.M3.3", "None, or a hole?", unknown)


async def thread_library(adapter, tools, report: Report) -> None:
    """1.5: the definition, the libraries, their sizes and the property forms."""
    await rod(adapter, tools)
    for library in ("Metric Tap", "Metric Die"):
        data = _thread_definition(adapter)
        report.record("1.5.definition", "CreateDefinition(87)", type(data).__name__)
        report.record(
            "1.5.initialize", "InitializeThreadData", _try(data.InitializeThreadData)
        )
        data.Type = library
        file = _try(lambda d=data: d.Type)
        report.record(f"1.5.{library}.file", "Type reads back as the file", file)
        names = _try(
            lambda f=file: list(adapter.swApp.GetConfigurationNames(f.get("value")))
        )
        listed = names.get("value") or []
        for diameter in ("M3x", "M4x", "M10x", "M30x"):
            report.record(
                f"1.5.{library}.{diameter}",
                "the sizes and their spelling",
                [n for n in listed if str(n).startswith(diameter)],
            )
        for name, value in (
            ("ThreadMethod", 0),
            ("EndCondition", 0),
            ("BlindDepth", 0.02),
            ("RightHanded", True),
            ("Size", "M10x1.5"),
        ):
            _try(lambda d=data, n=name, v=value: setattr(d, n, v))
            report.record(
                f"1.5.{library}.{name}",
                "set, then read back",
                _try(lambda d=data, n=name: getattr(d, n)),
            )


async def thread_diameters(adapter, tools, report: Report) -> None:
    """1.6: the diameter rule, and the volumes against the spec."""
    for diameter in (10.0, 9.9, 10.1):
        await rod(adapter, tools, diameter)
        before = await _volume(adapter)
        reply = await tools["create_thread"](
            {"size": "M10x1.5", "edge_point": [diameter / 2, 0, 40], "length_mm": 20}
        )
        report.record(
            f"1.6.external.{diameter}",
            "268 mm³ within 0.5% on Ø10; refused otherwise",
            {"reply": reply, "removed": (before or 0) - (await _volume(adapter) or 0)},
        )
    for hole in (8.376202, 8.5, 8.2):
        await drilled_block(adapter, tools, hole)
        before = await _volume(adapter)
        reply = await tools["create_thread"](
            {
                "size": "M10x1.5",
                "edge_point": [15 + hole / 2, 15, 20],
                "length_mm": 12,
                "internal": True,
            }
        )
        report.record(
            f"1.6.internal.{hole}",
            "120.4 mm³ within 0.5% on Ø8.376; refused otherwise",
            {"reply": reply, "removed": (before or 0) - (await _volume(adapter) or 0)},
        )


async def versions(adapter, tools, report: Report) -> None:
    """1.7: the revision, and the API help's availability lines."""
    from solidworks_mcp.adapters.solidworks import threads

    report.record("1.7.revision", "RevisionNumber", threads.revision(adapter))
    await block(adapter, tools)
    report.record(
        "1.7.definition",
        "CreateDefinition(87)",
        _try(lambda: type(_thread_definition(adapter)).__name__),
    )
    from solidworks_mcp.adapters.solidworks import holes

    report.record(
        "1.7.hole_wizard",
        "IFeatureManager has HoleWizard5",
        holes.has_member(adapter, _model(adapter).FeatureManager, "HoleWizard5"),
    )


async def rebuild_time(adapter, tools, report: Report) -> None:
    """1.8: time to make each thread and to rebuild after it."""
    for size, diameter, length in (
        ("M3x0.5", 3.0, 10),
        ("M3x0.5", 3.0, 50),
        ("M3x0.5", 3.0, 100),
        ("M10x1.5", 10.0, 20),
        ("M10x1.5", 10.0, 100),
    ):
        await _part(adapter, tools)
        await tools["create_sketch"]({"plane": "Front"})
        await tools["add_circle"](
            {"center_x": 0, "center_y": 0, "radius": diameter / 2}
        )
        await tools["exit_sketch"]()
        await tools["create_extrusion"]({"depth": float(length) + 5})
        started = time.perf_counter()
        reply = await tools["create_thread"](
            {
                "size": size,
                "edge_point": [diameter / 2, 0, length + 5],
                "length_mm": length,
            }
        )
        made = time.perf_counter() - started
        started = time.perf_counter()
        _model(adapter).ForceRebuild3(False)
        rebuilt = time.perf_counter() - started
        report.record(
            f"1.8.{size}.{length}",
            "seconds to make and to rebuild (sets MAX_THREAD_TURNS)",
            {
                "status": reply.get("status"),
                "message": reply.get("message"),
                "make_s": made,
                "rebuild_s": rebuilt,
            },
        )


async def modeled_tapped(adapter, tools, report: Report) -> None:
    """1.9: modeled tapped holes, blind and through."""
    for size, depth, expected in (("M3", 8.0, 45.10), ("M4", None, 100.95)):
        await block(adapter, tools)
        before = await _volume(adapter)
        reply = await tools["create_hole_wizard_hole"](
            {
                "kind": "tapped",
                "size": size,
                "face_point": [20, 10, 10],
                "depth_mm": depth,
                "thread": "modeled",
            }
        )
        removed = (before or 0) - (await _volume(adapter) or 0)
        features = await tools["list_features"]({})
        report.record(
            f"1.9.{size}.{depth or 'through'}",
            f"about {expected} mm³; the cosmetic thread beside the modeled one?",
            {"reply": reply, "removed": removed, "features": features.get("features")},
        )


async def cleanup(adapter, tools, report: Report) -> None:
    """1.10: an unknown size, a refused CreateFeature, and deleting a hole."""
    from solidworks_mcp.adapters.solidworks import threads

    await rod(adapter, tools)
    count = threads.feature_count(adapter, _model(adapter))
    refused = await tools["create_thread"](
        {"size": "M10x1.5", "edge_point": [5, 0, 0], "length_mm": 20}
    )
    report.record(
        "1.10.no_material",
        "what a refused CreateFeature leaves (from the base edge, outward)",
        {
            "reply": refused,
            "count_before": count,
            "count_after": threads.feature_count(adapter, _model(adapter)),
        },
    )
    await block(adapter, tools)
    before = await _volume(adapter)
    count = threads.feature_count(adapter, _model(adapter))
    made = await tools["create_hole_wizard_hole"](
        {
            "kind": "tapped",
            "size": "M3",
            "face_point": [20, 10, 10],
            "depth_mm": 8,
            "thread": "modeled",
        }
    )
    name = (made.get("data") or {}).get("feature")
    deleted = await tools["delete_feature"]({"name": name}) if name else None
    report.record(
        "1.10.delete",
        "deleting the hole removes its sketches and thread; count and volume return",
        {
            "deleted": deleted,
            "count": (count, threads.feature_count(adapter, _model(adapter))),
            "volume": (before, await _volume(adapter)),
        },
    )


async def drawing_import(adapter, tools, report: Report) -> None:
    """1.11: the pinned position dimensions on a drawing."""
    await block(adapter, tools)
    made = await tools["create_hole_wizard_hole"](
        {"kind": "clearance", "size": "M3", "face_point": [20, 14, 10]}
    )
    report.record(
        "1.11.hole",
        "save this part, make a drawing of it and import dimensions with include_hole_wizard by hand",
        made,
    )


async def chamber(adapter, tools, report: Report) -> None:
    """The imaging chamber's M30 x 1 threads on scratch geometry (Y-up axis)."""
    from solidworks_mcp.adapters.solidworks import threads

    await _part(adapter, tools)
    for internal in (False, True):
        sizes = _try(
            lambda i=internal: threads.library_sizes(adapter, _model(adapter), i)
        )
        report.record(
            f"chamber.library.{'tap' if internal else 'die'}",
            "M30 sizes in the library",
            [s for s in sizes.get("value") or [] if str(s).startswith("M30")],
        )

    async def shaft(diameter: float) -> None:
        """A shaft on Top (Y up) to y = 7.5 on a Ø40 flange to y = 1.77."""
        await _part(adapter, tools)
        await tools["create_sketch"]({"plane": "Top"})
        await tools["add_circle"](
            {"center_x": 0, "center_y": 0, "radius": diameter / 2}
        )
        await tools["exit_sketch"]()
        await tools["create_extrusion"]({"depth": 7.5})
        await tools["create_sketch"]({"plane": "Top"})
        await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 20})
        await tools["exit_sketch"]()
        await tools["create_extrusion"]({"depth": 1.77})

    for diameter in (30.0, 29.7):
        await shaft(diameter)
        before = await _volume(adapter)
        reply = await tools["create_thread"](
            {"size": "M30x1", "edge_point": [diameter / 2, 1.77, 0], "length_mm": 5.5}
        )
        report.record(
            f"chamber.external.{diameter}",
            "Ø30 threads; Ø29.7 is refused by the spec's diameter rule",
            {"reply": reply, "removed": (before or 0) - (await _volume(adapter) or 0)},
        )
    for setting in (
        {"DiameterOverride": True, "Diameter": 0.0297},
        {"Offset": True, "OffsetDistance": 0.00015},
    ):
        await shaft(29.7)
        model = _model(adapter)
        data, _file, _sizes = threads.open_library(adapter, model, internal=False)
        edge, _info = threads.find_circular_edge(adapter, [14.85, 1.77, 0])
        for name, value in {
            "Edge": edge,
            "ThreadMethod": 0,
            "Size": "M30x1.0",
            "EndCondition": 0,
            "BlindDepth": 0.0055,
            "RightHanded": True,
            **setting,
        }.items():
            _try(lambda n=name, v=value, d=data: setattr(d, n, v))
        before = await _volume(adapter)
        made = _try(
            lambda m=model, d=data: m.FeatureManager.CreateFeature(d) is not None
        )
        report.record(
            f"chamber.external.29.7.{next(iter(setting))}",
            "what an override or offset does on the Ø29.7 shaft",
            {"made": made, "removed": (before or 0) - (await _volume(adapter) or 0)},
        )
    await _part(adapter, tools)
    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 25})
    await tools["exit_sketch"]()
    await tools["create_extrusion"]({"depth": 7.0})
    await tools["create_sketch"]({"plane": "Top"})
    await tools["add_circle"]({"center_x": 0, "center_y": 0, "radius": 28.92 / 2})
    await tools["exit_sketch"]()
    # The bore is cut from the Top plane (y = 0) up 6 mm, so its mouth is at
    # y = 0 here; the chamber's opens at y = 7, which only changes the side.
    await tools["create_cut_extrude"]({"depth": 6.0})
    before = await _volume(adapter)
    reply = await tools["create_thread"](
        {
            "size": "M30x1",
            "edge_point": [28.92 / 2, 0, 0],
            "length_mm": 6,
            "internal": True,
        }
    )
    report.record(
        "chamber.internal.28.92",
        "M30x1 tap in the Ø28.92 bore (needs Ø28.9175 within 0.01)",
        {"reply": reply, "removed": (before or 0) - (await _volume(adapter) or 0)},
    )
    report.record(
        "chamber.minor",
        "M30x1 basic minor",
        threads.minor_diameter(30, 1),
        ok=math.isclose(threads.minor_diameter(30, 1), 28.9175),
    )


STEPS = {
    "1.1": hole_wizard_signature,
    "1.2": face_pick,
    "1.3": landing,
    "1.4": wizard_sizes,
    "1.5": thread_library,
    "1.6": thread_diameters,
    "1.7": versions,
    "1.8": rebuild_time,
    "1.9": modeled_tapped,
    "1.10": cleanup,
    "1.11": drawing_import,
    "chamber": chamber,
}


async def run(names: list[str]) -> int:
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
        for name in names or list(STEPS):
            try:
                await STEPS[name](adapter, tools, report)
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
    sys.exit(asyncio.run(run(sys.argv[1:])))
