"""The SolidWorks-to-Blender hand-off: units, axes, the manifest and the import code.

The import code runs here against a small stand-in for ``bpy`` that imports
a box the way Blender's importers do: the STL importer keeps the file's
axes and scales by ``global_scale``; the glTF importer turns glTF's +Y up
into Blender's +Z up. So these tests check the whole chain: a model of known
size, exported, imported and turned, measures what it should in Blender.
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.utils import blender_handoff

# --- a stand-in for bpy and mathutils --------------------------------------


class Matrix:
    """A 4 x 4 (or 3 x 3, padded) row-major matrix."""

    def __init__(self, rows: Any) -> None:
        rows = [list(map(float, r)) for r in rows]
        if len(rows) == 3:
            rows = [r + [0.0] for r in rows] + [[0.0, 0.0, 0.0, 1.0]]
        self.rows = rows

    def __matmul__(self, other: Any) -> Any:
        if isinstance(other, Matrix):
            return Matrix(
                [
                    [
                        sum(self.rows[r][k] * other.rows[k][c] for k in range(4))
                        for c in range(4)
                    ]
                    for r in range(4)
                ]
            )
        point = list(other) + [1.0]
        return tuple(
            sum(self.rows[r][c] * point[c] for c in range(4)) for r in range(3)
        )


def Vector(values: Any) -> tuple[float, ...]:  # noqa: N802 - mathutils' name
    return tuple(float(v) for v in values)


_IDENTITY = Matrix([[1, 0, 0], [0, 1, 0], [0, 0, 1]])


class FakeObject:
    def __init__(
        self, name: str, data: Any = None, size: tuple[float, ...] = ()
    ) -> None:
        self.name = name
        self.data = data
        self.type = "MESH" if data is not None else "EMPTY"
        self.parent: FakeObject | None = None
        self.basis = _IDENTITY
        self.users_collection: list[FakeCollection] = []
        self.material_slots = [
            types.SimpleNamespace(material=m) for m in getattr(data, "materials", [])
        ]
        self.bound_box = [
            (x, y, z)
            for x in (0.0, size[0] if size else 0.0)
            for y in (0.0, size[1] if size else 0.0)
            for z in (0.0, size[2] if size else 0.0)
        ]

    @property
    def matrix_world(self) -> Matrix:
        return self.parent.matrix_world @ self.basis if self.parent else self.basis

    @matrix_world.setter
    def matrix_world(self, value: Matrix) -> None:
        self.basis = value


class FakeObjects:
    def __init__(self, owner: FakeCollection) -> None:
        self.owner, self.items = owner, []

    def link(self, obj: FakeObject) -> None:
        self.items.append(obj)
        obj.users_collection.append(self.owner)

    def unlink(self, obj: FakeObject) -> None:
        self.items.remove(obj)
        obj.users_collection.remove(self.owner)

    def __iter__(self) -> Iterator[FakeObject]:
        return iter(list(self.items))


class FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.objects = FakeObjects(self)
        self.children = types.SimpleNamespace(link=lambda c: None)

    @property
    def all_objects(self) -> list[FakeObject]:
        return list(self.objects)


class FakeBlender:
    """``bpy``: data, context and the two import operators."""

    def __init__(self, sizes: list[tuple[float, float, float]]) -> None:
        self.sizes = sizes  # each import's box, in file units along file X, Y, Z
        self.imports: list[dict[str, Any]] = []
        self.scene_collection = FakeCollection("Scene Collection")
        self.collections: dict[str, FakeCollection] = {}
        self.all: list[FakeObject] = []
        self.meshes: list[Any] = []
        self.materials: list[Any] = []
        blender = self

        class Collections:
            def get(self, name: str) -> FakeCollection | None:
                return blender.collections.get(name)

            def new(self, name: str) -> FakeCollection:
                blender.collections[name] = FakeCollection(name)
                return blender.collections[name]

        class Objects:
            def __iter__(self) -> Iterator[FakeObject]:
                return iter(list(blender.all))

            def new(self, name: str, data: Any) -> FakeObject:
                obj = FakeObject(name, data)
                blender.all.append(obj)
                return obj

            def remove(self, obj: FakeObject, do_unlink: bool = False) -> None:
                for owner in list(obj.users_collection):
                    owner.objects.unlink(obj)
                blender.all.remove(obj)

        self.data = types.SimpleNamespace(
            collections=Collections(),
            objects=Objects(),
            meshes=_Store(self.meshes),
            materials=_Store(self.materials),
        )
        self.context = types.SimpleNamespace(
            scene=types.SimpleNamespace(collection=self.scene_collection),
            view_layer=types.SimpleNamespace(update=lambda: None),
        )
        self.ops = types.SimpleNamespace(
            wm=types.SimpleNamespace(stl_import=self._stl_import),
            import_scene=types.SimpleNamespace(gltf=self._gltf_import),
        )

    def _mesh(self, name: str) -> Any:
        material = _Material(self, f"{name} material")
        mesh = _Mesh(self, name, materials=[material])
        self.meshes.append(mesh)
        self.materials.append(material)
        return mesh

    def _add(self, obj: FakeObject) -> FakeObject:
        self.all.append(obj)
        self.scene_collection.objects.link(obj)
        return obj

    def _stl_import(self, filepath: str, global_scale: float) -> None:
        self.imports.append(
            {"operator": "stl", "filepath": filepath, "scale": global_scale}
        )
        x, y, z = (v * global_scale for v in self.sizes[len(self.imports) - 1])
        self._add(FakeObject("stl", self._mesh("stl"), (x, y, z)))

    def _gltf_import(self, filepath: str) -> None:
        self.imports.append({"operator": "gltf", "filepath": filepath})
        x, y, z = self.sizes[len(self.imports) - 1]
        root = self._add(FakeObject("Assembly"))
        # glTF +Y up becomes Blender +Z up: (x, y, z) -> (x, -z, y), sizes (x, z, y).
        child = self._add(FakeObject("part-1", self._mesh("part"), (x, z, y)))
        child.parent = root


class _Mesh:
    """A mesh: its users are the objects that show it."""

    def __init__(self, blender: FakeBlender, name: str, materials: list[Any]) -> None:
        self.blender, self.name, self.materials = blender, name, materials

    @property
    def users(self) -> int:
        return sum(1 for o in self.blender.all if o.data is self)


class _Material:
    """A material: its users are the meshes that hold it, and ``others``."""

    def __init__(self, blender: FakeBlender, name: str) -> None:
        self.blender, self.name, self.others = blender, name, 0

    @property
    def users(self) -> int:
        held = sum(1 for m in self.blender.meshes if self in m.materials)
        return held + self.others


class _Store(list):  # type: ignore[type-arg]
    """``bpy.data.meshes`` / ``materials``: a list with ``remove``."""

    def __init__(self, backing: list[Any]) -> None:
        super().__init__()
        self.backing = backing

    def __iter__(self) -> Iterator[Any]:
        return iter(list(self.backing))

    def remove(self, block: Any) -> None:  # type: ignore[override]
        self.backing.remove(block)


@pytest.fixture
def run_in_blender(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run ``blender_code`` against a :class:`FakeBlender`; return its ``result``."""

    def run(code: str, blender: FakeBlender) -> dict[str, Any]:
        mathutils = types.SimpleNamespace(Matrix=Matrix, Vector=Vector)
        monkeypatch.setitem(sys.modules, "bpy", blender)
        monkeypatch.setitem(sys.modules, "mathutils", mathutils)
        namespace: dict[str, Any] = {}
        exec(compile(code, "<blender>", "exec"), namespace)
        return namespace["result"]

    return run


# --- names, turns and units ------------------------------------------------


@pytest.mark.parametrize(
    ("title", "name"),
    [
        ("lid.SLDPRT", "lid"),
        ("Dipper Assembly.sldasm", "Dipper Assembly"),
        ('rack: v2/"final"', "rack_ v2_final"),
        ("  ...  ", "model"),
    ],
)
def test_names_are_safe_for_files_and_blender(title: str, name: str) -> None:
    assert blender_handoff.safe_name(title) == name


@pytest.mark.parametrize(
    ("file_format", "up_axis", "model_up_lands_on"),
    [
        # Where the model's up axis is right after the importer, and the turn on top.
        ("stl", "y", (0, 1, 0)),
        ("stl", "z", (0, 0, 1)),
        ("glb", "y", (0, 0, 1)),
        ("glb", "z", (0, -1, 0)),
    ],
)
def test_the_turn_puts_the_models_up_on_blenders_z(
    file_format: str, up_axis: str, model_up_lands_on: tuple[int, int, int]
) -> None:
    turn = Matrix(blender_handoff.blender_rotation(file_format, up_axis))

    assert turn @ model_up_lands_on == pytest.approx((0, 0, 1))


def test_an_unknown_format_has_no_turn() -> None:
    with pytest.raises(ValueError, match="no turn"):
        blender_handoff.blender_rotation("obj", "y")


def test_the_matrix_carries_the_scale() -> None:
    matrix = blender_handoff.blender_matrix("glb", "y", 0.001)

    assert matrix[0] == [0.001, 0.0, 0.0, 0.0]
    assert matrix[3] == [0.0, 0.0, 0.0, 1.0]


def test_blender_measures_a_y_up_model_with_its_y_and_z_swapped() -> None:
    assert blender_handoff.blender_size_mm([10, 20, 30], "y") == [10.0, 30.0, 20.0]
    assert blender_handoff.blender_size_mm([10, 20, 30], "z") == [10.0, 20.0, 30.0]


@pytest.mark.parametrize(
    ("file_size", "units", "axes"),
    [
        ([0.04, 0.02, 0.01], "m", "same"),  # the glTF standard
        ([40.0, 20.0, 10.0], "mm", "same"),  # an exporter that kept mm
        ([0.04, 0.01, 0.02], "m", "y_z_swapped"),  # an exporter that turned it
        ([40 / 25.4, 20 / 25.4, 10 / 25.4], "in", "same"),
    ],
)
def test_the_files_units_and_axes_come_from_its_size(
    file_size: list[float], units: str, axes: str
) -> None:
    check = blender_handoff.file_units(file_size, [40.0, 20.0, 10.0])

    assert (check["units"], check["axes"], check["checked"]) == (units, axes, True)


def test_axes_are_unknown_when_y_and_z_are_the_same_length() -> None:
    check = blender_handoff.file_units([0.03, 0.01, 0.01], [30.0, 10.0, 10.0])

    assert check["checked"] and check["axes"] is None


@pytest.mark.parametrize("model_size", [None, [0.0, 0.0, 0.0], [1.0]])
def test_without_the_models_size_the_glb_is_taken_as_metres(
    model_size: list[float] | None,
) -> None:
    check = blender_handoff.file_units([1.0, 2.0, 3.0], model_size)

    assert check == {
        "units": "m",
        "metres_per_unit": 1.0,
        "checked": False,
        "axes": None,
        "mismatch": None,
    }


def test_a_file_the_size_of_nothing_like_the_model_is_unchecked() -> None:
    check = blender_handoff.file_units([0.004, 0.002, 0.001], [40.0, 20.0, 10.0])

    assert not check["checked"] and check["mismatch"] > 0.02


# --- the manifest and the import, end to end ------------------------------


def _write(tmp_path: Path, manifest: dict[str, Any]) -> str:
    path = tmp_path / "model.blender.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return str(path)


def _manifest(
    file_format: str, up_axis: str, metres: float, size: list[float]
) -> dict[str, Any]:
    return blender_handoff.build_manifest(
        document={
            "title": "Rack.SLDASM",
            "path": "C:\\parts\\Rack.SLDASM",
            "type": "Assembly",
        },
        file_format=file_format,
        file_path="C:\\out\\Rack." + file_format,
        up_axis=up_axis,
        metres_per_unit=metres,
        model_size_mm=size,
    )


@pytest.mark.parametrize(
    ("file_format", "up_axis", "metres", "file_box"),
    [
        # A 10 x 20 x 30 mm model, in the file's units along the file's axes.
        ("stl", "y", 0.001, (10.0, 20.0, 30.0)),
        ("stl", "z", 0.001, (10.0, 20.0, 30.0)),
        ("glb", "y", 1.0, (0.01, 0.02, 0.03)),
        ("glb", "z", 1.0, (0.01, 0.02, 0.03)),
        ("glb", "y", 0.001, (10.0, 20.0, 30.0)),  # a GLB written in mm
    ],
)
def test_blender_measures_the_models_size_after_the_import(
    tmp_path: Path,
    run_in_blender: Any,
    file_format: str,
    up_axis: str,
    metres: float,
    file_box: tuple[float, float, float],
) -> None:
    manifest = _manifest(file_format, up_axis, metres, [10.0, 20.0, 30.0])
    blender = FakeBlender([file_box])

    result = run_in_blender(
        blender_handoff.blender_code(_write(tmp_path, manifest)), blender
    )

    assert result["size_matches"] is True, result
    assert result["size_mm"] == pytest.approx(manifest["expected_size_mm"], abs=1e-6)
    assert result["collection"] == "SW Rack"
    assert blender.imports[0]["filepath"] == "C:/out/Rack." + file_format
    # Everything imported sits in the model's collection, under its root.
    collection = blender.collections["SW Rack"]
    assert {o.name for o in collection.all_objects} >= {
        "SW Rack root",
        *result["objects"],
    }
    assert not blender.scene_collection.all_objects
    assert all(
        o.parent is not None for o in collection.all_objects if o.name != "SW Rack root"
    )


def test_a_reimport_replaces_the_earlier_one_and_nothing_else(
    tmp_path: Path, run_in_blender: Any
) -> None:
    manifest = _manifest("stl", "y", 0.001, [10.0, 20.0, 30.0])
    code = blender_handoff.blender_code(_write(tmp_path, manifest))
    blender = FakeBlender([(10.0, 20.0, 30.0), (10.0, 20.0, 31.0)])
    users_own = blender._add(FakeObject("Camera"))
    run_in_blender(code, blender)
    first_mesh = blender.meshes[0]

    result = run_in_blender(code, blender)

    assert result["replaced_objects"] == 2  # the mesh and the root
    assert first_mesh not in blender.meshes
    assert len(blender.materials) == 1
    assert users_own in blender.all
    assert result["size_mm"] == pytest.approx([10.0, 31.0, 20.0])


def test_a_material_still_used_elsewhere_stays(
    tmp_path: Path, run_in_blender: Any
) -> None:
    manifest = _manifest("stl", "z", 0.001, [10.0, 20.0, 30.0])
    code = blender_handoff.blender_code(_write(tmp_path, manifest))
    blender = FakeBlender([(10.0, 20.0, 30.0), (10.0, 20.0, 30.0)])
    run_in_blender(code, blender)
    shared = blender.materials[0]
    shared.others = 1  # the user put it on something of their own too

    run_in_blender(code, blender)

    assert shared in blender.materials


def test_a_size_far_from_the_model_is_reported(
    tmp_path: Path, run_in_blender: Any
) -> None:
    manifest = _manifest("stl", "z", 0.001, [10.0, 20.0, 30.0])
    blender = FakeBlender([(10000.0, 20000.0, 30000.0)])  # an STL read as metres

    result = run_in_blender(
        blender_handoff.blender_code(_write(tmp_path, manifest)), blender
    )

    assert result["size_matches"] is False


def test_without_an_expected_size_nothing_is_compared(
    tmp_path: Path, run_in_blender: Any
) -> None:
    manifest = blender_handoff.build_manifest(
        document={"title": "lid"},
        file_format="glb",
        file_path="C:/out/lid.glb",
        up_axis="y",
        metres_per_unit=1.0,
        model_size_mm=None,
        glb={"nodes": [{"name": "lid"}], "materials": []},
        units_check={"units": "m"},
    )
    blender = FakeBlender([(0.01, 0.01, 0.01)])

    result = run_in_blender(
        blender_handoff.blender_code(_write(tmp_path, manifest)), blender
    )

    assert result["size_matches"] is None
    assert manifest["parts"] == [{"name": "lid"}]
    assert manifest["units_check"] == {"units": "m"}


def test_an_import_that_brings_nothing_is_an_error(
    tmp_path: Path, run_in_blender: Any
) -> None:
    manifest = _manifest("stl", "z", 0.001, [10.0, 20.0, 30.0])
    blender = FakeBlender([(1.0, 1.0, 1.0)])
    blender.ops.wm.stl_import = lambda filepath, global_scale: None

    with pytest.raises(RuntimeError, match="imported nothing"):
        run_in_blender(
            blender_handoff.blender_code(_write(tmp_path, manifest)), blender
        )


def test_another_manifest_version_is_refused(
    tmp_path: Path, run_in_blender: Any
) -> None:
    manifest = {**_manifest("stl", "z", 0.001, [1.0, 1.0, 1.0]), "version": 2}

    with pytest.raises(ValueError, match="manifest version 2"):
        run_in_blender(
            blender_handoff.blender_code(_write(tmp_path, manifest)), FakeBlender([])
        )


def test_the_code_holds_a_windows_path_without_escapes() -> None:
    code = blender_handoff.blender_code("C:\\Users\\new folder\\x.blender.json")

    assert code.startswith('MANIFEST_PATH = "C:/Users/new folder/x.blender.json"\n')
    compile(code, "<blender>", "exec")
