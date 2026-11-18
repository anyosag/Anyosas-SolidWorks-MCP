"""Sketch patterns are real, parametric sketch patterns (the MMPLA well plate).

The well-plate build got plain copies from ``sketch_circular_pattern``: every
dimension flag of ``CreateCircularSketchStepAndRepeat`` was False and the
centre was always the origin. These pin the flags, the centre and the
dimensions the reply names.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import sketch as sketch_ops

from .test_sketch_pattern_dimensions import FakeDimension, FakeSketchFeature


def _attempt(fn: Any, default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        return default


def _handle(_name: str, fn: Any) -> AdapterResult[Any]:
    try:
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=fn())
    except Exception as exc:
        return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))


def _adapter(seed_m: tuple[float, float], added: list[FakeDimension]) -> Any:
    """An adapter whose pattern call adds ``added`` to the open sketch."""
    seed = Mock()
    seed.Select4 = Mock(return_value=True)
    seed.GetCenterPoint = Mock(return_value=seed_m)
    feature = FakeSketchFeature([FakeDimension("D1@Sketch2@P.SLDPRT", 0, 0.0012)])

    def _create(*_args: Any) -> bool:
        feature.dimensions.extend(added)
        return True

    manager = SimpleNamespace(
        CreateCircularSketchStepAndRepeat=Mock(side_effect=_create),
        CreateLinearSketchStepAndRepeat=Mock(side_effect=_create),
    )
    selection = SimpleNamespace(CreateSelectData=Mock(return_value=Mock()))
    model = SimpleNamespace(
        ClearSelection2=Mock(return_value=True),
        SelectionManager=selection,
        FeatureByName=lambda name: feature if name == "Sketch2" else None,
    )
    return SimpleNamespace(
        _sketch_entities={"Circle_1": seed},
        _sketch_entity_centers={},
        currentSketchManager=manager,
        currentModel=model,
        currentSketch=SimpleNamespace(Name="Sketch2"),
        _last_sketch_name="Sketch2",
        _handle_com_operation=_handle,
        _attempt=_attempt,
    )


_CIRCULAR_DIMS = [
    FakeDimension("D2@Sketch2@P.SLDPRT", 0, 0.02),
    FakeDimension("D3@Sketch2@P.SLDPRT", 1, math.radians(360)),
    FakeDimension("D4@Sketch2@P.SLDPRT", 2, 8.0),
]


def test_circular_pattern_asks_for_its_dimensions_and_names_them() -> None:
    adapter = _adapter((0.030, 0.0), _CIRCULAR_DIMS)

    result = sketch_ops._sketch_circular_pattern_impl(
        adapter, ["Circle_1"], 360.0, 8, 10.0, 0.0
    )

    assert result.is_success
    args = adapter.currentSketchManager.CreateCircularSketchStepAndRepeat.call_args.args
    # RadiusDim, AngleDim, CreateNumOfInstancesDim
    assert args[6:9] == (True, True, True)
    meta = result.metadata
    assert meta["parametric"] is True
    assert meta["center"] == {"x": 10.0, "y": 0.0}
    assert [(d["name"], d["role"], d["value"]) for d in meta["dimensions"]] == [
        ("D2@Sketch2", "radius", 20.0),
        ("D3@Sketch2", "angle", 360.0),
        ("D4@Sketch2", "count", 8.0),
    ]


@pytest.mark.parametrize(
    ("seed_mm", "center_mm", "radius_mm", "angle_rad"),
    [
        # Seed 20 mm right of the centre: the centre lies at pi from it.
        ((30.0, 0.0), (10.0, 0.0), 20.0, math.pi),
        # The centre up and right of a seed at the origin.
        ((0.0, 0.0), (20.0, 20.0), math.hypot(20, 20), math.pi / 4),
        # The centre straight below the seed: 3 pi / 2, never negative.
        ((5.0, 15.0), (5.0, 5.0), 10.0, 3 * math.pi / 2),
    ],
)
def test_circular_pattern_turns_about_the_centre_given(
    seed_mm: tuple[float, float],
    center_mm: tuple[float, float],
    radius_mm: float,
    angle_rad: float,
) -> None:
    adapter = _adapter((seed_mm[0] / 1000, seed_mm[1] / 1000), [])

    result = sketch_ops._sketch_circular_pattern_impl(
        adapter, ["Circle_1"], 360.0, 4, center_mm[0], center_mm[1]
    )

    assert result.is_success
    args = adapter.currentSketchManager.CreateCircularSketchStepAndRepeat.call_args.args
    assert args[0] == pytest.approx(radius_mm / 1000)
    assert args[1] == pytest.approx(angle_rad)
    assert result.metadata["radius"] == pytest.approx(radius_mm)


def test_circular_pattern_without_dimensions_makes_plain_copies() -> None:
    adapter = _adapter((0.030, 0.0), _CIRCULAR_DIMS)

    result = sketch_ops._sketch_circular_pattern_impl(
        adapter, ["Circle_1"], 180.0, 3, add_dimensions=False
    )

    args = adapter.currentSketchManager.CreateCircularSketchStepAndRepeat.call_args.args
    assert args[6:9] == (False, False, False)
    assert result.metadata["parametric"] is False
    assert result.metadata["dimensions"] == []


def test_circular_pattern_failure_carries_no_metadata() -> None:
    adapter = _adapter((0.030, 0.0), [])
    adapter.currentSketchManager.CreateCircularSketchStepAndRepeat = Mock(
        return_value=False
    )

    result = sketch_ops._sketch_circular_pattern_impl(adapter, ["Circle_1"], 360.0, 4)

    assert result.is_error
    assert not result.metadata


def test_linear_pattern_asks_for_its_spacing_and_count_dimensions() -> None:
    added = [
        FakeDimension("D2@Sketch2@P.SLDPRT", 0, 0.0045),
        FakeDimension("D3@Sketch2@P.SLDPRT", 2, 12.0),
    ]
    adapter = _adapter((0.0, 0.0), added)
    adapter._sketch_entities = {"Line_1": adapter._sketch_entities["Circle_1"]}

    result = sketch_ops._sketch_linear_pattern_impl(
        adapter, ["Line_1"], 1.0, 0.0, 4.5, 12
    )

    args = adapter.currentSketchManager.CreateLinearSketchStepAndRepeat.call_args.args
    # XSpacingDim, YSpacingDim, AngleDim, NumX dim, NumY dim
    assert args[7:12] == (True, False, False, True, False)
    assert [(d["role"], d["value"]) for d in result.metadata["dimensions"]] == [
        ("spacing", 4.5),
        ("count", 12.0),
    ]


def test_linear_pattern_without_dimensions() -> None:
    adapter = _adapter((0.0, 0.0), [])
    adapter._sketch_entities = {"Line_1": adapter._sketch_entities["Circle_1"]}

    result = sketch_ops._sketch_linear_pattern_impl(
        adapter, ["Line_1"], 0.0, 1.0, 4.5, 3, add_dimensions=False
    )

    args = adapter.currentSketchManager.CreateLinearSketchStepAndRepeat.call_args.args
    assert args[7:12] == (False, False, False, False, False)
    assert result.metadata == {"parametric": False, "dimensions": []}


@pytest.mark.asyncio
async def test_the_mixin_forwards_the_centre_and_the_flag() -> None:
    from solidworks_mcp.adapters.solidworks.sketch import SolidWorksSketchMixin

    adapter = _adapter((0.030, 0.0), [])
    adapter.sketch_circular_pattern = (
        SolidWorksSketchMixin.sketch_circular_pattern.__get__(adapter)
    )
    adapter.sketch_linear_pattern = SolidWorksSketchMixin.sketch_linear_pattern.__get__(
        adapter
    )
    adapter._sketch_entities["Line_1"] = adapter._sketch_entities["Circle_1"]

    circular = await adapter.sketch_circular_pattern(
        ["Circle_1"], 360.0, 4, 0.0, 30.0, False
    )
    linear = await adapter.sketch_linear_pattern(["Line_1"], 1.0, 0.0, 5.0, 3, False)

    assert circular.metadata["center"] == {"x": 0.0, "y": 30.0}
    assert circular.metadata["parametric"] is False
    assert linear.metadata["parametric"] is False
