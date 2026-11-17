"""Probe the COM sequences behind the Dipper build's analysis tools.

``rotation_clearance`` (``adapters/solidworks/rotation_clearance.py``),
``set_component_transform``'s turn (``placement.turned``), ``volume_by_plane``
(``plane_volume.py``), ``set_density`` (``density.py``) and
``check_printability``'s ``orientations`` and ``min_feature_mm`` were written
without SolidWorks (another session was using it). Every COM sequence they use
runs here on scratch parts and an assembly, through ``PyWin32Adapter`` and the
registered tools, raw first where the code assumes something, then through the
tools end to end. Each step says what it settles and the numbers to expect.

The scratch geometry (sketches on Top: sketch (x, y) is model (x, -z); the
extrusions grow along +Y):

* the **arm**: a 40 x 4 x 4 bar, x 10..50, y 0..4, z -2..2 (640 mm3);
* the **wall**: 120 x 4 x 5, x -60..60, y 0..4, z -40..-35 (2400 mm3).

Turned about +Y through the origin, the arm swings toward -Z (a quarter turn
about Y sends X to -Z). Its far corner (r 50, z -2) reaches the wall's face at
z -35 when ``50 sin t + 2 cos t = 35``: **t = 42.09 degrees**. At the start the
gap is **33.0 mm**; at 30 degrees it is 35 - (25 + 1.732) = **8.27 mm**.

* the **block**: 40 x 10 x 40, y 0..10 (16000 mm3), with Plane1 4 mm above
  Top: **9600 mm3 above, 6400 below**; at 1.15 g/cm3 it weighs **18.4 g** and
  its part below the plane **7.36 g**;
* the **cup**: 40 x 30 x 40, y 0..30, with a 30 x 30 pocket 25 deep cut from
  its top (floor at y 5; 25500 mm3 of solid), and Plane1 at y 20: filled to
  it, it holds **30 x 15 x 30 = 13500 mm3 = 13.5 ml**;
* the **fin plate**: 40 x 10 x 40 with a 0.5 mm fin 15 high on top.

Steps (``STEPS`` below, in order; each records what it found):

1. ``temp_turn``: ``IBody2.Copy`` of the arm, ``ApplyTransform`` of
   ``placement.turn_data([0, 1, 0], origin, 90)``. Settles: ApplyTransform turns
   by ``p . R + t`` like ``Transform2`` (#41): the copy's box should be
   x -2..2, y 0..4, z -50..-10; the part's arm box is unchanged.
2. ``temp_faces``: ``ClosestDistance`` between a temporary copy's faces and the
   wall's part faces, unturned (expect 33.0) and turned 30 degrees (8.27).
   Settles: whether ``ClosestDistance`` takes a temporary body's faces (the
   body sweep's gap depends on it; without it only contacts are found).
3. ``body_sweep``: the ``rotation_clearance`` tool on the part, about "y",
   range [-90, 90]. Expect ``positive.first_contact_deg`` 42.09 (+/-0.1),
   ``negative`` free to -90, ``start_clearance_mm`` 33.0; the part's body
   boxes and ``GetSaveFlag`` the same before and after.
4. ``component_raw``: an assembly of the arm part (inserted first, so fixed)
   and the wall part, at the origin. ``Transform2`` set to the arm turned 30
   degrees, without a rebuild: read back (settles: it sticks unmated); then
   ``ClosestDistance`` on the twins (expect 8.27: settles that the twins move
   with it without ``EditRebuild3``); the arm's ``GetBodies2`` box against its
   faces (settles ``body_frame``: "part" means bodies come in the part's
   coordinates); then the start put back.
5. ``component_sweep``: the ``rotation_clearance`` tool on the assembly.
   Expect 42.09, ``restored`` true, the arm's ``Transform2`` equal to the start
   and still fixed afterwards.
6. ``component_turn``: ``set_component_transform`` with ``turn_deg`` 30 about
   "y" through the origin: rotation rows read back
   ``[[0.866, 0, -0.5], [0, 1, 0], [0.5, 0, 0.866]]``; then -30 back.
7. ``mated_sweep``: a coincident mate of the two components' Front Planes,
   then ``rotation_clearance`` again. Settles: whether a mate that fixes the
   angle stops ``Transform2`` without a rebuild (the sweep then says "mate(s)
   likely hold it"), or the sweep runs anyway. The mate is deleted after.
8. ``cube_box``: ``IModeler.CreateBodyFromBox3([0, 0.004, 0, 0, 1, 0, L, L,
   L])`` (L 0.2 m) and its ``GetBodyBox`` and centre of mass. Settles: whether
   the centre given is the box's (box y -0.096..0.104) or its base's (y
   0.004..0.204); ``half_space`` corrects either.
9. ``block_volume``: the ``volume_by_plane`` tool with Plane1, then with
   ``point_mm`` [0, 4, 0] and ``normal`` "-y". Expect 9600 / 6400 and the
   reverse; ``check_ok``; the feature count and ``GetSaveFlag`` unchanged.
10. ``density``: ``GetUserPreferenceDouble(swMaterialPropertyDensity 7, 0)``
    before; ``set_density`` 1.15: expect ``applied``, ``read_back_kg_m3`` 1150,
    ``mass_g`` 18.4. Then ``volume_by_plane`` Plane1: below ``mass_g`` 7.36.
    Then ``set_material`` "ABS": ``set_density`` is refused; with
    ``remove_material`` the material comes off (``get_material`` none) and the
    density is 1150 again. Settles: the document density is what mass
    properties use without a material, and an empty ``SetMaterialPropertyName2``
    takes the material off.
11. ``cup_volume``: ``volume_by_plane`` Plane1 with ``enclosed``: expect below
    ``enclosed_ml`` 13.5, 1 pocket; above 0. Settles the ``Operations2`` cut
    of a cube by the cup into the outside and the pocket.
12. ``printability``: ``check_printability`` with ``orientations`` 26 and
    ``min_feature_mm`` 0.8 on the fin plate: one thin region, thinnest 0.5,
    on the fin (x near 0, y 10..25).

It only touches documents it creates, closes only those, and deletes its files
at the end. The report stays in ``tests/.generated/probe_dipper_analysis/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_dipper_analysis.py
"""

from __future__ import annotations

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
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks import placement
from solidworks_mcp.adapters.solidworks import rotation_clearance as rc
from solidworks_mcp.adapters.solidworks.clearance import _closest
from solidworks_mcp.adapters.solidworks.com_utils import double_array
from solidworks_mcp.adapters.solidworks.io import _find_components

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_dipper_analysis"
CONTACT_DEG = math.degrees(math.asin(35 / math.hypot(50, 2))) - math.degrees(
    math.atan2(2, 50)
)
GAP_AT_30_MM = 35 - (50 * math.sin(math.radians(30)) + 2 * math.cos(math.radians(30)))


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.analysis import register_analysis_tools
    from solidworks_mcp.tools.bodies import register_bodies_tools
    from solidworks_mcp.tools.engineering_data import register_engineering_data_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-dipper-analysis")
    for register in (
        register_sketching_tools,
        register_modeling_tools,
        register_bodies_tools,
        register_analysis_tools,
        register_engineering_data_tools,
    ):
        await register(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _invoke(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    """Call a tool; one with no input model (``exit_sketch``) takes keywords only."""
    fn = tools[name]
    positional = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if not positional:
        return await fn(**args)
    return await fn(args)


async def _ok(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    reply = await _invoke(tools, name, args)
    assert reply["status"] == "success", (name, reply)
    return reply


async def _box(
    tools: dict[str, Any],
    plane: str,
    rect: tuple[float, float, float, float],
    depth: float,
    merge: bool = True,
) -> None:
    """A rectangle (sketch x1, y1, x2, y2 in mm) on ``plane``, extruded."""
    x1, y1, x2, y2 = rect
    await _ok(tools, "create_sketch", {"plane": plane})
    await _ok(tools, "add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": depth, "merge_result": merge})


_ARM = (10.0, -2.0, 50.0, 2.0)
_WALL = (-60.0, 35.0, 60.0, 40.0)


def _model(adapter: PyWin32Adapter) -> Any:
    return adapter.currentModel


def _mm(values: Any) -> list[float] | None:
    return [round(float(v) * 1000, 4) for v in values] if values else None


def _bodies(adapter: PyWin32Adapter) -> dict[str, Any]:
    return dict(bd.solid_bodies(adapter, _model(adapter)))


def _by_volume(adapter: PyWin32Adapter) -> tuple[str, str]:
    """The arm (640 mm3) and the wall (2400 mm3) by volume."""
    found = {n: bd.volume_mm3(adapter, b) or 0.0 for n, b in _bodies(adapter).items()}
    arm = min(found, key=lambda n: abs(found[n] - 640.0))
    wall = min(found, key=lambda n: abs(found[n] - 2400.0))
    return arm, wall


def _boxes(adapter: PyWin32Adapter) -> dict[str, list[float] | None]:
    return {n: _mm(bd._box_m(adapter, b)) for n, b in _bodies(adapter).items()}


def _save_flag(adapter: PyWin32Adapter) -> Any:
    model = _model(adapter)
    sw_type_info.flag_members(model, "GetSaveFlag")
    return adapter._attempt(lambda: model.GetSaveFlag())


# ---- the steps on the arm part ------------------------------------------------------


def step_temp_turn(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles ApplyTransform's convention on a temporary copy (p . R + t)."""
    arm, _wall = _by_volume(adapter)
    body = _bodies(adapter)[arm]
    utility = bd._math_utility(adapter)
    piece = bd.temp_copy(adapter, body)
    data = placement.turn_data([0, 1, 0], [0, 0, 0], 90)
    xform = utility.CreateTransform(double_array(data))
    applied = piece.ApplyTransform(xform)
    return {
        "applied": applied,
        "copy_box_mm": _mm(bd._box_m(adapter, piece)),
        "expected_copy_box_mm": [-2, 0, -50, 2, 4, -10],
        "part_arm_box_mm": _mm(bd._box_m(adapter, body)),
        "expected_part_arm_box_mm": [10, 0, -2, 50, 4, 2],
    }


def step_temp_faces(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles whether ClosestDistance takes a temporary body's faces."""
    model = _model(adapter)
    arm, wall = _by_volume(adapter)
    found = _bodies(adapter)
    utility = bd._math_utility(adapter)
    sw_type_info.flag_members(model, "ClosestDistance")
    wall_faces = bd._faces(adapter, wall, found[wall])
    out: dict[str, Any] = {}
    for angle, expected in ((0.0, 33.0), (30.0, round(GAP_AT_30_MM, 3))):
        piece = bd.temp_copy(adapter, found[arm])
        if angle:
            piece.ApplyTransform(
                utility.CreateTransform(
                    double_array(placement.turn_data([0, 1, 0], [0, 0, 0], angle))
                )
            )
        faces = bd._faces(adapter, arm, piece)
        gaps = []
        for face, _box in faces:
            for other, _obox in wall_faces:
                got = _closest(adapter, model, face, other)
                gaps.append(None if got is None else round(got[0] * 1000, 4))
        real = [g for g in gaps if g is not None]
        out[f"at_{angle:g}_deg"] = {
            "pairs": len(gaps),
            "measured": len(real),
            "min_mm": min(real) if real else None,
            "expected_mm": expected,
        }
    return out


async def step_body_sweep(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """The rotation_clearance tool on the part's bodies; nothing in the part changes."""
    arm, wall = _by_volume(adapter)
    before, flag_before = _boxes(adapter), _save_flag(adapter)
    started = time.perf_counter()
    reply = await _invoke(
        tools,
        "rotation_clearance",
        {"moving": arm, "other": wall, "axis": "y", "range_deg": [-90, 90]},
    )
    data = reply.get("data") or {}
    return {
        "seconds": round(time.perf_counter() - started, 2),
        "status": reply["status"],
        "message": reply.get("message"),
        "warning": reply.get("warning"),
        "positive": data.get("positive"),
        "negative": data.get("negative"),
        "start_clearance_mm": data.get("start_clearance_mm"),
        "clearance_measured": data.get("clearance_measured"),
        "evaluations": data.get("evaluations"),
        "expected_first_contact_deg": round(CONTACT_DEG, 3),
        "expected_start_clearance_mm": 33.0,
        "boxes_unchanged": _boxes(adapter) == before,
        "save_flag_before_after": [flag_before, _save_flag(adapter)],
    }


# ---- the steps on the assembly -----------------------------------------------------


def _components(adapter: PyWin32Adapter) -> dict[str, Any]:
    model = _model(adapter)
    assembly = sw_type_info.flagged(model, "IAssemblyDoc")
    return _find_components(adapter, assembly, ["pd_arm-1", "pd_wall-1"])


def step_component_raw(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles Transform2 without a rebuild, ClosestDistance on moved twins, the frame."""
    model = _model(adapter)
    found = _components(adapter)
    arm, wall = found["pd_arm-1"], found["pd_wall-1"]
    start = placement._transform_data(adapter, arm)
    utility = bd._math_utility(adapter)
    asked = placement.turned(start, [0, 1, 0], [0, 0, 0], 30)
    out: dict[str, Any] = {
        "start": start,
        "was_fixed": placement._is_fixed(adapter, arm),
    }
    if out["was_fixed"]:
        placement._set_fixed(
            adapter, model, sw_type_info.flagged(model, "IAssemblyDoc"), arm, False
        )
    arm.Transform2 = utility.CreateTransform(double_array(asked))
    after = placement._transform_data(adapter, arm)
    out["read_back_matches"] = after is not None and placement._matches(
        after, asked[9:12], [asked[0:3], asked[3:6], asked[6:9]]
    )
    moving = rc._part_faces(adapter, arm, "arm")
    others = rc._placed_faces(
        rc._part_faces(adapter, wall, "wall"), placement._transform_data(adapter, wall)
    )
    sw_type_info.flag_members(model, "ClosestDistance")
    gaps = [
        got[0] * 1000
        for twin, _b, _o in moving
        for other, _ob in others
        if (got := _closest(adapter, model, twin, other)) is not None
    ]
    out["gap_at_30_mm"] = round(min(gaps), 4) if gaps else None
    out["expected_gap_at_30_mm"] = round(GAP_AT_30_MM, 4)
    bodies = rc._component_bodies(adapter, arm, "arm")
    out["body_frame"] = rc._frame_of(moving, bodies, after or asked)
    out["raw_body_box_mm"] = _mm(bodies[0][1]) if bodies else None
    arm.Transform2 = utility.CreateTransform(double_array(start))
    if out["was_fixed"]:
        placement._set_fixed(
            adapter, model, sw_type_info.flagged(model, "IAssemblyDoc"), arm, True
        )
    out["put_back"] = _same(placement._transform_data(adapter, arm), start)
    return out


def _same(data: list[float] | None, start: list[float]) -> bool:
    """Whether a placement read back is ``start`` (within the placement tolerances)."""
    rows = [start[0:3], start[3:6], start[6:9]]
    return data is not None and placement._matches(data, start[9:12], rows)


async def step_component_sweep(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """The rotation_clearance tool on the assembly; the arm is put back, still fixed."""
    arm = _components(adapter)["pd_arm-1"]
    start = placement._transform_data(adapter, arm)
    fixed = placement._is_fixed(adapter, arm)
    started = time.perf_counter()
    reply = await _invoke(
        tools,
        "rotation_clearance",
        {
            "moving": "pd_arm-1",
            "other": "pd_wall-1",
            "axis": "y",
            "range_deg": [-90, 90],
        },
    )
    data = reply.get("data") or {}
    return {
        "seconds": round(time.perf_counter() - started, 2),
        "status": reply["status"],
        "message": reply.get("message"),
        "warning": reply.get("warning"),
        "positive": data.get("positive"),
        "negative": data.get("negative"),
        "body_frame": data.get("body_frame"),
        "restored": data.get("restored"),
        "expected_first_contact_deg": round(CONTACT_DEG, 3),
        "transform_back": _same(placement._transform_data(adapter, arm), start),
        "fixed_before_after": [fixed, placement._is_fixed(adapter, arm)],
    }


async def step_component_turn(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """set_component_transform turned 30 degrees about y, then back."""
    turned = await _invoke(
        tools,
        "set_component_transform",
        {"component": "pd_arm-1", "turn_deg": 30, "turn_axis": "y"},
    )
    back = await _invoke(
        tools,
        "set_component_transform",
        {"component": "pd_arm-1", "turn_deg": -30, "turn_axis": "y"},
    )
    return {
        "turned": turned.get("transform"),
        "turned_placed": turned.get("placed"),
        "expected_rows": placement.axis_rotation_rows([0, 1, 0], 30),
        "back": back.get("transform"),
    }


async def step_mated_sweep(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """Settles whether a mate fixing the angle holds the arm against Transform2."""
    mate = await _invoke(
        tools,
        "add_mate",
        {
            "component_a": "pd_arm-1",
            "component_b": "pd_wall-1",
            "mate_type": "coincident",
            "entity_a": "Front Plane",
            "entity_b": "Front Plane",
        },
    )
    reply = await _invoke(
        tools,
        "rotation_clearance",
        {"moving": "pd_arm-1", "other": "pd_wall-1", "axis": "y", "range_deg": [0, 30]},
    )
    # add_mate gives no name; the first coincident mate is "Coincident1".
    deleted = await _invoke(tools, "delete_feature", {"name": "Coincident1"})
    return {
        "mate": mate.get("mate") or mate.get("message"),
        "sweep_status": reply["status"],
        "sweep_message": reply.get("message"),
        "sweep_error": reply.get("error_type"),
        "positive": (reply.get("data") or {}).get("positive"),
        "mate_deleted": deleted["status"],
    }


# ---- the steps on the block, the cup and the fin plate -------------------------------


def step_cube_box(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles where CreateBodyFromBox3 puts its box: centre or base centre."""
    app = adapter.swApp
    sw_type_info.flag_members(app, "GetModeler")
    modeler = app.GetModeler()
    sw_type_info.flag_members(modeler, "CreateBodyFromBox3")
    size = 0.2
    raw = modeler.CreateBodyFromBox3(
        double_array([0, 0.004, 0, 0, 1, 0, size, size, size])
    )
    cube = bd._wrapped(adapter, raw, "GetMassProperties")
    return {
        "made": raw is not None,
        "box_mm": _mm(bd._box_m(adapter, cube)) if raw is not None else None,
        "centre_of_mass_mm": _mm(bd._mass_m(adapter, cube)[:3])
        if raw is not None
        else None,
        "if_box_centre_y_mm": [-96, 104],
        "if_base_centre_y_mm": [4, 204],
    }


async def step_block_volume(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """volume_by_plane on the block, by Plane1 and by a point and normal."""
    listed = await _invoke(tools, "list_features", {})
    flag = _save_flag(adapter)
    named = await _invoke(tools, "volume_by_plane", {"plane": "Plane1"})
    given = await _invoke(
        tools, "volume_by_plane", {"point_mm": [0, 4, 0], "normal": "-y"}
    )
    after = await _invoke(tools, "list_features", {})
    return {
        "named": named.get("data") or named,
        "given_above_below": [
            ((given.get("data") or {}).get(s) or {}).get("volume_mm3")
            for s in ("above", "below")
        ],
        "expected_named_above_below": [9600.0, 6400.0],
        "features_unchanged": listed.get("features") == after.get("features"),
        "save_flag_before_after": [flag, _save_flag(adapter)],
    }


async def step_density(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """set_density on the block; the material comes off when asked."""
    extension = _model(adapter).Extension
    sw_type_info.flag_members(extension, "GetUserPreferenceDouble")
    out: dict[str, Any] = {"preference_before": extension.GetUserPreferenceDouble(7, 0)}
    out["set"] = (await _invoke(tools, "set_density", {"density_g_cm3": 1.15})).get(
        "data"
    )
    out["expected"] = {"read_back_kg_m3": 1150.0, "mass_g": 18.4, "below_mass_g": 7.36}
    below = await _invoke(tools, "volume_by_plane", {"plane": "Plane1"})
    out["below_mass_g"] = ((below.get("data") or {}).get("below") or {}).get("mass_g")
    out["material"] = (
        await _invoke(tools, "set_material", {"material_name": "ABS"})
    ).get("message")
    refused = await _invoke(tools, "set_density", {"density_g_cm3": 1.15})
    out["refused"] = [refused["status"], refused.get("message")]
    removed = await _invoke(
        tools, "set_density", {"density_g_cm3": 1.15, "remove_material": True}
    )
    out["removed"] = removed.get("data") or removed
    out["material_after"] = (await _invoke(tools, "get_material", {})).get("data")
    return out


async def step_cup_volume(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """volume_by_plane with enclosed on the cup: 13.5 ml below Plane1."""
    listed = await _invoke(tools, "list_bodies", {})
    reply = await _invoke(
        tools, "volume_by_plane", {"plane": "Plane1", "enclosed": True}
    )
    data = reply.get("data") or {}
    return {
        "solid_mm3": [
            b.get("volume_mm3") for b in (listed.get("data") or {}).get("bodies", [])
        ],
        "expected_solid_mm3": 25500.0,
        "status": reply["status"],
        "message": reply.get("message"),
        "below": data.get("below"),
        "above": data.get("above"),
        "expected_below_enclosed_ml": 13.5,
    }


async def step_printability(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """check_printability with orientations and min_feature_mm on the fin plate."""
    started = time.perf_counter()
    reply = await _invoke(
        tools, "check_printability", {"orientations": 26, "min_feature_mm": 0.8}
    )
    data = reply.get("data") or {}
    return {
        "seconds": round(time.perf_counter() - started, 2),
        "message": reply.get("message"),
        "best": (data.get("orientations") or {}).get("best"),
        "thin": data.get("thin_features"),
        "expected_thin": "one region, thinnest 0.5 mm, x near 0, y 10..25",
    }


# ---- the run -----------------------------------------------------------------------

Step = Callable[..., Any]


async def _record(report: dict[str, Any], key: str, step: Step, *args: Any) -> None:
    print(f"[probe] {key}: {step.__doc__}")
    try:
        found = step(*args)
        report[key] = await found if inspect.isawaitable(found) else found
    except Exception as exc:  # noqa: BLE001 - a probe records every failure
        report[key] = {"error": repr(exc)}


async def run() -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    scratch = out / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }
    arm_path, wall_path = scratch / "pd_arm.SLDPRT", scratch / "pd_wall.SLDPRT"
    try:
        tools = await _tools(adapter)
        # The arm and the wall as two bodies of one part.
        assert (await adapter.create_part()).is_success
        await _box(tools, "Top", _ARM, 4)
        await _box(tools, "Top", _WALL, 4, merge=False)
        await _record(report, "temp_turn", step_temp_turn, adapter)
        await _record(report, "temp_faces", step_temp_faces, adapter)
        await _record(report, "body_sweep", step_body_sweep, adapter, tools)
        # The same two as parts of an assembly, the arm inserted first (fixed).
        for path, rect in ((arm_path, _ARM), (wall_path, _WALL)):
            assert (await adapter.create_part()).is_success
            await _box(tools, "Top", rect, 4)
            assert (await adapter.save_file(str(path))).is_success
        assert (await adapter.create_assembly()).is_success
        for path in (arm_path, wall_path):
            placed = await adapter.insert_component(str(path), 0, 0, 0)
            report.setdefault("inserted", []).append(
                placed.data if placed.is_success else placed.error
            )
        await _record(report, "component_raw", step_component_raw, adapter)
        await _record(report, "component_sweep", step_component_sweep, adapter, tools)
        await _record(report, "component_turn", step_component_turn, adapter, tools)
        await _record(report, "mated_sweep", step_mated_sweep, adapter, tools)
        # The block, with Plane1 4 mm up.
        assert (await adapter.create_part()).is_success
        await _box(tools, "Top", (-20, -20, 20, 20), 10)
        await _ok(
            tools, "create_reference_plane", {"reference": "Top Plane", "offset": 4}
        )
        await _record(report, "cube_box", step_cube_box, adapter)
        await _record(report, "block_volume", step_block_volume, adapter, tools)
        await _record(report, "density", step_density, adapter, tools)
        # The cup: a 30 x 30 pocket 25 deep from its top, Plane1 at y 20.
        assert (await adapter.create_part()).is_success
        await _box(tools, "Top", (-20, -20, 20, 20), 30)
        await _ok(
            tools, "create_reference_plane", {"reference": "Top Plane", "offset": 20}
        )
        await _ok(
            tools, "create_reference_plane", {"reference": "Top Plane", "offset": 30}
        )
        await _ok(tools, "create_sketch", {"plane": "Plane2"})
        await _ok(tools, "add_rectangle", {"x1": -15, "y1": -15, "x2": 15, "y2": 15})
        await _ok(tools, "exit_sketch", {})
        report["cup_cut"] = await _invoke(tools, "create_cut_extrude", {"depth": 25})
        await _record(report, "cup_volume", step_cup_volume, adapter, tools)
        # The fin plate: a 0.5 mm fin 15 high on a 40 x 10 x 40 block.
        assert (await adapter.create_part()).is_success
        await _box(tools, "Top", (-20, -20, 20, 20), 10)
        await _ok(
            tools, "create_reference_plane", {"reference": "Top Plane", "offset": 10}
        )
        await _box(tools, "Plane1", (-0.25, -15, 0.25, 15), 15)
        await _record(report, "printability", step_printability, adapter, tools)
    finally:
        for title in {
            str(adapter._get_attr_or_call(d, "GetTitle"))
            for d in adapter.swApp.GetDocuments() or []
        } - before:
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


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
