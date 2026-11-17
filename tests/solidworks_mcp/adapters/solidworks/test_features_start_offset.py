"""Start offsets, draft direction and blind mid-plane cuts on extrudes and cuts.

From the Dipper build (SOLIDWORKS 2026 SP4.1): about 32 reference planes made
only to start features at a depth, drafted cuts that could only taper inward,
and no blind cut both ways. ``FeatureExtrusion3``, ``FeatureExtrusionThin2``
and ``FeatureCut4`` take the start as ``T0`` (``swStartOffset`` = 3),
``StartOffset`` (m) and ``FlipStartOffset``, and the draft's side as
``Ddir1`` (pitfall #53: False drafted a boss inward).
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
)
from solidworks_mcp.adapters.solidworks import features

# FeatureExtrusion3 / FeatureCut4 argument positions.
_SD, _DIR, _T1, _D1, _DDIR1, _DANG1 = 0, 2, 3, 5, 9, 11
_T0, _START, _FLIP_START = -3, -2, -1  # the extrude's last three
_CUT_T0, _CUT_START, _CUT_FLIP = 23, 24, 25


class _Adapter:
    """Just enough adapter for one extrude or cut call."""

    def __init__(self, model: Any) -> None:
        self.currentModel = model
        self.constants = {
            "swEndCondBlind": 0,
            "swEndCondThroughAll": 1,
            "swEndCondMidPlane": 6,
            "swEndCondThroughAllBoth": 9,
            "swStartSketchPlane": 0,
        }
        self._last_sketch_name = "Sketch4"

    def _handle_com_operation(self, _name: str, callback: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _attempt_with_error(self, callback: Any) -> tuple[Any, Exception | None]:
        try:
            return callback(), None
        except Exception as exc:
            return None, exc

    def _get_feature_id(self, feature: Any) -> str:
        return str(feature.Name)


def _sketch() -> SimpleNamespace:
    return SimpleNamespace(
        GetTypeName2=lambda: "ProfileFeature",
        Name="Sketch4",
        GetNextFeature=lambda: None,
    )


def _model(calls: list[tuple], built: Any = "Feature1", **extra: Any) -> Any:
    """A part whose every extrude and cut call is recorded and builds ``built``."""

    def record(api: str) -> Any:
        def call(*args: Any) -> Any:
            calls.append((api, args))
            return SimpleNamespace(Name=built) if built else None

        return call

    manager = SimpleNamespace(
        **{
            api: record(api)
            for api in (
                "FeatureExtrusion3",
                "FeatureExtrusionThin2",
                "FeatureCut4",
                "FeatureCut3",
            )
        },
        **extra,
    )
    floors: list[str] = []
    return SimpleNamespace(
        FeatureManager=manager,
        FirstFeature=_sketch,
        ClearSelection2=lambda *_a: True,
        Extension=SimpleNamespace(SelectByID2=lambda *_a: True),
        FeatureByName=lambda name: floors.append(name) or SimpleNamespace(Name=name),
        floors=floors,
    )


# ---- the start condition -------------------------------------------------------


@pytest.mark.parametrize(
    ("offset", "expected"),
    [(0.0, (0, 0.0, False)), (2.5, (3, 0.0025, False)), (-4.0, (3, 0.004, True))],
)
def test_a_start_offset_is_swStartOffset_with_its_side(
    offset: float, expected: tuple
) -> None:
    t0, metres, flip = features._start_condition(_Adapter(None), offset)

    assert (t0, flip) == (expected[0], expected[2])
    assert metres == pytest.approx(expected[1])


# ---- bosses ---------------------------------------------------------------------


def test_a_boss_starts_off_its_plane_and_drafts_outward() -> None:
    calls: list[tuple] = []
    adapter = _Adapter(_model(calls))
    params = ExtrusionParameters(
        depth=10.0, start_offset=-3.0, draft_angle=2.0, draft_outward=True
    )

    result = features._create_extrusion_impl(adapter, params)

    assert result.is_success, result.error
    api, args = calls[0]
    assert api == "FeatureExtrusion3"
    assert (args[_T0], args[_FLIP_START]) == (3, True)
    assert args[_START] == pytest.approx(0.003)
    assert args[_DDIR1] is True
    assert args[_DANG1] == pytest.approx(math.radians(2.0))
    assert result.data.parameters["start_offset"] == -3.0
    assert result.data.parameters["draft_outward"] is True


def test_a_boss_left_as_before_starts_on_its_plane_drafting_inward() -> None:
    calls: list[tuple] = []

    features._create_extrusion_impl(
        _Adapter(_model(calls)), ExtrusionParameters(depth=10.0, draft_angle=3.0)
    )

    args = calls[0][1]
    assert (args[_T0], args[_START], args[_FLIP_START], args[_DDIR1]) == (
        0,
        0.0,
        False,
        False,
    )


def test_a_thin_wall_takes_the_offset_and_draft_too() -> None:
    calls: list[tuple] = []
    params = ExtrusionParameters(
        depth=5.0,
        thin_feature=True,
        thin_thickness=1.0,
        start_offset=1.5,
        draft_angle=1.0,
        draft_outward=True,
    )

    features._create_extrusion_impl(_Adapter(_model(calls)), params)

    api, args = calls[0]
    assert api == "FeatureExtrusionThin2"
    assert (args[_T0], args[_FLIP_START], args[_DDIR1]) == (3, False, True)
    assert args[_START] == pytest.approx(0.0015)


def test_the_legacy_boss_call_takes_them_as_well() -> None:
    calls: list[tuple] = []

    def missing(*_a: Any) -> Any:
        raise AttributeError("FeatureExtrusion3")

    def legacy(*args: Any) -> Any:
        calls.append(("FeatureExtrusion2", args))
        return SimpleNamespace(Name="Boss1")

    model = _model([], FeatureExtrusion2=legacy)
    model.FeatureManager.FeatureExtrusion3 = missing

    features._create_extrusion_impl(
        _Adapter(model), ExtrusionParameters(depth=5.0, start_offset=2.0)
    )

    args = calls[0][1]
    assert (args[_T0], args[_FLIP_START]) == (3, False)
    assert args[_START] == pytest.approx(0.002)


# ---- cuts -----------------------------------------------------------------------


def _cut(calls: list[tuple], built: Any = "Cut-Extrude1", **fields: Any):
    adapter = _Adapter(_model(calls, built))
    params = ExtrusionParameters(
        depth=fields.pop("depth", 4.0), sketch_name="Sketch4", **fields
    )
    return adapter, features._create_cut_extrude_impl(adapter, params)


def test_a_cut_starts_off_its_plane_and_drafts_outward() -> None:
    calls: list[tuple] = []

    _adapter, result = _cut(
        calls,
        reverse_direction=False,
        start_offset=2.0,
        draft_angle=5.0,
        draft_outward=True,
    )

    assert result.is_success, result.error
    args = calls[0][1]
    assert (args[_CUT_T0], args[_CUT_FLIP], args[_DDIR1]) == (3, False, True)
    assert args[_CUT_START] == pytest.approx(0.002)
    assert result.data.parameters["start_offset"] == 2.0
    assert result.data.parameters["draft_outward"] is True


def test_a_cut_started_off_its_plane_has_no_floor_measured() -> None:
    """A closed pocket's ceiling faces along the normal too: it would count twice."""
    calls: list[tuple] = []

    adapter, result = _cut(calls, reverse_direction=True, start_offset=-1.0)

    assert result.data.parameters["floor_area_mm2"] is None
    assert adapter.currentModel.floors == []


@pytest.mark.parametrize("spelling", ["MidPlane", "mid_plane", "Mid Plane", "midplane"])
def test_mid_plane_is_read_in_any_spelling(spelling: str) -> None:
    assert features._cut_mid_plane(SimpleNamespace(end_condition=spelling))


@pytest.mark.parametrize("other", ["Blind", "ThroughAll", None])
def test_other_end_conditions_are_not_mid_plane(other: str | None) -> None:
    assert not features._cut_mid_plane(SimpleNamespace(end_condition=other))


def test_a_mid_plane_cut_is_one_single_ended_call_with_its_depth_in_total() -> None:
    calls: list[tuple] = []

    adapter, result = _cut(calls, depth=6.0, end_condition="MidPlane")

    assert result.is_success, result.error
    assert len(calls) == 1
    args = calls[0][1]
    assert (args[_SD], args[_T1]) == (True, 6)
    assert args[_D1] == pytest.approx(0.006)
    assert result.data.parameters["direction_choice"] == "mid_plane"
    assert result.data.parameters["mid_plane"] is True
    assert result.data.parameters["direction_flipped"] is False
    assert adapter.currentModel.floors == []


def test_a_mid_plane_cut_that_builds_nothing_is_not_tried_the_other_way() -> None:
    calls: list[tuple] = []

    _adapter, result = _cut(calls, built=None, end_condition="MidPlane")

    assert not result.is_success
    assert [api for api, _args in calls] == ["FeatureCut4", "FeatureCut3"]
    assert "tried: MidPlane" in (result.error or "")


async def test_a_mid_plane_cut_has_no_side_to_choose() -> None:
    """The side-choosing cut and its volume reads are skipped."""
    calls: list[tuple] = []
    adapter = _Adapter(_model(calls))

    async def no_volume_read() -> Any:
        raise AssertionError("a mid-plane cut measures no sides")

    adapter.get_mass_properties = no_volume_read  # type: ignore[attr-defined]
    params = ExtrusionParameters(
        depth=4.0, sketch_name="Sketch4", end_condition="MidPlane"
    )

    result = await features._create_cut_extrude_choosing_side(adapter, params)

    assert result.is_success, result.error
    assert len(calls) == 1
