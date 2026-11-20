"""Modeled ISO threads (``tools/threads``): the pure checks and the COM order on a fake part."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import rebuild_inspector
from solidworks_mcp.adapters.solidworks import threads as th
from solidworks_mcp.adapters.solidworks.threads import SolidWorksThreadsMixin
from solidworks_mcp.tools.error_classifier import ErrorClassifier

from .fake_threads import Adapter, FakeApp, block_with_hole, rod_part

TAP = r"C:\ProgramData\Thread Profiles\Metric Tap.sldlfp"
DIE = r"C:\ProgramData\Thread Profiles\Metric Die.sldlfp"
LIBRARY = ["M3x0.5", "M3x0.35", "M10x1.5", "M10x1.25", "M10x1.0", "M30x1.0", "M30x3.5"]


class _Harness(SolidWorksThreadsMixin, Adapter):
    pass


def _kind(error: str | None) -> str:
    return ErrorClassifier().classify(error or "").error_type


def _adapter(part: Any, sizes: list[str] | None = None) -> _Harness:
    library = sizes if sizes is not None else LIBRARY
    return _Harness(part, FakeApp({TAP: library, DIE: library}))


@pytest.fixture(autouse=True)
def _no_rebuild_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rebuild_inspector, "collect_rebuild_issues", lambda _a, _m: [])


# ---- pure helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("size", "parsed"),
    [
        ("M10x1.5", (10.0, 1.5)),
        ("M3x0.5", (3.0, 0.5)),
        ("M2.5x0.45", (2.5, 0.45)),
        (" M30 x 1 ", (30.0, 1.0)),
    ],
)
def test_sizes_parse_to_diameter_and_pitch(size: str, parsed: tuple) -> None:
    assert th.parse_thread_size(size) == parsed


@pytest.mark.parametrize("size", ["1/4-20", "M10", "10x1.5", "M0x1", "", None])
def test_a_bad_size_shows_the_form(size: Any) -> None:
    with pytest.raises(ValueError, match="Invalid request") as raised:
        th.parse_thread_size(size)
    assert "M10x1.5" in str(raised.value) or "above 0" in str(raised.value)


def test_minor_diameter_and_names() -> None:
    assert th.minor_diameter(10, 1.5) == 8.3762
    assert th.minor_diameter(30, 1) == 28.9175
    assert th.minor_diameter(3, 0.5) == 2.4587
    assert th.size_name(30.0, 1.0) == "M30x1"
    assert th.parse_diameter("M3") == 3.0
    assert th.parse_diameter("M3x0.5") is None


def test_coarse_size_is_the_largest_pitch_the_library_holds() -> None:
    assert th.coarse_size("M10", LIBRARY) == "M10x1.5"
    assert th.coarse_size("M30x1", LIBRARY) == "M30x3.5"
    assert th.coarse_size(3.0, LIBRARY) == "M3x0.5"
    assert th.coarse_size("M4", LIBRARY) is None


def test_library_spelling_is_matched_as_numbers() -> None:
    assert th.library_size("M30x1", LIBRARY) == "M30x1.0"
    assert th.library_size("M10x1.3", LIBRARY) is None
    assert th.sizes_for(10, LIBRARY + ["junk"]) == ["M10x1.5", "M10x1.25", "M10x1.0"]


def test_turns_and_the_turn_limit() -> None:
    assert th.thread_turns(20, 1.5) == pytest.approx(13.333, abs=1e-3)
    assert th.check_request("M30x1", 6) == (30.0, 1.0)
    with pytest.raises(ValueError, match=r"has 120 turns, over the limit of 100"):
        th.check_request("M3x0.5", 60)
    with pytest.raises(ValueError, match="length_mm must be above 0"):
        th.check_request("M3x0.5", 0)
    with pytest.raises(ValueError, match="length_mm"):
        th.check_request("M3x0.5", True)


def test_library_check_lists_that_diameter() -> None:
    assert th.check_library("M10x1.5", "Metric Die", LIBRARY) == "M10x1.5"
    with pytest.raises(ValueError) as raised:
        th.check_library("M10x1.3", "Metric Die", LIBRARY)
    assert "M10x1.5, M10x1.25, M10x1.0" in str(raised.value)
    assert _kind(str(raised.value)) == "invalid_input"
    with pytest.raises(ValueError, match="no M12 size at all"):
        th.check_library("M12x1.75", "Metric Die", LIBRARY)


def test_diameter_check_names_needed_and_found() -> None:
    th.check_diameter(10, 1.5, False, 10.005)
    th.check_diameter(10, 1.5, True, 8.3762)
    with pytest.raises(ValueError) as inside:
        th.check_diameter(10, 1.5, True, 8.5)
    assert "Ø8.376" in str(inside.value) and "Ø8.500" in str(inside.value)
    assert "Drill the Ø8.376 hole first" in str(inside.value)
    with pytest.raises(ValueError) as outside:
        th.check_diameter(10, 1.5, False, 9.9)
    assert "Ø10 rod" in str(outside.value) and "Ø9.900" in str(outside.value)
    assert _kind(str(outside.value)) == "invalid_input"


def test_revision_text() -> None:
    assert th.revision_text("34.4.1") == "SOLIDWORKS 2026 (revision 34)"
    assert th.revision_text(None) == "this SOLIDWORKS (revision unknown)"


def test_thread_block() -> None:
    assert th.thread_block(10, 1.5, 20, False) == {
        "size": "M10x1.5",
        "pitch_mm": 1.5,
        "major_diameter_mm": 10,
        "minor_diameter_mm": 8.3762,
        "length_mm": 20.0,
        "internal": False,
        "right_handed": True,
    }


# ---- COM order on a fake part ----------------------------------------------


@pytest.mark.asyncio
async def test_an_external_thread_on_a_rod() -> None:
    part = rod_part()
    adapter = _adapter(part)

    result = await adapter.create_thread("M10x1.5", [5, 0, 40], 20)

    assert result.is_success, result.error
    data = result.data
    assert data["feature"] == "Thread1"
    assert data["thread"]["minor_diameter_mm"] == 8.3762
    assert data["edge"] == {"center_mm": [0.0, 0.0, 40.0], "diameter_mm": 10.0}
    assert data["library"] == DIE
    sent = part.FeatureManager.created[0]
    assert sent.initialized and sent.Size == "M10x1.5"
    assert sent.Edge is part.body.faces[0].edges[0]
    assert (sent.ThreadMethod, sent.EndCondition, sent.RightHanded) == (0, 0, True)
    assert sent.BlindDepth == pytest.approx(0.02)
    assert part.rebuilds == 1


@pytest.mark.asyncio
async def test_an_internal_thread_uses_the_tap_library_and_its_spelling() -> None:
    part = block_with_hole(28.9175)
    adapter = _adapter(part)

    result = await adapter.create_thread(
        "M30x1", [15 + 28.9175 / 2, 15, 20], 6, internal=True
    )

    assert result.is_success, result.error
    assert result.data["library"] == TAP
    assert part.FeatureManager.created[0].Size == "M30x1.0"


@pytest.mark.asyncio
async def test_a_size_not_in_the_library_changes_nothing() -> None:
    part = rod_part()
    result = await _adapter(part).create_thread("M10x1.3", [5, 0, 40], 20)

    assert _kind(result.error) == "invalid_input"
    assert "M10x1.5" in result.error
    assert part.FeatureManager.created == [] and len(part.features) == 1


@pytest.mark.asyncio
async def test_a_hole_of_the_wrong_size_is_refused() -> None:
    part = block_with_hole(8.5)
    result = await _adapter(part).create_thread(
        "M10x1.5", [15 + 4.25, 15, 20], 12, internal=True
    )

    assert _kind(result.error) == "invalid_input"
    assert "Ø8.376" in result.error and "Ø8.500" in result.error
    assert part.FeatureManager.created == []


@pytest.mark.asyncio
async def test_a_point_in_the_air_names_the_nearest_edge() -> None:
    part = rod_part()
    result = await _adapter(part).create_thread("M10x1.5", [30, 30, 40], 20)

    assert _kind(result.error) == "not_found"
    assert "radius 5.0000 mm" in result.error
    assert part.FeatureManager.created == []


@pytest.mark.asyncio
async def test_a_part_without_circles_says_so() -> None:
    part = rod_part()
    for face in part.body.faces:
        face.edges = []
    result = await _adapter(part).create_thread("M10x1.5", [5, 0, 40], 20)

    assert _kind(result.error) == "not_found"
    assert "no circular edges" in result.error


@pytest.mark.asyncio
async def test_two_coaxial_edges_one_point_picks_one() -> None:
    from .fake_threads import FakeEdge

    part = rod_part()
    chamfer = FakeEdge([0, 0, 39], 10)
    part.body.faces[1].edges.append(chamfer)
    part.body.faces[0].edges.append(chamfer)

    on_end = await _adapter(part).create_thread("M10x1.5", [5, 0, 40], 20)
    assert on_end.data["edge"]["center_mm"] == [0.0, 0.0, 40.0]
    on_chamfer = await _adapter(part).create_thread("M10x1.5", [0, 5, 39], 20)
    assert on_chamfer.data["edge"]["center_mm"] == [0.0, 0.0, 39.0]


@pytest.mark.asyncio
async def test_two_edges_at_one_point_are_listed() -> None:
    from .fake_threads import FakeEdge

    part = rod_part()
    part.body.faces[0].edges.append(FakeEdge([0, 0, 40], 10.0))

    result = await _adapter(part).create_thread("M10x1.5", [5, 0, 40], 20)

    assert _kind(result.error) == "invalid_input"
    assert "2 circular edges" in result.error


@pytest.mark.asyncio
async def test_no_thread_definition_is_not_supported() -> None:
    part = rod_part()
    part.has_thread_feature = False
    result = await _adapter(part).create_thread("M10x1.5", [5, 0, 40], 20)

    assert _kind(result.error) == "not_supported"
    assert (
        "does not support" in result.error
        and "SOLIDWORKS 2026 (revision 34)" in result.error
    )


@pytest.mark.asyncio
async def test_a_missing_library_is_not_supported() -> None:
    part = rod_part()
    part.libraries = {}
    result = await _adapter(part).create_thread("M10x1.5", [5, 0, 40], 20)

    assert _kind(result.error) == "not_supported"
    assert "Metric Die" in result.error and "Thread Profiles" in result.error


@pytest.mark.asyncio
async def test_an_empty_library_is_not_supported() -> None:
    result = await _adapter(rod_part(), sizes=[]).create_thread(
        "M10x1.5", [5, 0, 40], 20
    )

    assert _kind(result.error) == "not_supported"
    assert "lists no thread sizes" in result.error


@pytest.mark.asyncio
async def test_a_refused_create_feature_leaves_the_tree_as_it_was() -> None:
    part = rod_part()
    part.refuse_thread = True
    part.refused_leaves_feature = True

    result = await _adapter(part).create_thread("M10x1.5", [5, 0, 40], 20)

    assert _kind(result.error) == "operation_failed"
    assert "Is there 20 mm of material" in result.error
    assert [f.Name for f in part.features] == ["Boss-Extrude1"]


@pytest.mark.asyncio
async def test_the_turn_limit_is_checked_before_solidworks() -> None:
    part = rod_part()
    adapter = _adapter(part)
    adapter.currentModel = None

    result = await adapter.create_thread("M3x0.5", [1.5, 0, 40], 60)

    assert _kind(result.error) == "invalid_input"
    assert "over the limit" in result.error


@pytest.mark.asyncio
async def test_no_document_and_not_a_part() -> None:
    adapter = _adapter(None)
    assert (
        "no document is open"
        in (await adapter.create_thread("M3x0.5", [0, 0, 0], 1)).error
    )
    part = rod_part()
    part.GetType = lambda: 2  # type: ignore[method-assign]
    refused = await _adapter(part).create_thread("M3x0.5", [0, 0, 0], 1)
    assert "an assembly" in refused.error
