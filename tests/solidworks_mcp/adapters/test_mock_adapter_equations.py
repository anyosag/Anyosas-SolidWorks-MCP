"""The mock adapter's equations mirror the real payloads (``parametric-equations`` D9)."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.adapters.solidworks.rebuild_diagnostics import RawRebuildIssue
from solidworks_mcp.tools.error_classifier import ErrorClassifier


def _kind(error: str | None) -> str:
    return ErrorClassifier().classify(error or "").error_type


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def test_mock_restore_path(mock: MockSolidWorksAdapter) -> None:
    refused = await mock.set_equations(['"A" = 1', '"B" = __refuse__'])
    listed = await mock.list_equations()

    assert refused.is_error
    assert '"B" = __refuse__' in (refused.error or "")
    assert _kind(refused.error) == "invalid_input"
    assert listed.data["count"] == 0
    assert listed.data["automatic_solve_order"] is False


async def test_a_plain_number_sets_the_dimension(mock: MockSolidWorksAdapter) -> None:
    done = await mock.set_equations(['"W" = 45', '"D1@Sketch1" = 30mm'])
    again = await mock.set_equations(['"w" = 50'])
    listed = (await mock.list_equations()).data

    assert done.data["equations"] == [
        {"equation": '"W" = 45', "index": 0, "replaced": False},
        {"equation": '"D1@Sketch1" = 30mm', "index": 1, "replaced": False},
    ]
    assert done.data["automatic_solve_order_was"] is False
    assert again.data["equations"][0] == {
        "equation": '"w" = 50',
        "index": 0,
        "replaced": True,
    }
    assert again.data["automatic_solve_order_was"] is True
    assert mock._dimensions["D1@Sketch1"] == 30.0
    assert listed["count"] == 2
    assert listed["document_units"] == "mm"
    assert [(e["name"], e["value"], e["global"]) for e in listed["equations"]] == [
        ("w", 50.0, True),
        ("D1@Sketch1", 30.0, False),
    ]


async def test_an_expression_has_no_value(mock: MockSolidWorksAdapter) -> None:
    await mock.set_equations(['"W" = 45', '"D1@Sketch1" = "W" * 2'])
    entries = (await mock.list_equations()).data["equations"]

    assert entries[1]["value"] is None
    assert entries[1]["broken"] is False
    assert mock._dimensions["D1@Sketch1"] == 10.0


async def test_broken_and_deleted(mock: MockSolidWorksAdapter) -> None:
    await mock.set_equations(['"W" = 1', '"D1@Boss-Extrude1" = 2 * "D1@Sketch9"'])
    entries = (await mock.list_equations()).data["equations"]

    assert [e["broken"] for e in entries] == [False, True]

    done = await mock.delete_equation(1)
    gone = await mock.delete_equation("Q")

    assert done.data == {
        "deleted": '"D1@Boss-Extrude1" = 2 * "D1@Sketch9"',
        "count": 1,
        "failing_features": [],
        "left_broken": [],
    }
    assert _kind(gone.error) == "not_found"
    assert '0: "W" = 1' in (gone.error or "")


async def test_deleting_a_variable_in_use(mock: MockSolidWorksAdapter) -> None:
    await mock.set_equations(['"W" = 1', '"D1@Sketch1" = "W"'])

    done = await mock.delete_equation('"W"')

    assert done.data["left_broken"] == ['"D1@Sketch1" = "W"']


async def test_failing_features_come_from_the_rebuild(
    mock: MockSolidWorksAdapter,
) -> None:
    mock.injected_rebuild_issues.append(
        RawRebuildIssue(feature="Cut-Extrude1", type="ICE", code=2, is_warning=False)
    )

    done = await mock.set_equations(['"D1@Boss-Extrude1" = 500'])

    assert [f["feature"] for f in done.data["failing_features"]] == ["Cut-Extrude1"]


async def test_driven_by_and_the_set_dimension_refusal(
    mock: MockSolidWorksAdapter,
) -> None:
    await mock.set_equations(['"W" = 45', '"D1@Sketch1" = "W"'])

    dims = {d["full_name"]: d for d in (await mock.list_model_dimensions()).data}
    refused = await mock.set_dimension("D1@Sketch1", 30)
    free = await mock.set_dimension("D2@Sketch1", 30)

    assert dims["D1@Sketch1"]["driven_by"] == '"D1@Sketch1" = "W"'
    assert dims["D2@Sketch1"]["driven_by"] is None
    assert refused.is_error
    assert '`"D1@Sketch1" = "W"`' in (refused.error or "")
    assert "set_equations" in (refused.error or "")
    assert _kind(refused.error) == "invalid_input"
    assert mock._dimensions["D1@Sketch1"] == 10.0
    assert free.is_success


async def test_no_part_is_invalid_input() -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()

    result = await adapter.list_equations()
    await adapter.create_drawing()
    drawing = await adapter.set_equations(['"W" = 1'])

    assert "no document is open" in (result.error or "")
    assert _kind(result.error) == "invalid_input"
    assert "drawing" in (drawing.error or "")
    assert _kind(drawing.error) == "invalid_input"


async def test_bad_left_sides_change_nothing(mock: MockSolidWorksAdapter) -> None:
    missing = await mock.set_equations(["W = 40"])
    twice = await mock.set_equations(['"W" = 40', '"W" = 41'])
    single = await mock.set_equations('"H" = 2')  # type: ignore[arg-type]

    assert '"W" = 40' in (missing.error or "")
    assert _kind(twice.error) == "invalid_input"
    assert single.data["equations"][0]["equation"] == '"H" = 2'
    assert (await mock.list_equations()).data["count"] == 1
