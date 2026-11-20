"""Hand a SolidWorks model to Blender: units, axes, a manifest and the import code.

Three things go wrong when an agent moves a model from SolidWorks to Blender
by hand, and each is fixed here once:

* **Units.** An STL has no unit; SolidWorks writes the user's STL unit (mm
  here) and Blender reads every number as metres, so a 90 mm lid arrives
  90 m wide. glTF is metres by its specification; the GLB's size is checked
  against the model's anyway (:func:`file_units`).
* **Up axis.** SolidWorks' views are +Y up; Blender is +Z up. Blender's glTF
  importer turns +Y up into +Z up and its STL importer turns nothing, so
  the same part comes in standing in one format and lying on its back in
  the other, and a part modelled Z-up (the Dipper) the other way round.
  :func:`blender_matrix` is the one turn that puts the model's up on
  Blender's +Z, for each format and ``up_axis``.
* **Placement.** SolidWorks moves an STL so every coordinate is positive
  unless told otherwise; the STL path keeps model coordinates, so the
  model's origin is Blender's origin.

The manifest (:func:`build_manifest`) records the file, its units, the turn,
the colours the GLB holds and the size Blender should measure. The import
code (:func:`blender_code`) reads it inside Blender, replaces an earlier
import of the same model (a re-export after a design change updates the
scene instead of adding a copy), and returns the size it measured, so the
agent can see that the hand-off kept the model's size.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: Manifest format version; the import code refuses another.
MANIFEST_VERSION = 1

#: Units a file can be in, as metres per file unit.
_UNITS_M = {"m": 1.0, "mm": 0.001, "in": 0.0254, "cm": 0.01}

#: A size match within this share of the model's largest side counts.
_SIZE_TOLERANCE = 0.02

_IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
#: +90 degrees about X: (x, y, z) -> (x, -z, y). Model +Y up onto Blender +Z.
_RX_POS_90 = [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]]
#: -90 degrees about X: (x, y, z) -> (x, z, -y). Undoes the glTF importer's turn.
_RX_NEG_90 = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]


def safe_name(text: str) -> str:
    """A file and collection name from a document title: no folders, no odd characters.

    Args:
        text: A title such as ``lid.SLDPRT`` or ``Dipper Assembly``.

    Returns:
        str: The stem with characters Windows or Blender dislike replaced
        by ``_``; ``model`` when nothing is left.
    """
    stem = re.sub(r"\.(sldprt|sldasm)$", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", stem).strip(" ._")
    return cleaned or "model"


def blender_rotation(file_format: str, up_axis: str) -> list[list[float]]:
    """The turn after import that puts the model's up axis on Blender's +Z.

    Args:
        file_format: ``glb`` (Blender's importer already turns +Y up to +Z
            up) or ``stl`` (its importer keeps the file's axes).
        up_axis: The model's up axis in SolidWorks: ``y`` (SolidWorks'
            views) or ``z`` (a part modelled Z-up).

    Returns:
        list[list[float]]: 3 x 3, rows. Either way the model ends with its
        up on +Z, its front (SolidWorks +Z for ``y``, -Y for ``z``) toward
        Blender's -Y, and its +X on Blender's +X.

    Raises:
        ValueError: An unknown format or axis.
    """
    turns = {
        ("glb", "y"): _IDENTITY,
        ("glb", "z"): _RX_NEG_90,
        ("stl", "y"): _RX_POS_90,
        ("stl", "z"): _IDENTITY,
    }
    key = (file_format.lower(), up_axis.lower())
    if key not in turns:
        raise ValueError(f"no turn for format {file_format!r} and up axis {up_axis!r}")
    return [row[:] for row in turns[key]]


def blender_matrix(
    file_format: str, up_axis: str, scale: float = 1.0
) -> list[list[float]]:
    """:func:`blender_rotation` with a uniform scale, as a 4 x 4 world matrix.

    Args:
        file_format: ``glb`` or ``stl``.
        up_axis: ``y`` or ``z``.
        scale: Metres per unit of what the importer made (1 when the
            importer already scaled to metres).

    Returns:
        list[list[float]]: Row-major, for Blender's ``Matrix(rows)``.
    """
    rotation = blender_rotation(file_format, up_axis)
    rows = [[v * scale for v in row] + [0.0] for row in rotation]
    return rows + [[0.0, 0.0, 0.0, 1.0]]


def blender_size_mm(model_size_mm: list[float], up_axis: str) -> list[float]:
    """The size Blender should measure, in mm, along its X, Y and Z.

    Args:
        model_size_mm: The model's box size along SolidWorks X, Y and Z.
        up_axis: ``y`` swaps SolidWorks Y and Z onto Blender Z and Y.

    Returns:
        list[float]: The size along Blender's axes.
    """
    x, y, z = (float(v) for v in model_size_mm[:3])
    return [x, z, y] if up_axis.lower() == "y" else [x, y, z]


def _mismatch(file_size_mm: list[float], model_size_mm: list[float]) -> float:
    """The largest side difference, as a share of the model's largest side."""
    largest = max(model_size_mm) or 1.0
    return (
        max(abs(f - m) for f, m in zip(file_size_mm, model_size_mm, strict=True))
        / largest
    )


def file_units(
    file_size: list[float], model_size_mm: list[float] | None
) -> dict[str, Any]:
    """Which unit and axes a GLB was written in, from its size against the model's.

    Args:
        file_size: The GLB scene's size, in file units, along its X, Y, Z.
        model_size_mm: The model's box size along SolidWorks X, Y, Z, in
            mm; ``None`` when SolidWorks didn't give one.

    Returns:
        dict[str, Any]: ``units`` (``m``, ``mm``, ``in``, ``cm`` or the
        glTF default ``m`` when unknown); ``metres_per_unit``; ``checked``
        (whether the model's size settled it); ``axes``: ``same`` (file X,
        Y, Z are SolidWorks X, Y, Z), ``y_z_swapped`` (the exporter turned
        the model), or ``None`` when not known; ``mismatch``, the best
        match's largest side difference as a share of the model's largest
        side.
    """
    unknown = {
        "units": "m",
        "metres_per_unit": 1.0,
        "checked": False,
        "axes": None,
        "mismatch": None,
    }
    if not model_size_mm or len(model_size_mm) < 3 or max(model_size_mm[:3]) <= 0:
        return unknown
    model = [float(v) for v in model_size_mm[:3]]
    targets = {"same": model, "y_z_swapped": [model[0], model[2], model[1]]}
    # For each unit, how far the file is from the model with each axis order.
    scores = {
        units: {
            axes: _mismatch([float(v) * metres * 1000.0 for v in file_size[:3]], target)
            for axes, target in targets.items()
        }
        for units, metres in _UNITS_M.items()
    }
    units = min(scores, key=lambda u: min(scores[u].values()))
    same, swapped = scores[units]["same"], scores[units]["y_z_swapped"]
    score = min(same, swapped)
    if score > _SIZE_TOLERANCE:
        return {**unknown, "mismatch": round(score, 4)}
    # Y and Z the same length (a cube, a disc lying flat): the axes can't be told.
    axes_known: str | None = None
    if abs(same - swapped) > _SIZE_TOLERANCE:
        axes_known = "same" if same < swapped else "y_z_swapped"
    return {
        "units": units,
        "metres_per_unit": _UNITS_M[units],
        "checked": True,
        "axes": axes_known,
        "mismatch": round(score, 4),
    }


def build_manifest(
    *,
    document: dict[str, Any],
    file_format: str,
    file_path: str,
    up_axis: str,
    metres_per_unit: float,
    model_size_mm: list[float] | None,
    glb: dict[str, Any] | None = None,
    units_check: dict[str, Any] | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """The manifest the Blender import code reads.

    Args:
        document: ``title``, ``path`` and ``type`` of the SolidWorks document.
        name: The name for the Blender collection; the title's when ``None``.
        file_format: ``glb`` or ``stl``.
        file_path: The mesh file, absolute.
        up_axis: The model's up axis in SolidWorks, ``y`` or ``z``.
        metres_per_unit: Metres per file unit.
        model_size_mm: The model's box size along SolidWorks X, Y, Z, or ``None``.
        glb: :func:`gltf_summary.summarize`'s result for a GLB.
        units_check: :func:`file_units`'s result for a GLB.

    Returns:
        dict[str, Any]: JSON-ready. ``collection`` is the Blender collection
        the import fills (and empties first on a re-import); ``import`` says
        how to import the file (``operator``, and ``global_scale`` for STL);
        ``blender_matrix`` turns (and for a GLB in mm, scales) the import's
        root; ``expected_size_mm`` is along Blender's X, Y, Z.
    """
    file_format = file_format.lower()
    name = safe_name(name or str(document.get("title") or "model"))
    if file_format == "stl":
        how: dict[str, Any] = {
            "operator": "wm.stl_import",
            "global_scale": metres_per_unit,
        }
        root_scale = 1.0
    else:
        how = {"operator": "import_scene.gltf"}
        root_scale = metres_per_unit
    manifest: dict[str, Any] = {
        "version": MANIFEST_VERSION,
        "source": {
            "title": document.get("title"),
            "path": document.get("path"),
            "type": document.get("type"),
        },
        "collection": f"SW {name}",
        "root": f"SW {name} root",
        "file": {
            "path": file_path.replace("\\", "/"),
            "format": file_format,
            "metres_per_unit": metres_per_unit,
        },
        "import": how,
        "up_axis": up_axis.lower(),
        "blender_matrix": blender_matrix(file_format, up_axis, root_scale),
        "expected_size_mm": (
            None
            if not model_size_mm
            else [round(v, 3) for v in blender_size_mm(model_size_mm, up_axis)]
        ),
    }
    if glb is not None:
        manifest["parts"] = glb.get("nodes", [])
        manifest["materials"] = glb.get("materials", [])
    if units_check is not None:
        manifest["units_check"] = units_check
    return manifest


#: Runs inside Blender (``execute_blender_code``); ``MANIFEST_PATH`` is set
#: before it. Assigns ``result``, the MCP's way of returning data.
IMPORT_SCRIPT = """
import json

import bpy
from mathutils import Matrix, Vector

with open(MANIFEST_PATH, encoding="utf-8") as handle:
    manifest = json.load(handle)
if manifest.get("version") != 1:
    raise ValueError(f"manifest version {manifest.get('version')!r}, expected 1")

scene = bpy.context.scene
collection = bpy.data.collections.get(manifest["collection"])
replaced = 0
if collection is None:
    collection = bpy.data.collections.new(manifest["collection"])
    scene.collection.children.link(collection)
else:
    # A re-export after a design change: drop the earlier import first, with
    # the meshes and materials only it used. Nothing outside the collection.
    meshes, materials = set(), set()
    for old in list(collection.all_objects):
        if old.type == "MESH" and old.data is not None:
            meshes.add(old.data)
            materials.update(s.material for s in old.material_slots if s.material)
        bpy.data.objects.remove(old, do_unlink=True)
        replaced += 1
    for mesh in meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for material in materials:
        if material.users == 0:
            bpy.data.materials.remove(material)

before = set(bpy.data.objects)
source = manifest["file"]
how = manifest["import"]
if how["operator"] == "wm.stl_import":
    bpy.ops.wm.stl_import(filepath=source["path"], global_scale=how["global_scale"])
else:
    bpy.ops.import_scene.gltf(filepath=source["path"])
imported = [o for o in bpy.data.objects if o not in before]
if not imported:
    raise RuntimeError(f"Blender imported nothing from {source['path']}")

root = bpy.data.objects.new(manifest["root"], None)
collection.objects.link(root)
root.matrix_world = Matrix(manifest["blender_matrix"])
for obj in imported:
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    if obj.parent is None:
        obj.parent = root
bpy.context.view_layer.update()

corners = [
    obj.matrix_world @ Vector(corner)
    for obj in imported
    if obj.type == "MESH"
    for corner in obj.bound_box
]
size_mm = None
if corners:
    size_mm = [
        round((max(c[i] for c in corners) - min(c[i] for c in corners)) * 1000.0, 3)
        for i in range(3)
    ]
expected = manifest.get("expected_size_mm")
matches = None
if size_mm and expected:
    largest = max(expected) or 1.0
    matches = all(abs(a - b) <= 0.02 * largest for a, b in zip(size_mm, expected))
result = {
    "collection": collection.name,
    "root": root.name,
    "objects": sorted(o.name for o in imported if o.type == "MESH"),
    "replaced_objects": replaced,
    "size_mm": size_mm,
    "expected_size_mm": expected,
    "size_matches": matches,
}
"""


def blender_code(manifest_path: str) -> str:
    """The code to pass to the Blender MCP's ``execute_blender_code``.

    Args:
        manifest_path: The manifest written by ``export_for_blender``.

    Returns:
        str: Self-contained Python. The path goes in as a JSON string with
        forward slashes, so a Windows path's backslashes can't turn into
        escapes (``"C:\\Users"`` is a ``SyntaxError`` in Python).
    """
    literal = json.dumps(manifest_path.replace("\\", "/"))
    return f"MANIFEST_PATH = {literal}\n{IMPORT_SCRIPT.lstrip()}"
