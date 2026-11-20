"""Reading a GLB back: its parts, colours and size, without SolidWorks."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.utils import gltf_summary


def _glb(document: dict[str, Any]) -> bytes:
    """A GLB holding ``document`` as its JSON chunk, and no binary chunk."""
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    chunk = struct.pack("<II", len(text), 0x4E4F534A) + text
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunk)) + chunk


def _box_accessor(low: list[float], high: list[float]) -> dict[str, Any]:
    return {"componentType": 5126, "count": 8, "type": "VEC3", "min": low, "max": high}


def _assembly() -> dict[str, Any]:
    """Two components under one root: a 40 x 20 x 10 mm base and a lid moved up 30 mm."""
    return {
        "asset": {"version": "2.0", "generator": "SOLIDWORKS"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "Rack", "children": [1, 2]},
            {"name": "base-1", "mesh": 0},
            {"name": "lid-1", "mesh": 1, "translation": [0, 0.03, 0]},
        ],
        "meshes": [
            {
                "name": "base",
                "primitives": [{"attributes": {"POSITION": 0}, "material": 0}],
            },
            {
                "name": "lid",
                "primitives": [{"attributes": {"POSITION": 1}, "material": 1}],
            },
        ],
        "materials": [
            {
                "name": "PLA white",
                "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]},
            },
            {"name": "clear"},
        ],
        "accessors": [
            _box_accessor([0, 0, 0], [0.04, 0.02, 0.01]),
            _box_accessor([0, 0, 0], [0.04, 0.005, 0.01]),
        ],
    }


def test_an_assembly_reads_back_as_its_parts_in_place(tmp_path: Path) -> None:
    path = tmp_path / "rack.glb"
    path.write_bytes(_glb(_assembly()))

    summary = gltf_summary.check_glb(path)

    assert summary["generator"] == "SOLIDWORKS"
    assert summary["nodes"] == [
        {"name": "base-1", "mesh": "base", "materials": ["PLA white"]},
        {"name": "lid-1", "mesh": "lid", "materials": ["clear"]},
    ]
    assert summary["materials"] == [
        {"name": "PLA white", "base_color": [1.0, 1.0, 1.0, 1.0]},
        {"name": "clear", "base_color": None},
    ]
    # The lid's 5 mm sits 30 mm up: the scene is 35 mm tall.
    assert summary["bounds"]["size"] == [0.04, 0.035, 0.01]
    assert summary["mesh_count"] == 2


def test_a_column_major_matrix_and_a_quaternion_turn_the_same_way() -> None:
    # 90 degrees about Z, then 5 m along X, written both ways.
    half = math.sqrt(0.5)
    by_parts = gltf_summary.node_matrix(
        {"translation": [5, 0, 0], "rotation": [0, 0, half, half], "scale": [2, 2, 2]}
    )
    by_matrix = gltf_summary.node_matrix(
        {"matrix": [0, 2, 0, 0, -2, 0, 0, 0, 0, 0, 2, 0, 5, 0, 0, 1]}
    )

    for row_a, row_b in zip(by_parts, by_matrix, strict=True):
        assert row_a == pytest.approx(row_b, abs=1e-9)
    assert by_matrix[0] == [0.0, -2.0, 0.0, 5.0]


def test_without_a_scene_list_every_unparented_node_is_drawn(tmp_path: Path) -> None:
    document = _assembly()
    del document["scenes"], document["scene"]
    path = tmp_path / "rack.glb"
    path.write_bytes(_glb(document))

    summary = gltf_summary.check_glb(path)

    assert [n["name"] for n in summary["nodes"]] == ["base-1", "lid-1"]


def test_nodes_and_meshes_that_point_nowhere_are_left_out(tmp_path: Path) -> None:
    document = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0, 7, 0]}],
        "nodes": [{"mesh": 0, "children": [1]}, {"mesh": 5}],
        "meshes": [
            {
                "primitives": [
                    {"attributes": {"POSITION": 9}},
                    {"attributes": {}},
                    {"attributes": {"POSITION": 0}},
                    {"attributes": {"POSITION": 1}},
                    {"attributes": {"POSITION": 2}},
                ]
            }
        ],
        "accessors": [
            {"count": 3},
            {"min": [0, 0], "max": [1, 1]},
            _box_accessor([0, 0, 0], [1, 2, 3]),
        ],
    }
    path = tmp_path / "odd.glb"
    path.write_bytes(_glb(document))

    summary = gltf_summary.check_glb(path)

    assert summary["nodes"] == [{"name": "node 0", "mesh": "mesh 0", "materials": []}]
    assert summary["bounds"]["size"] == [1.0, 2.0, 3.0]
    assert gltf_summary._mesh_corners(document, 5) == []


def test_a_scene_with_no_boxes_has_no_bounds(tmp_path: Path) -> None:
    path = tmp_path / "empty.glb"
    path.write_bytes(_glb({"asset": {"version": "2.0"}, "nodes": [{"name": "empty"}]}))

    summary = gltf_summary.check_glb(path)

    assert summary["nodes"] == [] and summary["bounds"] is None


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        (b"solid stl", "no glTF header"),
        (b"glTF" + struct.pack("<II", 1, 12), "version 1"),
        (b"glTF" + struct.pack("<II", 2, 12), "no JSON chunk"),
        (
            b"glTF" + struct.pack("<II", 2, 20) + struct.pack("<II", 4, 0x004E4942),
            "not JSON",
        ),
        (
            b"glTF"
            + struct.pack("<II", 2, 24)
            + struct.pack("<II", 40, 0x4E4F534A)
            + b"{}",
            "cut short",
        ),
        (
            b"glTF"
            + struct.pack("<II", 2, 24)
            + struct.pack("<II", 2, 0x4E4F534A)
            + b"[]",
            "not a glTF object",
        ),
        (
            b"glTF"
            + struct.pack("<II", 2, 24)
            + struct.pack("<II", 2, 0x4E4F534A)
            + b"{x",
            "Expecting",
        ),
    ],
)
def test_a_file_that_is_not_a_glb_is_an_error(
    tmp_path: Path, content: bytes, fragment: str
) -> None:
    path = tmp_path / "bad.glb"
    path.write_bytes(content)

    summary = gltf_summary.check_glb(path)

    assert list(summary) == ["error"]
    assert fragment in summary["error"]


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    assert "error" in gltf_summary.check_glb(tmp_path / "none.glb")
