"""A blind cut records its floor area; a through cut and a drafted one don't."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import cut_check

from .test_features_cut_direction import _cut, _Part


class _PartWithSketch(_Part):
    """The part, whose sketch can be looked up by name."""

    def __init__(self, along: float | None = 9.05, against: float | None = None):
        super().__init__(along=along, against=against)
        self.sketch_feature = SimpleNamespace(name="Sketch3")

    def FeatureByName(self, name: str) -> Any:  # noqa: N802
        return self.sketch_feature if name == "Sketch3" else super().FeatureByName(name)


def _measured(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record each floor measurement instead of reading faces."""
    seen: list[Any] = []

    def measure(
        _adapter: Any, feature: Any, sketch_feature: Any, cut_along: bool
    ) -> float:
        seen.append((feature.Name, sketch_feature, cut_along))
        return 4.525

    monkeypatch.setattr(cut_check, "measure_floor", measure)
    return seen


@pytest.mark.parametrize(
    ("fields", "measured"),
    [
        ({}, True),
        ({"end_condition": "ThroughAll"}, False),
        ({"draft_angle": 2.0}, False),
    ],
)
async def test_only_a_straight_blind_cut_measures_its_floor(
    monkeypatch: pytest.MonkeyPatch, fields: dict[str, Any], measured: bool
) -> None:
    part = _PartWithSketch()
    seen = _measured(monkeypatch)

    _adapter, result = await _cut(part, reverse_direction=True, **fields)

    assert result.is_success, result.error
    floor = result.data.parameters["floor_area_mm2"]
    if measured:
        assert floor == 4.525
        assert seen == [("Cut-Extrude1", part.sketch_feature, True)]
    else:
        assert floor is None
        assert seen == []


async def test_the_floor_is_measured_for_the_side_that_was_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Asked along the normal, where there is no material: it cuts against it.
    part = _PartWithSketch(along=None, against=9.05)
    seen = _measured(monkeypatch)

    _adapter, result = await _cut(part, reverse_direction=True)

    assert result.is_success, result.error
    assert result.data.parameters["direction_flipped"] is True
    assert seen == [("Cut-Extrude1", part.sketch_feature, False)]


@pytest.mark.parametrize(
    ("along", "against", "fields", "toward"),
    [
        (9.05, None, {"reverse_direction": True}, "+Y"),
        (None, 9.05, {"reverse_direction": True}, "-Y"),  # flipped to the material
        (9.05, 9.05, {"end_condition": "ThroughAllBoth"}, "both ways along Y"),
    ],
)
async def test_the_cut_says_which_way_it_went_in_the_model(
    monkeypatch: pytest.MonkeyPatch,
    along: float | None,
    against: float | None,
    fields: dict[str, Any],
    toward: str,
) -> None:
    """The Dipper had to work out from the sketch normal where each cut went."""
    part = _PartWithSketch(along=along, against=against)
    _measured(monkeypatch)
    read: list[Any] = []
    monkeypatch.setattr(
        cut_check, "sketch_normal", lambda _a, f: read.append(f) or [0.0, 1.0, 0.0]
    )

    _adapter, result = await _cut(part, **fields)

    assert result.is_success, result.error
    assert result.data.parameters["cut_toward"] == toward
    assert read and read[-1] is part.sketch_feature


async def test_no_direction_is_given_without_the_sketch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from .test_features_cut_direction import _Part as _Plain

    _measured(monkeypatch)

    _adapter, result = await _cut(
        _Plain(along=9.05, against=None), reverse_direction=True
    )

    assert result.data.parameters["cut_toward"] is None
    assert result.data.parameters["cut_direction"] is None
