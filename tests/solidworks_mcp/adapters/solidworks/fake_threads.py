"""A fake part for the thread and Hole Wizard code: circles, flat faces, features.

Geometry is in metres, as SolidWorks gives it. A part is built from
:class:`FakeFace` s (each with its circular :class:`FakeEdge` s) on one
:class:`FakeBody`. The feature manager makes thread definitions
(``CreateDefinition(87)``), thread features (``CreateFeature``) and Hole
Wizard holes (``HoleWizard5``); each changes the body's volume by a set
amount, so the cleanup checks of D10 can be driven.
"""

from __future__ import annotations

import math
from typing import Any

from .fake_equations import FakeAdapter

MM = 1e-3


def _sub(a: Any, b: Any) -> list[float]:
    return [float(x) - float(y) for x, y in zip(a, b, strict=True)]


def _dot(a: Any, b: Any) -> float:
    return float(sum(float(x) * float(y) for x, y in zip(a, b, strict=True)))


class FakeCurve:
    def __init__(self, center: list[float], axis: list[float], radius: float) -> None:
        self.CircleParams = [*center, *axis, radius]

    def IsCircle(self) -> bool:  # noqa: N802 - COM name
        return True

    def IsLine(self) -> bool:  # noqa: N802 - COM name
        return False


class FakeEdge:
    """A full circle: ``center`` and ``radius`` in metres, ``axis`` a unit vector."""

    def __init__(self, center_mm: list[float], diameter_mm: float, axis=(0, 0, 1)):
        self.center = [c * MM for c in center_mm]
        self.radius = diameter_mm * MM / 2.0
        self.axis = list(axis)

    def GetCurve(self) -> FakeCurve:  # noqa: N802 - COM name
        return FakeCurve(self.center, self.axis, self.radius)

    def GetStartVertex(self) -> None:  # noqa: N802 - COM name
        return None

    def GetEndVertex(self) -> None:  # noqa: N802 - COM name
        return None

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        offset = _sub([x, y, z], self.center)
        along = _dot(offset, self.axis)
        radial = [o - along * a for o, a in zip(offset, self.axis, strict=True)]
        length = math.sqrt(_dot(radial, radial))
        if length == 0:
            radial, length = _perpendicular(self.axis), 1.0
        return [
            c + r / length * self.radius
            for c, r in zip(self.center, radial, strict=True)
        ]


def _perpendicular(axis: list[float]) -> list[float]:
    return [1.0, 0.0, 0.0] if abs(axis[0]) < 0.9 else [0.0, 1.0, 0.0]


class FakeSurface:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    def IsCylinder(self) -> bool:  # noqa: N802 - COM name
        return self.kind == "cylinder"

    def IsCone(self) -> bool:  # noqa: N802 - COM name
        return self.kind == "cone"

    def IsSphere(self) -> bool:  # noqa: N802 - COM name
        return False

    def IsTorus(self) -> bool:  # noqa: N802 - COM name
        return False


class FakeFace:
    """A rectangle in a plane (``normal``) or a curved face, with edges."""

    def __init__(
        self,
        box_mm: list[float],
        normal: list[float] | None = None,
        edges: list[FakeEdge] | None = None,
        surface: str = "plane",
    ) -> None:
        self.box = [c * MM for c in box_mm]
        self.Normal = list(normal) if normal is not None else [0.0, 0.0, 0.0]
        self.edges = list(edges or [])
        self.surface = surface

    def GetBox(self) -> list[float]:  # noqa: N802 - COM name
        return list(self.box)

    def GetArea(self) -> float:  # noqa: N802 - COM name
        return 1.0

    def GetEdges(self) -> list[FakeEdge]:  # noqa: N802 - COM name
        return self.edges

    def GetSurface(self) -> FakeSurface:  # noqa: N802 - COM name
        return FakeSurface(self.surface)

    def GetClosestPointOn(self, x: float, y: float, z: float) -> list[float]:  # noqa: N802
        b = self.box
        return [min(max(v, b[i]), b[i + 3]) for i, v in enumerate((x, y, z))]


class FakeBody:
    def __init__(self, faces: list[FakeFace], volume_mm3: float = 8000.0) -> None:
        self.faces = faces
        self.volume_mm3 = volume_mm3
        self.Name = "Body1"

    def GetFaces(self) -> list[FakeFace]:  # noqa: N802 - COM name
        return self.faces

    def GetEdges(self) -> list[FakeEdge]:  # noqa: N802 - COM name
        seen: list[FakeEdge] = []
        for face in self.faces:
            seen += [e for e in face.edges if all(e is not s for s in seen)]
        return seen

    def GetBodyBox(self) -> list[float]:  # noqa: N802 - COM name
        boxes = [f.box for f in self.faces]
        return [min(b[i] for b in boxes) for i in range(3)] + [
            max(b[i] for b in boxes) for i in range(3, 6)
        ]

    def GetMassProperties(self, _accuracy: float) -> list[float]:  # noqa: N802
        return [0.0, 0.0, 0.0, self.volume_mm3 * 1e-9, 0.0, 0.0]


class FakeThreadData:
    """``IThreadFeatureData``: properties, and the library ``Type`` resolves to."""

    def __init__(self, libraries: dict[str, str]) -> None:
        self._libraries = libraries
        self._type = ""
        self.initialized = False
        self.Edge = None
        self.Size = None
        self.ThreadMethod = None
        self.EndCondition = None
        self.BlindDepth = None
        self.RightHanded = None
        self.DiameterOverride = False
        self.Diameter = None

    def InitializeThreadData(self) -> None:  # noqa: N802 - COM name
        self.initialized = True

    @property
    def Type(self) -> str:  # noqa: N802 - COM name
        return self._type

    @Type.setter
    def Type(self, name: str) -> None:  # noqa: N802 - COM name
        self._type = self._libraries.get(name, "")


class FakeFeature:
    def __init__(self, name: str, kind: str, part: FakePart, **extra: Any) -> None:
        self.Name = name
        self.kind = kind
        self.part = part
        self.selected = False
        self.extra = extra
        self.subs: list[Any] = extra.get("subs", [])

    def Select2(self, _append: bool, _mark: int) -> bool:  # noqa: N802 - COM name
        self.part.selected = [self]
        return True

    def GetDefinition(self) -> Any:  # noqa: N802 - COM name
        return self.extra.get("definition")

    def GetFirstSubFeature(self) -> Any:  # noqa: N802 - COM name
        return self.subs[0] if self.subs else None

    def GetTypeName2(self) -> str:  # noqa: N802 - COM name
        return self.kind

    def GetFaces(self) -> list[Any]:  # noqa: N802 - COM name
        return self.extra.get("faces", [])


def _thread_faces(data: FakeThreadData) -> list[FakeFace]:
    """One face spanning ``BlindDepth`` from the edge, toward -axis (into a rod's end)."""
    edge = data.Edge
    if edge is None or data.BlindDepth is None:
        return []
    c = [v / MM for v in edge.center]
    r, depth = edge.radius / MM, float(data.BlindDepth) / MM
    end = [ci - depth * a for ci, a in zip(c, edge.axis, strict=True)]
    sides = list(zip(c, end, edge.axis, strict=True))
    low = [min(a, b) - (r if ax == 0 else 0) for a, b, ax in sides]
    high = [max(a, b) + (r if ax == 0 else 0) for a, b, ax in sides]
    return [FakeFace(low + high, surface="helix")]


class FakeFeatureManager:
    def __init__(self, part: FakePart) -> None:
        self.part = part
        self.definitions: list[FakeThreadData] = []
        self.created: list[Any] = []
        self.hole_calls: list[tuple[Any, ...]] = []

    def CreateDefinition(self, kind: int) -> Any:  # noqa: N802 - COM name
        if kind != 87 or not self.part.has_thread_feature:
            return None
        data = FakeThreadData(self.part.libraries)
        self.definitions.append(data)
        return data

    def CreateFeature(self, data: FakeThreadData) -> Any:  # noqa: N802 - COM name
        self.created.append(data)
        if self.part.refuse_thread:
            if self.part.refused_leaves_feature:
                self.part.add_feature(
                    FakeFeature("Thread-failed", "Thread", self.part), 0
                )
            return None
        count = sum(1 for f in self.part.features if f.kind == "Thread") + 1
        return self.part.add_feature(
            FakeFeature(
                f"Thread{count}", "Thread", self.part, faces=_thread_faces(data)
            ),
            self.part.thread_volume,
        )

    def GetFeatureCount(self, _top_only: bool) -> int:  # noqa: N802 - COM name
        return len(self.part.features)

    def HoleWizard5(self, *args: Any) -> Any:  # noqa: N802 - COM name
        self.hole_calls.append(args)
        if self.part.hole is None or args[3] not in self.part.hole_sizes:
            return None
        return self.part.add_feature(
            self.part.hole(self.part, args), self.part.hole_volume
        )


class FakeSelectionManager:
    def __init__(self, part: FakePart) -> None:
        self.part = part

    def GetSelectedObjectCount2(self, _mark: int) -> int:  # noqa: N802 - COM name
        return len(self.part.selected)

    def CreateSelectData(self) -> object:  # noqa: N802 - COM name
        return object()


class FakeExtension:
    def __init__(self, part: FakePart) -> None:
        self.part = part
        self.rays: list[tuple[Any, ...]] = []
        self.deletes: list[int] = []

    def SelectByRay(self, *args: Any) -> bool:  # noqa: N802 - COM name
        self.rays.append(args)
        self.part.selected = ["face"] * self.part.ray_hits
        return self.part.ray_hits > 0

    def DeleteSelection2(self, options: int) -> bool:  # noqa: N802 - COM name
        """Delete the selected features; with children, every feature after them."""
        self.deletes.append(options)
        for feature in self.part.selected:
            if feature not in self.part.features:
                continue
            at = self.part.features.index(feature)
            doomed = self.part.features[at:] if options & 1 else [feature]
            for gone in reversed(doomed):
                if not self.part.keep_on_delete:
                    self.part.remove_feature(gone)
        self.part.selected = []
        return True


class FakeApp:
    def __init__(self, sizes: dict[str, list[str]], revision: str = "34.4.1") -> None:
        self.sizes = sizes
        self.RevisionNumber = revision

    def GetConfigurationNames(self, file: str) -> list[str]:  # noqa: N802 - COM name
        return list(self.sizes.get(file, []))


class FakePart:
    """A part: one body of faces, a feature list and the managers on it."""

    def __init__(self, faces: list[FakeFace], volume_mm3: float = 8000.0) -> None:
        self.body = FakeBody(faces, volume_mm3)
        self.features: list[Any] = [FakeFeature("Boss-Extrude1", "Extrusion", self)]
        self.volumes: dict[int, float] = {}
        self.selected: list[Any] = []
        self.has_thread_feature = True
        self.libraries = {
            "Metric Tap": r"C:\ProgramData\Thread Profiles\Metric Tap.sldlfp",
            "Metric Die": r"C:\ProgramData\Thread Profiles\Metric Die.sldlfp",
        }
        self.refuse_thread = False
        self.refused_leaves_feature = False
        self.thread_volume = -268.0
        self.hole: Any = None
        self.hole_sizes = {"M3", "M4"}
        self.hole_volume = -90.792
        self.ray_hits = 1
        self.keep_on_delete = False
        self.rebuilds = 0
        self.stamp = 1
        self.edited: list[Any] = []
        self.FeatureManager = FakeFeatureManager(self)
        self.SelectionManager = FakeSelectionManager(self)
        self.Extension = FakeExtension(self)
        self.SketchManager = FakeSketchManager(self)
        self.dimensions: list[tuple[str, tuple[float, ...]]] = []

    def add_feature(self, feature: Any, volume_change: float) -> Any:
        self.features.append(feature)
        self.volumes[id(feature)] = volume_change
        self.body.volume_mm3 += volume_change
        self.stamp += 1
        return feature

    def remove_feature(self, feature: Any) -> None:
        if feature in self.features:
            self.features.remove(feature)
            self.body.volume_mm3 -= self.volumes.pop(id(feature), 0.0)
            self.stamp += 1

    def GetType(self) -> int:  # noqa: N802 - COM name
        return 1

    def GetBodies2(self, _kind: int, _visible_only: bool) -> list[FakeBody]:  # noqa: N802
        return [self.body]

    def GetUpdateStamp(self) -> int:  # noqa: N802 - COM name
        return self.stamp

    def ForceRebuild3(self, _top_only: bool) -> bool:  # noqa: N802 - COM name
        self.rebuilds += 1
        return True

    def ClearSelection2(self, _all: bool) -> bool:  # noqa: N802 - COM name
        self.selected = []
        return True

    def FeatureByPositionReverse(self, position: int) -> Any:  # noqa: N802
        return self.features[-1 - position]

    def EditSketch(self) -> None:  # noqa: N802 - COM name
        self.edited.append(self.selected[0] if self.selected else None)

    def AddHorizontalDimension2(self, *at: float) -> Any:  # noqa: N802 - COM name
        return FakeDisplayDimension(self, "D1")

    def AddVerticalDimension2(self, *at: float) -> Any:  # noqa: N802 - COM name
        return FakeDisplayDimension(self, "D2")


class FakeSketchManager:
    def __init__(self, part: FakePart) -> None:
        self.part = part
        self.closed = 0

    def InsertSketch(self, _rebuild: bool) -> None:  # noqa: N802 - COM name
        self.closed += 1


class FakeDimension:
    def __init__(self, part: FakePart, name: str) -> None:
        self.part = part
        self.FullName = f"{name}@Sketch3@Part1.SLDPRT"
        self.value: float | None = None

    def SetSystemValue3(self, value: float, which: int, names: Any) -> int:  # noqa: N802
        self.value = value
        self.part.dimensions.append((self.FullName, (value, which)))
        return 0


class FakeDisplayDimension:
    def __init__(self, part: FakePart, name: str) -> None:
        self.dimension = FakeDimension(part, name)

    def GetDimension2(self, _index: int) -> FakeDimension:  # noqa: N802 - COM name
        return self.dimension


def rod_part(diameter_mm: float = 10.0, length_mm: float = 40.0) -> FakePart:
    """A rod standing on the XY plane: its end edge at z = length."""
    r = diameter_mm / 2.0
    end = FakeEdge([0, 0, length_mm], diameter_mm)
    base = FakeEdge([0, 0, 0], diameter_mm)
    faces = [
        FakeFace([-r, -r, length_mm, r, r, length_mm], [0, 0, 1], [end]),
        FakeFace([-r, -r, 0, r, r, length_mm], None, [end, base], "cylinder"),
        FakeFace([-r, -r, 0, r, r, 0], [0, 0, -1], [base]),
    ]
    return FakePart(faces, math.pi * r * r * length_mm)


def block_with_hole(hole_diameter_mm: float) -> FakePart:
    """A 30 x 30 x 20 block with a hole through it at (15, 15)."""
    top_edge = FakeEdge([15, 15, 20], hole_diameter_mm)
    bottom_edge = FakeEdge([15, 15, 0], hole_diameter_mm)
    faces = [
        FakeFace([0, 0, 20, 30, 30, 20], [0, 0, 1], [top_edge]),
        FakeFace([0, 0, 0, 30, 30, 0], [0, 0, -1], [bottom_edge]),
        FakeFace([13, 13, 0, 17, 17, 20], None, [top_edge, bottom_edge], "cylinder"),
    ]
    return FakePart(faces, 18000.0)


class Adapter(FakeAdapter):
    """``FakeAdapter`` with an ``swApp`` and the edge index slot."""

    def __init__(self, model: Any, app: FakeApp | None = None) -> None:
        super().__init__(model)
        self.swApp = app or FakeApp({})
        self._edge_index = None
