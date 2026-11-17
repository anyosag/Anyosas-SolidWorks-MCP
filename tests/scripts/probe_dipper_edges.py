"""Probe the dipper-edges branch's COM paths: edge reading, convexity, ids, planes.

The ``dipper-edges`` branch was written without SolidWorks (another session
had it). Each COM sequence it relies on is run here on scratch parts built
through ``PyWin32Adapter``: raw first, to settle what the code assumes, then
through the adapter methods, to check them end to end. Each step says what
it settles; its findings go in the report.

Steps (``STEPS`` below, in order):

1. ``index`` - a 10 mm plate with a grid of 4 x 4 x 3 mm pockets on an 8 mm
   pitch cut into its top (one body: the top face carries every rim) and a
   10 mm block well away from it (a second body). With the default
   ``--grid 20x12`` the plate is 172 x 108 mm and the part has 2,904 edges
   (the Dipper's master had about 2,275). Settles:

   * ``IModelDoc2.GetUpdateStamp`` as the cache key: it is unchanged by reads
     (``list_features``, ``select_edges``, ``ClearSelection2``) and changes
     with a rebuild and a dimension change; also whether ``ForceRebuild3``
     on an unchanged part moves it;
   * the identity the two-face rule needs: ``IFace2.GetEdges`` read twice
     gives the same COM objects (``same_com_object``), whose ``_oleobj_``
     hash equal; an edge from two faces' lists is one object; and
     ``IBody2.GetFirstFace`` against ``GetFaces()[0]``;
   * that every edge of a few pockets has two faces (``GetTwoAdjacentFaces2``),
     both of which list it: the ground the two-face rule stands on;
   * the old path (every edge described, as ``select_edges`` did before)
     against the new one for three pocket boxes: the same edges, how long
     each took, what ``data.read`` says, and how long the second call on
     the unchanged part takes (the kept index).

2. ``convexity`` - a 40 x 20 x 30 block with a pocket (concave floor and
   corners, convex rims), a through hole (convex rims), a boss (concave
   foot, convex top) and a 2 mm fillet on an outer edge (two tangent edges,
   two convex arcs); and a revolved cylinder with a cone on it, whose
   shared circle is convex between two curved faces. Settles: each
   ``convexity`` answer against the expected; the raw reads behind it
   (``GetTwoAdjacentFaces2``, each face's ``Normal``, a curved face's
   ``GetSurface().EvaluateAtPoint`` and ``FaceInSurfaceSense``).

3. ``persist`` - on the convexity block. Settles: what
   ``GetPersistReference3`` returns through pywin32 (type, length), and
   ``GetObjectFromPersistReference3`` (the object, or a tuple, and the
   error code); that the ids resolve to the same edges after
   ``ForceRebuild3``, after the pocket's depth changes (``set_dimension``),
   and not after the boss is deleted; that ``add_fillet`` takes ``edge_ids``.

4. ``plane`` - a 20 mm block with a boss on ``Plane1``, 10 mm above Top
   Plane. Settles: what an offset plane's ``IRefPlaneFeatureData`` reads
   (``Distance``, ``Type``, ``Constraint(0)`` and
   ``ReversedReferenceDirection(0)`` by ``IDispatch.Invoke``, and the plain
   property forms); that ``edit_feature`` with ``offset: 9.8`` moves the
   plane and the boss (body box and volume), that ``flip: true`` puts it on
   the other side and back; and that Top Plane is refused as not offset.

It only touches the documents it creates and closes them without saving.
The report stays in ``tests/.generated/probe_dipper_edges/``.

Usage (SolidWorks running, nothing else driving it, the user's OK)::

    $env:PYTHONPATH = "src"
    .venv\\Scripts\\python.exe tests/scripts/probe_dipper_edges.py [--grid 20x12]
    [--steps index,convexity,persist,plane]
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from solidworks_mcp.adapters import sw_type_info
from solidworks_mcp.adapters.base import ExtrusionParameters, RevolveParameters
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import edge_index, edge_rules
from solidworks_mcp.adapters.solidworks import ref_plane_edit as rp
from solidworks_mcp.adapters.solidworks.face_selection import describe_edge
from solidworks_mcp.adapters.solidworks.io import _byref_int, _variant_array

OUT = Path(__file__).resolve().parents[1] / ".generated" / "probe_dipper_edges"
VT_UI1 = 17

PITCH, POCKET, MARGIN, DEPTH = 8.0, 4.0, 6.0, 3.0


# ---- building --------------------------------------------------------------------


async def _tools(adapter: PyWin32Adapter) -> dict[str, Any]:
    from fastmcp import FastMCP

    from solidworks_mcp.tools.modeling import register_modeling_tools

    mcp = FastMCP("probe-dipper-edges")
    await register_modeling_tools(mcp, adapter, {})
    return {t.name: t.fn for t in await mcp.list_tools()}


async def _invoke(tools: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    """Call a tool; one with no input model takes keywords only."""
    fn = tools[name]
    positional = [
        p
        for p in inspect.signature(fn).parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if not positional:
        return await fn(**args)
    return await fn(args)


def _ok(result: Any, what: str) -> Any:
    assert result.is_success, f"{what}: {result.error}"
    return result.data


async def _new_part(adapter: PyWin32Adapter) -> str:
    _ok(await adapter.create_part(), "create_part")
    return str(adapter._get_attr_or_call(adapter.currentModel, "GetTitle"))


async def _sketch(
    adapter: PyWin32Adapter,
    plane: str,
    rects: Sequence[tuple[float, float, float, float]] = (),
    circles: Sequence[tuple[float, float, float]] = (),
) -> None:
    _ok(await adapter.create_sketch(plane), f"create_sketch {plane}")
    for x1, y1, x2, y2 in rects:
        _ok(await adapter.add_rectangle(x1, y1, x2, y2), "add_rectangle")
    for x, y, r in circles:
        _ok(await adapter.add_circle(x, y, r), "add_circle")
    _ok(await adapter.exit_sketch(), "exit_sketch")


def _bodies(adapter: PyWin32Adapter) -> list[Any]:
    model = adapter.currentModel
    sw_type_info.flag_members(model, "GetBodies2")
    return list(model.GetBodies2(0, False) or [])


def _body_boxes_mm(adapter: PyWin32Adapter) -> list[list[float]]:
    boxes = []
    for body in _bodies(adapter):
        sw_type_info.flag_members(body, "GetBodyBox")
        boxes.append([round(float(c) * 1000, 4) for c in body.GetBodyBox()])
    return boxes


def _z_sign(adapter: PyWin32Adapter) -> float:
    """-1 when a Top sketch's y runs along model -Z (the default), else +1."""
    box = _body_boxes_mm(adapter)[0]
    return -1.0 if box[2] < -1.0 else 1.0


def _stamp(adapter: PyWin32Adapter) -> int | None:
    return edge_index.update_stamp(adapter, adapter.currentModel)


def _keys(edges: list[dict[str, Any]]) -> list[Any]:
    return sorted(str(edge_rules.edge_key(e)) for e in edges)


# ---- 1. index -----------------------------------------------------------------------


def _pocket_box(i: int, j: int, z_sign: float) -> list[list[float]]:
    """An inside_box (model mm) around pocket (i, j) and nothing else."""
    x0, y0 = MARGIN + PITCH * i, MARGIN + PITCH * j
    z = sorted([z_sign * y0, z_sign * (y0 + POCKET)])
    return [[x0 - 1, 10 - DEPTH - 1, z[0] - 1], [x0 + POCKET + 1, 11, z[1] + 1]]


async def _pocket_plate(adapter: PyWin32Adapter, grid: tuple[int, int]) -> str:
    title = await _new_part(adapter)
    width = MARGIN * 2 + PITCH * grid[0]
    depth = MARGIN * 2 + PITCH * grid[1]
    await _sketch(adapter, "Top", [(0, 0, width, depth)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=10)), "plate")
    plane = _ok(await adapter.create_reference_plane("Top Plane", offset=10), "Plane1")
    pockets = [
        (MARGIN + PITCH * i, MARGIN + PITCH * j, MARGIN + PITCH * i + POCKET)
        for i in range(grid[0])
        for j in range(grid[1])
    ]
    await _sketch(
        adapter, plane["name"], [(x, y, x2, y + POCKET) for x, y, x2 in pockets]
    )
    _ok(await adapter.create_cut_extrude(ExtrusionParameters(depth=DEPTH)), "pockets")
    await _sketch(adapter, "Top", [(width + 50, 0, width + 60, 10)])
    _ok(
        await adapter.create_extrusion(
            ExtrusionParameters(depth=10, merge_result=False)
        ),
        "far block",
    )
    return title


def _old_path(adapter: PyWin32Adapter) -> tuple[list[dict[str, Any]], float]:
    """Every edge of every body described, as select_edges did before."""
    started = time.perf_counter()
    infos = []
    for body in _bodies(adapter):
        sw_type_info.flag_members(body, "GetEdges")
        infos += [describe_edge(adapter, e) for e in body.GetEdges() or []]
    return infos, time.perf_counter() - started


def _identity(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles: one COM object per entity, and its hash."""
    body = _bodies(adapter)[0]
    sw_type_info.flag_members(body, "GetFaces", "GetFirstFace")
    faces = list(body.GetFaces() or [])
    first = adapter._attempt(lambda: body.GetFirstFace())
    small, a = faces[0], []
    for face in faces:  # a pocket's wall: four edges
        sw_type_info.flag_members(face, "GetEdges")
        a = list(face.GetEdges() or [])
        small = face
        if 0 < len(a) <= 4:
            break
    b = list(small.GetEdges() or [])
    hashes = []
    for x, y in zip(a, b, strict=True):
        try:
            hashes.append(hash(x._oleobj_) == hash(y._oleobj_))
        except TypeError as exc:
            hashes.append(repr(exc))
    return {
        "faces": len(faces),
        "first_face_is_faces_0": sw_type_info.same_com_object(first, faces[0]),
        "edges_read_twice_same": [
            sw_type_info.same_com_object(x, y) for x, y in zip(a, b, strict=True)
        ],
        "hashes_equal": hashes,
        "wrappers_are_new": all(x is not y for x, y in zip(a, b, strict=True)),
    }


def _two_faces(adapter: PyWin32Adapter, boxes: list[list[list[float]]]) -> list[Any]:
    """Settles: each edge of a pocket has two faces, and both list it."""
    out = []
    for box in boxes:
        index = edge_index.PartIndex(adapter, adapter.currentModel, None)
        reach = index.edges_in_reach(edge_index.box_from_mm(box))
        for edge in reach.edges:
            sw_type_info.flag_members(edge, "GetTwoAdjacentFaces2")
            faces = list(edge.GetTwoAdjacentFaces2() or [])
            listed = []
            for face in faces:
                sw_type_info.flag_members(face, "GetEdges")
                listed.append(
                    any(sw_type_info.same_com_object(edge, e) for e in face.GetEdges())
                )
            out.append({"faces": len(faces), "both_list_it": all(listed)})
    return out


async def step_index(adapter: PyWin32Adapter, grid: tuple[int, int]) -> dict[str, Any]:
    """Settles the update stamp, the identity, and the new path against the old."""
    out: dict[str, Any] = {"title": await _pocket_plate(adapter, grid)}
    z_sign = _z_sign(adapter)
    out["body_boxes_mm"] = _body_boxes_mm(adapter)
    model = adapter.currentModel
    stamps = {"start": _stamp(adapter)}
    await adapter.list_features()
    stamps["after_list_features"] = _stamp(adapter)
    boxes = [
        _pocket_box(0, 0, z_sign),
        _pocket_box(grid[0] // 2, grid[1] // 2, z_sign),
        _pocket_box(grid[0] - 1, grid[1] - 1, z_sign),
    ]
    await adapter.select_edges({"inside_box": boxes[0]})
    stamps["after_select_edges"] = _stamp(adapter)
    adapter._attempt(lambda: model.ClearSelection2(True))
    stamps["after_clear_selection"] = _stamp(adapter)
    adapter._attempt(lambda: model.ForceRebuild3(True))
    stamps["after_force_rebuild"] = _stamp(adapter)
    out["stamps"] = stamps
    out["identity"] = _identity(adapter)
    out["two_faces"] = _two_faces(adapter, boxes[1:2])

    infos, seconds = _old_path(adapter)
    out["old_path"] = {"edges": len(infos), "seconds": round(seconds, 2)}
    runs = []
    for box in boxes:
        expected, _ = edge_rules.filter_edges(
            [(None, i) for i in infos], {"inside_box": box}
        )
        edge_index.forget(adapter)
        started = time.perf_counter()
        first = await adapter.select_edges({"inside_box": box})
        first_seconds = time.perf_counter() - started
        started = time.perf_counter()
        again = await adapter.select_edges({"inside_box": box})
        again_seconds = time.perf_counter() - started
        data = first.data or {}
        runs.append(
            {
                "box_mm": box,
                "expected_edges": len(expected),
                "ok": first.is_success,
                "error": first.error,
                "selected": data.get("selected_edges"),
                "same_edges_as_old_path": _keys([info for _e, info in expected])
                == _keys(data.get("edges") or []),
                "matched": data.get("matched"),
                "read": data.get("read"),
                "seconds": round(first_seconds, 3),
                "again_seconds": round(again_seconds, 3),
                "again_read": (again.data or {}).get("read"),
            }
        )
    out["new_path"] = runs
    held = getattr(adapter, "_edge_index", None)
    out["index"] = {
        "edge_identity": getattr(held, "edge_identity", None),
        "hashed": getattr(getattr(held, "_edges", None), "hashed", None),
    }
    dims = await adapter.set_dimension("D1@Cut-Extrude1", 2.5)
    stamps["after_set_dimension"] = _stamp(adapter)
    out["set_dimension_ok"] = dims.is_success
    return out


# ---- 2. convexity --------------------------------------------------------------------


async def _feature_block(adapter: PyWin32Adapter) -> tuple[str, float]:
    title = await _new_part(adapter)
    await _sketch(adapter, "Top", [(0, 0, 40, 30)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=20)), "block")
    z_sign = _z_sign(adapter)
    plane = _ok(await adapter.create_reference_plane("Top Plane", offset=20), "plane")
    await _sketch(adapter, plane["name"], [(5, 5, 15, 15)])
    _ok(await adapter.create_cut_extrude(ExtrusionParameters(depth=5)), "pocket")
    await _sketch(adapter, plane["name"], circles=[(30, 8, 3)])
    _ok(await adapter.create_cut_extrude(ExtrusionParameters(depth=20)), "hole")
    await _sketch(adapter, plane["name"], circles=[(30, 22, 4)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=5)), "boss")
    _ok(await adapter.add_fillet(2.0, ["0.04,0.01,0"]), "fillet")
    return title, z_sign


def _z(box: list[list[float]], z_sign: float) -> list[list[float]]:
    """A box given with Top-sketch z (model -Z by default) in model z."""
    z = sorted([z_sign * -box[0][2], z_sign * -box[1][2]])
    return [[box[0][0], box[0][1], z[0]], [box[1][0], box[1][1], z[1]]]


#: (name, rule with a Top-sketch z, the edges expected).
CONVEXITY_CASES = [
    (
        "pocket concave",
        {"inside_box": [[4, 14, -16], [16, 21, -4]], "convexity": "concave"},
        8,
    ),
    (
        "pocket convex",
        {"inside_box": [[4, 14, -16], [16, 21, -4]], "convexity": "convex"},
        4,
    ),
    (
        "hole rims",
        {
            "inside_box": [[26, -1, -12], [34, 21, -4]],
            "edge_type": "circular",
            "convexity": "convex",
        },
        2,
    ),
    (
        "boss foot",
        {
            "inside_box": [[25, 19, -27], [35, 21, -17]],
            "edge_type": "circular",
            "convexity": "concave",
        },
        1,
    ),
    (
        "boss top",
        {
            "inside_box": [[25, 24, -27], [35, 26, -17]],
            "edge_type": "circular",
            "convexity": "convex",
        },
        1,
    ),
    (
        "fillet tangent",
        {"inside_box": [[37, -1, -3], [41, 21, 1]], "convexity": "tangent"},
        2,
    ),
    (
        "fillet arcs",
        {"inside_box": [[37, -1, -3], [41, 21, 1]], "convexity": "convex"},
        2,
    ),
]


def _faces_raw(adapter: PyWin32Adapter, edge: Any, point: Any) -> list[dict[str, Any]]:
    """What the convexity reading reads of an edge's faces."""
    sw_type_info.flag_members(edge, "GetTwoAdjacentFaces2")
    out = []
    for face in list(adapter._attempt(lambda: edge.GetTwoAdjacentFaces2()) or []):
        sw_type_info.flag_members(face, "GetSurface", "FaceInSurfaceSense")
        entry: dict[str, Any] = {
            "normal": adapter._attempt(lambda f=face: list(f.Normal)),
            "in_surface_sense": adapter._attempt(lambda f=face: f.FaceInSurfaceSense()),
        }
        surface = adapter._attempt(lambda f=face: f.GetSurface())
        if surface is not None and point is not None:
            sw_type_info.flag_members(surface, "EvaluateAtPoint")
            entry["evaluate_at_point"] = adapter._attempt(
                lambda s=surface: list(s.EvaluateAtPoint(*point))
            )
        out.append(entry)
    return out


async def _convexity_cases(adapter: PyWin32Adapter, z_sign: float) -> list[Any]:
    out = []
    for name, rule, expected in CONVEXITY_CASES:
        query = {**rule, "inside_box": _z(rule["inside_box"], z_sign)}
        result = await adapter.select_edges(query)
        data = result.data or {}
        out.append(
            {
                "case": name,
                "expected": expected,
                "selected": data.get("selected_edges"),
                "error": result.error,
                "matched": data.get("matched"),
            }
        )
    # The raw reads for every edge of the pocket, once.
    box = _z([[4, 14, -16], [16, 21, -4]], z_sign)
    selector = edge_rules.EdgeRuleSelector(adapter)
    for edge, info in selector.candidates({"inside_box": box}):
        located = selector._convexity.point_and_direction(edge, info)
        out.append(
            {
                "edge": {k: info.get(k) for k in ("start_mm", "end_mm", "center_mm")},
                "convexity": selector.convexity(edge, info),
                "faces": _faces_raw(adapter, edge, located[0] if located else None),
            }
        )
    return out


async def _cone(adapter: PyWin32Adapter) -> dict[str, Any]:
    """A cylinder with a cone on it: their circle is between two curved faces."""
    title = await _new_part(adapter)
    _ok(await adapter.create_sketch("Front"), "create_sketch")
    for x1, y1, x2, y2 in [
        (0, 0, 10, 0),
        (10, 0, 10, 10),
        (10, 10, 5, 15),
        (5, 15, 0, 15),
        (0, 15, 0, 0),
    ]:
        _ok(await adapter.add_line(x1, y1, x2, y2), "add_line")
    _ok(await adapter.add_centerline(0, -5, 0, 20), "add_centerline")
    _ok(await adapter.exit_sketch(), "exit_sketch")
    _ok(await adapter.create_revolve(RevolveParameters(angle=360)), "revolve")
    points = [[10, 5, 0], [7.5, 12.5, 0]]
    convex = await adapter.select_edges(
        {"between_faces": points, "convexity": "convex"}
    )
    concave = await adapter.select_edges(
        {"between_faces": points, "convexity": "concave"}
    )
    selector = edge_rules.EdgeRuleSelector(adapter)
    raw = []
    for edge, info in selector.candidates({"between_faces": points}):
        located = selector._convexity.point_and_direction(edge, info)
        raw.append(_faces_raw(adapter, edge, located[0] if located else None))
    return {
        "title": title,
        "convex": {
            "selected": (convex.data or {}).get("selected_edges"),
            "error": convex.error,
        },
        "concave_error": concave.error,
        "expected": "1 convex, 0 concave",
        "raw": raw,
    }


async def step_convexity(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles each convexity answer and the reads behind it."""
    title, z_sign = await _feature_block(adapter)
    return {
        "title": title,
        "z_sign": z_sign,
        "cases": await _convexity_cases(adapter, z_sign),
        "cone": await _cone(adapter),
    }


# ---- 3. persist ---------------------------------------------------------------------


def _raw_persist(adapter: PyWin32Adapter, edge: Any) -> dict[str, Any]:
    """Settles what the two persist calls return through pywin32."""
    extension = adapter.currentModel.Extension
    sw_type_info.flag_members(
        extension, "GetPersistReference3", "GetObjectFromPersistReference3"
    )
    raw = adapter._attempt(lambda: extension.GetPersistReference3(edge))
    code = _byref_int()
    back = adapter._attempt(
        lambda: extension.GetObjectFromPersistReference3(
            _variant_array(VT_UI1, list(bytes(raw))), code
        )
    )
    return {
        "reference_type": type(raw).__name__,
        "reference_length": None if raw is None else len(raw),
        "resolved_type": type(back).__name__,
        "resolved_is_same": sw_type_info.same_com_object(
            back[0] if isinstance(back, tuple) else back, edge
        ),
        "error_code": getattr(code, "value", code),
    }


async def step_persist(
    adapter: PyWin32Adapter, tools: dict[str, Any]
) -> dict[str, Any]:
    """Settles the persist calls' shapes, and ids across a rebuild, an edit, a delete."""
    title, z_sign = await _feature_block(adapter)
    out: dict[str, Any] = {"title": title}
    pocket = _z([[4, 14, -16], [16, 21, -4]], z_sign)
    boss_top = _z([[25, 24, -27], [35, 26, -17]], z_sign)
    first = _ok(
        await adapter.select_edges({"inside_box": pocket, "with_ids": True}), "ids"
    )
    ids = first["edge_ids"]
    out["id_lengths"] = sorted({len(i) for i in ids if i})
    selector = edge_rules.EdgeRuleSelector(adapter)
    edge = selector.candidates({"inside_box": pocket})[0][0]
    out["raw"] = _raw_persist(adapter, edge)

    adapter._attempt(lambda: adapter.currentModel.ForceRebuild3(True))
    again = await adapter.select_edges({"edge_ids": ids})
    out["after_rebuild"] = {
        "ok": again.is_success,
        "error": again.error,
        "same_edges": _keys((again.data or {}).get("edges") or [])
        == _keys(first["edges"]),
    }
    dims = await adapter.set_dimension("D1@Cut-Extrude1", 4.0)
    moved = await adapter.select_edges({"edge_ids": ids})
    out["after_depth_change"] = {
        "set_dimension": dims.is_success,
        "ok": moved.is_success,
        "error": moved.error,
        "edges": (moved.data or {}).get("edges"),
    }
    rims = [
        i
        for i, info in zip(ids, first["edges"], strict=False)
        if abs(abs(info["start_mm"][1]) - 20) < 1e-6
        and abs(abs(info["end_mm"][1]) - 20) < 1e-6
    ]
    fillet = await _invoke(tools, "add_fillet", {"radius": 0.5, "edge_ids": rims})
    out["add_fillet_by_ids"] = {"rims": len(rims), "reply": fillet}
    boss = _ok(
        await adapter.select_edges({"inside_box": boss_top, "with_ids": True}), "boss"
    )
    deleted = await adapter.delete_feature("Boss-Extrude2")
    gone = await adapter.select_edges({"edge_ids": boss["edge_ids"]})
    out["after_boss_deleted"] = {
        "deleted": deleted.is_success,
        "error": gone.error,
        "expected": "edge_ids 0 no longer name an edge",
    }
    return out


# ---- 4. plane -----------------------------------------------------------------------


def _plane_raw(adapter: PyWin32Adapter, name: str) -> dict[str, Any]:
    """Settles what an IRefPlaneFeatureData reads."""
    feature = adapter.currentModel.FeatureByName(name)
    sw_type_info.flag_members(feature, "GetDefinition", "GetTypeName2")
    data = feature.GetDefinition()
    reads: dict[str, Any] = {
        "type_name": adapter._attempt(lambda: feature.GetTypeName2())
    }
    for prop in (
        "Distance",
        "Type",
        "Angle",
        "ReversedReferenceDirection",
        "Constraint",
    ):
        try:
            reads[prop] = repr(getattr(data, prop))
        except Exception as exc:  # noqa: BLE001 - a probe records every failure
            reads[prop] = f"error: {exc!r}"
    for prop in ("Constraint", "ReversedReferenceDirection", "Reference"):
        try:
            reads[f"{prop}(0)"] = repr(rp.get_indexed(data, prop, 0))
        except Exception as exc:  # noqa: BLE001
            reads[f"{prop}(0)"] = f"error: {exc!r}"
    reads["definition"] = rp.read_definition(adapter, data).values()
    reads["origin_mm"] = rp.plane_origin_mm(adapter, feature)
    return reads


async def step_plane(adapter: PyWin32Adapter) -> dict[str, Any]:
    """Settles the plane reads, and that offset and flip move the plane and the boss."""
    title = await _new_part(adapter)
    await _sketch(adapter, "Top", [(0, 0, 20, 20)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=10)), "block")
    plane = _ok(await adapter.create_reference_plane("Top Plane", offset=10), "plane")
    await _sketch(adapter, plane["name"], [(5, 5, 15, 15)])
    _ok(await adapter.create_extrusion(ExtrusionParameters(depth=5)), "boss")
    out: dict[str, Any] = {"title": title, "plane": plane["name"]}
    out["raw_before"] = _plane_raw(adapter, plane["name"])

    async def state() -> dict[str, Any]:
        summary = await adapter.get_model_geometry_summary()
        return {"bodies_mm": _body_boxes_mm(adapter), "summary": summary.data}

    out["before"] = await state()
    for key, changes in (
        ("offset_9_8", {"offset": 9.8}),
        ("flip", {"flip": True}),
        ("back", {"flip": False, "offset": 10}),
    ):
        result = await adapter.edit_feature(plane["name"], changes)
        out[key] = {
            "ok": result.is_success,
            "error": result.error,
            "reply": result.data,
            "state": await state(),
            "raw": _plane_raw(adapter, plane["name"]),
        }
    top = await adapter.edit_feature("Top Plane", {"offset": 5})
    out["top_plane"] = {"error": top.error, "raw": _plane_raw(adapter, "Top Plane")}
    return out


# ---- the run -----------------------------------------------------------------------

Step = Callable[..., Any]


def _titles(adapter: PyWin32Adapter) -> set[str]:
    return {
        str(adapter._get_attr_or_call(d, "GetTitle"))
        for d in adapter.swApp.GetDocuments() or []
    }


async def run(grid: tuple[int, int], steps: list[str]) -> int:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = OUT / stamp
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"started": stamp, "grid": grid}
    adapter = PyWin32Adapter({})
    await adapter.connect()
    before = _titles(adapter)
    try:
        tools = await _tools(adapter)
        table: dict[str, tuple[Step, tuple[Any, ...]]] = {
            "index": (step_index, (grid,)),
            "convexity": (step_convexity, ()),
            "persist": (step_persist, (tools,)),
            "plane": (step_plane, ()),
        }
        for key in steps:
            step, args = table[key]
            print(f"[probe] {key}: {step.__doc__}")
            started = time.perf_counter()
            try:
                report[key] = await step(adapter, *args)
            except Exception as exc:  # noqa: BLE001 - a probe records every failure
                report[key] = {"error": repr(exc)}
            report[key]["probe_seconds"] = round(time.perf_counter() - started, 1)
    finally:
        for title in _titles(adapter) - before:
            adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        await adapter.disconnect()
        path = out / "report.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"[probe] report: {path}")
        for key, value in report.items():
            if isinstance(value, dict) and "error" in value:
                print(f"[probe] {key} FAILED: {value['error']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--grid", default="20x12", help="pockets across x down")
    parser.add_argument(
        "--steps",
        default="index,convexity,persist,plane",
        help="comma-separated: index, convexity, persist, plane",
    )
    args = parser.parse_args()
    across, down = (int(n) for n in args.grid.lower().split("x"))
    return asyncio.run(run((across, down), args.steps.split(",")))


if __name__ == "__main__":
    sys.exit(main())
