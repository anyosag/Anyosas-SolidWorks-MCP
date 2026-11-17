"""Every region of a sketch, selected for an extrude or a cut (``sketch_regions``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import sketch_contours as sc
from solidworks_mcp.adapters.solidworks import sketch_regions as sr

from .test_sketch_contours import _arc, _circle, _rectangle


class _Adapter:
    def __init__(self, model: Any) -> None:
        self.currentModel = model

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default


def _refusal(*segments: dict[str, Any]) -> str | None:
    described = list(segments)
    return sr.union_refusal(described, sc.contours(described))


def test_crossing_rings_alone_are_their_union() -> None:
    assert _refusal(_circle(0, 0, 18), _circle(35.5, 0, 18)) is None


def test_a_ring_inside_a_crossing_pair_would_be_filled() -> None:
    refusal = _refusal(_circle(0, 0, 18), _circle(30, 0, 18), _circle(0, 0, 5))

    assert refusal == (
        "the circle at (0, 0) r 5 is inside the circle at (0, 0) r 18, and "
        "building every region would fill it"
    )


def test_a_hole_in_a_square_is_found() -> None:
    square = _rectangle(-20, -20, 20, 20)
    refusal = _refusal(*square, _circle(30, 0, 15), _circle(0, 0, 3))

    assert refusal is not None and "inside the 4-line loop" in refusal


def test_a_point_outside_a_loop_is_outside() -> None:
    loop = sc.contours(_rectangle(0, 0, 10, 10))[0]

    assert sr._inside((5, 5), loop)
    assert not sr._inside((15, 5), loop)
    assert not sr._inside((5, -1), loop)


def test_segments_that_are_no_closed_contour_stop_the_union() -> None:
    open_line = {"type": "line", "start_mm": [50, 50], "end_mm": [60, 60]}
    construction = {**open_line, "construction": True}

    assert "not every segment" in _refusal(
        _circle(0, 0, 18), _circle(30, 0, 18), open_line
    )
    assert _refusal(_circle(0, 0, 18), _circle(30, 0, 18), construction) is None


def test_the_note_names_at_most_two_pairs() -> None:
    note = sr.note("Sketch5", ["a", "b", "c"])

    assert note.startswith("Sketch 'Sketch5': its contours cross (a; b; ...)")
    assert "regions: 'all'" in note
    assert "(a)" in sr.note("S", ["a"])


def test_the_union_check_reads_the_sketch_once() -> None:
    sketch = SimpleNamespace(
        GetSketchSegments=lambda: (_arc(0, 0, 18), _arc(35.5, 0, 18))
    )
    feature = SimpleNamespace(GetSpecificFeature2=lambda: sketch)
    model = SimpleNamespace(FeatureByName={"Rings": feature}.get)

    found = sc.union_check_in(_Adapter(model), "Rings")

    assert found == {
        "crossing": ["the circle at (0, 0) r 18 and the circle at (35.5, 0) r 18"],
        "refusal": None,
    }
    assert sc.union_check_in(_Adapter(model), "None") == {
        "crossing": [],
        "refusal": None,
    }


class _Region:
    def __init__(self, takes: str | None) -> None:
        self.takes, self.calls = takes, []

    def Select2(self, append: bool, data: Any) -> bool:  # noqa: N802
        self.calls.append(("Select2", append, getattr(data, "Mark", None)))
        return self.takes == "Select2"

    def Select(self, append: bool, mark: int) -> bool:  # noqa: N802
        self.calls.append(("Select", append, mark))
        return self.takes == "Select"


def _part(regions: list[Any], contours: list[Any] | None = None) -> Any:
    calls: list[Any] = []
    sketch = SimpleNamespace(
        GetSketchRegions=lambda: regions, GetSketchContours=lambda: contours
    )
    feature = SimpleNamespace(GetSpecificFeature2=lambda: sketch)
    sel_mgr = SimpleNamespace(
        EnableContourSelection=False, CreateSelectData=lambda: SimpleNamespace()
    )
    extension = SimpleNamespace(
        SelectByID2=lambda *args: calls.append(("SelectByID2", *args)) or True
    )
    model = SimpleNamespace(
        FeatureByName={"Rings": feature}.get,
        SelectionManager=sel_mgr,
        Extension=extension,
        ClearSelection2=lambda _all: calls.append("clear"),
        calls=calls,
    )
    return model


def test_every_region_is_appended_at_mark_four() -> None:
    regions = [_Region("Select2"), _Region("Select")]
    model = _part(regions)
    adapter = _Adapter(model)

    picked = sr.select_all(adapter, "Rings")

    assert picked == {"kind": "regions", "found": 2, "selected": 2}
    assert model.SelectionManager.EnableContourSelection is True
    assert model.calls[0] == "clear"
    assert model.calls[1][1:3] == ("Rings", "SKETCH") and model.calls[1][6:8] == (
        False,
        4,
    )
    assert regions[0].calls == [("Select2", True, 4)]
    assert regions[1].calls == [("Select2", True, 4), ("Select", True, 4)]
    sr.end(adapter)
    assert model.SelectionManager.EnableContourSelection is False


def test_without_regions_the_contours_are_selected() -> None:
    model = _part([], [_Region("Select2")])

    assert sr.select_all(_Adapter(model), "Rings")["kind"] == "contours"


def test_a_sketch_without_regions_or_contours_is_refused() -> None:
    with pytest.raises(RuntimeError, match="no closed regions"):
        sr.select_all(_Adapter(_part([], [])), "Rings")
    with pytest.raises(RuntimeError, match="gave no sketch"):
        sr.select_all(_Adapter(_part([])), "Missing")


def test_regions_none_of_which_select_are_refused() -> None:
    model = _part([_Region(None)])

    with pytest.raises(RuntimeError, match="would not select the 1 regions"):
        sr.select_all(_Adapter(model), "Rings")
    assert model.SelectionManager.EnableContourSelection is False


def test_a_raw_dispatch_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class PyIDispatch:
        pass

    raw, wrapped = PyIDispatch(), _Region("Select2")
    from solidworks_mcp.adapters.solidworks import io

    monkeypatch.setattr(io, "_as_com", lambda _a, item, _i: wrapped)
    assert sr._wrapped(object(), raw, "ISketchRegion") is wrapped
    plain = _Region(None)
    assert sr._wrapped(object(), plain, "ISketchRegion") is plain


def test_end_without_a_model_does_nothing() -> None:
    sr.end(_Adapter(None))
