"""ISO Hole Wizard holes (``tools/hole-wizard``): the pure checks and the COM order on a fake part."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import holes as hw
from solidworks_mcp.adapters.solidworks import rebuild_inspector, sketch_points
from solidworks_mcp.adapters.solidworks.holes import SolidWorksHolesMixin
from solidworks_mcp.tools.error_classifier import ErrorClassifier

from .fake_holes import FakeSketchPoint, FakeWizardData, block_part, hole_maker
from .fake_threads import Adapter, FakeApp, FakeEdge, FakeFace

TAP = r"C:\ProgramData\Thread Profiles\Metric Tap.sldlfp"
LIBRARY = ["M3x0.5", "M3x0.35", "M4x0.7", "M4x0.5"]


class _Harness(SolidWorksHolesMixin, Adapter):
    pass


def _kind(error: str | None) -> str:
    return ErrorClassifier().classify(error or "").error_type


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rebuild_inspector, "collect_rebuild_issues", lambda _a, _m: [])
    monkeypatch.setattr(sketch_points, "origin_point", lambda _a: FakeSketchPoint(0, 0))


def _setup(point: list[float] | None = None, **hole: Any) -> tuple[Any, _Harness]:
    part = block_part()
    part.hole = hole_maker(point or [20, 14, 10], 10, **hole)
    return part, _Harness(part, FakeApp({TAP: LIBRARY}))


# ---- pure helpers ----------------------------------------------------------


def test_value_vectors_for_each_kind() -> None:
    assert hw.wizard_values("clearance", "close", True) == [0.0] + [0.0] * 11
    assert hw.wizard_values("clearance", "loose", False) == [2.0] + [-1.0] * 11
    bore = hw.wizard_values("counterbore", "normal", True)
    assert bore[3] == 1.0 and bore.count(-1.0) == 11
    assert hw.wizard_values("countersink", None, False)[3] == 1.0
    tap = hw.wizard_values("tapped", None, False)
    assert (tap[6], tap[7]) == (1.0, 0.0) and tap.count(-1.0) == 10
    assert hw.wizard_values("tapped", None, True)[7] == 1.0
    with pytest.raises(ValueError):
        hw.wizard_values("slot", None, True)


def test_input_defaults_and_refusals() -> None:
    assert hw.check_hole_input("clearance", "M3") == {
        "kind": "clearance",
        "size": "M3",
        "diameter": 3.0,
        "depth_mm": None,
        "fit": "normal",
        "thread": None,
        "side": None,
    }
    tapped = hw.check_hole_input("tapped", "M30", 6, thread="modeled", side="+Y")
    assert (tapped["fit"], tapped["thread"], tapped["depth_mm"]) == (
        None,
        "modeled",
        6.0,
    )
    for args, words in [
        (("slot", "M3"), "kind must be one of"),
        (("clearance", "M3x0.5"), "no pitch"),
        (("clearance", "M0"), "above 0"),
        (("clearance", "M3", 0), "depth_mm must be above 0"),
        (("tapped", "M3", None, "close"), "fit is for clearance"),
        (("clearance", "M3", None, "snug"), "fit must be one of"),
        (("clearance", "M3", None, None, "fake"), "'cosmetic' or 'modeled'"),
        (("clearance", "M3", None, None, "modeled"), "is for tapped holes"),
        (("clearance", "M3", None, None, "cosmetic", "up"), "Unknown side"),
    ]:
        with pytest.raises(ValueError, match=words):
            hw.check_hole_input(*args)


def test_side_and_direction_names() -> None:
    hw.check_side(None, (0, 0, 1))
    hw.check_side("+Z", (0, 0, 1))
    with pytest.raises(ValueError, match=r"points \+Z, not '-Z'"):
        hw.check_side("-Z", (0, 0, 1))
    assert hw.direction_name((0, -1, 0)) == "-Y"
    assert hw.direction_name((1, 1, 0)) == "[0.7071, 0.7071, 0.0]"
    assert hw._unit((0, 0, 0)) == (0.0, 0.0, 0.0)


def test_in_plane_and_sketch_offsets() -> None:
    for normal in [(0, 0, 1), (1, 0, 0), (0, 1, 0)]:
        u = hw.in_plane(normal)
        assert abs(sum(a * b for a, b in zip(u, normal, strict=True))) < 1e-12
    axes = {"x": [1, 0, 0], "y": [0, 0, -1]}
    assert hw.sketch_offsets(axes, [20, 7, -5]) == (20.0, 5.0)


def test_hole_block_per_kind() -> None:
    raw = vars(FakeWizardData())
    request = hw.check_hole_input("counterbore", "M3")
    block = hw.hole_block(request, raw)
    assert block["diameter_mm"] == 3.4 and block["cbore_diameter_mm"] == 6.5
    sink = hw.hole_block(hw.check_hole_input("countersink", "M3"), raw)
    assert sink["csink_angle_deg"] == 90.0 and sink["csink_diameter_mm"] == 6.72
    tapped = hw.hole_block(hw.check_hole_input("tapped", "M3", 8), raw)
    assert tapped["tap_drill_diameter_mm"] == 2.5 and tapped["cosmetic_thread"] is True
    assert tapped["thread"] == "cosmetic"
    empty = hw.hole_block(hw.check_hole_input("clearance", "M3"), {})
    assert hw.missing_sizes(empty) == ["diameter_mm"]
    assert hw._mm(True) is None and hw._deg(-1) is None


# ---- COM order on a fake part ----------------------------------------------


@pytest.mark.asyncio
async def test_a_clearance_hole_is_made_pinned_and_read_back() -> None:
    part, adapter = _setup()

    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 10])

    assert result.is_success, result.error
    data = result.data
    assert data["hole"] == {
        "standard": "ISO",
        "kind": "clearance",
        "size": "M3",
        "fit": "normal",
        "diameter_mm": 3.4,
    }
    assert data["position"]["dimensions"] == {
        "x": "D1@Sketch3@Part1.SLDPRT",
        "y": "D2@Sketch3@Part1.SLDPRT",
    }
    assert data["position"]["fully_defined"] is True
    assert [value for _name, (value, _c) in part.dimensions] == pytest.approx(
        [0.02, 0.014]
    )
    assert data["face"] == {"center_mm": [20.0, 10.0, 10.0], "normal": [0.0, 0.0, 1.0]}
    call = part.FeatureManager.hole_calls[0]
    assert len(call) == 27
    assert call[:5] == (2, 8, 144, "M3", 1) and call[5] == -1.0
    ray = part.Extension.rays[0]
    assert ray[2] == pytest.approx(0.01001) and ray[3:6] == (-0.0, -0.0, -1.0)
    assert ray[7] == 2


@pytest.mark.asyncio
async def test_a_hole_on_the_origin_axis_gets_relations() -> None:
    part, adapter = _setup([0, 14, 10])
    part.body.faces[0].box[0] = -0.01

    result = await adapter.create_hole_wizard_hole("clearance", "M3", [0, 14, 10])

    assert result.data["position"]["dimensions"] == {
        "x": None,
        "y": "D2@Sketch3@Part1.SLDPRT",
    }
    sketch = part.features[-1].subs[0].sketch
    assert sketch.RelationManager.added == [(2, hw.VERTICAL_POINTS)]


@pytest.mark.asyncio
async def test_a_hole_at_the_origin_is_coincident() -> None:
    part = block_part()
    part.body.faces[0].box[:2] = [-0.01, -0.01]
    part.hole = hole_maker([0, 0, 10], 10)
    result = await _Harness(part, FakeApp({})).create_hole_wizard_hole(
        "clearance", "M3", [0, 0, 10]
    )

    sketch = part.features[-1].subs[0].sketch
    assert sketch.RelationManager.added == [(2, hw.COINCIDENT)]
    assert result.data["position"]["dimensions"] == {"x": None, "y": None}


@pytest.mark.asyncio
async def test_a_position_that_cannot_be_pinned_keeps_the_hole_with_a_warning() -> None:
    part, adapter = _setup(sketch=None)

    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 10])

    assert result.is_success
    assert result.data["position"]["fully_defined"] is False
    assert "could not be dimensioned" in result.data["warning"]
    assert len(part.features) == 2


@pytest.mark.asyncio
async def test_an_unknown_size_makes_nothing() -> None:
    part, adapter = _setup()

    result = await adapter.create_hole_wizard_hole("clearance", "M3.3", [20, 14, 10])

    assert _kind(result.error) == "invalid_input"
    assert "'M3.3'" in result.error and len(part.features) == 1


@pytest.mark.asyncio
async def test_a_point_on_no_face_is_not_found() -> None:
    part, adapter = _setup()
    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 30])
    assert _kind(result.error) == "not_found"
    assert part.FeatureManager.hole_calls == []


@pytest.mark.asyncio
async def test_a_curved_face_and_a_wrong_side_are_refused() -> None:
    part, adapter = _setup()
    part.body.faces.insert(0, FakeFace([21, 9, 9, 24, 11, 10], None, surface="cone"))
    cone = await adapter.create_hole_wizard_hole("clearance", "M3", [22.5, 10, 10])
    assert (
        _kind(cone.error) == "invalid_input" and "not flat (it is a cone" in cone.error
    )

    part, adapter = _setup()
    side = await adapter.create_hole_wizard_hole(
        "clearance", "M3", [20, 10, 10], side="-Z"
    )
    assert _kind(side.error) == "invalid_input" and "points +Z" in side.error
    assert part.FeatureManager.hole_calls == []


@pytest.mark.asyncio
async def test_a_ray_that_picks_two_things_changes_nothing() -> None:
    part, adapter = _setup()
    part.ray_hits = 2
    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 10])
    assert "selected 2 entities" in result.error
    assert part.FeatureManager.hole_calls == [] and part.selected == []


@pytest.mark.asyncio
async def test_sizes_that_dont_read_remove_the_hole() -> None:
    part, adapter = _setup(
        data=FakeWizardData(ThruHoleDiameter=None, HoleDiameter=None)
    )

    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 10])

    assert "gave no diameter_mm" in result.error
    assert "the model is as it was" in result.error
    assert len(part.features) == 1 and part.body.volume_mm3 == 8000.0
    assert part.Extension.deletes == [3]


@pytest.mark.asyncio
async def test_a_cleanup_that_leaves_something_says_what() -> None:
    part, adapter = _setup(
        data=FakeWizardData(ThruHoleDiameter=None, HoleDiameter=None)
    )
    part.keep_on_delete = True

    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 10])

    assert "left something behind" in result.error
    assert "features, not 1" in result.error and "not 8000.000" in result.error


def _with_bore(part: Any, diameter: float, depth_mm: float | None) -> None:
    """Make the fake hole add its mouth edge (and a far edge for through all)."""
    make = part.hole

    def drilled(p: Any, args: tuple[Any, ...]) -> Any:
        p.body.faces[0].edges.append(FakeEdge([20, 14, 10], diameter))
        if depth_mm is None:
            p.body.faces[1].edges.append(FakeEdge([20, 14, 0], diameter))
        return make(p, args)

    part.hole = drilled


@pytest.mark.asyncio
async def test_a_blind_modeled_tapped_hole() -> None:
    part, adapter = _setup()
    _with_bore(part, 2.4587, 8)

    result = await adapter.create_hole_wizard_hole(
        "tapped", "M3", [20, 14, 10], depth_mm=8, thread="modeled"
    )

    assert result.is_success, result.error
    assert result.data["thread"]["size"] == "M3x0.5"
    assert result.data["thread"]["length_mm"] == 6.0
    assert result.data["thread_feature"] == "Thread1"
    call = part.FeatureManager.hole_calls[0]
    assert call[:3] == (4, 8, 147) and call[5] == pytest.approx(0.0024587)
    assert call[6] == pytest.approx(0.008)


@pytest.mark.asyncio
async def test_a_through_modeled_hole_threads_its_whole_length() -> None:
    part, adapter = _setup()
    _with_bore(part, 3.2422, None)

    result = await adapter.create_hole_wizard_hole(
        "tapped", "M4", [20, 14, 10], thread="modeled"
    )

    assert result.is_success, result.error
    assert result.data["thread"]["size"] == "M4x0.7"
    assert result.data["thread"]["length_mm"] == 10.0


@pytest.mark.asyncio
async def test_a_refused_thread_removes_the_hole() -> None:
    part, adapter = _setup()
    _with_bore(part, 2.4587, 8)
    part.refuse_thread = True

    result = await adapter.create_hole_wizard_hole(
        "tapped", "M3", [20, 14, 10], depth_mm=8, thread="modeled"
    )

    assert not result.is_success
    assert "The hole was removed" in result.error
    assert len(part.features) == 1 and part.body.volume_mm3 == 8000.0


@pytest.mark.asyncio
async def test_a_through_length_that_doesnt_read_removes_the_hole() -> None:
    part, adapter = _setup()

    result = await adapter.create_hole_wizard_hole(
        "tapped", "M3", [20, 14, 10], thread="modeled"
    )

    assert "length of the hole" in result.error and len(part.features) == 1


@pytest.mark.asyncio
async def test_a_library_without_the_size_or_at_all() -> None:
    part, adapter = _setup()
    no_size = await adapter.create_hole_wizard_hole(
        "tapped", "M5", [20, 14, 10], thread="modeled"
    )
    assert _kind(no_size.error) == "invalid_input" and "no M5 size" in no_size.error

    part.libraries = {}
    no_library = await adapter.create_hole_wizard_hole(
        "tapped", "M3", [20, 14, 10], thread="modeled"
    )
    assert _kind(no_library.error) == "not_supported"
    assert "Metric Tap" in no_library.error and "revision 34" in no_library.error
    assert part.FeatureManager.hole_calls == []


@pytest.mark.asyncio
async def test_no_hole_wizard_is_not_supported() -> None:
    part, adapter = _setup()

    class _NoWizard:
        pass

    part.FeatureManager = _NoWizard()  # type: ignore[assignment]
    result = await adapter.create_hole_wizard_hole("clearance", "M3", [20, 14, 10])
    assert _kind(result.error) == "not_supported" and "HoleWizard5" in result.error


def test_has_member_asks_the_dispatch() -> None:
    class _Ole:
        def GetIDsOfNames(self, name: str) -> int:  # noqa: N802 - COM name
            if name != "HoleWizard5":
                raise AttributeError(name)
            return 1

    class _Dispatch:
        _oleobj_ = _Ole()

    adapter = Adapter(None)
    assert hw.has_member(adapter, _Dispatch(), "HoleWizard5")
    assert not hw.has_member(adapter, _Dispatch(), "HoleWizard6")


@pytest.mark.asyncio
async def test_bad_input_and_documents_are_refused_before_solidworks() -> None:
    part, adapter = _setup()
    assert (
        _kind(
            (
                await adapter.create_hole_wizard_hole("clearance", "M3x0.5", [0, 0, 0])
            ).error
        )
        == "invalid_input"
    )
    adapter.currentModel = None
    assert (
        "no document is open"
        in (await adapter.create_hole_wizard_hole("clearance", "M3", [0, 0, 0])).error
    )
