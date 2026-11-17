"""The mock's sketch text, linear patterns, cut-revolves and extrude options.

It refuses what the live adapter refuses, with the same messages, and never
invents a measurement: the tools read volumes from the geometry summary.
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters, RevolveParameters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def _slot(adapter: MockSolidWorksAdapter) -> str:
    await adapter.create_sketch("Top")
    await adapter.add_rectangle(0, 0, 4, 10)
    await adapter.exit_sketch()
    result = await adapter.create_extrusion(ExtrusionParameters(depth=5.0))
    return result.data.name


# ---- add_sketch_text -----------------------------------------------------------


async def test_text_needs_an_open_sketch(part: MockSolidWorksAdapter) -> None:
    assert (await part.add_sketch_text("MAX")).error == "No active sketch"


async def test_text_keeps_the_document_font_unless_one_is_asked(
    part: MockSolidWorksAdapter,
) -> None:
    await part.create_sketch("Top")

    plain = (await part.add_sketch_text("MAX", 1.0, 2.0)).data
    styled = (await part.add_sketch_text("MIN", height=4.0, font="Arial")).data

    assert plain["id"] == "Text_1"
    assert plain["position_mm"] == [1.0, 2.0]
    assert (plain["uses_document_font"], plain["format_applied"]) == (True, None)
    assert (plain["font"], plain["height_mm"]) == (None, None)
    assert (styled["font"], styled["height_mm"]) == ("Arial", 4.0)
    assert styled["format_applied"] is True


async def test_an_angled_text_follows_a_guide_line(part: MockSolidWorksAdapter) -> None:
    await part.create_sketch("Front")

    data = (await part.add_sketch_text("UP", angle=90.0)).data

    assert data["along_line"] == "TextLine_1"
    assert data["angle_deg"] == 90.0
    assert data["shares_sketch_with"] == []


# ---- pattern_linear ------------------------------------------------------------


async def test_a_pattern_needs_a_part() -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()

    result = await adapter.pattern_linear(["Slot"], "x", 2, 5.0)

    assert result.error == "No active model"


@pytest.mark.parametrize(
    ("args", "fragment"),
    [
        ((["Boss"], "x", 1, 5.0), "count of at least 2"),
        ((["Boss"], "edge:1", 2, 5.0), "an edge is given as"),
    ],
)
async def test_refusals_read_as_invalid_requests(
    part: MockSolidWorksAdapter, args: tuple, fragment: str
) -> None:
    result = await part.pattern_linear(*args)

    assert (result.error or "").startswith("Invalid request:")
    assert fragment in (result.error or "")


async def test_a_direction_never_made_is_refused(part: MockSolidWorksAdapter) -> None:
    slot = await _slot(part)

    result = await part.pattern_linear([slot], "Axis7", 2, 5.0)

    assert result.error == "Failed to select 'Axis7' as a pattern direction."


async def test_a_seed_not_in_the_part_is_refused(part: MockSolidWorksAdapter) -> None:
    await _slot(part)

    feature = await part.pattern_linear(["Nope"], "x", 2, 5.0)
    body = await part.pattern_linear(["Nope"], "x", 2, 5.0, bodies=True)

    assert "feature to pattern: Nope (list_features names them)" in (
        feature.error or ""
    )
    assert "body to pattern: Nope (list_bodies names them)" in (body.error or "")


async def test_a_row_of_a_feature(part: MockSolidWorksAdapter) -> None:
    slot = await _slot(part)

    result = await part.pattern_linear([slot], "-x", 8, 12.0, reverse_direction=False)

    assert result.is_success, result.error
    data = result.data
    assert data["name"].startswith("LPattern")
    assert data["patterned"] == [slot]
    assert data["direction"] == {
        "spec": "-x",
        "follows": "right",
        "count": 8,
        "spacing_mm": 12.0,
        "flipped": True,
    }
    assert data["direction2"] is None
    assert data["instances"] == 8


async def test_a_grid_of_bodies_along_an_axis_and_an_edge(
    part: MockSolidWorksAdapter,
) -> None:
    await part.create_axis("y")
    body = next(iter(part._mock_bodies()))

    result = await part.pattern_linear(
        [body], "Axis1", 2, 5.0, "3,0,0", 3, 4.0, False, True, True
    )

    assert result.is_success, result.error
    assert result.data["bodies"] is True
    assert result.data["direction"]["follows"] == "Axis1"
    assert result.data["direction2"]["follows"] == (3.0, 0.0, 0.0)
    assert result.data["direction2"]["flipped"] is True
    assert result.data["instances"] == 6


# ---- cut-revolve and extrude options -------------------------------------------


async def test_a_cut_revolve_is_named_as_a_cut(part: MockSolidWorksAdapter) -> None:
    await part.create_sketch("Front")
    await part.exit_sketch()

    cut = await part.create_revolve(
        RevolveParameters(angle=360.0, cut=True, thin_feature=True)
    )

    assert cut.data.name.startswith("Cut-Revolve")
    assert cut.data.type == "Cut-Revolve"
    assert cut.data.parameters["cut"] is True
    assert cut.data.parameters["thin_feature"] is False


async def test_an_extrusion_records_its_start_offset_and_draft(
    part: MockSolidWorksAdapter,
) -> None:
    await part.create_sketch("Top")
    await part.exit_sketch()

    boss = await part.create_extrusion(
        ExtrusionParameters(
            depth=5.0, start_offset=-2.0, draft_angle=3.0, draft_outward=True
        )
    )

    assert boss.data.parameters["start_offset"] == -2.0
    assert boss.data.parameters["draft_outward"] is True
