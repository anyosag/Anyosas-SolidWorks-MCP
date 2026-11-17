"""``regions: "all"`` builds an extrude or a cut from every region of its sketch."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.solidworks import features, sketch_regions

from .test_features import _fake_sketch_feature, _FakeFeatureAdapter


@pytest.fixture
def picks(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []

    def select_all(_adapter: Any, name: str) -> dict[str, Any]:
        calls.append(("select", name))
        return {"kind": "regions", "found": 3, "selected": 3}

    monkeypatch.setattr(sketch_regions, "select_all", select_all)
    monkeypatch.setattr(sketch_regions, "end", lambda _a: calls.append("end"))
    return calls


def _adapter(**manager: Any) -> _FakeFeatureAdapter:
    adapter = _FakeFeatureAdapter()
    adapter.constants.update(swEndCondMidPlane=6, swEndCondUpToNext=2)
    adapter.currentModel = SimpleNamespace(
        FeatureManager=SimpleNamespace(**manager),
        ClearSelection2=lambda *_a: True,
        FirstFeature=_fake_sketch_feature("Rings"),
        Extension=SimpleNamespace(SelectByID2=lambda *_a: True),
        FeatureByName=lambda _n: None,
    )
    adapter._last_sketch_name = "Rings"
    return adapter


def test_an_extrusion_selects_every_region_of_the_last_sketch(picks) -> None:
    adapter = _adapter(FeatureExtrusion3=lambda *_a: SimpleNamespace(Name="Boss1"))

    result = features._create_extrusion_impl(
        adapter, ExtrusionParameters(depth=5, regions="all")
    )

    assert result.is_success, result.error
    assert picks == [("select", "Rings"), "end"]
    assert result.data.parameters["regions"] == "all"
    assert result.data.parameters["regions_selected"]["selected"] == 3
    assert result.data.parameters["sketch_name"] == "Rings"


def test_contour_selection_ends_when_the_extrusion_fails(picks) -> None:
    adapter = _adapter(FeatureExtrusion3=lambda *_a: None)

    result = features._create_extrusion_impl(
        adapter, ExtrusionParameters(depth=5, regions="all")
    )

    assert not result.is_success
    assert picks[-1] == "end"


def test_without_regions_nothing_is_selected_that_way(picks) -> None:
    adapter = _adapter(FeatureExtrusion3=lambda *_a: SimpleNamespace(Name="Boss1"))

    result = features._create_extrusion_impl(adapter, ExtrusionParameters(depth=5))

    assert picks == []
    assert result.data.parameters["regions"] is None


def test_a_cut_reselects_every_region_before_each_variant(picks) -> None:
    attempts: list[str] = []

    def cut4(*_a: Any) -> Any:
        attempts.append("cut4")
        return None

    def cut3(*_a: Any) -> Any:
        attempts.append("cut3")
        return SimpleNamespace(Name="Cut1")

    adapter = _adapter(FeatureCut4=cut4, FeatureCut3=cut3)

    result = features._create_cut_extrude_impl(
        adapter, ExtrusionParameters(depth=5, regions="all"), reverse=False
    )

    assert result.is_success, result.error
    assert attempts == ["cut4", "cut3"]
    assert picks == [("select", "Rings"), ("select", "Rings"), "end"]
    assert result.data.parameters["regions"] == "all"
