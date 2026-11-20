"""Mock SolidWorks adapter for testing and development.

This adapter simulates SolidWorks operations for testing on non-Windows platforms or
when SolidWorks is not available.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import shutil
import struct
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from ..exceptions import SolidWorksOperationError
from .base import (
    AdapterHealth,
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    LoftParameters,
    MassProperties,
    RevolveParameters,
    SolidWorksAdapter,
    SolidWorksFeature,
    SolidWorksModel,
    SweepParameters,
)
from .solidworks import view_matrix as _view_matrix
from .solidworks.drawing_standard import gtol_problem
from .solidworks.equations import equation_refusal
from .solidworks.feature_editing import missing_ids_message
from .solidworks.features import _AXIS_PLANE_PAIRS
from .solidworks.io import _normalise_unit_system as _normalise_mock_unit_system
from .solidworks.plane_frame import Frame, sub, two_points, unit, vector
from .solidworks.saving import confirmation_result
from .solidworks.sketch import (
    ARC_DIRECTION_FLAGS,
    MAX_OUTLINE_SEGMENTS,
    RELATION_NAME_MAP,
    SKETCH_ENTITY_PREFIXES,
    default_plane_axes,
    plane_candidates_for,
    sketch_entities_problem,
)
from .solidworks.stl_settings import ONE_FILE as _STL_ONE_FILE
from .solidworks.stl_settings import preference_changes as _stl_changes
from .solidworks.visibility import unknown_types as _unknown_display_types

#: The mock's STL: a closed tetrahedron with 10 mm legs, facing outwards.
_MOCK_STL = (
    "solid mock\n"
    + "".join(
        "  facet normal 0 0 0\n    outer loop\n"
        + "".join(f"      vertex {corner}\n" for corner in facet)
        + "    endloop\n  endfacet\n"
        for facet in (
            ("0 0 0", "0 10 0", "10 0 0"),
            ("0 0 0", "10 0 0", "0 0 10"),
            ("0 0 0", "0 0 10", "0 10 0"),
            ("10 0 0", "0 10 0", "0 0 10"),
        )
    )
    + "endsolid mock\n"
)


def _mock_glb() -> bytes:
    """The mock's GLB: the STL's tetrahedron (10 mm legs) in metres, one node.

    Like SolidWorks' exporter, the node is named after the document and the
    material carries a base colour, so ``export_for_blender`` reads back
    what a real export holds.
    """
    corners = [(0.0, 0.0, 0.0), (0.01, 0.0, 0.0), (0.0, 0.01, 0.0), (0.0, 0.0, 0.01)]
    faces = [0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3]
    positions = struct.pack("<12f", *(v for corner in corners for v in corner))
    indices = struct.pack("<12H", *faces)
    binary = positions + indices
    document = {
        "asset": {"version": "2.0", "generator": "solidworks-mcp mock"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "mock", "mesh": 0}],
        "meshes": [
            {
                "name": "mock",
                "primitives": [
                    {"attributes": {"POSITION": 0}, "indices": 1, "material": 0}
                ],
            }
        ],
        "materials": [
            {
                "name": "mock grey",
                "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0]},
            }
        ],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 4,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [0.01, 0.01, 0.01],
            },
            {"bufferView": 1, "componentType": 5123, "count": 12, "type": "SCALAR"},
        ],
    }
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\x00" * (-len(binary) % 4)
    chunks = (
        struct.pack("<II", len(text), 0x4E4F534A)
        + text
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def _iges_line(text: str, section: str, number: int) -> str:
    """One 80-column IGES line: text, the section letter in column 73, a number."""
    return f"{text:<72}{section}{number:>7}\n"


#: A 1 x 1 baseline JPEG (grey): one-code Huffman tables, a DC-only block.
_MOCK_JPEG = (
    b"\xff\xd8"
    + b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\xff\xdb\x00\x43\x00"
    + b"\x01" * 64
    + b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
    + b"\xff\xc4\x00\x14\x00\x01"
    + b"\x00" * 15
    + b"\x00"
    + b"\xff\xc4\x00\x14\x10\x01"
    + b"\x00" * 15
    + b"\x00"
    + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"
    + b"\x3f\xff\xd9"
)

#: What the mock writes for each export, by extension: the smallest file
#: each format's own check (``utils/export_check``) accepts.
_MOCK_EXPORTS: dict[str, bytes] = {
    ".step": (
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('mock'),'2;1');\n"
        "FILE_NAME('mock.step','',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF'));\n"
        "ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    ).encode("ascii"),
    ".igs": (
        _iges_line("mock IGES", "S", 1)
        + _iges_line("1H,,1H;;", "G", 1)
        + _iges_line("S      1G      1D      0P      0", "T", 1)
    ).encode("ascii"),
    ".pdf": b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n",
    ".dwg": b"AC1032" + b"\x00" * 122,
    ".dxf": b"  0\nSECTION\n  2\nHEADER\n  0\nENDSEC\n  0\nEOF\n",
    ".jpg": _MOCK_JPEG,
    ".bmp": (
        b"BM:\x00\x00\x00\x00\x00\x00\x006\x00\x00\x00(\x00\x00\x00"
        b"\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x18\x00\x00\x00\x00\x00"
        b"\x04\x00\x00\x00\x13\x0b\x00\x00\x13\x0b\x00\x00\x00\x00\x00\x00"
        b"\x00\x00\x00\x00\xff\xff\xff\x00"
    ),
    ".tif": b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00\x00\x00",
    ".glb": _mock_glb(),
    ".gltf": b'{"asset": {"version": "2.0"}}\n',
}
_MOCK_EXPORTS[".stp"] = _MOCK_EXPORTS[".step"]
_MOCK_EXPORTS[".iges"] = _MOCK_EXPORTS[".igs"]
_MOCK_EXPORTS[".jpeg"] = _MOCK_EXPORTS[".jpg"]
_MOCK_EXPORTS[".tiff"] = _MOCK_EXPORTS[".tif"]


def _mock_export_bytes(file_path: str) -> bytes | None:
    """What the mock writes for an export to ``file_path`` (None: nothing)."""
    return _MOCK_EXPORTS.get(os.path.splitext(file_path)[1].lower())


#: Orientations the mock accepts for a drawing view. Kept in step with
#: ``_NAMED_VIEWS`` in ``adapters/solidworks/io.py`` by a test — the two lists
#: are maintained separately, and an orientation accepted live but rejected
#: here could never be covered, since the suite runs against the mock.
_MOCK_NAMED_VIEWS: frozenset[str] = frozenset(
    {
        "front",
        "back",
        "left",
        "right",
        "top",
        "bottom",
        "isometric",
        "iso",
        "trimetric",
        "dimetric",
        "current",
    }
)


#: Canned assembly component tree used by ``list_features`` when a mock
#: Assembly's ``_assembly_components`` hasn't been configured by a test.
#: Two top-level Part components plus one nested sub-assembly one level
#: deep, per ``docs/agents`` / OpenSpec change ``assembly-aware-list-features``.
_DEFAULT_ASSEMBLY_COMPONENTS: dict[str, dict[str, Any]] = {
    "PartA": {
        "type": "Part",
        "path": "Mock://PartA.sldprt",
        "features": [
            {"name": "Boss-Extrude1", "type": "Extrusion", "suppressed": False},
            {"name": "Fillet1", "type": "Fillet", "suppressed": False},
        ],
    },
    "PartB": {
        "type": "Part",
        "path": "Mock://PartB.sldprt",
        "features": [
            {"name": "Cut-Extrude1", "type": "Cut", "suppressed": False},
        ],
    },
    "SubAssembly1": {
        "type": "Assembly",
        "path": "Mock://SubAssembly1.sldasm",
        "features": [
            {"name": "Mate1", "type": "Mate", "suppressed": False},
        ],
        "components": {
            "PartC": {
                "type": "Part",
                "path": "Mock://PartC.sldprt",
                "features": [
                    {"name": "Boss-Extrude1", "type": "Extrusion", "suppressed": False},
                ],
            },
        },
    },
}


class _BoolCallable:
    """Compatibility shim that behaves as both bool and callable.

    Args:
        getter (Callable[[], bool]): The getter value.

    Attributes:
        _getter (Any): The getter value.
    """

    def __init__(self, getter: Callable[[], bool]) -> None:
        """Initialize the bool callable.

        Args:
            getter (Callable[[], bool]): The getter value.

        Returns:
            None: None.
        """
        self._getter = getter

    def __call__(self) -> bool:
        """Build internal call.

        Returns:
            bool: True if call, otherwise False.
        """
        return bool(self._getter())

    def __bool__(self) -> bool:
        """Build internal bool.

        Returns:
            bool: True if bool, otherwise False.
        """
        return bool(self._getter())


def _mock_on_face(edge: dict[str, Any], normal: tuple[float, float, float]) -> bool:
    """Whether a canned block edge lies on the block face facing ``normal``."""
    axis = next(i for i in range(3) if normal[i] != 0)
    level = 10.0 if normal[axis] > 0 else 0.0
    return all(
        abs(p[axis] - level) < 1e-9
        for p in (edge.get("start_mm"), edge.get("end_mm"), edge.get("center_mm"))
        if p is not None
    )


def _mock_convex(_edge: Any, info: dict[str, Any]) -> str:
    """Every edge of the canned block (and its hole's rims) is convex."""
    info["convexity"] = "convex"
    return "convex"


def _mock_nearest_on_segment(
    point: list[float], a: list[float], b: list[float]
) -> list[float]:
    """The point of segment ``ab`` nearest ``point`` (mm)."""
    ab = [q - p for p, q in zip(a, b, strict=True)]
    length = sum(c * c for c in ab)
    t = 0.0
    if length:
        t = sum((p - s) * d for p, s, d in zip(point, a, ab, strict=True)) / length
    t = min(1.0, max(0.0, t))
    return [s + t * d for s, d in zip(a, ab, strict=True)]


def _mock_nearest_on_circle(
    point: list[float], center: list[float], radius: float
) -> list[float]:
    """The point of a circle about Z (the mock hole's rim) nearest ``point`` (mm)."""
    dx, dy = point[0] - center[0], point[1] - center[1]
    reach = math.hypot(dx, dy) or 1.0
    if not (dx or dy):
        dx = 1.0
    return [center[0] + radius * dx / reach, center[1] + radius * dy / reach, center[2]]


def _mock_block_faces(has_hole: bool) -> list[dict[str, Any]]:
    """The canned block's faces, each with how to find its point nearest another."""
    faces: list[dict[str, Any]] = []
    for axis in range(3):
        for level in (0.0, 10.0):
            low, high = [0.0, 0.0, 0.0], [10.0, 10.0, 10.0]
            low[axis] = high[axis] = level
            normal = [0.0, 0.0, 0.0]
            normal[axis] = 1.0 if level else -1.0
            area = 100.0 - (math.pi * 4.0 if has_hole and axis == 2 else 0.0)
            faces.append(
                {
                    "kind": "plane",
                    "normal": normal,
                    "area_mm2": round(area, 6),
                    "box_mm": low + high,
                    "nearest": lambda p, lo=low, hi=high: [
                        min(max(c, a), b) for c, a, b in zip(p, lo, hi, strict=True)
                    ],
                }
            )
    if has_hole:
        faces.append(
            {
                "kind": "cylinder",
                "axis": [0.0, 0.0, 1.0],
                "radius_mm": 2.0,
                "area_mm2": round(2 * math.pi * 2.0 * 10.0, 6),
                "box_mm": [3.0, 3.0, 0.0, 7.0, 7.0, 10.0],
                "nearest": lambda p: _mock_nearest_on_circle(
                    p, [5.0, 5.0, min(max(p[2], 0.0), 10.0)], 2.0
                ),
            }
        )
    return faces


class MockSolidWorksAdapter(SolidWorksAdapter):
    """Mock adapter that simulates SolidWorks operations.

    Args:
        config (object | None): Configuration values for the operation. Defaults to None.

    Attributes:
        _connected (Any): The connected value.
        _delays (Any): The delays value.
        _is_connected_proxy (Any): The is connected proxy value.
        _operation_count (Any): The operation count value.
        _simulate_errors (Any): The simulate errors value.
    """

    @property
    def _current_model(self) -> SolidWorksModel | None:
        """The active mock document."""
        return self.__dict__.get("_active_model")

    @_current_model.setter
    def _current_model(self, model: SolidWorksModel | None) -> None:
        # Switching documents drops the selection, as in real SolidWorks:
        # edges selected on one part must never feed an empty-edge_names
        # fillet/chamfer on another.
        if model is not self.__dict__.get("_active_model"):
            self.__dict__.get("_selection", []).clear()
        self.__dict__["_active_model"] = model

    def __init__(self, config: object | None = None) -> None:
        """Initialize the mock solid works adapter.

        Args:
            config (object | None): Configuration values for the operation. Defaults to None.

        Returns:
            None: None.
        """
        super().__init__(config)
        cfg: dict[str, Any] = dict(self.config_dict)
        self._connected = False
        self._current_model = None
        self._models: dict[str, SolidWorksModel] = {}
        self._features: dict[str, SolidWorksFeature] = {}
        # Names suppressed via suppress_feature. Kept beside _features rather
        # than on SolidWorksFeature, which has no suppression field.
        self._suppressed_features: set[str] = set()
        # Component tree for an Assembly-type current model, keyed by
        # component name. See ``_flatten_assembly_components`` for shape.
        # Empty means "use the canned default fixture" (see list_features).
        self._assembly_components: dict[str, dict[str, Any]] = {}
        self._sketches: dict[str, str] = {}
        self._current_sketch: str | None = None
        # Names currently "selected" via select_feature / select_face_edges,
        # so append and selection_count behave like the real selection set.
        self._selection: list[str] = []
        # Test hook: RawRebuildIssue objects rebuild_model should report, so
        # the error/warning paths can be exercised without real geometry.
        self.injected_rebuild_issues: list[Any] = []
        # Tracks IDs returned by add_line/add_arc/add_circle/add_centerline/
        # add_rectangle/add_spline so add_sketch_constraint can validate
        # entity1/entity2 the same way the real adapter validates against
        # its sketch-entity registry.
        self._sketch_entity_ids: set[str] = set()
        self._dimensions: dict[str, float] = {}
        # Assembly components inserted via insert_component, keyed by
        # insertion order. See insert_component/list_components/add_mate.
        self._components: list[str] = []
        # The file each inserted component holds (list_assembly_parts).
        self._component_files: dict[str, str] = {}
        # Save Bodies features save_body_as_part added, in order.
        self._save_bodies: list[dict[str, Any]] = []
        # Documents loaded without a window (path -> title, modified, used_by);
        # tests put leftovers of a closed assembly here.
        self._hidden_documents: dict[str, dict[str, Any]] = {}
        # Mates added via add_mate, in order, so the geometry summary can
        # report a mate count without real geometry.
        self._mates: list[dict[str, Any]] = []
        self._component_states: dict[str, dict[str, bool]] = {}
        # Components hidden by set_component_visibility (name -> shown);
        # a component not listed is shown.
        self._component_visibility: dict[str, bool] = {}
        # Each component's origin (mm) and rotation rows, as
        # set_component_transform reads them back.
        self._component_transforms: dict[str, dict[str, Any]] = {}
        # Drawing views placed via create_drawing_view / add_drawing_view /
        # create_technical_drawing, in sheet order. See list_drawing_views.
        self._drawing_views: list[str] = []
        # Reference planes created via create_reference_plane, in creation
        # order, used to invent sequential "PlaneN" names. Shared feature
        # tree counter with create_axis mirrors the live adapter's
        # features_before/features_after pair (see _feature_count in
        # solidworks/features.py).
        self._reference_planes: list[str] = []
        # Axes created via create_axis, in creation order, used to invent
        # sequential "AxisN" names the same way self._reference_planes
        # invents "PlaneN" names, and to validate the axis name passed to
        # pattern_circular the same way known_sources validates mirror
        # sources.
        self._axes: list[str] = []
        # The frame of each plane whose geometry the mock knows: a plane
        # offset from one it knows, or one made through a point square to a
        # normal (whose sketch x the mock picks, see Frame.through). Angled
        # and face planes have none.
        self._plane_frames: dict[str, Frame] = {}
        # Reference points created via create_reference_point, in creation
        # order, invented "PointN" names on the same pattern as _axes.
        self._reference_points: list[str] = []
        # Centre-mark count per drawing-view name, grown by auto_center_marks.
        self._center_marks: dict[str, int] = {}
        self._feature_tree_count = 0
        # Volume tracked for mirror_feature, in mm^3. Seeded at the
        # live-measured 1819569.1 mm^3 wing volume from
        # _mirror_feature_impl in solidworks/features.py, then doubled on
        # each successful mirror so volume_before/volume_after/volume_ratio
        # stay self-consistent without a real geometry engine.
        self._mirror_volume: float = 1819569.1
        # Volume tracked for pattern_circular, in mm^3. Seeded at the
        # live-measured 48431.5 mm^3 hub+blade volume from
        # _pattern_circular_impl in solidworks/features.py, then grown by
        # _PATTERN_BLADE_VOLUME per extra instance on each successful
        # pattern so volume_before/volume_after/volume_ratio stay
        # self-consistent without a real geometry engine.
        self._pattern_volume: float = 48431.5
        self._operation_count = 0
        # Simulated SolidWorks window (set_window_state, export_image) and the
        # user's modified files an assembly save would overwrite (tests set
        # these to exercise the confirmation flow).
        self._window_state = "normal"
        self._modified_user_files: list[str] = []
        # Canned clearance geometry (measure_clearance): each component's box
        # in its own coordinates, mm, as tests set it (None: no solid body);
        # its place in the assembly is in _component_transforms.
        self.component_boxes_mm: dict[str, list[float] | None] = {}
        # Canned multibody geometry (list_bodies and the body tools): each
        # part's solid bodies as boxes (mm), by document path. A part the mock
        # hasn't seen starts with part_bodies_mm; insert_part brings
        # inserted_part_bodies_mm[<file stem>] (a 10 mm cube when unset).
        self.part_bodies_mm: dict[str, list[float]] = {
            "Boss-Extrude1": [0.0, 0.0, 0.0, 40.0, 10.0, 40.0]
        }
        self.inserted_part_bodies_mm: dict[str, dict[str, list[float]]] = {}
        self._part_bodies: dict[str, dict[str, dict[str, Any]]] = {}
        self._body_features: dict[str, int] = {}

        # Configurable simulation delays (in seconds)
        self._delays = {
            "connect": cfg.get("mock_connect_delay", 0.1),
            "model_operation": cfg.get("mock_model_delay", 0.02),
            "feature_operation": cfg.get("mock_feature_delay", 0.03) if cfg else 0.03,
            "sketch_operation": cfg.get("mock_sketch_delay", 0.1),
        }
        self._is_connected_proxy = _BoolCallable(lambda: self._connected)
        self._simulate_errors = bool(cfg.get("simulate_errors", False))

    def __getattribute__(self, name: str) -> Any:
        """Build internal getattribute.

        Args:
            name (str): The name value.

        Returns:
            Any: The result produced by the operation.
        """
        if name == "is_connected":
            return object.__getattribute__(self, "_is_connected_proxy")
        return object.__getattribute__(self, name)

    async def connect(self) -> None:
        """Mock connection to SolidWorks.

        Returns:
            None: None.
        """
        await asyncio.sleep(self._delays["connect"])
        self._connected = True

        # Initialize some sample data
        self._dimensions = {
            "D1@Sketch1": 10.0,
            "D2@Sketch1": 20.0,
            "D1@Boss-Extrude1": 50.0,
        }

    async def disconnect(self) -> None:
        """Mock disconnection from SolidWorks.

        Returns:
            None: None.
        """
        self._connected = False
        self._current_model = None
        self._models.clear()
        self._features.clear()
        self._sketches.clear()
        self._current_sketch = None
        self._selection.clear()

    def is_connected(self) -> bool:
        """Check if mock connection is active.

        Returns:
            bool: True if connected, otherwise False.
        """
        return self._connected

    async def health_check(self) -> AdapterHealth:
        """Get mock health status.

        Returns:
            AdapterHealth: The result produced by the operation.
        """
        error_count = int(self._metrics["errors_count"])
        success_count = int(
            self._metrics["operations_count"] - self._metrics["errors_count"]
        )
        return AdapterHealth(
            healthy=self._connected,
            last_check=datetime.now(),
            error_count=error_count,
            success_count=success_count,
            average_response_time=self._metrics["average_response_time"],
            connection_status="connected" if self._connected else "disconnected",
            metrics={
                "adapter_type": "mock",
                "operations_count": self._operation_count,
                "models_count": len(self._models),
                "features_count": len(self._features),
                "sketches_count": len(self._sketches),
            },
        )

    async def get_application_info(self) -> AdapterResult[dict[str, Any]]:
        """Mock SolidWorks description: a fixed 2026 install in English.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "revision": "34.0.0",
                "language": "english",
                "templates": {
                    kind: {"file_name": f"{kind}.{ext}", "exists": True}
                    for kind, ext in (
                        ("part", "prtdot"),
                        ("assembly", "asmdot"),
                        ("drawing", "drwdot"),
                    )
                },
            },
        )

    async def open_model(self, file_path: str) -> AdapterResult[SolidWorksModel]:
        """Mock opening a SolidWorks model.

        Args:
            file_path (str): Path to the target file.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.

        Raises:
            SolidWorksOperationError: Simulated adapter failure.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        if self._simulate_errors:
            raise SolidWorksOperationError("Simulated adapter failure")

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        # Determine model type from file extension
        file_path_lower = file_path.lower()
        if file_path_lower.endswith(".sldprt"):
            model_type = "Part"
        elif file_path_lower.endswith(".sldasm"):
            model_type = "Assembly"
        elif file_path_lower.endswith(".slddrw"):
            model_type = "Drawing"
        else:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Unsupported file type: {file_path}",
            )

        # Create mock model
        model_name = file_path.split("/")[-1].split("\\")[-1]
        model = SolidWorksModel(
            path=file_path,
            name=model_name,
            type=model_type,
            is_active=True,
            configuration="Default",
            properties={
                "created": datetime.now().isoformat(),
                "model_id": model_name or f"Part{len(self._models) + 1}",
                "file_size": random.randint(100000, 5000000),
            },
        )

        self._current_model = model
        self._models[file_path] = model

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def open_model_read_only(
        self, file_path: str
    ) -> AdapterResult[SolidWorksModel]:
        """Mock opening a model read-only: ``open_model``, marked read-only.

        As in SolidWorks, a document already open stays as it was: one the
        mock opened for writing comes back with ``read_only`` False.
        """
        already = self._models.get(file_path)
        writable = already is not None and not (already.properties or {}).get(
            "read_only"
        )
        result = await self.open_model(file_path)
        if result.is_success and result.data is not None:
            result.data.properties = {
                **(result.data.properties or {}),
                "read_only": not writable,
            }
        return result

    async def set_window_state(self, state: str) -> AdapterResult[dict[str, Any]]:
        """Mock minimize/restore: changes the simulated window state.

        Args:
            state: ``minimize`` or ``restore``.

        Returns:
            AdapterResult[dict[str, Any]]: ``window_state`` afterwards.
        """
        states = {"minimize": "minimized", "restore": "normal"}
        if state not in states:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Unknown window state {state!r}; use minimize or restore",
            )
        self._window_state = states[state]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"window_state": self._window_state},
        )

    def _needs_confirmation(
        self, overwrite_modified_references: bool
    ) -> AdapterResult[Any] | None:
        """Mirror the real adapter: an assembly save that would overwrite the
        user's modified files (``_modified_user_files``) needs confirming."""
        if (
            self._modified_user_files
            and not overwrite_modified_references
            and self._current_model is not None
            and self._current_model.type == "Assembly"
        ):
            return confirmation_result(list(self._modified_user_files))
        return None

    def _saved_references(self) -> list[str]:
        """The user's files a confirmed save wrote; they are saved now."""
        saved = list(self._modified_user_files)
        self._modified_user_files = []
        return saved

    async def close_model(
        self, save: bool = False, overwrite_modified_references: bool = False
    ) -> AdapterResult[Any]:
        """Mock closing the current model.

        Args:
            save (bool): The save value. Defaults to False.
            overwrite_modified_references (bool): See
                ``SolidWorksAdapter.close_model``.

        Returns:
            AdapterResult[Any]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.WARNING, error="No active model to close"
            )
        if save and (needs := self._needs_confirmation(overwrite_modified_references)):
            return needs

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        if save:
            # Simulate save operation
            await asyncio.sleep(self._delays["model_operation"])
            self._saved_references()

        self._current_model = None

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=None,
            execution_time=self._delays["model_operation"],
        )

    async def get_model_info(self) -> AdapterResult[dict[str, Any]]:
        """Mock metadata for the currently active model.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        model = self._current_model
        feature_count = len(self._features)
        info = {
            "title": model.name,
            "path": model.path,
            "type": model.type,
            "configuration": model.configuration or "Default",
            "is_dirty": False,
            "feature_count": feature_count,
            "rebuild_status": 0,
        }

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=info,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def list_features(
        self, include_suppressed: bool = False, max_assembly_depth: int = 2
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Mock feature tree listing for the active model.

        For an Assembly model, flattens in every configured component's
        features (see ``_assembly_components``) alongside the document's own
        features, tagged with ``component``/``component_path`` the same way
        the real adapter does. Falls back to a small canned two-component
        fixture (plus one nested sub-assembly) when no components have been
        configured, mirroring the existing "seed realistic feature names"
        behavior for an empty Part.

        Args:
            include_suppressed (bool): The include suppressed value. Defaults to False.
            max_assembly_depth (int): Sub-assembly recursion budget. Ignored
                for non-Assembly models. Defaults to 2.

        Returns:
            AdapterResult[list[dict[str, Any]]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["feature_operation"] / 2)
        self._operation_count += 1

        is_assembly = self._current_model.type == "Assembly"

        # Seed realistic feature names for empty mock state.
        if not self._features:
            seeded: list[dict[str, Any]] = [
                {
                    "name": "Origin",
                    "type": "OriginProfileFeature",
                    "suppressed": False,
                    "component": None,
                    "component_path": None,
                    "component_parent": None,
                },
                {
                    "name": "Front Plane",
                    "type": "RefPlane",
                    "suppressed": False,
                    "component": None,
                    "component_path": None,
                    "component_parent": None,
                },
                {
                    "name": "Right Plane",
                    "type": "RefPlane",
                    "suppressed": False,
                    "component": None,
                    "component_path": None,
                    "component_parent": None,
                },
                {
                    "name": "Top Plane",
                    "type": "RefPlane",
                    "suppressed": False,
                    "component": None,
                    "component_path": None,
                    "component_parent": None,
                },
                {
                    "name": "Sketch1",
                    "type": "ProfileFeature",
                    "suppressed": False,
                    "component": None,
                    "component_path": None,
                    "component_parent": None,
                },
            ]
            if is_assembly:
                components = self._assembly_components or _DEFAULT_ASSEMBLY_COMPONENTS
                seeded.extend(
                    self._flatten_assembly_components(
                        components, include_suppressed, max_assembly_depth - 1, None
                    )
                )
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data=seeded,
                execution_time=self._delays["feature_operation"] / 2,
            )

        feature_rows: list[dict[str, Any]] = []
        for i, feature in enumerate(self._features.values()):
            row = {
                "name": feature.name,
                "type": feature.type,
                "suppressed": bool((feature.properties or {}).get("suppressed", False)),
                "position": i,
                "component": None,
                "component_path": None,
                "component_parent": None,
            }
            if include_suppressed or not row["suppressed"]:
                feature_rows.append(row)

        if is_assembly:
            components = self._assembly_components or _DEFAULT_ASSEMBLY_COMPONENTS
            feature_rows.extend(
                self._flatten_assembly_components(
                    components,
                    include_suppressed,
                    max_assembly_depth - 1,
                    None,
                )
            )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature_rows,
            execution_time=self._delays["feature_operation"] / 2,
        )

    def _flatten_assembly_components(
        self,
        components: dict[str, dict[str, Any]],
        include_suppressed: bool,
        depth_remaining: int,
        parent_component: str | None,
    ) -> list[dict[str, Any]]:
        """Flatten a mock component tree into tagged feature descriptors.

        Mirrors the real adapter's recursion rules: a sub-assembly component
        is expanded (its own features plus a recursive pass over its
        components) only while ``depth_remaining > 0``; beyond that it is
        represented by a single bare descriptor. An unresolvable component
        (``type: "Unresolved"`` in the fixture) always produces one
        ``UnresolvedComponent`` descriptor.

        Args:
            components: Mapping of component name to a fixture dict with
                ``type`` ("Part", "Assembly", or "Unresolved"), ``path``,
                ``features`` (list of ``{name, type, suppressed}``), and,
                for "Assembly" components, a nested ``components`` mapping.
            include_suppressed: Include suppressed entries when ``True``.
            depth_remaining: Sub-assembly recursion budget remaining.
            parent_component: Name of the component this level of
                ``components`` is nested inside, or ``None`` for top-level.

        Returns:
            list[dict[str, Any]]: Flattened, component-tagged descriptors.
        """
        results: list[dict[str, Any]] = []
        for name, comp in components.items():
            comp_type = comp.get("type")
            path = comp.get("path")

            if comp_type == "Unresolved":
                results.append(
                    {
                        "name": name,
                        "type": "UnresolvedComponent",
                        "suppressed": False,
                        "position": -1,
                        "component": name,
                        "component_path": None,
                        "component_parent": parent_component,
                    }
                )
                continue

            if comp_type == "Assembly" and depth_remaining <= 0:
                results.append(
                    {
                        "name": name,
                        "type": "Component",
                        "suppressed": False,
                        "position": -1,
                        "component": name,
                        "component_path": path,
                        "component_parent": parent_component,
                    }
                )
                continue

            for i, feature in enumerate(comp.get("features", [])):
                suppressed = bool(feature.get("suppressed", False))
                if not include_suppressed and suppressed:
                    continue
                results.append(
                    {
                        "name": feature["name"],
                        "type": feature["type"],
                        "suppressed": suppressed,
                        "position": i,
                        "component": name,
                        "component_path": path,
                        "component_parent": parent_component,
                    }
                )

            if comp_type == "Assembly":
                results.extend(
                    self._flatten_assembly_components(
                        comp.get("components", {}),
                        include_suppressed,
                        depth_remaining - 1,
                        name,
                    )
                )

        return results

    #: Default tree items every new part has, selectable like the real ones.
    _DEFAULT_SELECTABLE = ("Front Plane", "Top Plane", "Right Plane", "Origin")

    async def select_feature(
        self, feature_name: str, append: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock feature selection, mirroring the real adapter's contract.

        Succeeds only for names the mock is tracking for the active model
        (created features, sketches, default planes); anything else is an
        ERROR naming the feature - never SUCCESS with ``selected: False``.

        Args:
            feature_name (str): The feature name, optionally ``@doc`` qualified.
            append (bool): Add to the current selection. Defaults to False.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        self._operation_count += 1

        bare = feature_name.split("@", 1)[0].strip()
        known = (
            {f.name for f in self._features.values()}
            | set(self._sketches)
            | set(self._DEFAULT_SELECTABLE)
        )
        if bare not in known:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Feature not found or could not be selected: {feature_name}",
            )
        if not append:
            self._selection.clear()
        if bare not in self._selection:
            self._selection.append(bare)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "selected": True,
                "feature_name": feature_name,
                "selected_name": bare,
                "entity_type": "mock",
                "selection_count": len(self._selection),
            },
            execution_time=self._delays["feature_operation"] / 4,
        )

    async def get_selected_entities(self) -> AdapterResult[list[dict[str, Any]]]:
        """Mock UI selection — always reports one canned face and one canned edge.

        There's no real UI to read a selection from in mock mode, so this
        returns a stable, plausible-shaped fixture rather than an empty list,
        so callers exercising this tool in tests get realistic field names.

        Returns:
            AdapterResult[list[dict[str, Any]]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=[
                {
                    "index": 1,
                    "type": "FACE",
                    "bounding_box_mm": {
                        "min": [0.0, 0.0, 0.0],
                        "max": [30.0, 30.0, 0.0],
                    },
                    "center_mm": [15.0, 15.0, 0.0],
                    "area_mm2": 900.0,
                    "normal": [0.0, 0.0, 1.0],
                },
                {
                    "index": 2,
                    "type": "EDGE",
                    "start_mm": [0.0, 0.0, -15.0],
                    "end_mm": [30.0, 0.0, -15.0],
                    "length_mm": 30.0,
                },
            ],
            execution_time=self._delays["feature_operation"] / 4,
        )

    async def select_face_edges(
        self,
        point: list[float] | None = None,
        side: str | None = None,
        plane: str | None = None,
        edge_type: str = "all",
        append: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock face-edge multi-selection on a canned 10 mm block.

        Mirrors the real adapter's contract: the same input validation, the
        same payload shape, and the same failure cases. The block spans
        0..10 mm on every axis; the +Z/-Z faces carry a 2 mm radius hole rim
        once any circle has been sketched, so ``edge_type="circular"``
        behaves like a real drilled part. ``Front Plane`` (z = 0) is the only
        reference plane with a face on it.
        """
        from .solidworks.face_selection import side_vector, validate_face_query

        problem = validate_face_query(point, side, plane, edge_type)
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if getattr(self._current_model, "type", "Part") != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="select_face_edges requires an active part document.",
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)

        if point is not None:
            normal = self._mock_face_at(point)
            if normal is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=f"No face found at point {list(point)} mm.",
                )
        elif side is not None:
            normal = side_vector(side)
        elif plane == "Front Plane":
            normal = (0.0, 0.0, -1.0)
        else:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"No body face lies on {plane!r}.",
            )

        center = [5.0 + 5.0 * c for c in normal]
        has_hole = any(e.startswith("Circle") for e in self._sketch_entity_ids)
        edges: list[dict[str, Any]] = [
            {"curve": "linear", "length_mm": 10.0, "face_index": 0} for _ in range(4)
        ]
        if has_hole and normal[2] != 0:
            edges.append(
                {
                    "curve": "circular",
                    "center_mm": [5.0, 5.0, center[2]],
                    "axis": [0.0, 0.0, 1.0],
                    "radius_mm": 2.0,
                    "face_index": 0,
                }
            )
        edges = [e for e in edges if edge_type == "all" or e["curve"] == edge_type]
        if not edges:
            kind = "" if edge_type == "all" else f"{edge_type} "
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"The selected face has no {kind}edges (face centre {center} mm).",
            )

        if not append:
            self._selection.clear()
        self._selection.extend(f"Edge{i}" for i in range(len(edges)))
        face = {
            "index": 0,
            "center_mm": center,
            "normal": [float(c) for c in normal],
            "area_mm2": 100.0,
            "planar": True,
        }
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "face": face,
                "faces": [face],
                "edge_type": edge_type,
                "edges": edges,
                "selected_edges": len(edges),
                "selection_count": len(self._selection),
            },
            execution_time=self._delays["feature_operation"] / 4,
        )

    def _mock_block_edges(self) -> list[dict[str, Any]]:
        """The canned 10 mm block's edges, described as the live adapter does.

        Twelve straight edges, plus a 2 mm hole rim on each Z face once any
        circle has been sketched (the block ``select_face_edges`` uses).
        """
        edges: list[dict[str, Any]] = []
        for axis in range(3):
            others = [i for i in range(3) if i != axis]
            for a in (0.0, 10.0):
                for b in (0.0, 10.0):
                    start = [0.0, 0.0, 0.0]
                    start[others[0]], start[others[1]] = a, b
                    end = list(start)
                    end[axis] = 10.0
                    edges.append(
                        {
                            "curve": "linear",
                            "start_mm": start,
                            "end_mm": end,
                            "length_mm": 10.0,
                        }
                    )
        if any(e.startswith("Circle") for e in self._sketch_entity_ids):
            edges += [
                {
                    "curve": "circular",
                    "center_mm": [5.0, 5.0, z],
                    "axis": [0.0, 0.0, 1.0],
                    "radius_mm": 2.0,
                }
                for z in (0.0, 10.0)
            ]
        return edges

    async def select_edges(
        self, query: dict[str, Any], append: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock rule-based edge selection on the canned 10 mm block.

        The same validation, rules (``edge_rules.filter_edges``), messages
        and payload as the live adapter. Any feature in the mock tree made
        the whole block; a face source keeps the edges lying on that face.
        Every edge of the block is convex, and ``with_ids`` gives ids the
        mock's ``edge_ids`` takes back. A rule with no source reads every
        edge of the block, so the live adapter's scan limit
        (``SOLIDWORKS_MCP_EDGE_SCAN_LIMIT``) counts them all, box or not.
        """
        from .solidworks.edge_index import scan_limit
        from .solidworks.edge_rules import (
            MAX_DESCRIBED_EDGES,
            ScanTooLarge,
            describe_source,
            describe_sources,
            edge_key,
            expand_query,
            has_source,
            match_rules,
            no_match_message_rules,
            validate_edge_query,
        )
        from .solidworks.persist_ids import encode

        problem = validate_edge_query(query)
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if getattr(self._current_model, "type", "Part") != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="select_edges requires an active part document.",
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)

        rules = expand_query(query)
        limit = None if query.get("scan_whole_part") else scan_limit()
        block = len(self._mock_block_edges())
        for rule in rules:
            if limit and not has_source(rule) and block > limit:
                refused = ScanTooLarge(
                    describe_source(rule),
                    block,
                    limit,
                    rule.get("inside_box") is not None,
                )
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=str(refused),
                    data=refused.data(),
                )
        try:
            kept, counts = match_rules(rules, self._mock_rule_edges, _mock_convex)
        except LookupError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        if not kept:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=no_match_message_rules(rules, counts),
            )
        # The block has 14 edges at most, under with_ids' 100: no refusal here.
        with_ids = bool(query.get("with_ids"))
        if not append:
            self._selection.clear()
        self._selection.extend(f"Edge{i}" for i in range(len(kept)))
        data: dict[str, Any] = {
            "source": describe_sources(rules),
            "matched": counts[0] if len(rules) == 1 else {"rules": counts},
            "edges": [info for _edge, info in kept[:MAX_DESCRIBED_EDGES]],
            "selected_edges": len(kept),
            "selection_count": len(self._selection),
            "read": {
                "kept_from_earlier_call": False,
                "edges_described": len(self._mock_block_edges()),
                "face_boxes": 0,
            },
        }
        if with_ids:
            data["edge_ids"] = [
                encode(f"mock:{edge_key(info)}".encode()) for _edge, info in kept
            ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=data,
            execution_time=self._delays["feature_operation"] / 4,
        )

    async def query_geometry(
        self,
        point_mm: list[float],
        radius_mm: float = 2.0,
        kinds: str = "both",
        limit: int = 10,
        read_all: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock: the canned 10 mm block's faces and edges near a point, measured exactly.

        The block ``select_edges`` uses: six flat faces and twelve straight
        edges, and once any circle has been sketched, a 2 mm hole along Z
        through (5, 5) with its two rims. The validation and reply shape are
        the real adapter's. The mock reads the whole block, so ``method`` is
        ``index`` and ``complete`` true whatever ``read_all`` says; an edge's
        id is one the mock's ``select_edges`` takes back as ``edge_ids``.
        """
        from .solidworks.edge_rules import edge_key
        from .solidworks.geometry_query import query_problem
        from .solidworks.persist_ids import encode

        problem = query_problem(point_mm, radius_mm, kinds, limit)
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        model = self._current_model
        if model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "query_geometry needs an active part document (active "
                    f"document is {model.type.lower()})."
                ),
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        self._operation_count += 1
        point = [float(c) for c in point_mm]
        body = next(iter(self._mock_bodies()), None)
        has_hole = any(e.startswith("Circle") for e in self._sketch_entity_ids)

        def placed(closest: list[float]) -> dict[str, Any]:
            return {
                "distance_mm": round(math.dist(point, closest), 6),
                "closest_mm": [round(c, 6) + 0.0 for c in closest],
            }

        faces = []
        for number, face in enumerate(_mock_block_faces(has_hole)):
            entry = {"kind": face["kind"], **placed(face["nearest"](point))}
            entry.update(
                {k: v for k, v in face.items() if k not in ("kind", "nearest")}
            )
            if face["kind"] == "cylinder":
                entry["center_mm"] = [5.0, 5.0, min(max(point[2], 0.0), 10.0)]
            entry["body"] = body
            entry["id"] = encode(f"mock-face:{number}".encode())
            faces.append(entry)
        edges = []
        for info in self._mock_block_edges():
            if info["curve"] == "linear":
                entry = {
                    "kind": "line",
                    **placed(
                        _mock_nearest_on_segment(
                            point, info["start_mm"], info["end_mm"]
                        )
                    ),
                }
                entry.update(start_mm=info["start_mm"], end_mm=info["end_mm"])
                entry["length_mm"] = info["length_mm"]
            else:
                entry = {
                    "kind": "circle",
                    **placed(
                        _mock_nearest_on_circle(
                            point, info["center_mm"], info["radius_mm"]
                        )
                    ),
                }
                entry.update(
                    center_mm=info["center_mm"],
                    axis=info["axis"],
                    radius_mm=info["radius_mm"],
                    length_mm=round(2 * math.pi * info["radius_mm"], 6),
                )
            entry["body"] = body
            entry["id"] = encode(f"mock:{edge_key(info)}".encode())
            edges.append(entry)

        data: dict[str, Any] = {
            "point_mm": point,
            "radius_mm": float(radius_mm),
            "kinds": kinds,
            "method": "index",
            "complete": True,
        }
        for key, every in (("faces", faces), ("edges", edges)):
            if kinds not in (key, "both"):
                continue
            near = sorted(
                (e for e in every if e["distance_mm"] <= radius_mm),
                key=lambda e: e["distance_mm"],
            )
            data[key], data[f"{key}_found"] = near[:limit], len(near)
        data["read"] = {
            "kept_from_earlier_call": False,
            "bodies_near": 1,
            "picks": 0,
            "face_boxes": 0,
            "measured": len(faces) + len(edges),
            "described": len(faces) + len(edges),
        }
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=data,
            execution_time=self._delays["feature_operation"] / 4,
        )

    def _mock_rule_edges(
        self, rule: dict[str, Any]
    ) -> list[tuple[Any, dict[str, Any]]]:
        """The canned block's edges for one rule's source.

        Any feature in the mock tree made the whole block; a face source
        keeps the edges lying on that face, ``between_faces`` those lying on
        both, and ``edge_ids`` those the mock's ids name. The block is the
        part's one body.

        Raises:
            LookupError: The feature, face, plane, body or an edge id isn't
                there.
        """
        from .solidworks.edge_rules import edge_key
        from .solidworks.face_selection import side_vector
        from .solidworks.persist_ids import decode, missing_ids_message

        if rule.get("body") and rule["body"] not in self._mock_bodies():
            names = ", ".join(self._mock_bodies()) or "none"
            raise LookupError(
                f"No solid body named {rule['body']!r}; the part's bodies are "
                f"{names}. A feature renames the bodies it changes, so "
                "list_bodies names them as they are now."
            )
        if rule.get("edge_ids"):
            by_id = {
                f"mock:{edge_key(e)}".encode(): e for e in self._mock_block_edges()
            }
            wanted = [by_id.get(decode(text)) for text in rule["edge_ids"]]
            missing = [i for i, edge in enumerate(wanted) if edge is None]
            if missing:
                raise LookupError(missing_ids_message(missing))
            return [(None, e) for e in wanted if e is not None]
        if rule.get("between_faces") is not None:
            normals = [self._mock_face_at(p) for p in rule["between_faces"]]
            for point, normal in zip(rule["between_faces"], normals, strict=True):
                if normal is None:
                    raise LookupError(f"No face found at point {list(point)} mm.")
            on_both = [
                e
                for e in self._mock_block_edges()
                if all(_mock_on_face(e, n) for n in normals if n is not None)
            ]
            return [(None, e) for e in on_both]

        normal: tuple[float, float, float] | None = None
        if rule.get("feature"):
            name = str(rule["feature"])
            if not any(f.name == name for f in self._features.values()):
                raise LookupError(
                    f"Feature {name!r} not found; list_features names the features."
                )
        elif rule.get("face_point") is not None:
            normal = self._mock_face_at(rule["face_point"])
            if normal is None:
                raise LookupError(
                    f"No face found at point {list(rule['face_point'])} mm."
                )
        elif rule.get("side"):
            normal = side_vector(str(rule["side"]))
        elif rule.get("plane"):
            if rule["plane"] != "Front Plane":
                raise LookupError(f"No body face lies on {rule['plane']!r}.")
            normal = (0.0, 0.0, -1.0)

        edges = self._mock_block_edges()
        if normal is not None:
            edges = [e for e in edges if _mock_on_face(e, normal)]
        return [(None, e) for e in edges]

    def _mock_feature_kind(self, name: str) -> str | None:
        """``IFeature.GetTypeName2`` of a mock tree name, or None when absent."""
        if name in self._BUILTIN_PLANES or name in self._reference_planes:
            return "RefPlane"
        if name in self._axes:
            return "RefAxis"
        if name in self._sketches:
            return "ProfileFeature"
        if any(f.name == name for f in self._features.values()):
            return "Feature"
        return None

    async def set_visibility(
        self,
        names: list[str] | None = None,
        types: list[str] | None = None,
        visible: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock showing or hiding reference geometry and sketches.

        Keeps the document's display toggles (all shown at first) and the
        names hidden one by one, and refuses what the live adapter refuses,
        with its wording.
        """
        from .solidworks.visibility import (
            DISPLAY_TOGGLES,
            REFERENCE_TYPES,
            SKETCH_TYPES,
            unknown_types,
        )

        names, types = list(names or []), list(types or [])
        if not names and not types:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Give names, types or both to show or hide.",
            )
        unknown = unknown_types(types)
        if unknown:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown type(s) {', '.join(unknown)}; use "
                    f"{', '.join(DISPLAY_TOGGLES)}."
                ),
            )
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        toggles: dict[str, bool] = self.__dict__.setdefault(
            "_display_toggles", dict.fromkeys(DISPLAY_TOGGLES, True)
        )
        hidden: set[str] = self.__dict__.setdefault("_hidden_features", set())

        features = []
        for name in names:
            kind = self._mock_feature_kind(name)
            if kind is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=f"Feature {name!r} not found; list_features names them.",
                )
            if kind not in SKETCH_TYPES | REFERENCE_TYPES:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"{name!r} is a {kind}, not a plane, axis, point, "
                        "coordinate system or sketch; use suppress_feature to "
                        "turn a feature off"
                    ),
                )
            (hidden.discard if visible else hidden.add)(name)
            features.append({"name": name, "type": kind, "visible": bool(visible)})
        changed = {}
        for name in types:
            changed[name] = {"before": toggles[name], "after": bool(visible)}
            toggles[name] = bool(visible)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"visible": bool(visible), "features": features, "types": changed},
            execution_time=0.0,
        )

    #: The mock's feature types as SolidWorks' ``GetTypeName2`` names them.
    _MOCK_TYPE_NAMES = {
        "Extrusion": "Extrusion",
        "Cut": "Cut",
        "Revolve": "Revolution",
        "Fillet": "Fillet",
        "Chamfer": "Chamfer",
    }
    #: ``edit_feature``'s change names -> the mock feature's parameter keys.
    _MOCK_EDIT_KEYS = {
        "depth": "depth",
        "end_condition": "end_condition",
        "reverse_direction": "reverse_direction",
        "angle": "angle",
        "radius": "size",
    }

    async def edit_feature(
        self, name: str, changes: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock editing a feature's parameters, with the live adapter's rules.

        The change rules (which feature takes which change) are the live
        ones (``feature_editing.check_changes``); the values are kept in the
        feature's ``parameters``. New edges for a fillet or chamfer come from
        the mock selection's edges or the canned block's edges an edge query
        matches (as ``select_edges``); the mock keeps every edge given.
        """
        from .solidworks.feature_editing import (
            EDGE_CHANGES,
            check_changes,
            end_condition_value,
        )

        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if name in self._reference_planes:
            return self._mock_edit_plane(name, changes)
        feature = next((f for f in self._features.values() if f.name == name), None)
        if feature is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Feature {name!r} not found; list_features names them.",
            )
        kind = self._MOCK_TYPE_NAMES.get(feature.type, feature.type)
        problem = check_changes(kind, changes, name)
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        try:
            edges = self._mock_new_edges(name, changes)
        except (LookupError, ValueError) as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        parameters = feature.parameters if feature.parameters is not None else {}
        feature.parameters = parameters
        plain = {k: v for k, v in changes.items() if k not in EDGE_CHANGES}
        before = {key: parameters.get(self._MOCK_EDIT_KEYS[key]) for key in plain}
        for key, value in plain.items():
            if key == "end_condition":
                value = end_condition_value(str(value))[0]
            parameters[self._MOCK_EDIT_KEYS[key]] = value
        after = {key: parameters.get(self._MOCK_EDIT_KEYS[key]) for key in plain}
        data: dict[str, Any] = {
            "feature": name,
            "type": kind,
            "before": before,
            "after": after,
        }
        if edges is not None:
            data["edges_before"] = len(parameters.get("edges") or [])
            parameters["edges"] = edges
            data["edges_given"] = data["edges_after"] = len(edges)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=data,
            execution_time=self._delays["feature_operation"],
        )

    def _mock_edit_plane(
        self, name: str, changes: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock moving an offset plane, with the live adapter's rules and messages.

        A plane made at an angle (or on a face) is not an offset plane.
        """
        from .solidworks.feature_editing import check_changes
        from .solidworks.ref_plane_edit import REF_PLANE, not_offset_message

        problem = check_changes(REF_PLANE, changes, name)
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        definitions = self.__dict__.setdefault("_plane_definitions", {})
        plane = definitions.setdefault(name, {"offset": None, "flip": False})
        if plane["offset"] is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=not_offset_message(name)
            )
        before = dict(plane)
        plane.update(changes)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": name,
                "type": REF_PLANE,
                "before": before,
                "after": dict(plane),
            },
            execution_time=self._delays["feature_operation"],
        )

    def _mock_new_edges(self, name: str, changes: dict[str, Any]) -> list[str] | None:
        """The edge names ``changes`` gives a mock fillet or chamfer, or None.

        ``edges: "selection"`` takes the selected ``Edge<n>`` names;
        ``edge_rules`` names the canned block's matching edges ``Edge<n>``,
        as ``select_edges`` does.

        Raises:
            ValueError: No edge selected or matched (the live message).
            LookupError: A rule's feature, face or plane isn't there.
        """
        from .solidworks.edge_rules import expand_query, match_rules
        from .solidworks.feature_editing import (
            edge_query,
            no_edges_message,
            rules_reason,
            selection_reason,
        )

        if "edge_rules" in changes:
            rules = expand_query(edge_query(changes["edge_rules"]))
            kept, counts = match_rules(rules, self._mock_rule_edges, _mock_convex)
            if not kept:
                raise ValueError(no_edges_message(name, rules_reason(rules, counts)))
            return [f"Edge{i}" for i in range(len(kept))]
        if "edges" not in changes:
            return None
        edges = [s for s in self._selection if s.startswith("Edge")]
        if not edges:
            raise ValueError(
                no_edges_message(name, selection_reason(len(self._selection)))
            )
        return edges

    async def set_rollback(
        self, before: str | None = None, after: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock moving the rollback bar over the mock feature tree.

        ``rebuild`` explains the issues in ``injected_rebuild_issues`` of the
        features above the bar, as the live adapter reads them after a move.
        """
        from .solidworks.rebuild_diagnostics import explain, summarize

        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        names = [f.name for f in self._features.values()]
        target = before or after
        if target and target not in names:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Feature {target!r} not found; list_features names them.",
            )
        if before:
            index = names.index(before)
            position = f"before {before}"
        elif after:
            index = names.index(after) + 1
            position = f"after {after}"
        else:
            index, position = len(names), "the end"
        if index < len(names):
            self.__dict__["_rollback_index"] = index
        else:
            self.__dict__.pop("_rollback_index", None)
        self._operation_count += 1
        rolled = names[index:]
        entries = [
            explain(issue)
            for issue in self.injected_rebuild_issues
            if issue.feature not in rolled
        ]
        error_count, warning_count, summary = summarize(entries)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "position": position,
                "rolled_back": rolled,
                "rebuild": {
                    "errors": entries,
                    "error_count": error_count,
                    "warning_count": warning_count,
                    "summary": summary,
                },
            },
            execution_time=self._delays["model_operation"],
        )

    async def edit_sketch(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Mock opening an existing sketch for editing.

        The mock keeps no sketch geometry, so the sketch opens with no
        listed segments; new ``add_*`` calls draw into it as usual.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if name not in self._sketches:
            kind = self._mock_feature_kind(name)
            error = (
                f"Feature {name!r} not found; list_features names them."
                if kind is None
                else f"{name!r} is a {kind}, not a sketch."
            )
            return AdapterResult(status=AdapterResultStatus.ERROR, error=error)
        self._current_sketch = name
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"sketch": name, "segments": []},
            execution_time=self._delays["sketch_operation"],
        )

    async def delete_sketch_entities(
        self, entity_ids: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock deleting entities of the open sketch by id."""
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        missing = [i for i in entity_ids if i not in self._sketch_entity_ids]
        if missing:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=missing_ids_message(missing, sorted(self._sketch_entity_ids)),
            )
        for entity_id in entity_ids:
            self._sketch_entity_ids.discard(entity_id)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "deleted": list(entity_ids),
                "remaining": sorted(self._sketch_entity_ids),
            },
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def delete_all_sketch_entities(self) -> AdapterResult[dict[str, Any]]:
        """Mock deleting every entity of the open sketch (none is ever skipped)."""
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        deleted = sorted(self._sketch_entity_ids)
        self._sketch_entity_ids.clear()
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "deleted": deleted,
                "deleted_count": len(deleted),
                "skipped": [],
                "remaining": [],
            },
            execution_time=self._delays["sketch_operation"] / 2,
        )

    def _mock_sketch_definition(self, name: str) -> dict[str, Any]:
        """A mock sketch as ``get_sketch`` reads it: its plane, no geometry kept."""
        # The tree name of the plane it was made on, as create_sketch chose it.
        key, names = plane_candidates_for(self._sketches[name])
        return {
            "sketch": name,
            "kind": "2D",
            "plane": names[0],
            "references": [{"name": names[0], "type": "RefPlane"}],
            "axes": default_plane_axes(key) if key else None,
            "segments": [],
            "segment_counts": {},
        }

    def _mock_parameters(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        """A mock feature's parameters in ``get_feature_definition``'s shape."""
        if kind in ("Extrusion", "Cut"):
            draft = values.get("draft_angle") or 0
            return {
                "direction1": {
                    "depth_mm": values.get("depth"),
                    "end_condition": values.get("end_condition"),
                    "draft": bool(draft),
                    **({"draft_angle_deg": draft} if draft else {}),
                },
                "both_directions": bool(values.get("both_directions")),
                "reverse_direction": bool(values.get("reverse_direction")),
                "thin": bool(values.get("thin_feature")),
            }
        if kind == "Revolution":
            return {
                "angle_deg": values.get("angle"),
                "reverse_direction": bool(values.get("reverse_direction")),
            }
        edges = len(values.get("edges") or [])
        if kind == "Fillet":
            return {"radius_mm": values.get("size"), "edge_count": edges}
        return {"distance_mm": values.get("size"), "edge_count": edges}

    async def get_feature_definition(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Mock reading a feature's parameters, in the live reply's shape.

        Extrudes, cuts, revolves, fillets and chamfers give the values the
        mock kept when it made them; a sketch gives its plane; any other
        feature its type, as the live adapter does for types it doesn't read.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        dimensions = [
            {"name": key, "value": value, "units": "mm"}
            for key, value in self._dimensions.items()
            if key.endswith(f"@{name}")
        ]
        reply: dict[str, Any] = {
            "feature": name,
            "suppressed": name in self._suppressed_features,
            "parameters": {},
            "dimensions": dimensions,
        }
        feature = next((f for f in self._features.values() if f.name == name), None)
        if name in self._sketches:
            reply.update(type="ProfileFeature", known_type=True)
            reply["parameters"] = {
                "plane": self._mock_sketch_definition(name)["plane"],
                "segment_counts": {},
                "texts": [],
            }
        elif feature is not None:
            kind = self._MOCK_TYPE_NAMES.get(feature.type, feature.type)
            reply.update(type=kind, known_type=kind in self._MOCK_TYPE_NAMES.values())
            if reply["known_type"]:
                reply["parameters"] = self._mock_parameters(
                    kind, feature.parameters or {}
                )
            else:
                reply["note"] = f"A {kind} gives its dimensions only."
        elif self._mock_feature_kind(name) is not None:
            reply.update(type=self._mock_feature_kind(name), known_type=False)
            reply["note"] = f"A {reply['type']} gives its dimensions only."
        else:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Feature {name!r} not found; list_features names them.",
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=reply,
            execution_time=self._delays["feature_operation"],
        )

    async def get_sketch(
        self, name: str, detail: str = "full"
    ) -> AdapterResult[dict[str, Any]]:
        """Mock reading a sketch without opening it: its plane, no geometry kept.

        A ``"summary"`` gives the summary's keys, empty, and a fully defined
        state (the mock keeps no geometry to leave free).
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if name not in self._sketches:
            kind = self._mock_feature_kind(name)
            error = (
                f"Feature {name!r} not found; list_features names them."
                if kind is None
                else f"{name!r} is a {kind}, not a sketch; get_feature_definition "
                "reads other features."
            )
            return AdapterResult(status=AdapterResultStatus.ERROR, error=error)
        self._operation_count += 1
        data = self._mock_sketch_definition(name)
        if detail == "summary":
            data = {
                **{k: v for k, v in data.items() if k not in ("segments", "axes")},
                "detail": "summary",
                "circles": [],
                "dimensions": [],
                "relations": [],
                "relation_counts": {},
                "dimension_relations": 0,
                "is_fully_defined": True,
                "definition_state": "fully_defined",
                "source": "mock",
                "raw_status": 3,
            }
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=data,
            execution_time=self._delays["sketch_operation"],
        )

    def _mock_references(self, path: str) -> list[tuple[str, str]]:
        """What a file references in the mock, as ``(name, path)``.

        Tests set ``document_references``: a file's path -> the paths it
        references, as ``GetDocumentDependencies2`` would read them.
        """
        from .solidworks.references import normalize

        table = self.__dict__.get("document_references", {})
        for key, paths in table.items():
            if normalize(key) == normalize(path):
                return [(os.path.splitext(os.path.basename(p))[0], p) for p in paths]
        return []

    async def list_references(
        self, file_path: str, traverse: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock listing a file's references from ``document_references``.

        The file must be on disk, as for the live adapter; each reference is
        checked on disk the same way (``references.describe_reference``).
        """
        from .solidworks.references import (
            check_document,
            describe_reference,
            normalize,
        )

        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        resolved = os.path.abspath(file_path)
        try:
            check_document(resolved)
        except (LookupError, ValueError) as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        pairs = self._mock_references(resolved)
        seen = {normalize(path) for _name, path in pairs}
        index = 0
        while traverse and index < len(pairs):
            for name, path in self._mock_references(pairs[index][1]):
                if normalize(path) not in seen:
                    seen.add(normalize(path))
                    pairs.append((name, path))
            index += 1
        references = [describe_reference(n, p, resolved) for n, p in pairs]
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "file": resolved,
                "traverse": bool(traverse),
                "references": references,
                "count": len(references),
                "missing": [r["path"] for r in references if not r["exists"]],
            },
        )

    async def move_document(
        self,
        source: str,
        destination: str,
        referencing: list[str] | None = None,
        search_folders: list[str] | None = None,
        dry_run: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock moving a file: the live plan, then the file moved on disk.

        The plan is the live one (``references.plan_move``) over
        ``document_references``; every model the mock has open counts as
        open in SolidWorks. A move moves the file and repoints the
        documents' entries in ``document_references``.
        """
        from .solidworks.references import (
            blocked_message,
            dry_run_reply,
            normalize,
            plan_move,
        )

        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        try:
            plan = plan_move(
                source,
                destination,
                referencing,
                search_folders,
                self._mock_references,
                {normalize(path): False for path in self._models},
            )
        except (LookupError, ValueError) as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        if dry_run:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS, data=dry_run_reply(plan)
            )
        if plan["blocked"]:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=blocked_message(plan)
            )
        src, dst = plan["source"], plan["destination"]
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        table = self.__dict__.setdefault("document_references", {})
        users = {
            normalize(u["path"]): normalize(u["references_as"]) for u in plan["users"]
        }
        for key in list(table):
            old = users.get(normalize(key))
            if old is not None:
                table[key] = [dst if normalize(p) == old else p for p in table[key]]
            if normalize(key) == normalize(src):
                table[dst] = table.pop(key)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                **plan,
                "dry_run": False,
                "moved": True,
                "method": "mock move",
                "updated": [
                    {"path": u["path"], "verified": True} for u in plan["users"]
                ],
            },
        )

    async def check_printability(
        self,
        build_direction: str | list[float] | None,
        support_angle_deg: float = 45.0,
        min_bridge_mm: float = 5.0,
        orientations: int = 0,
        min_feature_mm: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock the print check: the real check on the mock's STL (a tetrahedron).

        Args:
            build_direction: The direction the part grows from the plate.
            support_angle_deg: Down-facing surfaces under this angle need
                supports.
            min_bridge_mm: Only longer bridges are listed.
            orientations: How many build directions to try (0: none).
            min_feature_mm: Report regions thinner than this (None: don't).

        Returns:
            AdapterResult[dict[str, Any]]: As the live adapter's.
        """
        from .solidworks.print_check import check_part

        if getattr(self._current_model, "type", None) != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="check_printability requires an active part document.",
            )
        return await check_part(
            self,
            build_direction,
            support_angle_deg,
            min_bridge_mm,
            orientations,
            min_feature_mm,
        )

    async def run_api_snippet(
        self, code: str, timeout_s: float = 60.0, compile_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a snippet run: nothing is compiled or run; a canned result echoes it.

        Args:
            code: The snippet; only its form is read.
            timeout_s: Unused.
            compile_only: Reply as a compile-only run.

        Returns:
            AdapterResult[dict[str, Any]]: Shaped like the live adapter's.
        """
        from .solidworks.api_snippets import prepare_source

        form = prepare_source(code).form
        data: dict[str, Any] = {
            "ok": True,
            "stage": "compiled" if compile_only else "done",
            "compiled": True,
            "entry_point": "Snippet.Run(SldWorks)",
            "source_form": form,
            "stdout": "",
            "stderr": "",
            "mock": True,
        }
        if not compile_only:
            data["result"] = {"mock": True, "code_chars": len(code)}
            data["result_type"] = "System.Collections.Generic.Dictionary"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=data, execution_time=0.01
        )

    async def check_wall_thickness(
        self,
        min_wall_mm: float = 0.8,
        max_rays: int = 5000,
        exclude_boxes_mm: list[list[float]] | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock wall thickness of the canned 10 mm block: 10 mm everywhere.

        One sample at the centre of each of the block's six faces (the block
        spans 0..10 mm on each axis), summarized by the live adapter's own
        ``thickness.summarize``, so the reply has the live shape,
        ``min_wall_mm`` above 10 finds every sample thin and
        ``exclude_boxes_mm`` works as it does live.
        """
        from .solidworks.thickness import summarize

        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        kind = str(getattr(self._current_model, "type", "Part")).lower()
        if kind != "part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "check_wall_thickness needs an active part document "
                    f"(active document is {kind})."
                ),
            )
        samples = []
        for axis in range(3):
            for sign in (1.0, -1.0):
                normal = [0.0, 0.0, 0.0]
                normal[axis] = sign
                point = [0.005, 0.005, 0.005]
                point[axis] = 0.01 if sign > 0 else 0.0
                samples.append((point, normal))
        samples = samples[: max(1, int(max_rays))]
        reply = summarize(
            samples,
            dict.fromkeys(range(len(samples)), 0.01),
            min_wall_mm,
            exclude_boxes_mm,
        )
        reply.update({"bodies": 1, "faces": 6})
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=reply,
            execution_time=self._delays["feature_operation"] / 4,
        )

    async def add_fillet(
        self, radius: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock constant-radius fillet (see :meth:`_mock_edge_feature`)."""
        return await self._mock_edge_feature("Fillet", radius, edge_names)

    async def add_chamfer(
        self,
        distance: float,
        edge_names: list[str],
        angle: float = 45.0,
        flip: bool = False,
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock angle-distance chamfer (see :meth:`_mock_edge_feature`)."""
        result = await self._mock_edge_feature("Chamfer", distance, edge_names)
        if result.is_success:
            result.data.parameters.update({"angle": angle, "flip": flip})
        return result

    async def _mock_edge_feature(
        self, kind: str, size: float, edge_names: list[str]
    ) -> AdapterResult[SolidWorksFeature]:
        """Create a mock fillet/chamfer on named edges or the live selection.

        Mirrors the real contract: an empty ``edge_names`` acts on whatever is
        currently selected (e.g. after ``select_face_edges``), and is an error
        when nothing is. It holds every edge it was given, and the features
        below a rollback bar ``set_rollback`` moved up are named, as the live
        adapter reads them (``edge_feature_check``).
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if size <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"{kind} size must be positive",
            )
        edges = list(edge_names) or list(self._selection)
        if not edges:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"No edges given and nothing is selected for the {kind.lower()}",
            )
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        count = sum(1 for f in self._features.values() if f.type == kind) + 1
        names = [f.name for f in self._features.values()]
        bar = self.__dict__.get("_rollback_index", len(names))
        feature = SolidWorksFeature(
            name=f"{kind}{count}",
            type=kind,
            id=f"{kind.lower()}_{uuid.uuid4().hex[:8]}",
            parameters={"size": size, "edges": edges},
            properties={
                "created": datetime.now().isoformat(),
                "mock": True,
                "edges_selected": len(edges),
                "selection_other": 0,
                "edges_held": len(edges),
                "features_below_bar": names[bar:],
            },
        )
        self._features[feature.id or feature.name] = feature
        self._selection.clear()
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"] / 4,
        )

    async def rebuild_model(
        self, top_level_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock rebuild with the real adapter's diagnostics contract.

        Clean by default; issues placed in ``injected_rebuild_issues`` are
        explained with the same ``rebuild_diagnostics`` logic the real
        adapter uses, so ERROR-with-data / warnings-only behave identically.
        """
        from .solidworks.rebuild_diagnostics import explain, summarize

        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        await asyncio.sleep(self._delays["model_operation"] / 4)
        entries = [explain(i) for i in self.injected_rebuild_issues]
        error_count, warning_count, summary = summarize(entries)
        data = {
            "rebuilt": True,
            "top_level_only": top_level_only,
            "errors": entries,
            "error_count": error_count,
            "warning_count": warning_count,
            "summary": summary,
        }
        return AdapterResult(
            status=(
                AdapterResultStatus.ERROR
                if error_count
                else AdapterResultStatus.SUCCESS
            ),
            data=data,
            error=summary if error_count else None,
            execution_time=self._delays["model_operation"] / 4,
        )

    @staticmethod
    def _mock_face_at(point: list[float]) -> tuple[float, float, float] | None:
        """Return the outward normal of the mock block face containing ``point``."""
        if not all(-1e-6 <= c <= 10.0 + 1e-6 for c in point):
            return None
        for axis in range(3):
            for value, sign in ((0.0, -1.0), (10.0, 1.0)):
                if abs(point[axis] - value) <= 1e-6:
                    normal = [0.0, 0.0, 0.0]
                    normal[axis] = sign
                    return (normal[0], normal[1], normal[2])
        return None

    async def list_configurations(self) -> AdapterResult[list[str]]:
        """Mock configuration listing for the active model.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        active = self._current_model.configuration or "Default"
        configs = [active] if active == "Default" else ["Default", active]

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=configs,
            execution_time=self._delays["model_operation"] / 2,
        )

    async def create_part(
        self,
        name: str | None = None,
        units: str | None = None,
        template: str | None = None,
    ) -> AdapterResult[SolidWorksModel]:
        """Mock creating a new part.

        Args:
            name (str | None): The name value. Defaults to None.
            units (str | None): The units value. Defaults to None.
            template (str | None): Template file path. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        token = "mm"
        if units is not None and str(units).strip():
            token = _normalise_mock_unit_system(str(units))
            if token is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unrecognised units {units!r}. "
                        "Expected one of: mm, cm, m, in, ft."
                    ),
                )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        model_id = name or f"Part{len(self._models) + 1}"
        model = SolidWorksModel(
            path=f"Mock://{model_id}.sldprt",
            name=f"{model_id}",
            type="Part",
            is_active=True,
            configuration="Default",
            properties={
                "created": datetime.now().isoformat(),
                "mock": True,
                "units": token,
                "template": template,
            },
        )

        self._current_model = model
        self._models[model.path] = model

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def create_assembly(
        self, name: str | None = None, template: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Mock creating a new assembly.

        Args:
            name (str | None): The name value. Defaults to None.
            template (str | None): Template file path. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        model_id = name or f"Assembly{len(self._models) + 1}"
        model = SolidWorksModel(
            path=f"Mock://{model_id}.sldasm",
            name=f"{model_id}",
            type="Assembly",
            is_active=True,
            configuration="Default",
            properties={
                "created": datetime.now().isoformat(),
                "mock": True,
                "template": template,
            },
        )

        self._current_model = model
        self._models[model.path] = model

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def create_drawing(
        self, name: str | None = None, template: str | None = None
    ) -> AdapterResult[SolidWorksModel]:
        """Mock creating a new drawing.

        Args:
            name (str | None): The name value. Defaults to None.
            template (str | None): Template file path. Defaults to None.

        Returns:
            AdapterResult[SolidWorksModel]: The result produced by the operation.
        """
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        model_id = name or f"Drawing{len(self._models) + 1}"
        model = SolidWorksModel(
            path=f"Mock://{model_id}.slddrw",
            name=f"{model_id}",
            type="Drawing",
            is_active=True,
            configuration="Default",
            properties={
                "created": datetime.now().isoformat(),
                "mock": True,
                "template": template,
            },
        )

        self._current_model = model
        self._models[model.path] = model

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=model,
            execution_time=self._delays["model_operation"],
        )

    async def create_extrusion(
        self,
        params: ExtrusionParameters | str,
        depth: float | None = None,
        direction: str | None = None,
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating an extrusion feature.

        Args:
            params (ExtrusionParameters | str): The params value.
            depth (float | None): The depth value. Defaults to None.
            direction (str | None): The direction value. Defaults to None.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        if isinstance(params, str):
            params = ExtrusionParameters(depth=depth or 0.0)
        sketch = self._mock_named_sketch(
            getattr(params, "sketch_name", None), "extrude"
        )
        if isinstance(sketch, AdapterResult):
            return sketch

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        feature_id = f"Boss-Extrude{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type="Extrusion",
            id=str(uuid.uuid4()),
            parameters={
                "depth": params.depth,
                "direction": direction or "blind",
                "draft_angle": params.draft_angle,
                "reverse_direction": params.reverse_direction,
                "both_directions": params.both_directions,
                "end_condition": "MidPlane"
                if params.both_directions
                else params.end_condition,
                "thin_feature": params.thin_feature,
                "thin_thickness": params.thin_thickness,
                "thin_side": params.thin_side if params.thin_feature else None,
                "sketch_name": sketch,
                "start_offset": params.start_offset,
                "draft_outward": params.draft_outward,
                "regions": getattr(params, "regions", None),
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_revolve(
        self, params: RevolveParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a revolve feature.

        Args:
            params (RevolveParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        sketch = self._mock_named_sketch(
            getattr(params, "sketch_name", None), "revolve"
        )
        if isinstance(sketch, AdapterResult):
            return sketch
        axis = getattr(params, "axis_entity", None)
        if axis and axis not in self._axes:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Axis '{axis}' is not a reference axis in the "
                "model. Create one with create_axis.",
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        cut = bool(getattr(params, "cut", False))
        stem = "Cut-Revolve" if cut else "Boss-Revolve"
        feature_id = f"{stem}{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type="Cut-Revolve" if cut else "Revolve",
            id=str(uuid.uuid4()),
            parameters={
                "angle": params.angle,
                "reverse_direction": params.reverse_direction,
                "both_directions": params.both_directions,
                "thin_feature": params.thin_feature and not cut,
                "thin_thickness": params.thin_thickness,
                "sketch_name": sketch,
                "axis_entity": axis,
                "cut": cut,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    def _mock_rename_sketch(self, old: str, new: str) -> AdapterResult[dict[str, Any]]:
        """Rename a mock sketch, refusing a name already in use.

        Args:
            old: The sketch's current name.
            new: The name to give it.

        Returns:
            AdapterResult[dict[str, Any]]: As ``rename_feature`` reports it.
            The open sketch is refused, as SolidWorks refuses it (it accepts
            the new name but the tree keeps the old one).
        """
        if self._current_sketch == old:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Cannot rename '{old}' while it is open for editing",
            )
        taken = new in self._sketches or any(
            f.name == new for f in self._features.values()
        )
        if taken:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Cannot rename to '{new}': a feature with that name already exists",
            )
        self._sketches = {
            (new if name == old else name): plane
            for name, plane in self._sketches.items()
        }
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"renamed": True, "old_name": old, "new_name": new},
            execution_time=self._delays["model_operation"],
        )

    def _mock_named_sketch(
        self, name: str | None, purpose: str
    ) -> str | None | AdapterResult[SolidWorksFeature]:
        """The mock sketch ``name`` matches, without regard to case.

        Args:
            name: The sketch asked for, or ``None`` for the last closed.
            purpose: How the error names the use, e.g. ``"extrude"``.

        Returns:
            The sketch's name, ``None`` when none was asked for, or an error
            result listing the sketches when ``name`` matches none.
        """
        if not name:
            return None
        for real in self._sketches:
            if real.lower() == name.lower():
                return real
        available = ", ".join(self._sketches) or "(none found)"
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=f"Sketch '{name}' not found in the feature tree. Sketches "
            f"available to {purpose}: {available}",
        )

    async def create_sweep(
        self, params: SweepParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a sweep feature.

        Args:
            params (SweepParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        feature_id = f"Boss-Sweep{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type="Sweep",
            id=str(uuid.uuid4()),
            parameters={
                "path": params.path,
                "twist_along_path": params.twist_along_path,
                "twist_angle": params.twist_angle,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_loft(
        self, params: LoftParameters
    ) -> AdapterResult[SolidWorksFeature]:
        """Mock creating a loft feature.

        Args:
            params (LoftParameters): The params value.

        Returns:
            AdapterResult[SolidWorksFeature]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        feature_id = f"Boss-Loft{len(self._features) + 1}"
        feature = SolidWorksFeature(
            name=feature_id,
            type="Loft",
            id=str(uuid.uuid4()),
            parameters={
                "profiles": params.profiles,
                "guide_curves": params.guide_curves,
                "start_tangent": params.start_tangent,
                "end_tangent": params.end_tangent,
            },
            properties={"created": datetime.now().isoformat(), "mock": True},
        )

        feature_key = feature.id or feature.name
        self._features[feature_key] = feature

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=feature,
            execution_time=self._delays["feature_operation"],
        )

    async def create_sketch(self, plane: str) -> AdapterResult[dict[str, Any]]:  # type: ignore[override]
        """Mock creating a sketch.

        Args:
            plane (str): The plane value.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            # Legacy tests start sketching immediately after connect.
            await self.create_part()

        # Like the live adapter: a default plane by any of its names, or a
        # plane create_reference_plane made - never a fallback to another.
        default_key, candidates = plane_candidates_for(plane)
        plane_used = candidates[0]
        if default_key is None and plane_used not in self._reference_planes:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Plane '{plane}' not found in the part. Use Front, Top "
                    "or Right, or a plane made by create_reference_plane "
                    "(list_features names them)"
                ),
            )

        await asyncio.sleep(self._delays["sketch_operation"])
        self._operation_count += 1

        sketch_id = f"Sketch{len(self._sketches) + 1}"
        self._sketches[sketch_id] = plane
        self._current_sketch = sketch_id

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "id": sketch_id,
                "name": sketch_id,
                "sketch_name": sketch_id,
                "plane": plane,
            },
            execution_time=self._delays["sketch_operation"],
            metadata={
                "plane": plane_used,
                # The default planes' axes, and a reference plane's when the
                # mock knows its geometry (_plane_frames).
                "axes": self._mock_axes_of(plane_used),
            },
        )

    async def add_sketch_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        construction: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Legacy compatibility wrapper around add_line.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.
            construction (bool): The construction value. Defaults to False.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        result = await self.add_line(x1, y1, x2, y2)
        if not result.is_success:
            return AdapterResult(
                status=result.status,
                error=result.error,
                execution_time=result.execution_time,
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "id": result.data,
                "start": {"x": x1, "y": y1},
                "end": {"x": x2, "y": y2},
                "construction": construction,
            },
            execution_time=result.execution_time,
        )

    async def save_file(
        self,
        file_path: str | None = None,
        overwrite_modified_references: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock save, with the real adapter's overwrite confirmation.

        Args:
            file_path (str | None): Path to the target file. Defaults to None.
            overwrite_modified_references (bool): See
                ``SolidWorksAdapter.save_file``.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )
        if needs := self._needs_confirmation(overwrite_modified_references):
            return needs
        await asyncio.sleep(0.02)
        self._operation_count += 1
        resolved_path = file_path or self._current_model.path
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "file_path": resolved_path,
                "saved": True,
                "saved_references": self._saved_references(),
                "warnings": [],
            },
            execution_time=0.02,
        )

    async def add_line(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Mock adding a line to sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        line_id = f"Line{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(line_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=line_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_centerline(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Mock adding a construction centerline to sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        centerline_id = f"Centerline_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(centerline_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=centerline_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_spline(self, points: list[dict[str, float]]) -> AdapterResult[str]:
        """Mock adding a NURBS spline through the supplied points.

        Args:
            points (list[dict[str, float]]): Ordered control points with
                ``"x"`` / ``"y"`` keys. Minimum 2 points.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if len(points) < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="add_spline requires at least 2 points",
            )
        # Match the real adapter's point contract: each dict must carry
        # both ``x`` and ``y`` keys, or the live impl raises KeyError on
        # ``point["x"]``. Validating here keeps mock/live parity so tests
        # that pass against the mock won't fail live on malformed input.
        for index, point in enumerate(points):
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"add_spline point at index {index} is missing "
                        "required 'x' and/or 'y' keys"
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        spline_id = f"Spline_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(spline_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=spline_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_circle(
        self, center_x: float, center_y: float, radius: float
    ) -> AdapterResult[str]:
        """Mock adding a circle to sketch.

        Args:
            center_x (float): The center x value.
            center_y (float): The center y value.
            radius (float): The radius value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        circle_id = f"Circle{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(circle_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=circle_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_arc(
        self,
        center_x: float,
        center_y: float,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        direction: str = "ccw",
    ) -> AdapterResult[str]:
        """Mock adding a circular arc to sketch.

        Args:
            center_x (float): Arc centre X.
            center_y (float): Arc centre Y.
            start_x (float): Arc start point X.
            start_y (float): Arc start point Y.
            end_x (float): Arc end point X.
            end_y (float): Arc end point Y.
            direction (str): ``"ccw"`` (default) or ``"cw"``.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if direction not in ARC_DIRECTION_FLAGS:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"direction must be 'ccw' or 'cw', not {direction!r}",
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        arc_id = f"Arc_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(arc_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=arc_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    def _new_sketch_entity_id(self, prefix: str) -> str:
        """A ``<prefix>_<n>`` id no entity in the mock sketch has yet."""
        number = len(self._sketch_entity_ids) + 1
        while f"{prefix}_{number}" in self._sketch_entity_ids:
            number += 1
        entity_id = f"{prefix}_{number}"
        self._sketch_entity_ids.add(entity_id)
        return entity_id

    async def add_sketch_outlines(
        self, outlines: list[dict[str, Any]]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock drawing whole outlines of lines and arcs in one call.

        Refuses what the live adapter refuses (no open sketch, no segments,
        more than ``MAX_OUTLINE_SEGMENTS``) with the same messages, and gives
        each segment an id the way the live adapter does (``Line_N``,
        ``Arc_N``).

        Args:
            outlines (list[dict[str, Any]]): The resolved outlines.

        Returns:
            AdapterResult[dict[str, Any]]: ``outlines`` with segment ``ids``,
            ``segment_count``, ``line_count`` and ``arc_count``.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        total = sum(len(outline.get("segments") or []) for outline in outlines)
        if total == 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="outlines contain no segments"
            )
        if total > MAX_OUTLINE_SEGMENTS:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"{total} segments in one call; the most is "
                    f"{MAX_OUTLINE_SEGMENTS}. Split the outlines over several calls"
                ),
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        replies = []
        counts = {"line": 0, "arc": 0}
        for outline in outlines:
            ids = []
            for segment in outline["segments"]:
                prefix = "Arc" if segment["type"] == "arc" else "Line"
                ids.append(self._new_sketch_entity_id(prefix))
                counts[segment["type"]] += 1
            replies.append(
                {"ids": ids, "construction": bool(outline.get("construction", False))}
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "outlines": replies,
                "segment_count": total,
                "line_count": counts["line"],
                "arc_count": counts["arc"],
            },
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_sketch_entities(
        self, items: list[dict[str, Any]], rollback_on_error: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        """Mock drawing lines, circles, rectangles, arcs and centrelines in one call.

        Refuses what the live adapter refuses (no open sketch, an item it
        can't draw, too many segments) with the same messages, and gives each
        entity an id the way the live adapter does (``Circle_N``, ...). Mock
        geometry never fails part-way, so ``rollback_on_error`` changes
        nothing here.

        Args:
            items (list[dict[str, Any]]): The entities, in order.
            rollback_on_error (bool): Accepted as the live adapter takes it.

        Returns:
            AdapterResult[dict[str, Any]]: ``items``, one ``{"index", "tool",
            "id", "execution_time"}`` per item, and ``count``.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        problem = sketch_entities_problem(items)
        if problem is not None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        drawn = [
            {
                "index": index,
                "tool": item["tool"],
                "id": self._new_sketch_entity_id(SKETCH_ENTITY_PREFIXES[item["tool"]]),
                "execution_time": 0.0,
            }
            for index, item in enumerate(items)
        ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"items": drawn, "count": len(drawn)},
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_sketch_text(
        self,
        text: str,
        x: float = 0.0,
        y: float = 0.0,
        height: float | None = None,
        font: str | None = None,
        angle: float = 0.0,
        bold: bool = False,
        italic: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock writing text in the open sketch.

        Refuses without an open sketch, as the live adapter does, and reports
        what was asked as what was read back: a document font and height it
        doesn't know are None. An angled text gets its guide line's id
        (``TextLine_N``), as ``sketch_text.add_sketch_text`` registers it.

        Returns:
            AdapterResult[dict[str, Any]]: The text's ``id`` and its fields.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1
        guide = self._new_sketch_entity_id("TextLine") if angle else None
        formatted = height is not None or bool(font) or bold or italic
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "id": self._new_sketch_entity_id("Text"),
                "text": text,
                "position_mm": [float(x), float(y)],
                "uses_document_font": not formatted,
                "font": font,
                "height_mm": height,
                "angle_deg": float(angle),
                "bold": bool(bold),
                "italic": bool(italic),
                "along_line": guide,
                "format_applied": True if formatted else None,
                "shares_sketch_with": [],
            },
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def set_sketch_construction(
        self, entity_ids: list[str], construction: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        """Mock making sketch entities construction (or normal) geometry.

        Args:
            entity_ids (list[str]): Ids the ``add_*`` calls returned.
            construction (bool): True for construction geometry.

        Returns:
            AdapterResult[dict[str, Any]]: ``entity_ids``, ``segment_count``
            (four for a rectangle) and ``construction``.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        missing = [i for i in entity_ids if i not in self._sketch_entity_ids]
        if missing:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Sketch entity {', '.join(missing)} not found in the open sketch"
                ),
            )
        self._operation_count += 1
        segment_count = sum(4 if i.startswith("Rectangle") else 1 for i in entity_ids)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "entity_ids": list(entity_ids),
                "segment_count": segment_count,
                "construction": construction,
            },
            execution_time=0.0,
        )

    async def add_rectangle(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> AdapterResult[str]:
        """Mock adding a rectangle to sketch.

        Args:
            x1 (float): The x1 value.
            y1 (float): The y1 value.
            x2 (float): The x2 value.
            y2 (float): The y2 value.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"])
        self._operation_count += 1

        rect_id = f"Rectangle{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(rect_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=rect_id,
            execution_time=self._delays["sketch_operation"],
        )

    async def add_ellipse(
        self,
        center_x: float,
        center_y: float,
        major_axis: float,
        minor_axis: float,
    ) -> AdapterResult[str]:
        """Mock adding an axis-aligned ellipse to sketch.

        Args:
            center_x (float): Ellipse centre X in millimetres.
            center_y (float): Ellipse centre Y in millimetres.
            major_axis (float): Full major-axis length in millimetres.
            minor_axis (float): Full minor-axis length in millimetres.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        ellipse_id = f"Ellipse_{random.randint(1000, 9999)}"
        self._sketch_entity_ids.add(ellipse_id)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=ellipse_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_polygon(
        self, center_x: float, center_y: float, radius: float, sides: int
    ) -> AdapterResult[str]:
        """Mock adding a regular polygon to sketch.

        Args:
            center_x (float): Polygon centre X in millimetres.
            center_y (float): Polygon centre Y in millimetres.
            radius (float): Circumradius in millimetres (distance from centre
                to each vertex; matches the real adapter's
                ``CreatePolygon(..., Inscribed=True)`` semantics).
            sides (int): Number of polygon sides.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        # ID format mirrors the real PyWin32 adapter, which uses
        # ``_register_sketch_entity("Polygon", ...)`` (counter-based) so the
        # returned ID is a valid input to downstream sketch_linear_pattern /
        # sketch_circular_pattern / sketch_mirror / sketch_offset calls.
        polygon_id = f"Polygon_{len(self._sketch_entity_ids) + 1}"
        self._sketch_entity_ids.add(polygon_id)
        _ = sides  # informational only; the sides count is no longer part of the ID

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=polygon_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_linear_pattern(
        self,
        entities: list[str],
        direction_x: float,
        direction_y: float,
        spacing: float,
        count: int,
        add_dimensions: bool = True,
    ) -> AdapterResult[str]:
        """Mock creating a linear sketch pattern.

        Mirrors the real adapter's validation rules — empty entities,
        count < 2, non-positive spacing, and a zero direction vector each
        produce a clear error without "creating" a pattern.

        Args:
            entities (list[str]): Seed entity IDs.
            direction_x (float): Pattern direction X component.
            direction_y (float): Pattern direction Y component.
            spacing (float): Distance between instances in millimetres.
            count (int): Total number of instances (including the seed).
            add_dimensions (bool): Add the spacing and count dimensions.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_linear_pattern requires at least one entity",
            )
        if count < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_linear_pattern requires count >= 2",
            )
        if spacing <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_linear_pattern requires spacing > 0",
            )
        if math.hypot(direction_x, direction_y) < 1e-9:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=("sketch_linear_pattern requires a non-zero direction vector"),
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        pattern_id = f"LinearPattern_{count}x{spacing}_{random.randint(1000, 9999)}"
        dimensions = (
            self._mock_pattern_dimensions([("spacing", spacing), ("count", count)])
            if add_dimensions
            else []
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=pattern_id,
            execution_time=self._delays["sketch_operation"] / 2,
            metadata={"parametric": add_dimensions, "dimensions": dimensions},
        )

    def _mock_pattern_dimensions(
        self, roles: list[tuple[str, float]]
    ) -> list[dict[str, Any]]:
        """Name a sketch pattern's dimensions as SolidWorks would and track them."""
        units = {"count": "count", "angle": "deg"}
        sketch = self._current_sketch or "Sketch1"
        taken = sum(1 for name in self._dimensions if name.endswith(f"@{sketch}"))
        out: list[dict[str, Any]] = []
        for offset, (role, value) in enumerate(roles, start=1):
            name = f"D{taken + offset}@{sketch}"
            self._dimensions[name] = float(value)
            unit = units.get(role, "mm")
            kind = {"count": "count", "deg": "angle"}.get(unit, "length")
            out.append(
                {
                    "name": name,
                    "role": role,
                    "value": float(value),
                    "units": unit,
                    "kind": kind,
                }
            )
        return out

    async def sketch_circular_pattern(
        self,
        entities: list[str],
        angle: float,
        count: int,
        center_x: float = 0.0,
        center_y: float = 0.0,
        add_dimensions: bool = True,
    ) -> AdapterResult[str]:
        """Mock creating a circular sketch pattern about a centre.

        Args:
            entities (list[str]): Seed entity IDs.
            angle (float): Total swept angle in degrees.
            count (int): Total number of instances (including the seed).
            center_x (float): The centre's x, sketch mm.
            center_y (float): The centre's y, sketch mm.
            add_dimensions (bool): Add the angle and count dimensions (the
                mock doesn't know the seed's radius, so it has none).

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_circular_pattern requires at least one entity",
            )
        if count < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_circular_pattern requires count >= 2",
            )
        if angle <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_circular_pattern requires angle > 0",
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        pattern_id = f"CircularPattern_{count}x{angle}deg_{random.randint(1000, 9999)}"
        dimensions = (
            self._mock_pattern_dimensions([("angle", angle), ("count", count)])
            if add_dimensions
            else []
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=pattern_id,
            execution_time=self._delays["sketch_operation"] / 2,
            metadata={
                "parametric": add_dimensions,
                "dimensions": dimensions,
                "center": {"x": center_x, "y": center_y},
            },
        )

    async def sketch_mirror(
        self, entities: list[str], mirror_line: str
    ) -> AdapterResult[str]:
        """Mock mirroring sketch entities about a centerline.

        Args:
            entities (list[str]): IDs of segments to mirror.
            mirror_line (str): ID of the centerline to mirror across.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_mirror requires at least one entity",
            )
        if not mirror_line:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "sketch_mirror requires a mirror_line entity ID (add_centerline)"
                ),
            )
        if mirror_line not in self._sketch_entity_ids:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown mirror_line entity '{mirror_line}'. Use the "
                    "ID returned by add_centerline."
                ),
            )
        # Mirror the real adapter — IModelDoc2::SketchMirror needs a
        # centreline as the mirror axis; other segments silently no-op.
        if not mirror_line.startswith("Centerline_"):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"mirror_line must be a centerline (from add_centerline), "
                    f"got '{mirror_line}'"
                ),
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        mirror_id = f"Mirror_{mirror_line}_{random.randint(1000, 9999)}"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=mirror_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def sketch_offset(
        self,
        entities: list[str],
        offset_distance: float,
        reverse_direction: bool,
    ) -> AdapterResult[str]:
        """Mock offsetting sketch entities by a fixed distance.

        Args:
            entities (list[str]): IDs of segments to offset.
            offset_distance (float): Distance in millimetres. Must be > 0.
            reverse_direction (bool): Flip the offset direction.

        Returns:
            AdapterResult[str]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )
        if not entities:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="sketch_offset requires at least one entity",
            )
        if offset_distance <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "sketch_offset requires offset_distance > 0 — use "
                    "reverse_direction to flip the side"
                ),
            )
        for ent in entities:
            if ent not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        direction = "inward" if reverse_direction else "outward"
        offset_id = f"Offset_{offset_distance}_{direction}_{random.randint(1000, 9999)}"
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=offset_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def add_sketch_constraint(
        self,
        entity1: str,
        entity2: str | None,
        relation_type: str,
        entity3: str | None = None,
    ) -> AdapterResult[str]:
        """Mock adding a geometric constraint between sketch entities.

        Args:
            entity1 (str): Primary sketch-entity ID.
            entity2 (str | None): Secondary sketch-entity ID, or None for
                single-entity relations (horizontal, vertical, fix).
            relation_type (str): Constraint name (see RELATION_NAME_MAP).
            entity3 (str | None): Third entity ID — only used by the
                ``symmetric`` relation (the centerline of symmetry). All
                other relation types reject a non-null ``entity3``.

        Returns:
            AdapterResult[str]: SUCCESS with a placeholder constraint ID, or
                ERROR if no active sketch, the relation_type is unsupported,
                arity is wrong, or an entity ID is unknown.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active sketch"
            )

        rt_norm = (relation_type or "").strip().lower()
        if rt_norm not in RELATION_NAME_MAP:
            supported = ", ".join(sorted(RELATION_NAME_MAP))
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unsupported relation type '{relation_type}'. "
                    f"Supported: {supported}"
                ),
            )

        # Arity validation — mirror the real adapter
        if rt_norm == "symmetric":
            if entity2 is None or entity3 is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Relation '{relation_type}' requires entity1, "
                        "entity2, and entity3 (the centerline of symmetry)"
                    ),
                )
        elif entity3 is not None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Relation '{relation_type}' does not accept entity3 — "
                    "only 'symmetric' takes a third entity (the centerline)"
                ),
            )

        # Validate entity IDs against the in-process sketch-entity registry
        # so the mock surfaces the same "Unknown sketch entity" error as the
        # real adapter (which checks adapter._sketch_entities).
        for ent in (entity1, entity2, entity3):
            # "Origin" and "<id>.center" / ".start" / ".end" name points, as
            # the real adapter takes them (sketch_points.resolve).
            if ent is None or ent.strip().lower() == "origin":
                continue
            if (ent.rpartition(".")[0] or ent) not in self._sketch_entity_ids:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Unknown sketch entity '{ent}'. Use IDs returned by "
                        "add_line/add_arc/add_circle/add_spline/add_centerline."
                    ),
                )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._operation_count += 1

        constraint_id = f"Constraint_{relation_type}_{random.randint(1000, 9999)}"

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=constraint_id,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def exit_sketch(self) -> AdapterResult[None]:
        """Mock exiting sketch mode.

        Returns:
            AdapterResult[None]: The result produced by the operation.
        """
        if not self._current_sketch:
            return AdapterResult(
                status=AdapterResultStatus.WARNING, error="No active sketch to exit"
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 2)
        self._current_sketch = None

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=None,
            execution_time=self._delays["sketch_operation"] / 2,
        )

    async def check_sketch_fully_defined(
        self, sketch_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock check for sketch definition status.

        Args:
            sketch_name (str | None): Optional sketch name to inspect. Defaults to None.

        Returns:
            AdapterResult[dict[str, Any]]: Definition status payload.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="No active model",
            )

        await asyncio.sleep(self._delays["sketch_operation"] / 3)
        self._operation_count += 1

        resolved_name = sketch_name or self._current_sketch or "Sketch1"
        if sketch_name and sketch_name not in self._sketches:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Sketch not found: {sketch_name}",
            )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "sketch_name": resolved_name,
                "is_fully_defined": True,
                "definition_state": "fully_defined",
                "source": "mock",
                "raw_status": True,
            },
            execution_time=self._delays["sketch_operation"] / 3,
        )

    async def get_mass_properties(self) -> AdapterResult[MassProperties]:
        """Mock getting mass properties.

        Returns:
            AdapterResult[MassProperties]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        # Generate realistic mock data
        properties = MassProperties(
            volume=random.uniform(1000, 100000),  # mm³
            surface_area=random.uniform(1000, 50000),  # mm²
            mass=random.uniform(0.1, 50.0),  # kg (assuming typical density)
            center_of_mass=[
                random.uniform(-100, 100),
                random.uniform(-100, 100),
                random.uniform(-100, 100),
            ],
            moments_of_inertia={
                "Ixx": random.uniform(1e6, 1e9),
                "Iyy": random.uniform(1e6, 1e9),
                "Izz": random.uniform(1e6, 1e9),
                "Ixy": random.uniform(-1e8, 1e8),
                "Ixz": random.uniform(-1e8, 1e8),
                "Iyz": random.uniform(-1e8, 1e8),
            },
        )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=properties,
            execution_time=self._delays["feature_operation"],
        )

    async def export_image(self, payload: dict) -> AdapterResult[dict]:
        """Mock export of a PNG/JPG viewport screenshot.

        Reports the zoom like the real adapter: ``zoom_to_fit`` left out (or
        ``None``) zooms for every orientation but ``current``, which keeps the
        view as it is.

        Args:
            payload (dict): The payload value.

        Returns:
            AdapterResult[dict]: The result produced by the operation.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        import pathlib

        file_path = payload.get("file_path", "")
        width = int(payload.get("width", 1280))
        height = int(payload.get("height", 720))
        orientation = str(payload.get("view_orientation", "current")).lower()
        zoom = payload.get("zoom_to_fit")
        zoom_box = payload.get("zoom_box_mm")
        zoom_to_fit = (
            False
            if zoom_box is not None
            else (
                (orientation != "current" or payload.get("view_direction") is not None)
                if zoom is None
                else bool(zoom)
            )
        )
        hide = list(payload.get("hide") or [])
        unknown = _unknown_display_types(hide)
        if unknown:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Unknown display type(s) to hide: {', '.join(unknown)}",
            )
        # A section for this picture only, on a plane the mock knows; it is
        # taken off again at once, as the real adapter's finally does.
        section = payload.get("section") or None
        applied_section = None
        if section:
            plane = str(section.get("plane") or "")
            if self._mock_feature_kind(plane) != "RefPlane":
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        "Section plane not found: the model has no plane named "
                        f"{plane!r}, so no picture was taken. Use a plane's name "
                        "from list_features, such as Front Plane."
                    ),
                )
            applied_section = {
                "plane": plane,
                "offset_mm": float(section.get("offset_mm") or 0.0),
                "reverse": bool(section.get("reverse", False)),
                "removed": True,
            }
        # The view as the real adapter turns it: a direction to look from, or
        # a standard view turned Z-up (dimetric and trimetric are SolidWorks'
        # own angles, which the mock doesn't know).
        up_axis = str(payload.get("up_axis") or "y").lower()
        try:
            rows = _view_matrix.picture_rows(
                orientation, payload.get("view_direction"), payload.get("up"), up_axis
            )
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        if rows is None and up_axis == "z":
            standard = _view_matrix.standard_rows(orientation)
            rows = _view_matrix.z_up(standard) if standard else None
        # Components hidden for this picture only, shown again at once.
        hide_components = list(dict.fromkeys(payload.get("hide_components") or []))
        problem = (
            self._component_names_problem(hide_components, "hide_components")
            if hide_components
            else None
        )
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        hidden_components = [
            {
                "name": name,
                "before": self._component_visibility.get(name, True),
                "restored": True,
            }
            for name in hide_components
        ]

        await asyncio.sleep(0.3)
        self._operation_count += 1

        out = pathlib.Path(file_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Write a valid 1×1 white PNG binary so UI image tags don't break
        _PNG_1X1_WHITE = (
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            b"\x00\x00\x00\rIHDR"  # IHDR chunk length+type
            b"\x00\x00\x00\x01\x00\x00\x00\x01"  # 1x1
            b"\x08\x02\x00\x00\x00\x90wS\xde"  # 8-bit RGB + CRC
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff\xff\x00\x05\xfe\x02\xfe\xdc\xcaY\xe7"  # IDAT
            b"\x00\x00\x00\x00IEND\xaeB`\x82"  # IEND
        )
        # A .jpg or .bmp gets that format's bytes, as SolidWorks writes it.
        out.write_bytes(_mock_export_bytes(file_path) or _PNG_1X1_WHITE)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "file_path": file_path,
                "format": "PNG",
                "dimensions": f"{width}x{height}",
                "view": "custom" if payload.get("view_direction") else orientation,
                "up_axis": up_axis,
                "view_axes": _view_matrix.axes(rows) if rows else None,
                "zoomed_to_fit": zoom_to_fit,
                "zoom_box_mm": list(zoom_box) if zoom_box is not None else None,
                "hidden": sorted(hide),
                "hidden_components": hidden_components,
                "section": applied_section,
                "window_state": self._window_state,
            },
            execution_time=0.3,
            metadata={"mock": True, "orientation": orientation},
        )

    async def pack_and_go_assembly(
        self,
        source_path: str,
        target_dir: str,
    ) -> AdapterResult[dict]:
        """Mock Pack-and-Go: simulate copying an assembly to a target directory.

        Args:
            source_path (str): Path to the source .sldasm file.
            target_dir (str): Directory to copy files into.

        Returns:
            AdapterResult[dict]: Simulated pack-and-go result.
        """
        import pathlib

        await asyncio.sleep(0.05)
        self._operation_count += 1

        source = pathlib.Path(source_path)
        out_dir = pathlib.Path(target_dir)
        mock_parts = [f"MockPart{i}.SLDPRT" for i in range(1, 3)]
        source_files = [str(source)] + [str(source.parent / p) for p in mock_parts]
        copied_files = [str(out_dir / source.name)] + [
            str(out_dir / p) for p in mock_parts
        ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "source_assembly": str(source),
                "target_dir": str(out_dir),
                "copied_files": copied_files,
                "source_files": source_files,
                "save_statuses": [0] * (len(mock_parts) + 1),
                "all_files_saved": True,
            },
            execution_time=0.05,
            metadata={"mock": True},
        )

    async def export_stl_with_settings(
        self, file_path: str, settings: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock exporting the active document to STL with mesh settings.

        SolidWorks keeps the STL options as the user's preferences; they are
        changed for the export and put back afterwards.

        Args:
            file_path (str): Path to the ``.stl`` file.
            settings (dict[str, Any]): Any of ``resolution``, ``deviation_mm``,
                ``angle_deg``, ``units`` and ``binary``.

        Returns:
            AdapterResult[dict[str, Any]]: ``file_path``, ``mesh`` (the
            settings ``applied``, and whether the user's were ``restored``),
            and the export's ``units`` and ``document_type``.
        """
        try:
            changes = _stl_changes(settings)
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        exported = await self.export_file(file_path, "stl")
        if not exported.is_success:
            return cast(AdapterResult[dict[str, Any]], exported)
        data: dict[str, Any] = dict(exported.data or {})
        own = data.pop("mesh", {"applied": []})
        data["units"] = settings.get("units", data.get("units"))
        data["mesh"] = {
            "applied": [label for *_rest, label in changes] + own["applied"],
            "restored": True,
        }
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=data,
            execution_time=exported.execution_time,
        )

    async def export_file(self, file_path: str, format_type: str) -> AdapterResult[Any]:
        """Mock exporting a file.

        An STL export writes a closed tetrahedron (10 mm legs) and reports
        what the real adapter does: ``file_path``, ``units`` (mm),
        ``document_type`` and, for an assembly, the one-file option in
        ``mesh``.

        Args:
            file_path (str): Path to the target file.
            format_type (str): The format type value.

        Returns:
            AdapterResult[Any]: The STL details for STL, else ``None`` data.
        """
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        # Simulate export time based on format
        export_times = {
            "step": 2.0,
            "iges": 1.5,
            "stl": 1.0,
            "pdf": 0.5,
            "jpg": 0.3,
            "glb": 1.2,
            "gltf": 1.2,
        }

        format_lower = format_type.lower()
        delay = export_times.get(format_lower, 1.0)

        await asyncio.sleep(delay)
        self._operation_count += 1

        data: dict[str, Any] | None = None
        if format_lower == "stl":
            import pathlib

            out = pathlib.Path(file_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(_MOCK_STL, encoding="utf-8")
            document_type = self._current_model.type.lower()
            data = {
                "file_path": file_path,
                "units": "mm",
                "document_type": document_type,
            }
            if document_type == "assembly":
                data["mesh"] = {"applied": [_STL_ONE_FILE[3]], "restored": True}
        elif (written := _mock_export_bytes(file_path)) is not None:
            # The smallest file of its format, so the export tools' read-back
            # check (utils/export_check) sees what a real export leaves.
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            with open(file_path, "wb") as handle:
                handle.write(written)

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=data,
            execution_time=delay,
            metadata={"exported_format": format_type, "file_path": file_path},
        )

    async def get_dimension(self, name: str) -> AdapterResult[dict[str, Any]]:
        """Mock getting a dimension value (every mock dimension is a length).

        Args:
            name (str): The name value.

        Returns:
            AdapterResult[dict[str, Any]]: ``value``, ``units`` and ``kind``.
        """
        await asyncio.sleep(0.05)  # Very fast operation
        self._operation_count += 1

        if name in self._dimensions:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data={"value": self._dimensions[name], "units": "mm", "kind": "length"},
                execution_time=0.05,
            )
        else:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Dimension '{name}' not found"
            )

    async def set_dimension(
        self, name: str, value: float
    ) -> AdapterResult[dict[str, Any]]:
        """Mock setting a dimension value (every mock dimension is a length).

        Args:
            name (str): The name value.
            value (float): The value value.

        Returns:
            AdapterResult[dict[str, Any]]: ``old_value``, ``new_value``,
            ``units`` and ``kind``.
        """
        await asyncio.sleep(0.1)  # Fast operation
        self._operation_count += 1

        equation = self._mock_driven().get(name.casefold())
        if equation is not None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=equation_refusal(name, equation),
            )
        old_value = self._dimensions.get(name)
        self._dimensions[name] = value

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "old_value": old_value,
                "new_value": value,
                "units": "mm",
                "kind": "length",
            },
            execution_time=0.1,
        )

    async def set_units(self, unit_system: str) -> AdapterResult[dict[str, Any]]:
        """Mock setting the active document's linear unit system.

        Mirrors the real adapter: an unrecognised token is an error, and the
        applied token is recorded on the current model's ``properties``.

        Args:
            unit_system (str): One of ``mm``, ``cm``, ``m``, ``in``, ``ft``
                (aliases such as ``inch`` / ``millimeters`` accepted).

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(0.1)

        token = _normalise_mock_unit_system(unit_system)
        if token is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unrecognised unit system {unit_system!r}. "
                    "Expected one of: mm, cm, m, in, ft."
                ),
            )
        if self._current_model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        self._operation_count += 1
        props = dict(self._current_model.properties or {})
        props["units"] = token
        self._current_model.properties = props

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"unit_system": token, "verified": True},
            execution_time=0.1,
        )

    async def list_open_documents(self) -> AdapterResult[list[dict[str, Any]]]:
        """Mock enumerating the currently open documents.

        Returns every model the mock has created or opened this session, with
        ``is_active`` reflecting ``self._current_model``.

        Returns:
            AdapterResult[list[dict[str, Any]]]: The result produced by the
            operation.
        """
        await asyncio.sleep(0.05)
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )

        active_path = (
            self._current_model.path if self._current_model is not None else None
        )
        docs = [
            {
                "title": model.name,
                "path": model.path,
                "type": model.type,
                "is_active": model.path == active_path,
            }
            for model in self._models.values()
        ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=docs, execution_time=0.05
        )

    async def activate_document(
        self, title_or_path: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock making an already-open document active.

        Args:
            title_or_path (str): Title, full path, or file name of an open
                document.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(0.05)
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        target = (title_or_path or "").strip()
        if not target:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="title_or_path is required"
            )

        needle = target.lower()
        match = None
        for model in self._models.values():
            names = {
                model.name.lower(),
                model.path.lower(),
                model.path.rsplit("/", 1)[-1].lower(),
            }
            if needle in names:
                match = model
                break
        if match is None:
            open_titles = ", ".join(m.name for m in self._models.values()) or "none"
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"No open document matches {target!r}. Open: {open_titles}",
            )

        self._current_model = match
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"activated": match.name, "verified": True},
            execution_time=0.05,
        )

    async def insert_component(
        self,
        file_path: str,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        fixed: bool | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock inserting a component into the active assembly.

        Records the component in ``self._components`` so repeated inserts
        accumulate and ``list_components`` reflects them. As in SolidWorks,
        the first component is fixed unless ``fixed`` says otherwise.

        Args:
            file_path (str): Path to the part or sub-assembly to insert.
            x (float): X position in millimetres. Defaults to 0.0.
            y (float): Y position in millimetres. Defaults to 0.0.
            z (float): Z position in millimetres. Defaults to 0.0.
            fixed (bool | None): Fix or float it; None leaves SolidWorks'
                choice.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        self._components.append(f"component-{len(self._components) + 1}")
        name = self._components[-1]
        self._component_files[name] = file_path
        first = len(self._components) == 1
        is_fixed = first if fixed is None else bool(fixed)
        self._component_states[name] = {"fixed": is_fixed, "flexible": False}
        self._component_transforms[name] = {
            "origin_mm": [float(x), float(y), float(z)],
            "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        }

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "component": name,
                "file_path": file_path,
                "position": {"x": x, "y": y, "z": z},
                "placement": {
                    "placed": True,
                    "moved": True,
                    "fixed": is_fixed,
                    **self._component_transforms[name],
                },
                "components_before": len(self._components) - 1,
                "components_after": len(self._components),
            },
            execution_time=self._delays["model_operation"],
        )

    async def list_components(self) -> AdapterResult[list[str]]:
        """Mock listing the top-level components of the active assembly.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=list(self._components),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def get_model_geometry_summary(self) -> AdapterResult[dict[str, Any]]:
        """Mock summary of the active part or assembly.

        The mock has no geometry, so volume, bounding box and body count are
        ``None`` ("not determinable") rather than invented numbers a caller
        could mistake for a measurement. Component and mate counts are real:
        they come from ``insert_component`` and ``add_mate`` this session.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        guard = self._mock_doc_error({"Part", "Assembly"}, "get_model_geometry_summary")
        if guard is not None:
            return guard
        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        is_assembly = self._current_model.type == "Assembly"
        summary: dict[str, Any] = {
            "document_type": "assembly" if is_assembly else "part",
            "volume_mm3": None,
            "bounding_box_mm": None,
            "bounding_box_method": None,
            "solid_body_count": None,
        }
        if is_assembly:
            summary["component_count"] = len(self._components)
            summary["mate_count"] = len(self._mates)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=summary,
            execution_time=self._delays["model_operation"] / 2,
        )

    # Drawing Operations
    def _mock_place_view(self, payload: Any) -> AdapterResult[dict[str, Any]]:
        """Record a drawing view so ``list_drawing_views`` reflects it.

        Args:
            payload (Any): Tool payload, as the real adapter receives it.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        data = payload if isinstance(payload, dict) else {}
        model_path = data.get("model_path") or data.get("model_file")
        if not model_path:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="A model path is required (model_path or model_file)",
            )

        orientation = str(data.get("orientation") or data.get("view_type") or "front")
        if orientation.strip().lower() not in _MOCK_NAMED_VIEWS:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown orientation '{orientation}'. Use one of: "
                    f"{', '.join(sorted(_MOCK_NAMED_VIEWS))}."
                ),
            )

        before = len(self._drawing_views)
        self._drawing_views.append(f"Drawing View{before + 1}")
        self._operation_count += 1

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": self._drawing_views[-1],
                "model_path": model_path,
                "orientation": orientation,
                "position": {
                    "x": float(data.get("position_x", 100.0)),
                    "y": float(data.get("position_y", 150.0)),
                },
                "views_before": before,
                "views_after": len(self._drawing_views),
            },
            execution_time=self._delays["model_operation"],
        )

    async def create_drawing_view(
        self, payload: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock placing a view of a model on the active drawing sheet.

        Args:
            payload (Any): Tool payload carrying the model path and orientation.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        return self._mock_place_view(payload)

    async def add_drawing_view(
        self, payload: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock adding a view of a model to the active drawing sheet.

        Args:
            payload (Any): Tool payload carrying the model path and orientation.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        return self._mock_place_view(payload)

    async def create_technical_drawing(
        self, payload: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock laying out the three standard views of a model.

        Args:
            payload (Any): Tool payload carrying the model path and projection.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        data = payload if isinstance(payload, dict) else {}
        model_path = data.get("model_path") or data.get("model_file")
        if not model_path:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="A model path is required (model_path or model_file)",
            )

        third_angle = bool(data.get("third_angle", True))
        if str(data.get("projection", "")).lower() in {"first", "first_angle"}:
            third_angle = False

        before = len(self._drawing_views)
        added = [f"Drawing View{before + offset}" for offset in (1, 2, 3)]
        self._drawing_views.extend(added)
        self._operation_count += 1

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "views": added,
                "model_path": model_path,
                "projection": "third_angle" if third_angle else "first_angle",
                "views_before": before,
                "views_after": len(self._drawing_views),
            },
            execution_time=self._delays["model_operation"],
        )

    async def add_note(self, payload: Any = None) -> AdapterResult[dict[str, Any]]:
        """Mock placing a text note on the active drawing sheet.

        Args:
            payload (Any): Tool payload carrying the text and position.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        data = payload if isinstance(payload, dict) else {}
        text = data.get("text")
        if not text:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="add_note requires text"
            )

        self._operation_count += 1
        font_points = float(data.get("font_size") or 0.0)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "text": text,
                "position": {
                    "x": float(data.get("position_x", 100.0)),
                    "y": float(data.get("position_y", 50.0)),
                },
                # The real adapter sets this from IAnnotation::SetPosition's
                # return. Mock mode has no sheet to place anything on, so it
                # reports "not determinable" rather than claiming success at a
                # check it never performed.
                "positioned": None,
                "font_size_points": font_points or None,
            },
            execution_time=self._delays["model_operation"],
        )

    async def list_drawing_views(self) -> AdapterResult[list[str]]:
        """Mock listing the views on the active drawing.

        Returns:
            AdapterResult[list[str]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"] / 2)
        self._operation_count += 1

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=list(self._drawing_views),
            execution_time=self._delays["model_operation"] / 2,
        )

    async def auto_center_marks(
        self,
        view_name: str,
        mark_holes: bool = True,
        mark_fillets: bool = False,
        mark_slots: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock auto-inserting centre marks on a drawing view.

        The mock has no geometry, so it cannot know how many circular
        features a view holds - it mirrors the live adapter's refusals (not
        a drawing, no feature type selected, unknown view) and, on a valid
        request, records a nominal centre-mark increase per view so
        before/after deltas stay monotonic across calls.

        Args:
            view_name (str): Name of a view on the active drawing.
            mark_holes (bool): Mark holes / bores.
            mark_fillets (bool): Mark fillets.
            mark_slots (bool): Mark slots.

        Returns:
            AdapterResult[dict[str, Any]]: The view, the feature types acted
            on, and before/after centre-mark counts, or an error.
        """
        await asyncio.sleep(self._delays["model_operation"] / 2)

        model = self._current_model
        if model is None or getattr(model, "type", None) != "Drawing":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="auto_center_marks requires an active drawing document",
            )
        if not (mark_holes or mark_fillets or mark_slots):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "Select at least one feature type: mark_holes, "
                    "mark_fillets or mark_slots."
                ),
            )
        if view_name not in self._drawing_views:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"No view named {view_name!r} on the active drawing. "
                    f"Views: {', '.join(self._drawing_views) or 'none'}"
                ),
            )

        self._operation_count += 1
        before = self._center_marks.get(view_name, 0)
        # Nominal: two marks per selected feature type, added once.
        added = 0 if before else 2 * sum((mark_holes, mark_fillets, mark_slots))
        after = before + added
        self._center_marks[view_name] = after

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "view": view_name,
                "feature_types": [
                    label
                    for label, on in (
                        ("holes", mark_holes),
                        ("fillets", mark_fillets),
                        ("slots", mark_slots),
                    )
                    if on
                ],
                "center_marks_before": before,
                "center_marks_after": after,
                "center_marks_added": added,
            },
            execution_time=self._delays["model_operation"] / 2,
        )

    async def save_body_as_part(
        self, body_name: str, file_path: str, assembly_path: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock extracting a solid body from the active part to a new file.

        The mock has no bodies, so it cannot validate ``body_name`` against
        real geometry - it mirrors the live adapter's other refusals (no
        model, not a part, blank arguments, missing parent directory) and, on
        a valid request, writes an empty placeholder file so callers and
        tests can observe the side effect.

        Args:
            body_name (str): Name of a solid body in the active part.
            file_path (str): Absolute path for the new ``.sldprt``.
            assembly_path (str | None): The assembly asked for; recorded.

        Returns:
            AdapterResult[dict[str, Any]]: The body, the written path, and a
            single-element body list, or an error.
        """
        import os as _os

        await asyncio.sleep(self._delays["model_operation"])

        model = self._current_model
        if model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if getattr(model, "type", None) != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="save_body_as_part requires an active part document",
            )
        if not str(body_name or "").strip():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="body_name is required"
            )
        target = str(file_path or "").strip()
        if not target:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="file_path is required"
            )
        target = _os.path.abspath(target)
        parent = _os.path.dirname(target)
        if not _os.path.isdir(parent):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Parent directory does not exist: {parent}",
            )

        replaced = _os.path.exists(target)
        with open(target, "w", encoding="utf-8"):
            pass
        self._operation_count += 1
        feature = f"Save Bodies{len(self._save_bodies) + 1}"
        self._save_bodies.append(
            {
                "feature": feature,
                "bodies": [{"body": body_name, "file_path": target}],
                "assembly_path": assembly_path or "",
                "consume_body": False,
                "copy_custom_properties": True,
            }
        )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "body": body_name,
                "file_path": target,
                "feature": feature,
                "solid_bodies": [body_name],
                "replaced": replaced,
                "assembly_path": assembly_path,
                "file_check": {
                    "file_path": target,
                    "exists": True,
                    "size_bytes": 0,
                    "written_now": True,
                    "volume_mm3": None,
                    "body_volume_mm3": None,
                    "holds_body": None,
                },
                "closed_documents": [],
            },
            execution_time=self._delays["model_operation"],
        )

    def _mock_save_bodies(
        self, feature_name: str | None
    ) -> list[dict[str, Any]] | AdapterResult[dict[str, Any]]:
        """The mock part's Save Bodies features (or one), or the error."""
        model = self._current_model
        if model is None or getattr(model, "type", None) != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Save Bodies requires an active part document",
            )
        found = [
            f
            for f in self._save_bodies
            if feature_name is None or f["feature"] == feature_name
        ]
        if not found:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Feature {feature_name!r} not found; list_save_bodies names them."
                    if feature_name
                    else "No Save Bodies feature found in the active part; "
                    "save_body_as_part makes one."
                ),
            )
        return found

    @staticmethod
    def _mock_definition(feature: dict[str, Any]) -> dict[str, Any]:
        import os as _os

        return {
            **feature,
            "type": "SaveBodies",
            "bodies": [
                {
                    **b,
                    "consumed": False,
                    "exists": _os.path.isfile(b["file_path"]),
                    "lock_file": None,
                }
                for b in feature["bodies"]
            ],
            "selections_accessed": False,
            "settable": [
                "files",
                "assembly_path",
                "consume_body",
                "copy_custom_properties",
            ],
        }

    async def get_save_bodies(
        self, feature_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock reading the Save Bodies features save_body_as_part added."""
        found = self._mock_save_bodies(feature_name)
        if isinstance(found, AdapterResult):
            return found
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"features": [self._mock_definition(f) for f in found]},
            execution_time=0.05,
        )

    async def edit_save_bodies(
        self, feature_name: str, changes: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock changing a Save Bodies feature's options; files are refused."""
        from .solidworks.save_bodies import FILES_REFUSED

        found = self._mock_save_bodies(feature_name)
        if isinstance(found, AdapterResult):
            return found
        (feature,) = found
        if "files" in changes:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=FILES_REFUSED)
        for key in ("assembly_path", "consume_body", "copy_custom_properties"):
            if key in changes:
                feature[key] = changes[key]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": feature["feature"],
                "changed": sorted(changes),
                "definition": self._mock_definition(feature),
                "closed_documents": [],
            },
            execution_time=0.05,
        )

    async def resplit_bodies(
        self, feature_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock updating the files the Save Bodies features write."""
        import os as _os

        found = self._mock_save_bodies(feature_name)
        if isinstance(found, AdapterResult):
            return found
        files = [
            {
                "feature": f["feature"],
                "body": b["body"],
                "file_path": b["file_path"],
                "status": "updated" if _os.path.isfile(b["file_path"]) else "missing",
            }
            for f in found
            for b in f["bodies"]
        ]
        counts: dict[str, int] = {}
        for item in files:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"files": files, "counts": counts, "closed_documents": []},
            execution_time=0.05,
        )

    async def list_loaded_documents(self) -> AdapterResult[dict[str, Any]]:
        """Mock listing every loaded document: the open ones and the hidden ones."""
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        active = self._current_model.path if self._current_model else None
        documents = [
            {
                "title": m.name,
                "path": m.path,
                "type": m.type,
                "is_active": m.path == active,
                "visible": path not in self._hidden_documents,
                "modified": False,
                "used_by": [],
                "leftover": False,
                "lock_file": None,
            }
            for path, m in self._models.items()
        ]
        for path, hidden in self._hidden_documents.items():
            if path in self._models:
                continue
            used_by = list(hidden.get("used_by") or [])
            documents.append(
                {
                    "title": hidden["title"],
                    "path": path,
                    "type": "Part",
                    "is_active": False,
                    "visible": False,
                    "modified": bool(hidden.get("modified")),
                    "used_by": used_by,
                    "leftover": not used_by,
                    "lock_file": None,
                }
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "documents": documents,
                "leftovers": [d["title"] for d in documents if d["leftover"]],
                "count": len(documents),
            },
            execution_time=0.05,
        )

    async def close_leftover_documents(self) -> AdapterResult[dict[str, Any]]:
        """Mock closing the hidden documents nothing uses; modified ones stay."""
        if not self._connected:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        closed, kept = [], []
        for path, hidden in list(self._hidden_documents.items()):
            if path in self._models or hidden.get("used_by"):
                continue
            entry = {"title": hidden["title"], "path": path}
            if hidden.get("modified"):
                kept.append(entry)
                continue
            del self._hidden_documents[path]
            closed.append({**entry, "lock_file": None})
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"closed": closed, "still_loaded": [], "kept_modified": kept},
            execution_time=0.05,
        )

    async def list_assembly_parts(self) -> AdapterResult[dict[str, Any]]:
        """Mock listing the active assembly's parts, once per file.

        Each part is registered as a document the assembly loaded (no window
        of its own), so ``activate_document`` can reach it by path.
        """
        model = self._current_model
        if model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if getattr(model, "type", None) != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="list_assembly_parts requires an active assembly document",
            )
        parts: dict[str, dict[str, Any]] = {}
        for name in self._components:
            path = self._component_files.get(name, "")
            if not path.lower().endswith(".sldprt"):
                continue
            entry = parts.setdefault(
                path,
                {
                    "path": path,
                    "title": path.replace("\\", "/").rsplit("/", 1)[-1],
                    "configuration": "Default",
                    "active_configuration": "Default",
                    "instances": 0,
                    "components": [],
                    "virtual": False,
                },
            )
            entry["instances"] += 1
            entry["components"].append(name)
            if path not in self._models:
                self._models[path] = SolidWorksModel(
                    path=path,
                    name=entry["title"],
                    type="Part",
                    is_active=False,
                    configuration="Default",
                    properties={},
                )
                self._hidden_documents.setdefault(
                    path, {"title": entry["title"], "used_by": [model.name]}
                )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "assembly": {"title": model.name, "path": model.path},
                "parts": list(parts.values()),
            },
            execution_time=0.05,
        )

    # ---- Multibody parts (list_bodies and the body tools) ----------------------
    #
    # The mock has no geometry engine: each solid body is its box (mm), with
    # its own volume, so a subtract can lower the volume while the box stays.

    def _bodies_refusal(self, operation: str) -> AdapterResult[dict[str, Any]] | None:
        """The real adapter's refusals: no model, or not a part."""
        from .solidworks.bodies import not_a_part_message

        model = self._current_model
        if model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if model.type != "Part":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=not_a_part_message(operation, model.type.lower()),
            )
        return None

    @staticmethod
    def _mock_body(box: list[float], volume: float | None = None) -> dict[str, Any]:
        """One mock body: its box (mm), volume (mm3), face count, visibility."""
        from .solidworks.bodies import box_volume

        return {
            "box": [float(v) for v in box],
            "volume": box_volume(box) if volume is None else float(volume),
            "faces": 6,
            "visible": True,
        }

    def _mock_bodies(self) -> dict[str, dict[str, Any]]:
        """The active part's bodies; a part first seen gets ``part_bodies_mm``."""
        key = self._current_model.path if self._current_model else ""
        if key not in self._part_bodies:
            self._part_bodies[key] = {
                name: self._mock_body(box) for name, box in self.part_bodies_mm.items()
            }
        return self._part_bodies[key]

    @staticmethod
    def _mock_facts(name: str, body: dict[str, Any]) -> dict[str, Any]:
        """``list_bodies``' facts for a mock body, in the real reply's shape."""
        from .solidworks.bodies import box_area, box_center

        box = body["box"]
        return {
            "name": name,
            "volume_mm3": round(body["volume"], 4) + 0.0,
            "surface_area_mm2": round(box_area(box), 4) + 0.0,
            "center_of_mass_mm": [round(v, 4) + 0.0 for v in box_center(box)],
            "box_mm": [round(v, 4) + 0.0 for v in box],
            "size_mm": [round(box[k + 3] - box[k], 4) + 0.0 for k in range(3)],
            "face_count": body["faces"],
            "visible": body["visible"],
        }

    def _mock_missing(self, names: list[str]) -> AdapterResult[dict[str, Any]] | None:
        """The not-found error for bodies the part doesn't hold."""
        from .solidworks.bodies import missing_bodies_message

        bodies = self._mock_bodies()
        missing = [n for n in names if n not in bodies]
        if missing:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=missing_bodies_message(missing, list(bodies)),
            )
        return None

    def _mock_feature(self, stem: str) -> str:
        """The next feature name of a kind, e.g. ``Combine2``."""
        self._body_features[stem] = self._body_features.get(stem, 0) + 1
        self._feature_tree_count += 1
        return f"{stem}{self._body_features[stem]}"

    @staticmethod
    def _mock_invalid(message: str) -> AdapterResult[dict[str, Any]]:
        from .solidworks.clearance import InvalidRequest

        return AdapterResult(
            status=AdapterResultStatus.ERROR, error=str(InvalidRequest(message))
        )

    @staticmethod
    def _mock_moved(
        box: list[float],
        translate_mm: list[float] | None,
        rotate_deg: float,
        axis: str,
        rotate_about_mm: list[float] | None,
    ) -> list[float]:
        """A box moved by ``translate_mm``, then turned about ``axis``."""
        from .solidworks.bodies import rotate_box, translate_box

        moved = translate_box(box, translate_mm) if translate_mm else list(box)
        if rotate_deg:
            moved = rotate_box(moved, axis, rotate_deg, rotate_about_mm or [0, 0, 0])
        return [round(v, 9) + 0.0 for v in moved]

    async def list_bodies(
        self, visible_only: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock listing the active part's solid bodies (each a box).

        Args:
            visible_only (bool): Only the visible bodies.

        Returns:
            AdapterResult[dict[str, Any]]: ``bodies`` and ``count``.
        """
        refused = self._bodies_refusal("list_bodies")
        if refused is not None:
            return refused
        await asyncio.sleep(self._delays["model_operation"])
        bodies = [
            self._mock_facts(name, body)
            for name, body in self._mock_bodies().items()
            if body["visible"] or not visible_only
        ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"bodies": bodies, "count": len(bodies)},
            execution_time=self._delays["model_operation"],
        )

    async def insert_part(
        self,
        file_path: str,
        configuration: str | None = None,
        translate_mm: list[float] | None = None,
        rotate_deg: float = 0.0,
        axis: str = "z",
        rotate_about_mm: list[float] | None = None,
        break_link: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock inserting a part's bodies into the active part.

        The file must exist, as for the real adapter. Its bodies are
        ``inserted_part_bodies_mm[<file stem>]`` (name to box, mm), or one
        10 mm cube named ``<stem>-1``, moved as asked.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        import os as _os

        refused = self._bodies_refusal("insert_part")
        if refused is not None:
            return refused
        path = _os.path.abspath(str(file_path))
        if not path.lower().endswith(".sldprt"):
            return self._mock_invalid(f"{path} is not a part file (.sldprt).")
        if not _os.path.isfile(path):
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Part file not found: {path}"
            )
        await asyncio.sleep(self._delays["feature_operation"])
        stem = _os.path.splitext(_os.path.basename(path))[0]
        bodies = self._mock_bodies()
        given = self.inserted_part_bodies_mm.get(stem) or {
            f"{stem}-1": [0.0, 0.0, 0.0, 10.0, 10.0, 10.0]
        }
        added = []
        for name, box in given.items():
            unique, count = name, 1
            while unique in bodies:
                count += 1
                unique = f"{name}[{count}]"
            bodies[unique] = self._mock_body(
                self._mock_moved(
                    list(box), translate_mm, rotate_deg, axis, rotate_about_mm
                )
            )
            added.append(unique)
        feature = self._mock_feature(f"{stem}-")
        moves = (
            [self._mock_feature("Body-Move/Copy")]
            if (translate_mm and any(translate_mm)) or rotate_deg
            else []
        )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": feature,
                "file_path": path,
                "configuration": configuration,
                "bodies_added": added,
                "bodies": [self._mock_facts(n, bodies[n]) for n in added],
                "move_features": moves,
            },
            execution_time=self._delays["feature_operation"],
        )

    async def combine_bodies(
        self, operation: str, main: str, tools: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock combining bodies, each a box.

        An add's box is the box around them all; a subtract takes the boxes'
        overlap off the main body's volume; a common keeps the box they all
        share, refused when there is none.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.bodies import box_overlap, overlap_volume, union_box

        refused = self._bodies_refusal("combine_bodies")
        if refused is not None:
            return refused
        if main in tools or len(set(tools)) != len(tools):
            return self._mock_invalid(
                "give the main body once and each tool once, the main body not "
                "among the tools."
            )
        refused = self._mock_missing([main, *tools])
        if refused is not None:
            return refused
        await asyncio.sleep(self._delays["feature_operation"])
        bodies = self._mock_bodies()
        before = list(bodies)
        target = bodies[main]
        volume_before = target["volume"]
        if operation == "common":
            shared: list[float] | None = target["box"]
            for tool in tools:
                shared = box_overlap(shared, bodies[tool]["box"]) if shared else None
            if shared is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"SolidWorks made no Combine (common) feature from {main} "
                        f"and {', '.join(tools)}: the bodies don't overlap."
                    ),
                )
            bodies[main] = self._mock_body(shared)
        else:
            box, volume = list(target["box"]), target["volume"]
            for tool in tools:
                other = bodies[tool]
                shared_volume = min(
                    overlap_volume(target["box"], other["box"]), other["volume"]
                )
                if operation == "add":
                    box = union_box(box, other["box"])
                    volume += other["volume"] - shared_volume
                else:
                    volume -= shared_volume
            bodies[main] = self._mock_body(box, max(0.0, volume))
            bodies[main]["faces"] = target["faces"] + 6 * len(tools)
        for tool in tools:
            del bodies[tool]
        mark = 1 if operation == "subtract" else 2
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": self._mock_feature("Combine"),
                "operation": operation,
                "main": main,
                "tools": list(tools),
                "form": f"preselected (main body at mark {mark})",
                "bodies_before": before,
                "bodies_after": list(bodies),
                "result": [self._mock_facts(main, bodies[main])],
                "main_volume_before_mm3": round(volume_before, 4) + 0.0,
                "result_volume_mm3": round(bodies[main]["volume"], 4) + 0.0,
            },
            execution_time=self._delays["feature_operation"],
        )

    async def move_copy_body(
        self,
        bodies: list[str],
        translate_mm: list[float] | None = None,
        rotate_deg: float = 0.0,
        axis: str = "z",
        rotate_about_mm: list[float] | None = None,
        copy: bool = False,
        copies: int = 1,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock moving or copying bodies: their boxes move.

        Copy ``k`` of a body is moved ``k`` times the translation (or turned
        ``k`` times the angle, with no translation), as SolidWorks spaces
        copies.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.bodies import box_center

        refused = self._bodies_refusal("move_copy_body")
        if refused is not None:
            return refused
        moving = bool(translate_mm and any(translate_mm))
        if not moving and not rotate_deg:
            return self._mock_invalid(
                "give translate_mm or rotate_deg; nothing would move."
            )
        refused = self._mock_missing(list(bodies))
        if refused is not None:
            return refused
        await asyncio.sleep(self._delays["feature_operation"])
        store = self._mock_bodies()
        features = [self._mock_feature("Body-Move/Copy")]
        if moving and rotate_deg:
            features.append(self._mock_feature("Body-Move/Copy"))
        new, moved = [], []
        for name in bodies:
            source = store[name]
            if copy:
                for k in range(1, copies + 1):
                    shift = [k * float(v) for v in translate_mm] if moving else None
                    turn = rotate_deg if moving else k * rotate_deg
                    copy_name = f"{features[0]}[{len(new) + 1}]"
                    store[copy_name] = self._mock_body(
                        self._mock_moved(
                            source["box"], shift, turn, axis, rotate_about_mm
                        ),
                        source["volume"],
                    )
                    new.append(copy_name)
                continue
            was = box_center(source["box"])
            source["box"] = self._mock_moved(
                source["box"],
                translate_mm if moving else None,
                rotate_deg,
                axis,
                rotate_about_mm,
            )
            now = box_center(source["box"])
            moved.append(
                {
                    "name": name,
                    "name_now": name,
                    "center_before_mm": [round(v, 4) + 0.0 for v in was],
                    "center_after_mm": [round(v, 4) + 0.0 for v in now],
                    "center_shift_mm": [
                        round(now[k] - was[k], 4) + 0.0 for k in range(3)
                    ],
                }
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "features": features,
                "copy": bool(copy),
                "bodies": list(bodies),
                "new_bodies": [self._mock_facts(n, store[n]) for n in new],
                "moved": moved,
                "bodies_after": list(store),
            },
            execution_time=self._delays["feature_operation"],
        )

    async def delete_keep_body(
        self, bodies: list[str], keep: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a Body-Delete/Keep feature.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        refused = self._bodies_refusal("delete_keep_body")
        if refused is not None:
            return refused
        refused = self._mock_missing(list(bodies))
        if refused is not None:
            return refused
        store = self._mock_bodies()
        before = list(store)
        if keep and set(bodies) >= set(before):
            return self._mock_invalid(
                "keeping every solid body deletes nothing; name the bodies to keep."
            )
        await asyncio.sleep(self._delays["feature_operation"])
        removed = [n for n in before if (n not in bodies) == keep]
        for name in removed:
            del store[name]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": self._mock_feature("Body-Delete/Keep"),
                "keep": bool(keep),
                "bodies": list(bodies),
                "removed": removed,
                "expected_removed": list(removed),
                "bodies_after": list(store),
            },
            execution_time=self._delays["feature_operation"],
        )

    async def split_body(
        self,
        plane: str | None = None,
        sketch: str | None = None,
        body: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a Split feature: each box cut in two.

        A default plane cuts at 0 across its normal (Front: z, Top: y,
        Right: x); a plane made with ``create_reference_plane`` or a sketch
        cuts each box in half across its longest side.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.bodies import AXES, box_volume, split_box
        from .solidworks.sketch import plane_candidates_for

        refused = self._bodies_refusal("split_body")
        if refused is not None:
            return refused
        normals = {"front": AXES["z"], "top": AXES["y"], "right": AXES["x"]}
        key, candidates = plane_candidates_for(plane) if plane else (None, [])
        if plane and key is None and plane not in self._reference_planes:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Plane not found: {plane}"
            )
        if sketch and sketch not in self._sketches:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Sketch not found: {sketch}"
            )
        if body:
            refused = self._mock_missing([body])
            if refused is not None:
                return refused
        store = self._mock_bodies()
        before = list(store)
        tool = candidates[0] if key else (plane or sketch or "")
        await asyncio.sleep(self._delays["feature_operation"])
        cuts = {}
        for name in [body] if body else before:
            box = store[name]["box"]
            if key is not None:
                axis = normals[key]
                at = 0.0
            else:
                sizes = [box[k + 3] - box[k] for k in range(3)]
                axis = sizes.index(max(sizes))
                at = (box[axis] + box[axis + 3]) / 2.0
            halves = split_box(box, axis, at)
            if halves is not None:
                cuts[name] = halves
        if not cuts:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"{tool} splits no body (PreSplitBody2 gave none); it must cut "
                    "through a solid body."
                ),
            )
        feature = self._mock_feature("Split")
        pieces = []
        for name, halves in cuts.items():
            whole = store.pop(name)
            # A subtracted body keeps its box, so share its own volume out.
            total = sum(box_volume(h) for h in halves)
            for half in halves:
                piece = f"{feature}[{len(pieces) + 1}]"
                share = box_volume(half) / total
                store[piece] = self._mock_body(half, whole["volume"] * share)
                pieces.append(piece)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": feature,
                "tool": tool,
                "body": body,
                "pieces": [self._mock_facts(n, store[n]) for n in pieces],
                "bodies_before": before,
                "bodies_after": list(store),
            },
            execution_time=self._delays["feature_operation"],
        )

    async def body_clearance(
        self, body_a: str, body_b: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock the gap between two bodies: the distance between their boxes.

        Overlapping boxes are touching, with their shared volume as
        ``overlap_mm3``.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.bodies import OVERLAP_MM3, nearest_points, overlap_volume
        from .solidworks.clearance import TOUCHING_MM, box_distance

        refused = self._bodies_refusal("body_clearance")
        if refused is not None:
            return refused
        if body_a == body_b:
            return self._mock_invalid(
                f"body_a and body_b are both {body_a!r}; give two different bodies."
            )
        refused = self._mock_missing([body_a, body_b])
        if refused is not None:
            return refused
        await asyncio.sleep(self._delays["model_operation"])
        store = self._mock_bodies()
        a, b = store[body_a]["box"], store[body_b]["box"]
        overlap = overlap_volume(a, b)
        reply: dict[str, Any] = {"body_a": body_a, "body_b": body_b}
        if overlap > OVERLAP_MM3:
            reply.update(
                clearance_mm=0.0,
                touching=True,
                overlap_mm3=round(overlap, 6) + 0.0,
                point_a_mm=None,
                point_b_mm=None,
                face_a=None,
                face_b=None,
                pairs_checked=0,
                pairs_total=0,
            )
        else:
            clearance = box_distance(a, b)
            touching = clearance <= TOUCHING_MM
            point_a, point_b = nearest_points(a, b)
            reply.update(
                clearance_mm=0.0 if touching else round(clearance, 6),
                touching=touching,
                overlap_mm3=0.0,
                point_a_mm=[round(v, 4) + 0.0 for v in point_a],
                point_b_mm=[round(v, 4) + 0.0 for v in point_b],
                face_a={"kind": "plane", "box_mm": [round(v, 4) for v in a]},
                face_b={"kind": "plane", "box_mm": [round(v, 4) for v in b]},
                pairs_checked=1,
                pairs_total=36,
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=reply,
            execution_time=self._delays["model_operation"],
        )

    async def make_mating_negative(
        self,
        target: str,
        tools: list[str],
        clearance_mm: float,
        directions: int = 32,
        axis: str = "y",
        method: str = "auto",
    ) -> AdapterResult[dict[str, Any]]:
        """Mock cutting the tools, grown by the clearance, out of the target.

        The volume removed is each tool's box, grown by the clearance,
        overlapping the target's box; nothing of the tools is left in it.
        ``auto`` reports the shifted copies the session needed.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.bodies import folder_name, grow_box, overlap_volume

        refused = self._bodies_refusal("make_mating_negative")
        if refused is not None:
            return refused
        if target in tools or len(set(tools)) != len(tools):
            return self._mock_invalid(
                "give the target once and each tool once, the target not among "
                "the tools."
            )
        refused = self._mock_missing([target, *tools])
        if refused is not None:
            return refused
        await asyncio.sleep(self._delays["feature_operation"])
        store = self._mock_bodies()
        body = store[target]
        before = body["volume"]
        removed = min(
            before,
            sum(
                overlap_volume(body["box"], grow_box(store[t]["box"], clearance_mm))
                for t in tools
            ),
        )
        body["volume"] = before - removed
        indent = method == "indent"
        if indent:
            features = [self._mock_feature("Indent")]
        else:
            features = [self._mock_feature("Imported") for _t in tools]
            features.append(self._mock_feature("Combine"))
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "method": "indent" if indent else "shifted_copies",
                "target": target,
                "result_body": target,
                "tools": list(tools),
                "clearance_mm": float(clearance_mm),
                "directions": int(directions),
                "axis": axis,
                "copies_per_tool": 0 if indent else 2 * int(directions) + 3,
                "volume_before_mm3": round(before, 6) + 0.0,
                "volume_after_mm3": round(body["volume"], 6) + 0.0,
                "volume_removed_mm3": round(removed, 6) + 0.0,
                "residual_overlap_mm3": 0.0,
                "features": features,
                "folder": None if indent else folder_name(tools),
                "attempts": [],
            },
            execution_time=self._delays["feature_operation"],
        )

    async def check_interference(
        self, params: Any = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock checking the active assembly for interfering components.

        Args:
            params (Any): Optional settings; ``coincident``, ``components``.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        options = params if isinstance(params, dict) else {}

        if len(self._components) < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "Interference detection needs at least two components; the "
                    f"assembly has {len(self._components)}."
                ),
            )

        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                # The real adapter gets this from SolidWorks' interference
                # engine. The mock has no geometry, so it cannot know whether
                # anything overlaps and says so rather than reporting a clean
                # assembly it never checked - a mock that always answered
                # False would make this tool useless wherever mock mode runs.
                "interference_found": None,
                "interference_count": None,
                "interferences": [],
                "coincident_treated_as_interference": bool(
                    options.get("coincident", False)
                ),
                "scope": "whole assembly",
                "tolerance_applied": None,
            },
            execution_time=self._delays["model_operation"],
        )

    #: A component's box in its own coordinates when a test set none: a 10 mm
    #: cube from its origin.
    _DEFAULT_COMPONENT_BOX_MM = (0.0, 0.0, 0.0, 10.0, 10.0, 10.0)

    def _mock_placement(self, name: str) -> tuple[list[list[float]], list[float]]:
        """A component's rotation rows and origin (mm) in the mock assembly."""
        held = self._component_transforms.get(name, {})
        rows = held.get("rotation") or [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
        return [list(r) for r in rows], list(held.get("origin_mm") or [0.0, 0.0, 0.0])

    async def measure_clearance(
        self,
        component_a: str,
        component_b: str,
        coarse: bool = False,
        timeout_s: float | None = None,
        faces: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock clearance between two components, each standing in for its box.

        The mock has no faces: each component is its box,
        ``component_boxes_mm`` (its own coordinates, mm; a 10 mm cube from its
        origin when a test set none), placed where ``insert_component`` put
        it. The refusals are the real adapter's: not an assembly, the same
        component twice, one not in the assembly, one with no solid body
        (a box of ``None``). One pair, measured exactly, so the reply is
        always complete. By default it is the real adapter's one-call answer
        (``method: components``, no faces); with ``faces``, the boxes stand
        in for the nearest faces (``method: face_pairs``).

        Args:
            component_a (str): An instance name, as ``list_components`` gives it.
            component_b (str): Another one.
            coarse (bool): Ignored: one pair is all there is.
            timeout_s (float | None): Ignored, likewise.
            faces (bool): Answer as the face search, with the boxes as faces.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.clearance import (
            TOUCHING_MM,
            InvalidRequest,
            box_distance,
            transform_box,
        )

        def refused(error: str) -> AdapterResult[dict[str, Any]]:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=error)

        if not self._current_model:
            return refused("No active model")
        if self._current_model.type != "Assembly":
            return refused(
                str(
                    InvalidRequest(
                        "measure_clearance measures between components of an "
                        "assembly, and the active document is a "
                        f"{self._current_model.type.lower()}; activate the "
                        "assembly first."
                    )
                )
            )
        if component_a == component_b:
            return refused(
                str(
                    InvalidRequest(
                        f"component_a and component_b are both {component_a!r}; "
                        "give two different components."
                    )
                )
            )
        missing = [n for n in (component_a, component_b) if n not in self._components]
        if missing:
            return refused(
                f"Component(s) not found: {', '.join(missing)}. "
                f"The assembly holds: {', '.join(self._components)}."
            )
        boxes = []
        for role, name in (("component_a", component_a), ("component_b", component_b)):
            box = self.component_boxes_mm.get(name, self._DEFAULT_COMPONENT_BOX_MM)
            if box is None:
                return refused(
                    str(
                        InvalidRequest(
                            f"{role} {name!r} holds no solid body (a sketch-only "
                            "or surface part), so there is nothing of it to "
                            "measure."
                        )
                    )
                )
            boxes.append(transform_box(box, *self._mock_placement(name)))
        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1

        a, b = boxes
        point_a, point_b = [], []
        for k in range(3):
            if a[k + 3] < b[k]:
                point_a.append(a[k + 3])
                point_b.append(b[k])
            elif b[k + 3] < a[k]:
                point_a.append(a[k])
                point_b.append(b[k + 3])
            else:  # overlapping on this axis: the middle of the overlap
                middle = (max(a[k], b[k]) + min(a[k + 3], b[k + 3])) / 2
                point_a.append(middle)
                point_b.append(middle)
        clearance = box_distance(a, b)
        touching = clearance <= TOUCHING_MM
        whole = not faces
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "component_a": component_a,
                "component_b": component_b,
                "clearance_mm": 0.0 if touching else round(clearance, 6),
                "touching": touching,
                "point_a_mm": [round(v, 4) + 0.0 for v in point_a],
                "point_b_mm": [round(v, 4) + 0.0 for v in point_b],
                "face_a": None
                if whole
                else {"kind": "plane", "box_mm": [round(v, 4) for v in a]},
                "face_b": None
                if whole
                else {"kind": "plane", "box_mm": [round(v, 4) for v in b]},
                "pairs_checked": 0 if whole else 1,
                "pairs_total": None if whole else 1,
                "complete": True,
                "at_least_mm": 0.0 if touching else round(clearance, 6),
                "method": "components" if whole else "face_pairs",
            },
            execution_time=self._delays["model_operation"],
        )

    def _mock_turned_boxes(
        self, moving: str, other: str
    ) -> tuple[str, list[float], list[float], list[float] | None] | str:
        """The sweep's two boxes (mm) and the moving one's placement, or the refusal."""
        from .solidworks.clearance import transform_box

        model = self._current_model
        if model.type == "Part":
            missing = self._mock_missing([moving, other])
            if missing is not None:
                return str(missing.error)
            store = self._mock_bodies()
            return "bodies", store[moving]["box"], store[other]["box"], None
        absent = [n for n in (moving, other) if n not in self._components]
        if absent:
            return (
                f"Component(s) not found: {', '.join(absent)}. "
                f"The assembly holds: {', '.join(self._components)}."
            )
        boxes = []
        for role, name in (("moving", moving), ("other", other)):
            box = self.component_boxes_mm.get(name, self._DEFAULT_COMPONENT_BOX_MM)
            if box is None:
                return (
                    f"Invalid request: {role} {name!r} holds no solid body, so "
                    "there is nothing of it to measure."
                )
            boxes.append([float(v) for v in box])
        rows, origin = self._mock_placement(moving)
        start = [x for row in rows for x in row] + list(origin) + [1.0, 0, 0, 0]
        placed_other = transform_box(boxes[1], *self._mock_placement(other))
        return "components", boxes[0], placed_other, start

    async def rotation_clearance(
        self,
        moving: str,
        other: str,
        axis: list[float],
        pivot_mm: list[float] | None = None,
        range_deg: list[float] | None = None,
        step_deg: float = 5.0,
        tolerance_deg: float = 0.1,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a turn's clearance: the real sweep, on each one's box.

        A part's bodies and an assembly's components stand in for their boxes
        (``part_bodies_mm``, ``component_boxes_mm``); nothing moves.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.rotation_clearance import box_sweep, range_problem

        model = self._current_model
        if model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if model.type not in ("Part", "Assembly"):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "rotation_clearance needs an active assembly or part document "
                    f"(active document is {model.type.lower()})."
                ),
            )
        if moving == other:
            return self._mock_invalid(
                f"moving and other are both {moving!r}; give two different ones."
            )
        span = list(range_deg or (-180.0, 180.0))
        problem = range_problem(span, step_deg, tolerance_deg)
        if problem:
            return self._mock_invalid(problem)
        found = self._mock_turned_boxes(moving, other)
        if isinstance(found, str):
            return AdapterResult(status=AdapterResultStatus.ERROR, error=found)
        kind, moving_box, other_box, start = found
        await asyncio.sleep(self._delays["model_operation"])
        data = box_sweep(
            moving_box,
            other_box,
            axis,
            list(pivot_mm or (0.0, 0.0, 0.0)),
            span,
            step_deg,
            tolerance_deg,
            start,
        )
        extra = {"body_frame": "part", "restored": True} if start else {}
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"kind": kind, "moving": moving, "other": other, **data, **extra},
            execution_time=self._delays["model_operation"],
        )

    #: The mock's default planes: a point on each (mm) and its normal.
    _MOCK_PLANES: dict[str, tuple[list[float], list[float]]] = {
        "Front Plane": ([0.0, 0.0, 0.0], [0.0, 0.0, 1.0]),
        "Top Plane": ([0.0, 0.0, 0.0], [0.0, 1.0, 0.0]),
        "Right Plane": ([0.0, 0.0, 0.0], [1.0, 0.0, 0.0]),
    }

    def _mock_density_g_cm3(self) -> float | None:
        """The density the mock part has: its material's, else its own."""
        assigned = getattr(self, "_materials", {}).get(self._mock_doc_key())
        if assigned:
            return self._MOCK_DENSITIES.get(assigned["name"], 1000.0) / 1000.0
        return getattr(self, "_mock_densities", {}).get(self._mock_doc_key())

    async def volume_by_plane(
        self,
        plane: str | None = None,
        point_mm: list[float] | None = None,
        normal: list[float] | None = None,
        bodies: list[str] | None = None,
        enclosed: bool = False,
        density_g_cm3: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock the volume on each side of a plane: each body is its box.

        Only the default planes are known by name; a mock box encloses nothing.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.placement import unit_vector
        from .solidworks.plane_volume import box_volume_below, mass_g
        from .solidworks.sketch import plane_candidates_for

        refused = self._bodies_refusal("volume_by_plane")
        if refused is not None:
            return refused
        if plane:
            names = plane_candidates_for(plane)[1]
            known = next((n for n in names if n in self._MOCK_PLANES), None)
            if known is None:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"Plane not found: {plane}. Use a plane's name from "
                        "list_features, such as Top Plane, or give point_mm and "
                        "normal."
                    ),
                )
            origin, direction = self._MOCK_PLANES[known]
        else:
            known, origin = None, [float(v) for v in point_mm or (0.0, 0.0, 0.0)]
            direction = unit_vector(normal or (0.0, 1.0, 0.0))
        store = self._mock_bodies()
        names = list(bodies or store)
        refused = self._mock_missing(names)
        if refused is not None:
            return refused
        offset = sum(o * n for o, n in zip(origin, direction, strict=True))
        rows = []
        for name in names:
            body = store[name]
            box_share = box_volume_below(body["box"], direction, offset)
            full = max(
                1e-12, math.prod(body["box"][k + 3] - body["box"][k] for k in range(3))
            )
            below = round(body["volume"] * box_share / full, 6) + 0.0
            rows.append(
                {
                    "name": name,
                    "volume_mm3": round(body["volume"], 6) + 0.0,
                    "above_mm3": round(body["volume"] - below, 6) + 0.0,
                    "below_mm3": below,
                }
            )
        density, source = density_g_cm3, "input" if density_g_cm3 is not None else None
        if density is None:
            density = self._mock_density_g_cm3()
            source = "solidworks" if density is not None else None
        sides: dict[str, dict[str, Any]] = {}
        for side in ("above", "below"):
            volume = round(sum(r[f"{side}_mm3"] for r in rows), 6) + 0.0
            sides[side] = {"volume_mm3": volume, "mass_g": mass_g(volume, density)}
            if enclosed:
                sides[side].update(enclosed_mm3=0.0, enclosed_ml=0.0, pockets=0)
        total = round(sum(r["volume_mm3"] for r in rows), 6) + 0.0
        check = sides["above"]["volume_mm3"] + sides["below"]["volume_mm3"] - total
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "plane": {"name": known, "origin_mm": origin, "normal": direction},
                **sides,
                "bodies": rows,
                "total_volume_mm3": total,
                "check_mm3": round(check, 6) + 0.0,
                "check_ok": True,
                "density_g_cm3": density,
                "density_source": source,
            },
            execution_time=self._delays["model_operation"],
        )

    async def set_density(
        self, density_g_cm3: float, remove_material: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a document density: kept per part, refused over a material.

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        from .solidworks.density import METHOD, PERSISTS, material_message

        guard = self._mock_doc_error({"Part"}, "set_density")
        if guard is not None:
            return guard
        key = self._mock_doc_key()
        materials = getattr(self, "_materials", {})
        assigned = materials.get(key)
        if assigned and not remove_material:
            return self._mock_invalid(
                material_message(assigned["name"], assigned["database"])
            )
        if assigned:
            del materials[key]
        if not hasattr(self, "_mock_densities"):
            self._mock_densities: dict[str, float] = {}
        self._mock_densities[key] = float(density_g_cm3)
        self._operation_count += 1
        volume = round(sum(b["volume"] for b in self._mock_bodies().values()), 4)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "density_g_cm3": float(density_g_cm3),
                "density_kg_m3": float(density_g_cm3) * 1000.0,
                "read_back_kg_m3": float(density_g_cm3) * 1000.0,
                "mass_properties_density_g_cm3": float(density_g_cm3),
                "applied": True,
                "volume_mm3": volume,
                "mass_g": round(volume * float(density_g_cm3) / 1000.0, 6),
                "material_removed": (
                    {"material": assigned["name"], "database": assigned["database"]}
                    if assigned
                    else None
                ),
                "method": METHOD,
                "persists": PERSISTS,
            },
        )

    # Feature Editing
    async def delete_feature(
        self, name: str, delete_absorbed: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Mock deleting a named feature from the active model.

        A feature's absorbed sketch is the one its parameters name
        (``sketch_name``, which the mock's extrusions and cuts record); like
        SolidWorks, it stays unless ``delete_absorbed``.

        Args:
            name (str): Feature or sketch name.
            delete_absorbed (bool): Delete the feature's own sketch with it.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])

        # _features is keyed by an internal id, so resolve by feature name.
        before = [f.name for f in self._features.values()]
        key = next((k for k, f in self._features.items() if f.name == name), None)
        if key is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Feature not found: {name}. "
                    f"Available: {', '.join(before) or 'none'}"
                ),
            )

        sketch = (self._features[key].parameters or {}).get("sketch_name")
        absorbed = (
            [sketch] if isinstance(sketch, str) and sketch in self._sketches else []
        )
        del self._features[key]
        self._suppressed_features.discard(name)
        if delete_absorbed:
            for sketch_name in absorbed:
                del self._sketches[sketch_name]
        self._operation_count += 1
        after = [f.name for f in self._features.values()]
        removed = [n for n in before if n not in after]
        if delete_absorbed:
            removed += absorbed

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "deleted": name,
                "removed_features": removed,
                "features_before": len(before),
                "features_after": len(after),
                "absorbed_sketches": absorbed,
                "absorbed_sketches_left": [] if delete_absorbed else absorbed,
            },
            execution_time=self._delays["model_operation"],
        )

    async def suppress_feature(
        self, name: str, suppress: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        """Mock suppressing or unsuppressing a named feature.

        Args:
            name (str): Feature name.
            suppress (bool): True to suppress, False to unsuppress.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])

        known = any(f.name == name for f in self._features.values())
        if not known:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Feature not found: {name}"
            )

        was = name in self._suppressed_features
        if suppress:
            self._suppressed_features.add(name)
        else:
            self._suppressed_features.discard(name)
        self._operation_count += 1

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "feature": name,
                "requested": suppress,
                "suppressed": bool(suppress),
                "was_suppressed": was,
            },
            execution_time=self._delays["model_operation"],
        )

    async def rename_feature(
        self, old_name: str, new_name: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock renaming a named feature in the active model.

        Mirrors the real adapter: an empty target name, an unknown feature, or
        a target name already in use are all errors; renaming to the current
        name is a no-op success.

        Args:
            old_name (str): Current feature name.
            new_name (str): New feature name.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])

        new = new_name.split("@", 1)[0].strip()
        if not new:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="new_name must not be empty"
            )

        if old_name in self._sketches and new != old_name:
            return self._mock_rename_sketch(old_name, new)
        key = next((k for k, f in self._features.items() if f.name == old_name), None)
        if key is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Feature not found: {old_name}",
            )
        if new == old_name:
            return AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data={
                    "renamed": False,
                    "old_name": old_name,
                    "new_name": new,
                    "reason": "old and new names are the same",
                },
                execution_time=self._delays["model_operation"],
            )
        if any(f.name == new for f in self._features.values()):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Cannot rename to '{new}': a feature with that name already exists"
                ),
            )

        self._features[key].name = new
        if old_name in self._suppressed_features:
            self._suppressed_features.discard(old_name)
            self._suppressed_features.add(new)
        self._operation_count += 1

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"renamed": True, "old_name": old_name, "new_name": new},
            execution_time=self._delays["model_operation"],
        )

    async def undo(self, count: int = 1) -> AdapterResult[dict[str, Any]]:
        """Mock undoing the last operations in the active model.

        Removes the most recently added features, mirroring what an undo of
        feature-creation steps does to the tree. ``tree_changed`` is ``False``
        when there was nothing left to undo, which is what the real adapter
        reports too - SolidWorks accepts an undo on an empty stack quietly.

        Args:
            count (int): Number of steps to undo.

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        await asyncio.sleep(self._delays["model_operation"])
        steps = max(1, int(count))

        keys = list(self._features)
        before = [self._features[k].name for k in keys]
        doomed = keys[-steps:] if steps <= len(keys) else list(keys)
        removed = [self._features[k].name for k in doomed]
        for key in doomed:
            self._suppressed_features.discard(self._features[key].name)
            del self._features[key]
        self._operation_count += 1
        after = [f.name for f in self._features.values()]

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "requested_steps": steps,
                "features_before": len(before),
                "features_after": len(after),
                "removed_features": removed,
                "tree_changed": before != after,
            },
            execution_time=self._delays["model_operation"],
        )

    async def set_component_transform(
        self,
        component: str,
        origin_mm: list[float] | None = None,
        rotation_rows: list[list[float]] | None = None,
        fixed: bool | None = None,
        turn_axis: list[float] | None = None,
        turn_pivot_mm: list[float] | None = None,
        turn_deg: float = 0.0,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock placing a component: its stored origin, rotation and fixed state.

        Args:
            component (str): Instance name.
            origin_mm (list[float] | None): The new origin; None keeps it.
            rotation_rows (list[list[float]] | None): The new axes; None
                keeps them.
            fixed (bool | None): Fix or float it; None keeps it.
            turn_axis (list[float] | None): Then turn it about this direction.
            turn_pivot_mm (list[float] | None): A point on that axis, mm.
            turn_deg (float): The turn, degrees.

        Returns:
            AdapterResult[dict[str, Any]]: As the live adapter's.
        """
        if getattr(self._current_model, "type", None) != "Assembly":
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="set_component_transform requires an assembly document",
            )
        if component not in self._components:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Component(s) not found: {component}. The assembly holds: "
                    f"{', '.join(self._components) or 'nothing'}."
                ),
            )
        identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        held = self._component_transforms.setdefault(
            component, {"origin_mm": [0.0, 0.0, 0.0], "rotation": identity}
        )
        before = {"origin_mm": list(held["origin_mm"]), "rotation": held["rotation"]}
        if origin_mm is not None:
            held["origin_mm"] = [float(c) for c in origin_mm]
        if rotation_rows is not None:
            held["rotation"] = [[float(x) for x in row] for row in rotation_rows]
        if turn_axis is not None and turn_deg:
            from .solidworks.placement import turned

            data = turned(
                [x for row in held["rotation"] for x in row]
                + held["origin_mm"]
                + [1.0, 0.0, 0.0, 0.0],
                turn_axis,
                turn_pivot_mm or [0.0, 0.0, 0.0],
                turn_deg,
            )
            held["rotation"] = [data[0:3], data[3:6], data[6:9]]
            held["origin_mm"] = [round(v, 9) + 0.0 for v in data[9:12]]
        state = self._component_states.setdefault(
            component, {"fixed": False, "flexible": False}
        )
        if fixed is not None:
            state["fixed"] = bool(fixed)
        mates = sum(1 for m in self._mates if component in m.get("components", []))
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "component": component,
                "placed": True,
                "moved": before != held,
                "fixed": state["fixed"],
                "origin_mm": held["origin_mm"],
                "rotation": held["rotation"],
                "mates": mates,
            },
        )

    async def set_component_state(
        self,
        component: str,
        fixed: bool | None = None,
        flexible: bool | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock fixing, floating and flexing a component.

        Tracks each component's state on ``self._component_states``.

        Args:
            component (str): Instance name.
            fixed (bool | None): True fixes it, False floats it.
            flexible (bool | None): True flexible, False rigid.

        Returns:
            AdapterResult[dict[str, Any]]: The state before and after.
        """
        if fixed is None and flexible is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Nothing to change: give fixed, flexible or both.",
            )
        states = self._component_states
        before = dict(states.get(component, {"fixed": False, "flexible": False}))
        after = dict(before)
        if fixed is not None:
            after["fixed"] = fixed
        if flexible is not None:
            after["flexible"] = flexible
        states[component] = after
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"component": component, "before": before, "after": after},
        )

    def _component_names_problem(self, names: list[str], what: str) -> str | None:
        """Why ``names`` can't be hidden in the active document, or None."""
        if getattr(self._current_model, "type", None) != "Assembly":
            return f"{what} needs an assembly; the active document isn't one."
        missing = [n for n in names if n not in self._components]
        if missing:
            return (
                f"Component(s) not found: {', '.join(missing)}. The assembly holds: "
                f"{', '.join(self._components) or 'nothing'} (list_components "
                "names them)."
            )
        return None

    async def set_component_visibility(
        self, components: list[str], visible: bool
    ) -> AdapterResult[dict[str, Any]]:
        """Mock hiding and showing components, kept on ``_component_visibility``.

        Args:
            components (list[str]): Component names.
            visible (bool): Show (True) or hide (False).

        Returns:
            AdapterResult[dict[str, Any]]: The real adapter's reply shape.
        """
        names = list(dict.fromkeys(components or []))
        if not names:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Give the components to show or hide.",
            )
        problem = self._component_names_problem(names, "set_component_visibility")
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        done = []
        for name in names:
            before = self._component_visibility.get(name, True)
            self._component_visibility[name] = bool(visible)
            done.append(
                {
                    "name": name,
                    "before": before,
                    "after": bool(visible),
                    "method": None if before is bool(visible) else "Visible",
                }
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"visible": bool(visible), "components": done},
        )

    async def add_mate(
        self,
        component_a: str,
        component_b: str,
        entity_a: str = "Front Plane",
        entity_b: str = "Front Plane",
        mate_type: str = "coincident",
        alignment: str = "aligned",
        distance: float = 0.0,
        angle: float = 0.0,
        face_a: list[float] | None = None,
        face_b: list[float] | None = None,
        flip: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock mating two components in the active assembly.

        Args:
            component_a (str): First component instance name.
            component_b (str): Second component instance name.
            entity_a (str): Named feature on the first component. Defaults to
                "Front Plane".
            entity_b (str): Named feature on the second component. Defaults to
                "Front Plane".
            mate_type (str): Mate type. Defaults to "coincident".
            alignment (str): Mate alignment. Defaults to "aligned".
            distance (float): Distance in millimetres, for a distance mate.
                Defaults to 0.0.
            angle (float): Angle in degrees, for an angle mate. Defaults to 0.0.
            face_a (list[float] | None): A point on a face of the first
                component (part coordinates, mm), used instead of entity_a.
            face_b (list[float] | None): The same for the second component.
            flip (bool): Put a distance or angle mate's component on the
                other side (the mate's ``FlipDimension``).

        Returns:
            AdapterResult[dict[str, Any]]: The result produced by the operation.
        """
        from .solidworks.face_selection import mate_entity_label

        known_mate_types = {
            "coincident",
            "concentric",
            "perpendicular",
            "parallel",
            "tangent",
            "distance",
            "angle",
        }
        if mate_type not in known_mate_types:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown mate type '{mate_type}'. "
                    f"Use one of: {', '.join(sorted(known_mate_types))}."
                ),
            )

        await asyncio.sleep(self._delays["model_operation"])
        self._operation_count += 1
        self._mates.append(
            {"mate_type": mate_type, "components": [component_a, component_b]}
        )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "mate_type": mate_type,
                "alignment": alignment,
                "components": [component_a, component_b],
                "entities": [
                    mate_entity_label(entity_a, face_a),
                    mate_entity_label(entity_b, face_b),
                ],
                "distance": distance or None,
                "angle": angle or None,
                "flipped": bool(flip) if mate_type in ("distance", "angle") else None,
                # The real adapter fills these by comparing every component's
                # Transform2 before and after the mate. The mock has no
                # geometry to move, so it reports "not determinable" rather
                # than True: this field exists precisely so a caller can tell
                # a mate that positioned something from one SolidWorks
                # accepted and ignored, and a mock that always answers True
                # would make that check useless wherever mock mode is used.
                "moved_components": [],
                "geometry_moved": None,
            },
            execution_time=self._delays["model_operation"],
        )

    async def create_reference_plane(
        self,
        reference: str,
        offset: float = 0.0,
        angle: float = 0.0,
        flip: bool = False,
        axis: str | None = None,
        face_point: list[float] | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock creating a reference plane offset from, angled to, or on a face.

        Refuses exactly what the live adapter refuses (the shared
        ``reference_plane_refusal`` in ``solidworks/features.py``). On
        success, invents a sequential ``PlaneN`` name tracked on
        ``self._reference_planes`` and advances the shared mock feature-tree
        counter so ``features_before``/``features_after`` differ by exactly
        one, the same signal the live adapter derives from
        ``IFeatureManager::GetFeatureCount``.

        Args:
            reference (str): Name of the reference plane.
            offset (float): Offset distance in millimetres. Defaults to 0.0.
            angle (float): Angle in degrees about ``axis``. Defaults to 0.0.
            flip (bool): Reverse the offset direction. Defaults to False.
            axis (str | None): Axis to rotate about, for an angle.
            face_point (list[float] | None): Point on a flat face, in mm.

        Returns:
            AdapterResult[dict[str, Any]]: The new plane's name and
            parameters, or error.
        """
        from .solidworks.features import reference_plane_refusal

        refusal = reference_plane_refusal(reference, offset, angle, axis, face_point)
        if refusal is not None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=refusal)

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        before = self._feature_tree_count
        self._feature_tree_count += 1
        after = self._feature_tree_count

        self._reference_planes.append(f"Plane{len(self._reference_planes) + 1}")
        # What edit_feature reads back from an IRefPlaneFeatureData.
        self.__dict__.setdefault("_plane_definitions", {})[
            self._reference_planes[-1]
        ] = {"offset": abs(offset) if offset and not angle else None, "flip": flip}
        base = self._mock_frame_of(reference) if face_point is None else None
        if base is not None and offset and not angle:
            # The side is the offset's sign, turned by flip.
            self._plane_frames[self._reference_planes[-1]] = base.offset(
                -offset if flip else offset
            )

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": self._reference_planes[-1],
                "reference": reference if face_point is None else None,
                "face_point": face_point,
                "offset": offset or None,
                "angle": angle or None,
                "axis": axis if angle else None,
                "flip": flip,
                "axes": self._mock_axes_of(self._reference_planes[-1]),
                "features_before": before,
                "features_after": after,
            },
            execution_time=self._delays["feature_operation"],
        )

    async def create_axis(self, reference: str) -> AdapterResult[dict[str, Any]]:
        """Mock creating a reference axis along a principal direction.

        Mirrors the live adapter's plane-pair mapping (``_AXIS_PLANE_PAIRS``
        in ``solidworks/features.py``, imported here directly so the two can
        never drift apart) and validation: only ``x``/``y``/``z``
        (case-insensitive, leading +/- stripped) are accepted.

        Args:
            reference (str): "x", "y" or "z" (case-insensitive; leading +/-
                stripped).

        Returns:
            AdapterResult[dict[str, Any]]: The plane pair used and the
            feature counter before/after, or error for an unknown reference.
        """
        key = str(reference or "").strip().lower().lstrip("+-")
        if key not in _AXIS_PLANE_PAIRS:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Unknown axis reference '{reference}'. "
                    f"Use one of: {', '.join(sorted(_AXIS_PLANE_PAIRS))}."
                ),
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        before = self._feature_tree_count
        self._feature_tree_count += 1
        after = self._feature_tree_count

        plane_a, plane_b = _AXIS_PLANE_PAIRS[key]

        # Invent a sequential "AxisN" name, tracked on self._axes, the same
        # way create_reference_plane invents "PlaneN" names on
        # self._reference_planes. The live adapter reads the new axis's name
        # from the end of the feature tree (InsertAxis2 returns only a bool).
        self._axes.append(f"Axis{len(self._axes) + 1}")

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": self._axes[-1],
                "reference": key,
                "planes": [plane_a, plane_b],
                "features_before": before,
                "features_after": after,
            },
            execution_time=self._delays["feature_operation"],
        )

    def _mock_frame_of(self, plane: str | None) -> Frame | None:
        """The frame of a plane the mock knows, by any name create_sketch takes."""
        if not plane:
            return None
        key, candidates = plane_candidates_for(plane)
        if key is not None:
            return Frame.from_mapping(default_plane_axes(key))
        return self._plane_frames.get(candidates[0])

    def _mock_axes_of(self, plane: str | None) -> dict[str, Any] | None:
        """``create_sketch``'s ``axes`` for a plane, or None when unknown."""
        frame = self._mock_frame_of(plane)
        return frame.describe() if frame is not None else None

    def _mock_sketch_open_refusal(self) -> AdapterResult[dict[str, Any]] | None:
        """The live adapter's refusal while a sketch is open, or None."""
        if self._current_sketch is None:
            return None
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error=(
                "A sketch is open; exit_sketch first. The axis or plane is built "
                "on a 3D sketch of its own, and opening it would close yours."
            ),
        )

    async def get_open_sketch_axes(self) -> AdapterResult[dict[str, Any]]:
        """Mock reading the open sketch's frame: its plane's, when the mock knows it."""
        if self._current_sketch is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "No sketch is open: open one with create_sketch or "
                    "edit_sketch first."
                ),
            )
        plane = self._sketches.get(self._current_sketch)
        frame = self._mock_frame_of(plane)
        if frame is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"The mock keeps no geometry for {plane!r}, so model points "
                    "can't be placed on its sketch."
                ),
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={**frame.as_dict(), "summary": frame.describe()["summary"]},
        )

    async def plane_axes(self, plane: str) -> AdapterResult[dict[str, Any]]:
        """Mock reading a plane's sketch axes: the default planes and known ones."""
        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        key, candidates = plane_candidates_for(plane)
        name = candidates[0]
        if key is None and name not in self._reference_planes:
            kind = self._mock_feature_kind(name)
            error = (
                f"{name!r} is a {kind}, not a plane; create_reference_plane makes one."
                if kind
                else f"Plane '{plane}' not found in the part. Use Front, Top or "
                "Right, or a plane made by create_reference_plane (list_features "
                "names them)."
            )
            return AdapterResult(status=AdapterResultStatus.ERROR, error=error)
        frame = self._mock_frame_of(name)
        if frame is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"The mock keeps no geometry for {name!r}.",
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "plane": name,
                "axes": frame.describe(),
                "frame": frame.as_dict(),
                "method": "plane_transform",
            },
        )

    async def create_axis_through(
        self, point_mm: list[float], second_point_mm: list[float]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a reference axis through two points: a 3D sketch and an axis."""
        try:
            start, end = two_points(point_mm, second_point_mm)
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        refusal = self._mock_sketch_open_refusal()
        if refusal is not None:
            return refusal
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        before = self._feature_tree_count
        self._feature_tree_count += 2  # the 3D sketch and the axis
        self._axes.append(f"Axis{len(self._axes) + 1}")
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": self._axes[-1],
                "sketch": f"3DSketch{len(self._axes)}",
                "point_mm": list(start),
                "second_point_mm": list(end),
                "direction": [round(c, 9) + 0.0 for c in unit(sub(end, start))],
                "read_back": {
                    "start_mm": list(start),
                    "end_mm": list(end),
                    "on_line": True,
                    "same_sense": True,
                },
                "features_before": before,
                "features_after": self._feature_tree_count,
            },
            execution_time=self._delays["feature_operation"],
        )

    async def create_plane_through(
        self, point_mm: list[float], normal: list[float]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a plane through a point square to ``normal``; its sketch x is the mock's pick."""
        try:
            frame = Frame.through(vector(point_mm, "point"), vector(normal, "normal"))
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        refusal = self._mock_sketch_open_refusal()
        if refusal is not None:
            return refusal
        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        before = self._feature_tree_count
        self._feature_tree_count += 2  # the 3D sketch and the plane
        name = f"Plane{len(self._reference_planes) + 1}"
        self._reference_planes.append(name)
        self._plane_frames[name] = frame
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": name,
                "point_mm": list(frame.origin_mm),
                "normal": [round(c, 9) + 0.0 for c in frame.normal],
                "sketch": f"3DSketch{len(self._reference_planes)}",
                "axes": frame.describe(),
                "normal_matches": True,
                "through_point": True,
                "features_before": before,
                "features_after": self._feature_tree_count,
            },
            execution_time=self._delays["feature_operation"],
        )

    async def create_reference_point(
        self,
        mode: str,
        x: float,
        y: float,
        z: float,
        distance: float | None = None,
        percent: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock creating a reference point on the active part.

        The mock has no geometry, so it cannot resolve a coordinate to an
        edge/face - it mirrors the live adapter's *validation* (unknown mode,
        the distance/percent XOR, range checks) and, on a valid request,
        bumps the feature counter the way ``InsertReferencePoint`` would.

        Args:
            mode (str): "along_curve" or "face_center".
            x (float): X of a point on the target entity, in millimetres.
            y (float): Y of a point on the target entity, in millimetres.
            z (float): Z of a point on the target entity, in millimetres.
            distance (float | None): For "along_curve", offset from the edge
                start in millimetres.
            percent (float | None): For "along_curve", 0-100 of edge length.

        Returns:
            AdapterResult[dict[str, Any]]: The mode and feature counts, or an
            error matching the live adapter's refusals.
        """
        key = str(mode or "").strip().lower()
        if key not in ("along_curve", "face_center"):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(f"Unknown mode {mode!r}. Use 'along_curve' or 'face_center'."),
            )
        if key == "along_curve":
            if (distance is None) == (percent is None):
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error="along_curve needs exactly one of distance or percent",
                )
            if distance is not None and float(distance) <= 0.0:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error="distance must be positive",
                )
            if percent is not None and not 0.0 < float(percent) < 100.0:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error="percent must be between 0 and 100 (exclusive)",
                )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        before = self._feature_tree_count
        self._feature_tree_count += 1
        self._reference_points.append(f"Point{len(self._reference_points) + 1}")

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": self._reference_points[-1],
                "mode": key,
                "at_mm": [x, y, z],
                "distance_mm": distance if key == "along_curve" else None,
                "percent": percent if key == "along_curve" else None,
                "features_before": before,
                "features_after": self._feature_tree_count,
            },
            execution_time=self._delays["feature_operation"],
        )

    #: Built-in planes every SolidWorks part starts with. Combined with
    #: ``self._reference_planes`` (planes created via create_reference_plane)
    #: this is the set of plane names ``mirror_feature`` will accept.
    _BUILTIN_PLANES = frozenset({"Front Plane", "Top Plane", "Right Plane"})

    async def mirror_feature(
        self,
        features: list[str],
        mirror_plane: str,
        merge: bool = True,
        mirror_bodies: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock mirroring solid bodies or features about a plane.

        The mock has no geometry engine, so it cannot measure a real volume
        change - it mirrors the live adapter's refusals instead of
        fabricating a result it cannot know. An empty source list or plane
        name is rejected outright (matching ``_mirror_feature_impl`` in
        ``solidworks/features.py`` verbatim), and a source or plane name
        that was never created in this session is rejected the same way
        ``_select_named_feature`` / ``_select_reference_entity`` would fail
        to select it there.

        On success, the tracked ``self._mirror_volume`` figure (seeded at
        the live-measured 1819569.1 mm^3 wing volume) is doubled, matching
        the ~2.0 ratio a correct mirror produces (measured live: 1.998528
        for a lofted wing, exactly 2.0 for a boss whose sketch sits on the
        plane).

        Args:
            features (list[str]): Body or feature names to mirror.
            mirror_plane (str): Plane name to mirror about.
            merge (bool): Merge the mirrored result with the original. Defaults to True.
            mirror_bodies (bool): Mirror whole bodies rather than features. Defaults to True.

        Returns:
            AdapterResult[dict[str, Any]]: The mirror feature's name, inputs
            and volume before/after, or error.
        """
        names = [n for n in (features or []) if n]
        if not names:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="mirror_feature requires at least one body or feature name",
            )
        if not mirror_plane:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="mirror_feature requires a mirror plane name",
            )

        known_sources = {feature.name for feature in self._features.values()} | set(
            self._components
        )
        for name in names:
            if name not in known_sources:
                kind = "body" if mirror_bodies else "feature"
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=f"Failed to select {kind} to mirror: {name}",
                )

        known_planes = self._BUILTIN_PLANES | set(self._reference_planes)
        if mirror_plane not in known_planes:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Failed to select mirror plane: {mirror_plane}",
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        volume_before = self._mirror_volume
        volume_after = volume_before * 2
        self._mirror_volume = volume_after

        self._feature_tree_count += 1
        feature_name = f"Mirror{self._feature_tree_count}"

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": feature_name,
                "mirrored": names,
                "mirror_plane": mirror_plane,
                "merge": bool(merge),
                "mirror_bodies": bool(mirror_bodies),
                "volume_before": volume_before,
                "volume_after": volume_after,
                "volume_change": volume_after - volume_before,
                "volume_ratio": round(volume_after / volume_before, 6),
            },
            execution_time=self._delays["feature_operation"],
        )

    #: Live-measured "volume added per extra circular-pattern instance", in
    #: mm^3: a hub+blade part measured 48431.5 mm3 for hub + 1 blade, and
    #: 75916.4 mm3 once patterned to 4 blades (3 extra instances) - see
    #: ``_pattern_circular_impl`` in ``solidworks/features.py``. Dividing the
    #: growth by the 3 extra instances gives the per-instance figure used
    #: below to scale ``self._pattern_volume`` by roughly ``count`` without a
    #: real geometry engine.
    _PATTERN_BLADE_VOLUME = (75916.4 - 48431.5) / 3

    async def pattern_circular(
        self,
        features: list[str],
        axis: str,
        count: int,
        angle: float = 360.0,
        equal_spacing: bool = True,
        reverse_direction: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock patterning features around an axis.

        The mock has no geometry engine, so it cannot measure a real volume
        change - it mirrors the live adapter's refusals instead of
        fabricating a result it cannot know (matching
        ``_pattern_circular_impl`` in ``solidworks/features.py`` verbatim
        for the three argument checks below). An axis name that was never
        created via ``create_axis`` in this session is rejected the same
        way ``SelectByID2`` would fail to select it there.

        On success, the tracked ``self._pattern_volume`` figure (seeded at
        the live-measured 48431.5 mm^3 hub+blade volume) grows by
        ``_PATTERN_BLADE_VOLUME * (count - 1)``, the live-derived
        per-instance volume, so volume_before/volume_after/volume_ratio
        stay self-consistent and scale with ``count`` roughly the way
        adding more blades to the hub would - reproducing the live
        48431.5 -> 75916.4 mm^3 measurement exactly for ``count=4``.

        Args:
            features (list[str]): Names of the features to pattern.
            axis (str): Name of the axis to rotate about.
            count (int): Total number of instances including the original.
            angle (float): Degrees: the total with ``equal_spacing``, else the step.
            equal_spacing (bool): Whether ``angle`` is the total. Defaults to True.
            reverse_direction (bool): Pattern the other way round the axis.

        Returns:
            AdapterResult[dict[str, Any]]: The pattern's name, inputs and
            volume before/after, or error.
        """
        names = [n for n in (features or []) if n]
        if not names:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="pattern_circular requires at least one feature name",
            )
        if not axis:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="pattern_circular requires an axis name to rotate about",
            )
        if int(count) < 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"pattern_circular needs a count of at least 2 (got {count}); "
                    "a pattern of one is just the original feature"
                ),
            )

        known_axes = set(self._axes)
        if axis not in known_axes:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Failed to select axis '{axis}'. Create one with "
                    "create_axis first, and pass the name it returns."
                ),
            )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1

        volume_before = self._pattern_volume
        volume_after = volume_before + self._PATTERN_BLADE_VOLUME * (int(count) - 1)
        self._pattern_volume = volume_after

        self._feature_tree_count += 1
        pattern_name = f"CirPattern{self._feature_tree_count}"

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": pattern_name,
                "patterned": names,
                "axis": axis,
                "count": int(count),
                "angle": float(angle),
                "equal_spacing": bool(equal_spacing),
                "reverse_direction": bool(reverse_direction),
                "volume_before": volume_before,
                "volume_after": volume_after,
                "volume_change": volume_after - volume_before,
                "volume_ratio": round(volume_after / volume_before, 6),
            },
            execution_time=self._delays["feature_operation"],
        )

    async def pattern_linear(
        self,
        features: list[str],
        direction: str,
        count: int,
        spacing: float,
        direction2: str | None = None,
        count2: int = 1,
        spacing2: float = 0.0,
        reverse_direction: bool = False,
        reverse_direction2: bool = False,
        bodies: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a linear pattern of features or bodies.

        Refuses what ``patterns.pattern_linear`` refuses, with its messages:
        the argument checks, a direction named but never made (not a default
        plane, nor a plane or axis made this session), and a seed feature or
        body that isn't in the part. It reads no volume: the tool measures it.

        Returns:
            AdapterResult[dict[str, Any]]: The pattern's ``name`` and inputs.
        """
        from .solidworks.patterns import linear_pattern_refusal, parse_direction

        if not self._current_model:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        refusal = linear_pattern_refusal(
            features, count, spacing, direction2, count2, spacing2
        )
        given = [direction] + ([direction2] if direction2 is not None else [])
        parsed = []
        if refusal is None:
            try:
                parsed = [parse_direction(d) for d in given]
            except ValueError as exc:
                refusal = str(exc)
        if refusal is not None:
            return self._mock_invalid(refusal)
        known = self._BUILTIN_PLANES | set(self._reference_planes) | set(self._axes)
        for item in parsed:
            if item.kind == "named" and item.name not in known:
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=f"Failed to select '{item.name}' as a pattern direction.",
                )
        names = [n for n in features if n]
        seeds = (
            set(self._mock_bodies())
            if bodies
            else {feature.name for feature in self._features.values()}
        )
        for name in names:
            if name not in seeds:
                what, where = (
                    ("body", "list_bodies") if bodies else ("feature", "list_features")
                )
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=f"Failed to select {what} to pattern: {name} ({where} names them)",
                )

        await asyncio.sleep(self._delays["feature_operation"])
        self._operation_count += 1
        self._feature_tree_count += 1
        number2, gap2 = (int(count2), float(spacing2)) if direction2 else (1, 0.0)

        def _way(item: Any, number: int, gap: float, reverse: bool) -> dict[str, Any]:
            return {
                "spec": item.spec,
                "follows": item.name or item.plane_key or item.point_mm,
                "count": number,
                "spacing_mm": gap,
                "flipped": item.flip != bool(reverse),
            }

        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": f"LPattern{self._feature_tree_count}",
                "patterned": names,
                "bodies": bool(bodies),
                "direction": _way(
                    parsed[0], int(count), float(spacing), reverse_direction
                ),
                "direction2": (
                    _way(parsed[1], number2, gap2, reverse_direction2)
                    if direction2
                    else None
                ),
                "instances": int(count) * number2,
            },
            execution_time=self._delays["feature_operation"],
        )

    # --- Engineering-data and drawing-documentation methods ---
    # Mirrors SolidWorksDocumentationMixin (solidworks/documentation.py): the
    # same refusals for wrong document types and unknown views, with
    # self-consistent state so set/get pairs round-trip.

    _MOCK_MATERIALS: tuple[dict[str, str], ...] = (
        {
            "name": "AISI 1020",
            "classification": "Steel",
            "database": "solidworks materials",
        },
        {
            "name": "AISI 304",
            "classification": "Steel",
            "database": "solidworks materials",
        },
        {
            "name": "6061-T6 (SS)",
            "classification": "Aluminium Alloys",
            "database": "solidworks materials",
        },
        {
            "name": "ABS",
            "classification": "Plastics",
            "database": "solidworks materials",
        },
    )
    _MOCK_MATERIAL_FOLDER = "mock://sldmaterials"
    #: Densities in kg/m³, as SolidWorks 2026's library holds them.
    _MOCK_DENSITIES: dict[str, float] = {
        "AISI 1020": 7900.0,
        "AISI 304": 8000.0,
        "6061-T6 (SS)": 2700.0,
        "ABS": 1020.0,
    }

    def _mock_doc_error(
        self, allowed: set[str], operation: str
    ) -> AdapterResult[Any] | None:
        """Return an error unless the active mock document type is allowed."""
        model = self._current_model
        doc_type = getattr(model, "type", None)
        if model is None or doc_type not in allowed:
            wanted = " or ".join(sorted(t.lower() for t in allowed))
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"{operation} needs an active {wanted} document",
            )
        return None

    def _mock_doc_key(self) -> str:
        """Key per-document mock state by path, falling back to name."""
        model = self._current_model
        return str(
            getattr(model, "path", "") or getattr(model, "name", "") or "untitled"
        )

    async def list_model_dimensions(
        self, feature_name: str | None = None
    ) -> AdapterResult[list[dict[str, Any]]]:
        """Mock listing model dimensions from the tracked dimension table."""
        await asyncio.sleep(self._delays["model_operation"] / 2)
        guard = self._mock_doc_error({"Part", "Assembly"}, "list_model_dimensions")
        if guard is not None:
            return guard
        self._operation_count += 1
        dims = []
        driven = self._mock_driven()
        for full_name, value in self._dimensions.items():
            feature = full_name.split("@", 1)[1] if "@" in full_name else ""
            if feature_name and feature.lower() != feature_name.lower():
                continue
            dims.append(
                {
                    "full_name": full_name,
                    "feature": feature,
                    "value": value,
                    "system_value": value / 1000.0,
                    "read_only": False,
                    "driven_by": driven.get(full_name.casefold()),
                }
            )
        if feature_name and not dims:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"No dimensions found on feature {feature_name!r}",
            )
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=dims)

    # ------------------------------------------------------------------
    # Equations and global variables (parametric-equations, D9)
    # ------------------------------------------------------------------

    def _mock_equation_list(self) -> list[str]:
        """This mock document's equation texts, in order."""
        if not hasattr(self, "_mock_equations"):
            self._mock_equations: dict[str, list[str]] = {}
        return self._mock_equations.setdefault(self._mock_doc_key(), [])

    def _mock_solve_orders(self) -> dict[str, bool]:
        if not hasattr(self, "_mock_solve_order"):
            self._mock_solve_order: dict[str, bool] = {}
        return self._mock_solve_order

    @staticmethod
    def _mock_number(text: str) -> float | None:
        """A right-hand side that is a plain number (``40``, ``2.5mm``), else None."""
        import re

        from .solidworks import equations as eq

        match = re.search(r"=\s*(-?\d+(?:\.\d+)?)\s*(mm)?\s*$", text)
        return float(match.group(1)) if match and eq.name_of(text) else None

    def _mock_equation_entries(self) -> list[dict[str, Any]]:
        """The real adapter's entry shape; values only for plain numbers."""
        from .solidworks import equations as eq

        texts = self._mock_equation_list()
        entries = []
        for index, text in enumerate(texts):
            name = eq.name_of(text)
            entries.append(
                {
                    "index": index,
                    "equation": text,
                    "name": name,
                    "value": self._mock_number(text),
                    "global": bool(name) and "@" not in (name or ""),
                }
            )
        variables = [e["name"] for e in entries if e["global"]]
        dimensions = {k.lower() for k in self._dimensions}
        for entry in entries:
            entry["broken"] = eq.is_broken(
                eq.right_names(entry["equation"]),
                variables,
                lambda n: eq.short_name(n).lower() in dimensions,
            )
        return entries

    def _mock_driven(self) -> dict[str, str]:
        """``driven_map`` for the mock document."""
        from .solidworks import equations as eq

        out: dict[str, str] = {}
        for text in self._mock_equation_list():
            name = eq.name_of(text)
            if name and "@" in name:
                out.setdefault(eq.short_name(name).casefold(), text)
        return out

    def _mock_failing_features(self) -> list[dict[str, Any]]:
        from .solidworks.rebuild_diagnostics import explain

        entries = [explain(i) for i in self.injected_rebuild_issues]
        return [e for e in entries if e["severity"] == "error"]

    async def _mock_equation_call(
        self, operation: str, work: Callable[[], dict[str, Any]]
    ) -> AdapterResult[dict[str, Any]]:
        model = self._current_model
        if model is None or getattr(model, "type", None) not in {"Part", "Assembly"}:
            what = (
                "no document is open"
                if model is None
                else f"the active document is a {str(model.type).lower()}"
            )
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Invalid request: {operation} takes a part or an assembly; {what}"
                ),
            )
        self._operation_count += 1
        try:
            data = work()
        except (ValueError, LookupError) as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)

    def _mock_units(self) -> str:
        props = getattr(self._current_model, "properties", None) or {}
        return str(props.get("units") or "mm")

    async def list_equations(self) -> AdapterResult[dict[str, Any]]:
        """Mock listing the equations and global variables."""

        def _list() -> dict[str, Any]:
            entries = self._mock_equation_entries()
            return {
                "equations": entries,
                "count": len(entries),
                "automatic_solve_order": self._mock_solve_orders().get(
                    self._mock_doc_key(), False
                ),
                "document_units": self._mock_units(),
            }

        return await self._mock_equation_call("list_equations", _list)

    async def set_equations(
        self, equations: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock adding or replacing equations; ``__refuse__`` is refused (D9)."""
        from .solidworks import equations as eq

        given = [equations] if isinstance(equations, str) else list(equations)

        def _set() -> dict[str, Any]:
            names = eq.check_equations(given)
            texts = self._mock_equation_list()
            saved, dims = list(texts), dict(self._dimensions)
            orders = self._mock_solve_orders()
            key = self._mock_doc_key()
            was = orders.get(key, False)
            orders[key] = True
            applied = []
            for text, name in zip(given, names, strict=True):
                text = text.strip()
                if "__refuse__" in text:
                    texts[:] = saved
                    self._dimensions.clear()
                    self._dimensions.update(dims)
                    orders[key] = was
                    raise ValueError(
                        f"Invalid request: SolidWorks refused the equation {text} "
                        "(mock); every equation of this call was put back as it was."
                    )
                index = next(
                    (
                        i
                        for i, t in enumerate(texts)
                        if (eq.name_of(t) or "").casefold() == name.casefold()
                    ),
                    None,
                )
                if index is None:
                    texts.append(text)
                    index = len(texts) - 1
                else:
                    texts[index] = text
                number = self._mock_number(text)
                if "@" in name and number is not None:
                    self._dimensions[name] = number
                applied.append(
                    {"equation": text, "index": index, "replaced": index < len(saved)}
                )
            return {
                "equations": applied,
                "automatic_solve_order_was": was,
                "failing_features": self._mock_failing_features(),
                "document_units": self._mock_units(),
            }

        return await self._mock_equation_call("set_equations", _set)

    async def delete_equation(
        self, equation: str | int
    ) -> AdapterResult[dict[str, Any]]:
        """Mock deleting one equation by the name it sets or its index."""
        from .solidworks import equations as eq

        def _delete() -> dict[str, Any]:
            entries = self._mock_equation_entries()
            target = eq.find(entries, equation)
            if target is None:
                raise eq.not_found(entries, equation)
            del self._mock_equation_list()[target["index"]]
            after = self._mock_equation_entries()
            name = (target["name"] or "").casefold()
            return {
                "deleted": target["equation"],
                "count": len(after),
                "failing_features": self._mock_failing_features(),
                "left_broken": [
                    e["equation"]
                    for e in after
                    if e["broken"]
                    and name in {n.casefold() for n in eq.right_names(e["equation"])}
                ],
            }

        return await self._mock_equation_call("delete_equation", _delete)

    # ---- holes and threads (hole-wizard-and-threads, D13) -------------------

    #: Mock data, not SolidWorks' tables: the M3 values the specs mark "to
    #: confirm" (hjbaard's guide, read from SOLIDWORKS 2026). Probe 1.4 has not
    #: run; it replaces these with what the installed SolidWorks reports.
    _MOCK_HOLE_SIZES: dict[str, dict[str, Any]] = {
        "M3": {
            "clearance": {"close": 3.2, "normal": 3.4, "loose": 3.6},
            "cbore_diameter_mm": 6.5,
            "cbore_depth_mm": 3.4,
            "csink_diameter_mm": 6.72,
            "csink_angle_deg": 90.0,
            "tap_drill_diameter_mm": 2.5,
            "thread_depth_mm": 6.0,
            "coarse_pitch_mm": 0.5,
        },
    }

    def mock_add_round_edge(
        self,
        center_mm: list[float],
        diameter_mm: float,
        axis: list[float] | None = None,
    ) -> None:
        """Give the mock a circular edge, as a rod end or a hole's mouth would be.

        The mock has no geometry; ``create_thread`` looks for its edge among
        these (``create_hole_wizard_hole`` adds each hole's mouth).
        """
        self.__dict__.setdefault("_mock_round_edges", []).append(
            {
                "center_mm": [float(c) for c in center_mm],
                "diameter_mm": float(diameter_mm),
                "axis": list(axis or [0.0, 0.0, 1.0]),
            }
        )

    def _mock_round_edge_at(self, point_mm: list[float]) -> dict[str, Any] | None:
        """The registered circular edge passing within 0.01 mm of ``point_mm``."""
        from .solidworks.threads import EDGE_TOLERANCE_MM

        for edge in self.__dict__.get("_mock_round_edges", []):
            offset = [p - c for p, c in zip(point_mm, edge["center_mm"], strict=True)]
            axis = edge["axis"]
            norm = math.sqrt(sum(a * a for a in axis)) or 1.0
            along = sum(o * a for o, a in zip(offset, axis, strict=True)) / norm
            radial = math.sqrt(max(sum(o * o for o in offset) - along * along, 0.0))
            gap = math.hypot(along, radial - edge["diameter_mm"] / 2.0)
            if gap <= EDGE_TOLERANCE_MM:
                return edge
        return None

    def _mock_add_named_feature(self, name: str, kind: str, **parameters: Any) -> str:
        feature = SolidWorksFeature(
            name=name,
            type=kind,
            id=f"{kind.lower()}_{uuid.uuid4().hex[:8]}",
            parameters=parameters,
            properties={"created": datetime.now().isoformat(), "mock": True},
        )
        self._features[feature.id or feature.name] = feature
        return name

    def _mock_remove_feature(self, name: str) -> None:
        for key, feature in list(self._features.items()):
            if feature.name == name:
                del self._features[key]

    def _mock_thread(
        self,
        size: str,
        edge_point: list[float],
        length_mm: float,
        internal: bool,
        diameter_offset_mm: float | None = None,
        start_offset_mm: float | None = None,
    ) -> dict[str, Any]:
        """The live adapter's checks and payload, on the mock's own edges.

        Raises:
            ValueError: ``Invalid request``: size, length, turns, offsets or
                diameter.
        """
        from .solidworks import threads

        diameter, pitch = threads.check_request(
            size, length_mm, diameter_offset_mm, start_offset_mm
        )
        offset = threads.check_offset(diameter_offset_mm, pitch)
        start = threads.check_start(start_offset_mm, length_mm)
        edge = self._mock_round_edge_at(list(edge_point))
        needed = threads.needed_diameter(diameter, pitch, internal, offset)
        note = None
        if edge is None:
            note = (
                "mock: no edge registered at edge_point, so it is taken as an "
                f"edge of the Ø{needed:g} the thread needs"
            )
            edge = {"center_mm": list(edge_point), "diameter_mm": needed}
        threads.check_diameter(diameter, pitch, internal, edge["diameter_mm"], offset)
        count = sum(1 for f in self._features.values() if f.type == "Thread") + 1
        name = self._mock_add_named_feature(
            f"Thread{count}", "Thread", size=size, length_mm=length_mm
        )
        reply: dict[str, Any] = {
            "feature": name,
            "thread": threads.thread_block(
                diameter, pitch, length_mm, internal, offset, start
            ),
            "edge": {
                "center_mm": edge["center_mm"],
                "diameter_mm": edge["diameter_mm"],
            },
            "library": threads.INTERNAL_LIBRARY
            if internal
            else threads.EXTERNAL_LIBRARY,
            "failing_features": [],
        }
        if note:
            reply["note"] = note
        return reply

    async def create_thread(
        self,
        size: str,
        edge_point: list[float],
        length_mm: float,
        internal: bool = False,
        diameter_offset_mm: float | None = None,
        start_offset_mm: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a modeled thread: the live checks and payload, no geometry (D13)."""
        guard = self._mock_doc_error({"Part"}, "create_thread")
        if guard is not None:
            return guard
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        self._operation_count += 1
        try:
            data = self._mock_thread(
                size,
                edge_point,
                length_mm,
                internal,
                diameter_offset_mm,
                start_offset_mm,
            )
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)

    async def create_hole_wizard_hole(
        self,
        kind: str,
        size: str,
        face_point: list[float],
        side: str | None = None,
        depth_mm: float | None = None,
        fit: str | None = None,
        thread: str = "cosmetic",
    ) -> AdapterResult[dict[str, Any]]:
        """Mock a Hole Wizard hole from the mock size table (D13).

        ``__refuse_thread__`` in ``size`` refuses the modeled thread after the
        hole is made, so the cleanup path runs.
        """
        from .solidworks import holes, threads

        guard = self._mock_doc_error({"Part"}, "create_hole_wizard_hole")
        if guard is not None:
            return guard
        refuse_thread = "__refuse_thread__" in str(size)
        size = str(size).replace("__refuse_thread__", "")
        try:
            request = holes.check_hole_input(kind, size, depth_mm, fit, thread, side)
        except ValueError as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
        await asyncio.sleep(self._delays["feature_operation"] / 4)
        self._operation_count += 1
        table = self._MOCK_HOLE_SIZES.get(request["size"])
        if table is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Invalid request: SolidWorks made no {request['kind']} hole "
                    f"of size {request['size']!r}: the size isn't in its ISO "
                    "table for that kind (mock table: "
                    f"{', '.join(self._MOCK_HOLE_SIZES)})."
                ),
            )
        block: dict[str, Any] = {
            "standard": "ISO",
            "kind": request["kind"],
            "size": request["size"],
        }
        modeled = request["thread"] == "modeled"
        pitch = table["coarse_pitch_mm"]
        if request["kind"] == "tapped":
            block.update(
                {
                    "tap_drill_diameter_mm": table["tap_drill_diameter_mm"],
                    "thread_diameter_mm": request["diameter"],
                    "thread_depth_mm": table["thread_depth_mm"],
                    "cosmetic_thread": True,
                    "thread": request["thread"],
                }
            )
            bore = (
                threads.minor_diameter(request["diameter"], pitch)
                if modeled
                else table["tap_drill_diameter_mm"]
            )
        else:
            block.update(
                {
                    "fit": request["fit"],
                    "diameter_mm": table["clearance"][request["fit"]],
                }
            )
            bore = block["diameter_mm"]
            if request["kind"] == "counterbore":
                block["cbore_diameter_mm"] = table["cbore_diameter_mm"]
                block["cbore_depth_mm"] = table["cbore_depth_mm"]
            if request["kind"] == "countersink":
                block["csink_diameter_mm"] = table["csink_diameter_mm"]
                block["csink_angle_deg"] = table["csink_angle_deg"]
        title = {
            "clearance": "Clearance Hole",
            "counterbore": "CBORE for Socket Head Cap Screw",
            "countersink": "CSK for Flat Head Screw",
            "tapped": "Tapped Hole",
        }[request["kind"]]
        count = sum(1 for f in self._features.values() if f.type == "HoleWzd") + 1
        name = self._mock_add_named_feature(
            f"{request['size']} {title}{count}", "HoleWzd", size=request["size"]
        )
        reply: dict[str, Any] = {
            "feature": name,
            "hole": block,
            "position": {
                "sketch": f"{name} position",
                "dimensions": {"x": f"D1@Sketch{count}", "y": f"D2@Sketch{count}"},
                "fully_defined": True,
            },
            "face": {"center_mm": list(face_point), "normal": None},
            "failing_features": [],
            "note": "mock: sizes from a mock table, not SolidWorks' ISO tables",
        }
        if modeled:
            length = (
                table["thread_depth_mm"]
                if request["depth_mm"] is not None
                else request["depth_mm"]
            )
            if refuse_thread or not length:
                self._mock_remove_feature(name)
                reason = (
                    "the mock refuses the thread (__refuse_thread__)"
                    if refuse_thread
                    else "the mock can't tell how long a through hole is"
                )
                return AdapterResult(
                    status=AdapterResultStatus.ERROR,
                    error=(
                        f"SolidWorks made no thread for the hole: {reason}. The "
                        "hole was removed; the model is as it was."
                    ),
                )
            size_name = threads.size_name(request["diameter"], pitch)
            made = self._mock_thread(size_name, list(face_point), length, True)
            reply["thread"] = made["thread"]
            reply["thread_feature"] = made["feature"]
        self.mock_add_round_edge(list(face_point), bore)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=reply)

    async def measure_entities(
        self, entities: list[str]
    ) -> AdapterResult[dict[str, Any]]:
        """Mock measuring: nominal values with the live adapter's validation."""
        await asyncio.sleep(self._delays["model_operation"] / 2)
        guard = self._mock_doc_error({"Part", "Assembly"}, "measure_entities")
        if guard is not None:
            return guard
        if not entities or len(entities) > 2:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="measure_entities takes one or two entity specs",
            )
        self._operation_count += 1
        data: dict[str, Any] = {"selected": list(entities)}
        if len(entities) == 1:
            data.update({"area_m2": 0.0025, "perimeter_m": 0.2})
        else:
            data.update({"distance_m": 0.05, "angle_rad": 0.0, "is_parallel": True})
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)

    async def get_material(
        self, configuration: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock reading the active part material."""
        guard = self._mock_doc_error({"Part"}, "get_material")
        if guard is not None:
            return guard
        assigned = getattr(self, "_materials", {}).get(self._mock_doc_key())
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "material": assigned["name"] if assigned else None,
                "database": assigned["database"] if assigned else None,
                "density_kg_m3": (
                    self._MOCK_DENSITIES.get(assigned["name"]) if assigned else None
                ),
                "configuration": configuration,
            },
        )

    async def set_material(
        self,
        material_name: str,
        database: str | None = None,
        configuration: str | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock assigning a material; unknown names fail as they do live."""
        guard = self._mock_doc_error({"Part"}, "set_material")
        if guard is not None:
            return guard
        wanted = material_name.strip().lower()
        match = next(
            (m for m in self._MOCK_MATERIALS if m["name"].lower() == wanted), None
        )
        if match is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"Material {material_name!r} not found in "
                    f"{database or 'SOLIDWORKS Materials'!r}; use "
                    "list_available_materials for the exact name and library."
                ),
            )
        if not hasattr(self, "_materials"):
            self._materials: dict[str, dict[str, str]] = {}
        self._materials[self._mock_doc_key()] = match
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "material": match["name"],
                "database": database or "SOLIDWORKS Materials",
                "configuration": configuration,
            },
        )

    async def list_available_materials(
        self, search: str | None = None
    ) -> AdapterResult[list[dict[str, str]]]:
        """Mock listing the material library, with the live ``metadata``."""
        needle = (search or "").strip().lower()
        items = [
            dict(m)
            for m in self._MOCK_MATERIALS
            if not needle
            or needle in m["name"].lower()
            or needle in m["classification"].lower()
        ]
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=items,
            metadata={
                "folders": [self._MOCK_MATERIAL_FOLDER],
                "total": len(self._MOCK_MATERIALS),
            },
        )

    async def get_custom_properties(
        self, configuration: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock reading custom properties."""
        if self._current_model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        store = getattr(self, "_custom_properties", {})
        props = store.get((self._mock_doc_key(), configuration or ""), {})
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "configuration": configuration or "",
                "properties": {
                    k: {"value": v, "resolved": v} for k, v in props.items()
                },
            },
        )

    async def set_custom_property(
        self, name: str, value: str, configuration: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock writing one custom property."""
        if self._current_model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if not name.strip():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="name is required"
            )
        if not hasattr(self, "_custom_properties"):
            self._custom_properties: dict[tuple[str, str], dict[str, str]] = {}
        key = (self._mock_doc_key(), configuration or "")
        self._custom_properties.setdefault(key, {})[name] = str(value)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "name": name,
                "value": str(value),
                "resolved": str(value),
                "configuration": configuration or "",
            },
        )

    async def export_flat_pattern(
        self, file_path: str
    ) -> AdapterResult[dict[str, Any]]:
        """Mock flat-pattern export: validates the target, writes the smallest DXF/DWG."""
        guard = self._mock_doc_error({"Part"}, "export_flat_pattern")
        if guard is not None:
            return guard
        if not file_path.lower().endswith((".dxf", ".dwg")):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="file_path must end in .dxf or .dwg",
            )
        written = _MOCK_EXPORTS[os.path.splitext(file_path)[1].lower()]
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        with open(file_path, "wb") as handle:
            handle.write(written)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"file_path": file_path, "size_bytes": len(written), "mock": True},
        )

    async def insert_model_dimensions(
        self, view_name: str | None = None, include_hole_wizard: bool = True
    ) -> AdapterResult[dict[str, Any]]:
        """Mock importing model dimensions: three per view, never twice.

        Mirrors the live result shape (``views``, ``dimensions_added``,
        ``unique_dimensions``, ``skipped_pictorial_views``, ``method``,
        ``autodimension_status``) and its refusals.
        """
        await asyncio.sleep(self._delays["model_operation"] / 2)
        guard = self._mock_doc_error({"Drawing"}, "insert_model_dimensions")
        if guard is not None:
            return guard
        names = [view_name] if view_name else list(self._drawing_views)
        unknown = [n for n in names if n not in self._drawing_views]
        if unknown or not names:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"No view named {unknown[0]!r}. Views: "
                    f"{', '.join(self._drawing_views) or 'none'}"
                    if unknown
                    else "No drawing views found to dimension; add views with "
                    "create_technical_drawing or create_drawing_view"
                ),
            )
        if not hasattr(self, "_view_dimensions"):
            self._view_dimensions: dict[str, int] = {}
        per_view = []
        for name in names:
            before = self._view_dimensions.get(name, 0)
            after = before or 3
            self._view_dimensions[name] = after
            per_view.append(
                {
                    "view": name,
                    "dimensions_before": before,
                    "dimensions_after": after,
                    "dimensions_added": after - before,
                    "duplicates_removed": 0,
                }
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "views": per_view,
                "dimensions_added": sum(v["dimensions_added"] for v in per_view),
                "unique_dimensions": sum(self._view_dimensions.values()),
                "skipped_pictorial_views": [],
                "method": "insert_model_annotations",
                "autodimension_status": None,
            },
        )

    async def arrange_dimensions(
        self, view_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Mock arranging drawing dimensions: reports a clean layout."""
        guard = self._mock_doc_error({"Drawing"}, "arrange_dimensions")
        if guard is not None:
            return guard
        if view_name and view_name not in self._drawing_views:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"No view named {view_name!r}. Views: "
                    f"{', '.join(self._drawing_views) or 'none'}"
                ),
            )
        counts = getattr(self, "_view_dimensions", {})
        total = sum(n for v, n in counts.items() if not view_name or v == view_name)
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "dimensions": total,
                "moved": total,
                "unplaced": [],
                "overlaps_before": total,
                "overlaps_after": 0,
                "remaining_overlaps": [],
                "sheet_size_m": [0.42, 0.297],
            },
        )

    async def insert_bom_table(
        self,
        view_name: str,
        template_path: str | None = None,
        bom_type: str = "top_level_only",
        x_mm: float | None = None,
        y_mm: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock inserting a BOM table."""
        guard = self._mock_doc_error({"Drawing"}, "insert_bom_table")
        if guard is not None:
            return guard
        if bom_type not in {"top_level_only", "parts_only", "indented"}:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="bom_type must be top_level_only, parts_only or indented",
            )
        if view_name not in self._drawing_views:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"No view named {view_name!r}. Views: "
                    f"{', '.join(self._drawing_views) or 'none'}"
                ),
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "view": view_name,
                "bom_type": bom_type,
                "template": template_path,
                "row_count": 2,
                "table_box_mm": [0.0, 0.0, 60.0, 15.0],
                "moved_onto_sheet": False,
            },
        )

    async def add_gtol(
        self,
        characteristic: str,
        tolerance: float,
        x_mm: float,
        y_mm: float,
        diameter_zone: bool = False,
        material_condition: str = "none",
        datums: list[str] | None = None,
        attach_at_mm: tuple[float, float] | None = None,
        dimension: tuple[str, str] | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock adding a feature control frame; refuses what the real one refuses."""
        guard = self._mock_doc_error({"Drawing"}, "add_gtol")
        if guard is not None:
            return guard
        problem = gtol_problem(characteristic, tolerance, material_condition, datums)
        if problem is not None:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "characteristic": characteristic,
                "tolerance": f"{tolerance:g}",
                "diameter_zone": diameter_zone,
                "material_condition": material_condition,
                "datums": [d.strip().upper() for d in datums or []],
                "attached": (
                    dimension[1] if dimension is not None else attach_at_mm is not None
                ),
                "position_mm": None if dimension is not None else [x_mm, y_mm],
                "layout": None,
            },
        )

    async def add_datum_tag(
        self,
        label: str,
        attach_at_mm: tuple[float, float],
        x_mm: float,
        y_mm: float,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock adding a datum tag; refuses what the real one refuses."""
        guard = self._mock_doc_error({"Drawing"}, "add_datum_tag")
        if guard is not None:
            return guard
        letter = label.strip().upper()
        if len(letter) != 1 or not letter.isalpha():
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="label must be a single letter, e.g. 'A'",
            )
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"label": letter, "position_mm": [x_mm, y_mm]},
        )

    async def set_sheet_scale(
        self, numerator: float, denominator: float
    ) -> AdapterResult[dict[str, Any]]:
        """Mock setting the sheet scale."""
        guard = self._mock_doc_error({"Drawing"}, "set_sheet_scale")
        if guard is not None:
            return guard
        if numerator <= 0 or denominator <= 0:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Scale values must be positive"
            )
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "sheet": "Sheet1",
                "scale": f"{numerator:g}:{denominator:g}",
                "requested": f"{numerator:g}:{denominator:g}",
            },
        )

    async def create_standard_drawing(
        self,
        model_path: str,
        output_dir: str | None = None,
        title: str | None = None,
        subtitle: str | None = None,
        drawing_number: str | None = None,
        author: str = "",
        revision: str = "A",
        company: str = "",
        drawn_date: str | None = None,
        notes: list[str] | None = None,
        include_isometric: bool = True,
        write_model_properties: bool = True,
        title_block_text_height_mm: float | None = None,
        include_bom: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """Mock standard A3 drawing: validates inputs, writes nothing to disk."""
        if not os.path.isfile(model_path):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Model file not found: {model_path}",
            )
        stem = os.path.splitext(os.path.basename(model_path))[0]
        number = drawing_number or f"{stem.upper().replace(' ', '-')}-Rev{revision}-01"
        out_dir = output_dir or os.path.dirname(model_path)
        views = {
            "Drawing View1": {"role": "front", "caption": "FRONT VIEW"},
            "Drawing View2": {"role": "plan", "caption": "TOP VIEW"},
            "Drawing View3": {"role": "side", "caption": "SIDE VIEW"},
        }
        if include_isometric:
            views["Drawing View4"] = {"role": "iso", "caption": "ISOMETRIC VIEW"}
        self._operation_count += 1
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={
                "scale": "1:1",
                "isometric_scale": "1:1" if include_isometric else None,
                "views": views,
                "hidden_lines_visible": {
                    n: True for n, v in views.items() if v["role"] != "iso"
                },
                "bom": {"bom_type": "top_level_only", "table_box_mm": None}
                if include_bom
                else None,
                "overlap_check": {"overlaps_after": 0, "annotation_overlaps": []},
                "drawing_number": number,
                "drawing_path": os.path.join(out_dir, f"{number}.SLDDRW"),
                "pdf_path": os.path.join(out_dir, f"{number}.pdf"),
                "properties_written": {
                    "model": {"Description": title or stem}
                    if write_model_properties
                    else {},
                    "drawing": {"DrawnBy": author, "Revision": revision},
                },
                "notes": ["NOTES", *[n.upper() for n in (notes or [])]],
                "title_block": {
                    "drawing number": {"found": True, "fits": True},
                    "title": {"found": True, "fits": True},
                },
                "mock": True,
            },
        )
