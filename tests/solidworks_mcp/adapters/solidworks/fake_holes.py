"""Hole Wizard pieces for the fake part of ``fake_threads``: a hole and its sketch."""

from __future__ import annotations

import math
from typing import Any

from .fake_threads import MM, FakeFace, FakeFeature, FakePart


class FakeSketchPoint:
    def __init__(self, x_m: float, y_m: float) -> None:
        self.X = x_m
        self.Y = y_m

    def Select4(self, _append: bool, _data: Any) -> bool:  # noqa: N802 - COM name
        return True


class FakeTransform:
    def __init__(self, data: list[float]) -> None:
        self.ArrayData = data

    def Inverse(self) -> FakeTransform:  # noqa: N802 - COM name
        return self


class FakeRelations:
    def __init__(self) -> None:
        self.added: list[tuple[int, int]] = []

    def AddRelation(self, entities: list[Any], kind: int) -> object:  # noqa: N802
        self.added.append((len(entities), kind))
        return object()

    def GetRelationsCount(self, _kind: int) -> int:  # noqa: N802 - COM name
        return 0


class FakeSketch:
    """A position sketch on a face whose sketch x, y are model X, Y at ``z_mm``."""

    def __init__(self, point_mm: list[float], z_mm: float, status: int = 3) -> None:
        self.points = [FakeSketchPoint(point_mm[0] * MM, point_mm[1] * MM)]
        self.ModelToSketchTransform = FakeTransform(
            [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, z_mm * MM, 1, 0, 0, 0]
        )
        self.RelationManager = FakeRelations()
        self.status = status

    def GetSketchPoints2(self) -> list[FakeSketchPoint]:  # noqa: N802 - COM name
        return self.points

    def GetSketchSegments(self) -> list[Any]:  # noqa: N802 - COM name
        return []

    def GetConstrainedStatus(self) -> int:  # noqa: N802 - COM name
        return self.status


class FakeSubFeature:
    def __init__(self, part: FakePart, sketch: Any, name: str = "Sketch3") -> None:
        self.part = part
        self.sketch = sketch
        self.Name = name

    def GetTypeName2(self) -> str:  # noqa: N802 - COM name
        return "ProfileFeature"

    def GetSpecificFeature2(self) -> Any:  # noqa: N802 - COM name
        return self.sketch

    def GetNextSubFeature(self) -> None:  # noqa: N802 - COM name
        return None

    def Select2(self, _append: bool, _mark: int) -> bool:  # noqa: N802 - COM name
        self.part.selected = [self]
        return True


class FakeWizardData:
    """``IWizardHoleFeatureData2`` with the M3 values (metres, radians)."""

    def __init__(self, **values: Any) -> None:
        defaults = {
            "ThruHoleDiameter": 0.0034,
            "HoleDiameter": 0.0034,
            "Diameter": None,
            "CounterBoreDiameter": 0.0065,
            "CounterBoreDepth": 0.0034,
            "CounterSinkDiameter": 0.00672,
            "CounterSinkAngle": math.pi / 2,
            "TapDrillDiameter": 0.0025,
            "ThreadDiameter": 0.003,
            "ThreadDepth": 0.006,
            "CosmeticThreadType": 1,
        }
        defaults.update(values)
        for name, value in defaults.items():
            setattr(self, name, value)


def hole_maker(
    point_mm: list[float],
    z_mm: float,
    data: FakeWizardData | None = None,
    sketch: Any = "position",
    status: int = 3,
) -> Any:
    """A ``part.hole`` factory: a Hole Wizard feature with a position sketch."""

    def make(part: FakePart, _args: tuple[Any, ...]) -> FakeFeature:
        count = sum(1 for f in part.features if f.kind == "HoleWzd") + 1
        feature = FakeFeature(
            f"M3 Clearance Hole{count}",
            "HoleWzd",
            part,
            definition=data or FakeWizardData(),
        )
        if sketch == "position":
            feature.subs = [FakeSubFeature(part, FakeSketch(point_mm, z_mm, status))]
        return feature

    return make


def block_part() -> FakePart:
    """A 40 x 20 x 10 block (8000 mm3) whose top face is at z = 10."""
    faces = [
        FakeFace([0, 0, 10, 40, 20, 10], [0, 0, 1]),
        FakeFace([0, 0, 0, 40, 20, 0], [0, 0, -1]),
        FakeFace([0, 0, 0, 40, 0, 10], [0, -1, 0]),
    ]
    return FakePart(faces, 8000.0)
