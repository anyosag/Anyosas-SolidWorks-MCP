"""Probe the COM sequences behind the multibody tools (``adapters/solidworks/bodies.py``).

The ``lid-multibody`` branch was written without SolidWorks (another session
shared it). Every COM sequence the tools use is run here on scratch parts
through ``PyWin32Adapter``, raw first (to settle what the code assumes), then
through the adapter methods (to check the tools end to end). Each step says
what it settles; its findings go in the report.

The scratch parts, all built here and saved in the report folder:

* ``pb_lid``: a 40 x 10 x 40 lid (y 0..10) and, from a plane 2 mm up, two
  1 mm labels (10 x 6) extruded unmerged: separate bodies lying fully inside
  the lid, the session's case;
* ``pb_holder``: a 20 x 5 x 20 block, inserted into the lid by ``insert_part``.

Steps (``STEPS`` below, in order):

1. ``read``: ``GetBodies2`` and each body's ``Name``, ``GetBodyBox``,
   ``GetFaceCount``, ``GetMassProperties(1)``. Settles: volume is item 3 and
   area item 4 (the lid: 16000 mm3, 4800 mm2), and whether ``GetBodyBox`` is
   exact or padded like ``IFace2.GetBox`` (the lid's box should be 0..40,
   0..10, -20..20).
2. ``select``: ``IBody2.Select2(True, data)`` with ``data.Mark`` set, against
   ``SelectByID2(name, "SOLIDBODY", ...)``. Settles: both select a body, and
   ``GetSelectedObjectMark`` reads back the mark asked.
3. ``combine``: ``InsertCombineFeature`` on copies of the part (undone each
   time): subtract with the lid at mark 1 and a label at mark 2; add and
   common with both at mark 2, then with the main body at mark 1; and the
   bodies passed directly. Settles: which forms make a feature, and the
   result bodies' names (does the main body keep its name?).
4. ``move``: ``InsertMoveCopyBody2`` with a label at mark 1: a 5 mm shift
   along x; a 90 degree turn about y through the origin (radians or
   degrees? the box after says); a copy with ``NumCopies`` 2 (the copies'
   names and spacing).
5. ``delete``: ``InsertDeleteBody2(False)`` with a label at mark 0, then
   ``True``. Settles: mark 0 is right, and keep deletes the others.
6. ``split``: Top Plane selected by ``FeatureByName(...).Select2``, then
   ``PreSplitBody2()`` and ``PostSplitBody2(pieces, False, [null]*n,
   [""]*n, "")``. Settles: the null-dispatch origins array and empty paths
   are accepted, nothing is saved, the pieces' names.
7. ``insert``: ``IPartDoc.InsertPart3(holder, swInsertPartImportSolids, "")``
   with the holder file closed, then open. Settles: it inserts at the origin
   without a dialog, and the inserted bodies' names.
8. ``clearance``: ``IModelDoc2.ClosestDistance`` between faces of two bodies
   of one part (two labels 4 mm apart), and ``Copy`` + ``Operations2``
   (``SWBODYINTERSECT``) between a label and the lid (60 mm3). Settles: the
   distance call works on part faces, and the intersect volume.
9. ``grow``: one label's ``Copy``, ``IMathUtility.CreateTransform`` +
   ``ApplyTransform`` (a 0.15 mm shift: its box moves), ``Operations2``
   (``SWBODYADD``, returns one body), ``CreateFeatureFromBody3`` with the
   check (1) and without (0), then a Combine subtract, then
   ``InsertFeatureTreeFolder2(2)`` and renaming it. Times one tool.
10. ``indent``: ``InsertIndent(0.001, 0.00015, False, True, True, False)``
    with the lid at mark 1 and, at mark 4, a face of each label; then the
    label bodies (the session's form, null). Settles: whether the face form
    makes an Indent for tools fully inside the target.
11. ``tools``: the adapter methods end to end on a fresh lid: list_bodies,
    insert_part (moved), move_copy_body, body_clearance,
    make_mating_negative (auto), split_body, combine_bodies,
    delete_keep_body. Each reply is recorded.

It only touches documents it creates, closes them without saving, and
deletes its files at the end. The report stays in
``tests/.generated/probe_bodies/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_bodies.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import bodies as bd
from solidworks_mcp.adapters.solidworks.com_utils import double_array
from solidworks_mcp.adapters.solidworks.features import _null_callout
from solidworks_mcp.adapters.solidworks.io import _byref_int, _variant_array

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_bodies"
VT_DISPATCH, VT_BSTR = 9, 8


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.bodies import register_bodies_tools
    from solidworks_mcp.tools.modeling import register_modeling_tools
    from solidworks_mcp.tools.sketching import register_sketching_tools

    mcp = FastMCP("probe-bodies")
    await register_sketching_tools(mcp, adapter, {})
    await register_modeling_tools(mcp, adapter, {})
    await register_bodies_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _ok(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    reply = await tools[name](args)
    assert reply["status"] == "success", (name, reply)
    return reply


async def _block(
    tools: dict[str, Any],
    plane: str,
    rects: list[tuple[float, float, float, float]],
    depth: float,
    merge: bool = True,
) -> None:
    """Rectangles (sketch x1, y1, x2, y2 in mm) on ``plane``, extruded."""
    await _ok(tools, "create_sketch", {"plane": plane})
    for x1, y1, x2, y2 in rects:
        await _ok(tools, "add_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    await _ok(tools, "exit_sketch", {})
    await _ok(tools, "create_extrusion", {"depth": depth, "merge_result": merge})


async def _lid(adapter: PyWin32Adapter, tools: dict[str, Any], path: Path) -> None:
    """The lid and its two labels, saved to ``path``."""
    assert (await adapter.create_part()).is_success
    await _block(tools, "Top", [(-20, -20, 20, 20)], 10)
    await _ok(tools, "create_reference_plane", {"reference": "Top", "offset": 2})
    await _block(tools, "Plane1", [(-12, -3, -2, 3), (2, -3, 12, 3)], 1, merge=False)
    assert (await adapter.save_file(str(path))).is_success


def _model(adapter: PyWin32Adapter) -> Any:
    return adapter.currentModel


def _bodies(adapter: PyWin32Adapter) -> dict[str, Any]:
    return dict(bd.solid_bodies(adapter, _model(adapter)))


def _mm(values: Any) -> list[float] | None:
    return [round(float(v) * 1000, 4) for v in values] if values else None


def _undo_to(adapter: PyWin32Adapter, features: list[Any]) -> None:
    bd.delete_features(adapter, _model(adapter), [f for f in features if f])


def _selection(adapter: PyWin32Adapter) -> list[int]:
    sel = sw_type_info.flagged(_model(adapter).SelectionManager, "ISelectionMgr")
    count = sel.GetSelectedObjectCount2(-1)
    return [sel.GetSelectedObjectMark(i) for i in range(1, count + 1)]


# ---- the steps ---------------------------------------------------------------------


def step_read(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles GetMassProperties' order and whether GetBodyBox is padded."""
    out = {}
    for name, body in _bodies(adapter).items():
        sw_type_info.flag_members(
            body, "GetBodyBox", "GetFaceCount", "GetMassProperties"
        )
        out[name] = {
            "box_mm": _mm(body.GetBodyBox()),
            "faces": body.GetFaceCount(),
            "mass_properties": [
                round(float(v), 12) for v in body.GetMassProperties(1.0)
            ],
            "facts": bd.body_facts(adapter, name, body),
        }
    return {"expected_lid": {"volume_mm3": 16000, "area_mm2": 4800}, "bodies": out}


def step_select(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles Select2 with a SelectData mark against SelectByID2."""
    model = _model(adapter)
    names = list(_bodies(adapter))
    out: dict[str, Any] = {}
    bd.select_marked(
        adapter, model, [(names[0], _bodies(adapter)[names[0]], 2, "body")]
    )
    out["select2_marks"] = _selection(adapter)
    model.ClearSelection2(True)
    out["select_by_id"] = bd._select_by_id(adapter, model, names[1], 4)
    out["select_by_id_marks"] = _selection(adapter)
    model.ClearSelection2(True)
    return out


def _combine_form(
    adapter: PyWin32Adapter, op: int, picks: list[tuple[str, int]], direct: bool
) -> dict[str, Any]:
    model = _model(adapter)
    found = _bodies(adapter)
    before = list(found)
    manager = bd.feature_manager(adapter, model, "InsertCombineFeature")
    if direct:
        main, *tools = [found[n] for n, _m in picks]
        main_arg = main if op == bd.BODY_CUT else _null_callout()
        tool_arg = tools if op == bd.BODY_CUT else [main, *tools]
        feature = manager.InsertCombineFeature(
            op, main_arg, _variant_array(VT_DISPATCH, tool_arg)
        )
    else:
        bd.select_marked(adapter, model, [(n, found[n], m, "body") for n, m in picks])
        feature = manager.InsertCombineFeature(
            op, _null_callout(), _variant_array(VT_DISPATCH, [])
        )
    model.ClearSelection2(True)
    model.EditRebuild3()
    result = {
        "feature": bd.feature_name(adapter, feature),
        "before": before,
        "after": list(_bodies(adapter)),
    }
    _undo_to(adapter, [feature])
    return result


def step_combine(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles which InsertCombineFeature forms make a feature."""
    lid, label = (
        "Boss-Extrude1",
        next(n for n in _bodies(adapter) if n != "Boss-Extrude1"),
    )
    forms = {
        "subtract_marks_1_2": (bd.BODY_CUT, [(lid, 1), (label, 2)], False),
        "subtract_direct": (bd.BODY_CUT, [(lid, 1), (label, 2)], True),
        "add_marks_2_2": (bd.BODY_ADD, [(lid, 2), (label, 2)], False),
        "add_marks_1_2": (bd.BODY_ADD, [(lid, 1), (label, 2)], False),
        "common_marks_2_2": (bd.BODY_INTERSECT, [(lid, 2), (label, 2)], False),
        "common_marks_1_2": (bd.BODY_INTERSECT, [(lid, 1), (label, 2)], False),
        "common_direct": (bd.BODY_INTERSECT, [(lid, 2), (label, 2)], True),
    }
    out = {}
    for key, (op, picks, direct) in forms.items():
        try:
            out[key] = _combine_form(adapter, op, picks, direct)
        except Exception as exc:  # noqa: BLE001 - a probe records every failure
            out[key] = {"error": repr(exc)}
    return out


def step_move(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles InsertMoveCopyBody2's units, turn sense and copy names."""
    model = _model(adapter)
    label = next(n for n in _bodies(adapter) if n != "Boss-Extrude1")
    manager = bd.feature_manager(adapter, model, "InsertMoveCopyBody2")
    out: dict[str, Any] = {"label": label}
    # TransX..Z, TransDist, RotPointX..Z, RotAngleX..Z, BCopy, NumCopies.
    quarter = 1.5707963267948966
    cases = {
        "shift_x_5mm": (0.005, 0, 0, 0, 0, 0, 0, 0, 0, 0, False, 1),
        "turn_y_quarter_about_origin": (0, 0, 0, 0, 0, 0, 0, 0, quarter, 0, False, 1),
        "copy_shift_z_10mm_twice": (0, 0, 0.01, 0, 0, 0, 0, 0, 0, 0, True, 2),
    }
    for key, args in cases.items():
        before = _boxes(adapter)
        found = _bodies(adapter)
        bd.select_marked(adapter, model, [(label, found[label], 1, "body")])
        feature = manager.InsertMoveCopyBody2(*args)
        model.ClearSelection2(True)
        model.EditRebuild3()
        out[key] = {
            "feature": bd.feature_name(adapter, feature),
            "box_before_mm": before.get(label),
            "boxes_after_mm": _boxes(adapter),
        }
        _undo_to(adapter, [feature])
    return out


def _boxes(adapter: PyWin32Adapter) -> dict[str, list[float] | None]:
    """Each body's GetBodyBox, mm."""
    boxes = {}
    for name, body in _bodies(adapter).items():
        sw_type_info.flag_members(body, "GetBodyBox")
        boxes[name] = _mm(body.GetBodyBox())
    return boxes


def step_delete(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles InsertDeleteBody2 with the body at mark 0, deleting and keeping."""
    model = _model(adapter)
    label = next(n for n in _bodies(adapter) if n != "Boss-Extrude1")
    manager = bd.feature_manager(adapter, model, "InsertDeleteBody2")
    out = {}
    for keep in (False, True):
        found = _bodies(adapter)
        bd.select_marked(adapter, model, [(label, found[label], 0, "body")])
        feature = manager.InsertDeleteBody2(keep)
        model.ClearSelection2(True)
        model.EditRebuild3()
        out[f"keep_{keep}"] = {
            "feature": bd.feature_name(adapter, feature),
            "after": list(_bodies(adapter)),
        }
        _undo_to(adapter, [feature])
    return out


def step_split(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles PreSplitBody2 / PostSplitBody2 with null origins and empty paths."""
    from solidworks_mcp.adapters.solidworks.features import _select_feature_by_name

    model = _model(adapter)
    before = list(_bodies(adapter))
    plane = _select_feature_by_name(adapter, "Plane1")
    manager = bd.feature_manager(adapter, model, "PreSplitBody2", "PostSplitBody2")
    pieces = list(manager.PreSplitBody2() or [])
    model.ClearSelection2(True)
    boxes = []
    for piece in pieces:
        sw_type_info.flag_members(piece, "GetBodyBox")
        boxes.append(_mm(piece.GetBodyBox()))
    n = len(pieces)
    feature = manager.PostSplitBody2(
        _variant_array(VT_DISPATCH, pieces),
        False,
        _variant_array(VT_DISPATCH, [None] * n),
        _variant_array(VT_BSTR, [""] * n),
        "",
    )
    model.EditRebuild3()
    out = {
        "plane_selected": plane is not None,
        "pieces": n,
        "piece_boxes_mm": boxes,
        "feature": bd.feature_name(adapter, feature),
        "before": before,
        "after": list(_bodies(adapter)),
    }
    _undo_to(adapter, [feature])
    return out


def step_insert(adapter: PyWin32Adapter, holder: Path) -> dict[str, Any]:
    """Settles InsertPart3 with import-solids, the file closed, and the body names."""
    model = _model(adapter)
    before = list(_bodies(adapter))
    sw_type_info.flag_members(model, "InsertPart3")
    started = time.perf_counter()
    feature = model.InsertPart3(str(holder), bd.INSERT_PART_IMPORT_SOLIDS, "")
    model.EditRebuild3()
    out = {
        "feature": bd.feature_name(adapter, feature),
        "seconds": round(time.perf_counter() - started, 2),
        "added": [n for n in _bodies(adapter) if n not in before],
    }
    found = _bodies(adapter)
    out["added_boxes_mm"] = {}
    for n in out["added"]:
        sw_type_info.flag_members(found[n], "GetBodyBox")
        out["added_boxes_mm"][n] = _mm(found[n].GetBodyBox())
    _undo_to(adapter, [feature])
    return out


def step_clearance(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles ClosestDistance on part faces and the Operations2 intersect volume."""
    labels = [n for n in _bodies(adapter) if n != "Boss-Extrude1"]
    out: dict[str, Any] = {"labels": labels, "expected_gap_mm": 4.0}
    if len(labels) >= 2:
        out["gap"] = bd.body_clearance(adapter, labels[0], labels[1])
    out["overlap"] = bd.body_clearance(adapter, labels[0], "Boss-Extrude1")
    found = _bodies(adapter)
    first, second = (
        bd.temp_copy(adapter, found[labels[0]]),
        bd.temp_copy(adapter, found["Boss-Extrude1"]),
    )
    code = _byref_int()
    pieces = first.Operations2(bd.BODY_INTERSECT, second, code)
    out["raw_intersect"] = {
        "pieces": len(pieces or []),
        "error_code": getattr(code, "value", None),
        "expected_mm3": 60.0,
    }
    return out


def step_grow(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles the shifted-copy union, the feature from it, the subtract and the folder."""
    model = _model(adapter)
    label = next(n for n in _bodies(adapter) if n != "Boss-Extrude1")
    found = _bodies(adapter)
    utility = bd._math_utility(adapter)
    out: dict[str, Any] = {"label": label}
    piece = bd.temp_copy(adapter, found[label])
    sw_type_info.flag_members(piece, "GetBodyBox")
    box_before = _mm(piece.GetBodyBox())
    xform = utility.CreateTransform(double_array(bd.translation_data((0.00015, 0, 0))))
    out["apply_transform"] = piece.ApplyTransform(xform)
    out["shifted_box_mm"] = {"before": box_before, "after": _mm(piece.GetBodyBox())}
    started = time.perf_counter()
    grown = bd.grow_tool(
        adapter, label, found[label], bd.shift_offsets(0.00015, 32), utility
    )
    out["grow_seconds_one_tool"] = round(time.perf_counter() - started, 2)
    out["grown_pieces"] = len(grown)
    out["grown_volume_mm3"] = bd.volume_mm3(adapter, grown[0])
    out["label_volume_mm3"] = bd.volume_mm3(adapter, found[label])
    before = list(_bodies(adapter))
    sw_type_info.flag_members(model, "CreateFeatureFromBody3")
    checked = model.CreateFeatureFromBody3(
        grown[0], False, bd.CREATE_FEATURE_BODY_CHECK
    )
    out["from_body_with_check"] = bd.feature_name(adapter, checked)
    made = [checked]
    if checked is None:
        unchecked = model.CreateFeatureFromBody3(grown[0], False, 0)
        out["from_body_without_check"] = bd.feature_name(adapter, unchecked)
        made = [unchecked]
    new = [n for n in _bodies(adapter) if n not in before]
    out["new_bodies"] = new
    found = _bodies(adapter)
    combine, form = bd._insert_combine(
        adapter,
        model,
        "subtract",
        ("Boss-Extrude1", found["Boss-Extrude1"]),
        [(n, found[n]) for n in new],
    )
    out["combine"] = {"feature": bd.feature_name(adapter, combine), "form": form}
    made.append(combine)
    model.EditRebuild3()
    out["lid_volume_after_mm3"] = bd.volume_mm3(
        adapter, _bodies(adapter)["Boss-Extrude1"]
    )
    out["residual_mm3"] = bd.intersect_mm3(
        adapter, _bodies(adapter)[label], _bodies(adapter)["Boss-Extrude1"]
    )
    out["folder"] = bd.make_folder(adapter, model, made, "Negative of probe")
    _undo_to(adapter, made)
    return out


def step_indent(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles whether InsertIndent cuts tools fully inside the target, by form."""
    model = _model(adapter)
    labels = [n for n in _bodies(adapter) if n != "Boss-Extrude1"]
    manager = bd.feature_manager(adapter, model, "InsertIndent")
    out = {}
    for whole in (False, True):
        picks = bd._indent_picks(adapter, model, "Boss-Extrude1", labels, whole)
        bd.select_marked(adapter, model, picks or [])
        feature = manager.InsertIndent(0.001, 0.00015, False, True, True, False)
        model.ClearSelection2(True)
        model.EditRebuild3()
        key = "tool_bodies" if whole else "tool_faces"
        out[key] = {
            "feature": bd.feature_name(adapter, feature),
            "lid_volume_mm3": bd.volume_mm3(adapter, _bodies(adapter)["Boss-Extrude1"]),
        }
        _undo_to(adapter, [feature])
    return out


async def step_tools(
    adapter: PyWin32Adapter, tools: dict[str, Any], holder: Path
) -> dict[str, Any]:
    """The adapter methods end to end, through the tools."""
    out: dict[str, Any] = {}

    async def call(key: str, name: str, args: dict[str, Any]) -> Any:
        started = time.perf_counter()
        reply = await tools[name](args)
        reply["probe_seconds"] = round(time.perf_counter() - started, 2)
        out[key] = reply
        return reply

    listed = await call("list_bodies", "list_bodies", {})
    labels = [b["name"] for b in listed["data"]["bodies"]][1:]
    await call(
        "insert_part",
        "insert_part",
        {"file_path": str(holder), "translate_mm": [0, 20, 0]},
    )
    if len(labels) >= 2:
        await call(
            "body_clearance",
            "body_clearance",
            {"body_a": labels[0], "body_b": labels[1]},
        )
        await call(
            "make_mating_negative",
            "make_mating_negative",
            {"target": "Boss-Extrude1", "tools": labels, "clearance_mm": 0.15},
        )
        await call("delete_keep_body", "delete_keep_body", {"bodies": labels})
    await call("split_body", "split_body", {"plane": "Right", "body": "Boss-Extrude1"})
    pieces = [
        p["name"] for p in (out["split_body"].get("data") or {}).get("pieces", [])
    ]
    if len(pieces) == 2:
        await call(
            "combine_bodies",
            "combine_bodies",
            {"operation": "add", "main": pieces[0], "tools": [pieces[1]]},
        )
    await call("list_bodies_after", "list_bodies", {})
    return out


# ---- the run -----------------------------------------------------------------------

Step = Callable[..., Any]


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
    lid, holder = scratch / "pb_lid.SLDPRT", scratch / "pb_holder.SLDPRT"
    try:
        tools = await _tools(adapter)
        assert (await adapter.create_part()).is_success
        await _block(tools, "Top", [(-10, -10, 10, 10)], 5)
        assert (await adapter.save_file(str(holder))).is_success
        assert (await adapter.close_model(False)).is_success
        await _lid(adapter, tools, lid)
        steps: list[tuple[str, Step, tuple[Any, ...]]] = [
            ("read", step_read, ()),
            ("select", step_select, ()),
            ("combine", step_combine, ()),
            ("move", step_move, ()),
            ("delete", step_delete, ()),
            ("split", step_split, ()),
            ("insert", step_insert, (holder,)),
            ("clearance", step_clearance, ()),
            ("grow", step_grow, ()),
            ("indent", step_indent, ()),
        ]
        for key, step, args in steps:
            print(f"[probe] {key}: {step.__doc__}")
            try:
                report[key] = step(adapter, *args)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report[key] = {"error": repr(exc)}
        await _lid(adapter, tools, scratch / "pb_lid_tools.SLDPRT")
        tools_step: Awaitable[Any] = step_tools(adapter, tools, holder)
        try:
            report["tools"] = await tools_step
        except Exception as exc:  # noqa: BLE001
            report["tools"] = {"error": repr(exc)}
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
