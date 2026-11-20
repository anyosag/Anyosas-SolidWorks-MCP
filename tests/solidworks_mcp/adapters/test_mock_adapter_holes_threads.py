"""The mock adapter's holes and threads mirror the real payloads (``hole-wizard-and-threads`` D13)."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.error_classifier import ErrorClassifier


def _kind(error: str | None) -> str:
    return ErrorClassifier().classify(error or "").error_type


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def _names(mock: MockSolidWorksAdapter) -> list[str]:
    return [f["name"] for f in (await mock.list_features()).data]


async def test_mock_diameter_check(mock: MockSolidWorksAdapter) -> None:
    mock.mock_add_round_edge([15, 15, 20], 8.5)

    refused = await mock.create_thread(
        "M10x1.5", [15 + 4.25, 15, 20], 12, internal=True
    )

    assert _kind(refused.error) == "invalid_input"
    assert "Ø8.376" in refused.error and "Ø8.500" in refused.error


async def test_a_thread_on_a_registered_edge(mock: MockSolidWorksAdapter) -> None:
    mock.mock_add_round_edge([0, 0, 40], 10.0)

    made = await mock.create_thread("M10x1.5", [5, 0, 40], 20)

    assert made.is_success, made.error
    assert set(made.data) >= {"feature", "thread", "edge", "library"}
    assert made.data["edge"] == {"center_mm": [0.0, 0.0, 40.0], "diameter_mm": 10.0}
    assert made.data["thread"]["minor_diameter_mm"] == 8.3762
    assert "note" not in made.data
    assert "Thread1" in await _names(mock)


async def test_an_unregistered_edge_is_assumed_with_a_note(mock) -> None:
    made = await mock.create_thread("M30x1", [14.85, 1.77, 0], 5.5)

    assert made.is_success and "no edge registered" in made.data["note"]
    assert made.data["edge"]["diameter_mm"] == 30.0


async def test_mock_thread_refusals(mock: MockSolidWorksAdapter) -> None:
    assert (
        _kind((await mock.create_thread("1/4-20", [0, 0, 0], 5)).error)
        == "invalid_input"
    )
    assert "over the limit" in (await mock.create_thread("M3x0.5", [0, 0, 0], 80)).error
    mock._current_model = None
    assert (await mock.create_thread("M3x0.5", [0, 0, 0], 5)).is_error


async def test_mock_clearance_counterbore_countersink(mock) -> None:
    clear = await mock.create_hole_wizard_hole(
        "clearance", "M3", [20, 10, 10], fit="close"
    )
    bore = await mock.create_hole_wizard_hole("counterbore", "M3", [10, 10, 10])
    sink = await mock.create_hole_wizard_hole("countersink", "M3", [30, 10, 10])

    assert clear.data["hole"]["diameter_mm"] == 3.2
    assert set(clear.data) >= {"feature", "hole", "position", "face"}
    assert clear.data["position"]["fully_defined"] is True
    assert bore.data["hole"]["cbore_diameter_mm"] == 6.5
    assert sink.data["hole"]["csink_angle_deg"] == 90.0
    assert (
        len(
            [n for n in await _names(mock) if "Hole" in n or "CBORE" in n or "CSK" in n]
        )
        == 3
    )


async def test_mock_modeled_tapped_hole(mock: MockSolidWorksAdapter) -> None:
    made = await mock.create_hole_wizard_hole(
        "tapped", "M3", [20, 10, 10], depth_mm=8, thread="modeled"
    )

    assert made.is_success, made.error
    assert made.data["thread"]["size"] == "M3x0.5"
    assert made.data["thread"]["length_mm"] == 6.0
    assert made.data["hole"]["thread"] == "modeled"


async def test_mock_cleanup_path(mock: MockSolidWorksAdapter) -> None:
    before = await _names(mock)

    refused = await mock.create_hole_wizard_hole(
        "tapped", "M3__refuse_thread__", [20, 10, 10], depth_mm=8, thread="modeled"
    )

    assert refused.is_error and "__refuse_thread__" in refused.error
    assert await _names(mock) == before


async def test_mock_hole_refusals(mock: MockSolidWorksAdapter) -> None:
    unknown = await mock.create_hole_wizard_hole("clearance", "M3.3", [20, 10, 10])
    assert _kind(unknown.error) == "invalid_input" and "'M3.3'" in unknown.error
    modeled = await mock.create_hole_wizard_hole(
        "clearance", "M3", [20, 10, 10], thread="modeled"
    )
    assert _kind(modeled.error) == "invalid_input"
    through = await mock.create_hole_wizard_hole(
        "tapped", "M3", [20, 10, 10], thread="modeled"
    )
    assert through.is_error and "through hole" in through.error
    mock._current_model = None
    assert (await mock.create_hole_wizard_hole("clearance", "M3", [0, 0, 0])).is_error
