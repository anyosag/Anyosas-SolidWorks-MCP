"""Read back a binary glTF (``.glb``) file: its parts, colours and size.

``export_for_blender`` writes a GLB through SolidWorks' own exporter and
reads it back here, without SolidWorks, to say what Blender will get: the
named nodes (one per component for an assembly), the materials and their
base colours, and the size of the whole scene in the file's own units. The
size, set against the model's in mm, tells which unit and axes the exporter
used (glTF says metres and +Y up; that is checked, not assumed).

Layout (glTF 2.0, "GLB File Format Specification"): a 12-byte header
(``glTF``, version 2, total length), then chunks of ``length``, ``type`` and
data. The first chunk is ``JSON``; a ``BIN`` chunk may follow. Only the JSON
is read: every ``POSITION`` accessor must carry ``min`` and ``max``, so the
size needs no vertex data.
"""

from __future__ import annotations

import json
import os
import struct
from typing import Any

#: A 4 x 4 matrix, row-major: ``m[row][column]``.
Matrix = list[list[float]]

_JSON_CHUNK = 0x4E4F534A  # "JSON", little-endian
_IDENTITY: Matrix = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def _multiply(a: Matrix, b: Matrix) -> Matrix:
    """``a @ b`` for 4 x 4 matrices."""
    return [
        [sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)
    ]


def node_matrix(node: dict[str, Any]) -> Matrix:
    """A node's local transform, from ``matrix`` or from ``translation``/``rotation``/``scale``.

    Args:
        node: The glTF node.

    Returns:
        Matrix: Row-major. glTF stores ``matrix`` column-major and the
        rotation as a quaternion ``[x, y, z, w]``.
    """
    raw = node.get("matrix")
    if isinstance(raw, list) and len(raw) == 16:
        return [[float(raw[c * 4 + r]) for c in range(4)] for r in range(4)]
    tx, ty, tz = (float(v) for v in node.get("translation", [0, 0, 0]))
    x, y, z, w = (float(v) for v in node.get("rotation", [0, 0, 0, 1]))
    sx, sy, sz = (float(v) for v in node.get("scale", [1, 1, 1]))
    rotation = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    scales = (sx, sy, sz)
    rows = [[rotation[r][c] * scales[c] for c in range(3)] for r in range(3)]
    return [rows[0] + [tx], rows[1] + [ty], rows[2] + [tz], [0.0, 0.0, 0.0, 1.0]]


def _apply(matrix: Matrix, point: tuple[float, float, float]) -> list[float]:
    """The point moved by ``matrix``."""
    return [
        sum(matrix[r][c] * point[c] for c in range(3)) + matrix[r][3] for r in range(3)
    ]


def read_json_chunk(path: str | os.PathLike[str]) -> dict[str, Any]:
    """The GLB's JSON chunk, parsed.

    Args:
        path: The ``.glb`` file.

    Returns:
        dict[str, Any]: The glTF document.

    Raises:
        ValueError: The file isn't a GLB or has no JSON chunk.
        OSError: It can't be read.
    """
    with open(path, "rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[:4] != b"glTF":
            raise ValueError("it is not a binary glTF file (no glTF header)")
        version = struct.unpack("<I", header[4:8])[0]
        if version != 2:
            raise ValueError(f"it is glTF version {version}, not 2")
        chunk = handle.read(8)
        if len(chunk) < 8:
            raise ValueError("it has no JSON chunk: the file holds no scene")
        length, kind = struct.unpack("<II", chunk)
        if kind != _JSON_CHUNK:
            raise ValueError("its first chunk is not JSON")
        text = handle.read(length)
        if len(text) < length:
            raise ValueError("its JSON chunk is cut short")
    document = json.loads(text.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("its JSON chunk is not a glTF object")
    return document


def _scene_roots(document: dict[str, Any]) -> list[int]:
    """The nodes the default scene starts from (every unparented node if none)."""
    scenes = document.get("scenes") or []
    index = document.get("scene", 0)
    if isinstance(index, int) and 0 <= index < len(scenes):
        return [int(n) for n in scenes[index].get("nodes", [])]
    nodes = document.get("nodes") or []
    children = {int(c) for n in nodes for c in n.get("children", [])}
    return [i for i in range(len(nodes)) if i not in children]


def _mesh_corners(
    document: dict[str, Any], mesh_index: int
) -> list[tuple[float, float, float]]:
    """The eight corners of a mesh's box, from its ``POSITION`` accessors' min and max."""
    meshes = document.get("meshes") or []
    accessors = document.get("accessors") or []
    if not 0 <= mesh_index < len(meshes):
        return []
    corners: list[tuple[float, float, float]] = []
    for primitive in meshes[mesh_index].get("primitives", []):
        position = primitive.get("attributes", {}).get("POSITION")
        if not isinstance(position, int) or not 0 <= position < len(accessors):
            continue
        low, high = accessors[position].get("min"), accessors[position].get("max")
        if not (isinstance(low, list) and isinstance(high, list)):
            continue
        if len(low) < 3 or len(high) < 3:
            continue
        for x in (low[0], high[0]):
            for y in (low[1], high[1]):
                for z in (low[2], high[2]):
                    corners.append((float(x), float(y), float(z)))
    return corners


def _color(material: dict[str, Any]) -> list[float] | None:
    """A material's base colour, RGBA 0-1, or ``None`` when it has none."""
    factor = (material.get("pbrMetallicRoughness") or {}).get("baseColorFactor")
    if isinstance(factor, list) and len(factor) == 4:
        return [round(float(v), 4) for v in factor]
    return None


def summarize(document: dict[str, Any]) -> dict[str, Any]:
    """What a glTF document holds: named nodes with meshes, materials and size.

    Args:
        document: From :func:`read_json_chunk`.

    Returns:
        dict[str, Any]: ``generator``; ``nodes`` (each node that draws a
        mesh: ``name``, ``mesh`` and ``materials``, in scene order);
        ``mesh_count``; ``materials`` (``name`` and ``base_color``);
        ``bounds`` (``min``, ``max``, ``size`` in the file's units, the
        whole scene with every node's transform; ``None`` when no mesh has
        a box).
    """
    nodes = document.get("nodes") or []
    meshes = document.get("meshes") or []
    materials = document.get("materials") or []
    material_names = [m.get("name") or f"material {i}" for i, m in enumerate(materials)]
    drawn: list[dict[str, Any]] = []
    points: list[list[float]] = []
    stack = [(root, _IDENTITY) for root in reversed(_scene_roots(document))]
    seen: set[int] = set()
    while stack:
        index, parent = stack.pop()
        if not 0 <= index < len(nodes) or index in seen:
            continue
        seen.add(index)
        node = nodes[index]
        world = _multiply(parent, node_matrix(node))
        mesh = node.get("mesh")
        if isinstance(mesh, int) and 0 <= mesh < len(meshes):
            used = {
                p.get("material")
                for p in meshes[mesh].get("primitives", [])
                if isinstance(p.get("material"), int)
            }
            drawn.append(
                {
                    "name": node.get("name")
                    or meshes[mesh].get("name")
                    or f"node {index}",
                    "mesh": meshes[mesh].get("name") or f"mesh {mesh}",
                    "materials": [
                        material_names[m]
                        for m in sorted(used)
                        if m < len(material_names)
                    ],
                }
            )
            points.extend(
                _apply(world, corner) for corner in _mesh_corners(document, mesh)
            )
        stack.extend((int(c), world) for c in reversed(node.get("children", [])))
    bounds = None
    if points:
        low = [min(p[i] for p in points) for i in range(3)]
        high = [max(p[i] for p in points) for i in range(3)]
        bounds = {
            "min": [round(v, 6) for v in low],
            "max": [round(v, 6) for v in high],
            "size": [round(high[i] - low[i], 6) for i in range(3)],
        }
    return {
        "generator": (document.get("asset") or {}).get("generator"),
        "nodes": drawn,
        "mesh_count": len(meshes),
        "materials": [
            {"name": material_names[i], "base_color": _color(m)}
            for i, m in enumerate(materials)
        ],
        "bounds": bounds,
    }


def check_glb(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a ``.glb`` back and summarise it; never raises.

    Args:
        path: The file.

    Returns:
        dict[str, Any]: :func:`summarize`'s result, or only ``error`` when
        the file can't be read as a GLB.
    """
    try:
        return summarize(read_json_chunk(path))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {"error": f"{os.path.basename(str(path))}: {exc}"}
