"""The mock's multibody methods: bodies as boxes, with the real adapter's refusals."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    adapter.part_bodies_mm = {
        "Lid": [0.0, 0.0, 0.0, 40.0, 10.0, 40.0],
        "Label1": [5.0, 9.0, 5.0, 15.0, 11.0, 8.0],
        "Label2": [20.0, 9.0, 5.0, 30.0, 11.0, 8.0],
    }
    await adapter.create_part()
    return adapter


async def _names(adapter: MockSolidWorksAdapter) -> list[str]:
    return [b["name"] for b in (await adapter.list_bodies()).data["bodies"]]


async def test_a_new_part_starts_with_the_canned_body() -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()

    result = await adapter.list_bodies()

    assert result.data["count"] == 1
    assert result.data["bodies"][0] == {
        "name": "Boss-Extrude1",
        "volume_mm3": 16000.0,
        "surface_area_mm2": 4800.0,
        "center_of_mass_mm": [20.0, 5.0, 20.0],
        "box_mm": [0.0, 0.0, 0.0, 40.0, 10.0, 40.0],
        "size_mm": [40.0, 10.0, 40.0],
        "face_count": 6,
        "visible": True,
    }


async def test_hidden_bodies_are_left_out_on_request(
    part: MockSolidWorksAdapter,
) -> None:
    part._mock_bodies()["Label2"]["visible"] = False

    result = await part.list_bodies(visible_only=True)

    assert [b["name"] for b in result.data["bodies"]] == ["Lid", "Label1"]


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("list_bodies", ()),
        ("insert_part", ("C:/h.sldprt",)),
        ("combine_bodies", ("add", "A", ["B"])),
        ("move_copy_body", (["A"], [1, 0, 0])),
        ("delete_keep_body", (["A"],)),
        ("split_body", ("Top",)),
        ("body_clearance", ("A", "B")),
        ("make_mating_negative", ("A", ["B"], 0.1)),
    ],
)
async def test_every_method_needs_a_part(method: str, args: tuple[Any, ...]) -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()

    assert (await getattr(adapter, method)(*args)).error == "No active model"

    await adapter.create_assembly()
    result = await getattr(adapter, method)(*args)

    assert result.error == (
        f"{method} needs an active part document (active document is assembly)."
    )


# ---- insert_part -----------------------------------------------------------------


async def test_a_part_comes_in_as_a_cube_by_default(
    part: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    holder = tmp_path / "holder.sldprt"
    holder.write_text("")

    result = await part.insert_part(str(holder), translate_mm=[0, 5, 0])

    assert result.data["feature"] == "holder-1"
    assert result.data["bodies_added"] == ["holder-1"]
    assert result.data["bodies"][0]["box_mm"] == [0.0, 5.0, 0.0, 10.0, 15.0, 10.0]
    assert result.data["move_features"] == ["Body-Move/Copy1"]


async def test_a_part_brings_its_bodies_with_new_names_when_taken(
    part: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    holder = tmp_path / "holder.sldprt"
    holder.write_text("")
    part.inserted_part_bodies_mm["holder"] = {
        "Label1": [0, 0, 0, 1, 1, 1],
        "Pin": [0, 0, 0, 1, 1, 1],
    }

    result = await part.insert_part(str(holder), rotate_deg=90, axis="z")

    assert result.data["bodies_added"] == ["Label1[2]", "Pin"]
    assert result.data["bodies"][1]["box_mm"] == [-1.0, 0.0, 0.0, 0.0, 1.0, 1.0]
    assert result.data["move_features"] == ["Body-Move/Copy1"]


async def test_only_an_existing_part_file_goes_in(
    part: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    assert (await part.insert_part(str(tmp_path / "a.sldasm"))).error.startswith(
        "Invalid request:"
    )
    missing = await part.insert_part(str(tmp_path / "gone.sldprt"))
    assert missing.error.startswith("Part file not found:")


# ---- combine_bodies --------------------------------------------------------------


async def test_subtract_takes_the_overlap_off_the_main_body(
    part: MockSolidWorksAdapter,
) -> None:
    result = await part.combine_bodies("subtract", "Lid", ["Label1"])

    data = result.data
    assert data["feature"] == "Combine1"
    assert data["form"] == "preselected (main body at mark 1)"
    assert data["main_volume_before_mm3"] == 16000.0
    assert data["result_volume_mm3"] == 16000.0 - 30.0
    assert data["bodies_after"] == ["Lid", "Label2"]
    assert data["result"][0]["face_count"] == 12


async def test_add_boxes_them_together(part: MockSolidWorksAdapter) -> None:
    result = await part.combine_bodies("add", "Label1", ["Label2"])

    assert result.data["form"] == "preselected (main body at mark 2)"
    assert result.data["result"][0]["box_mm"] == [5.0, 9.0, 5.0, 30.0, 11.0, 8.0]
    assert result.data["result_volume_mm3"] == 120.0


async def test_common_keeps_the_shared_box(part: MockSolidWorksAdapter) -> None:
    nothing = await part.combine_bodies("common", "Label1", ["Label2"])
    assert nothing.error.endswith("the bodies don't overlap.")

    result = await part.combine_bodies("common", "Lid", ["Label1"])

    assert result.data["result"][0]["box_mm"] == [5.0, 9.0, 5.0, 15.0, 10.0, 8.0]


async def test_combine_refuses_bad_names(part: MockSolidWorksAdapter) -> None:
    assert (await part.combine_bodies("add", "Lid", ["Lid"])).error.startswith(
        "Invalid request:"
    )
    missing = await part.combine_bodies("add", "Lid", ["Label9"])
    assert missing.error == (
        "Body(ies) not found: Label9. The part's solid bodies: Lid, Label1, Label2."
    )


# ---- move_copy_body --------------------------------------------------------------


async def test_a_move_reports_the_shift(part: MockSolidWorksAdapter) -> None:
    result = await part.move_copy_body(["Label1"], [0, -9, 0])

    assert result.data["features"] == ["Body-Move/Copy1"]
    assert result.data["moved"][0]["center_shift_mm"] == [0.0, -9.0, 0.0]


async def test_a_shift_and_a_turn_make_two_features(
    part: MockSolidWorksAdapter,
) -> None:
    result = await part.move_copy_body(["Label1"], [1, 0, 0], 90, "y")

    assert result.data["features"] == ["Body-Move/Copy1", "Body-Move/Copy2"]


async def test_copies_are_spaced_by_the_shift_or_the_turn(
    part: MockSolidWorksAdapter,
) -> None:
    shifted = await part.move_copy_body(["Label1"], [20, 0, 0], copy=True, copies=2)

    assert [b["name"] for b in shifted.data["new_bodies"]] == [
        "Body-Move/Copy1[1]",
        "Body-Move/Copy1[2]",
    ]
    assert shifted.data["new_bodies"][1]["box_mm"][0] == 45.0
    turned = await part.move_copy_body(
        ["Label1"], rotate_deg=180, axis="y", rotate_about_mm=[10, 0, 6.5], copy=True
    )
    assert turned.data["new_bodies"][0]["box_mm"] == [5.0, 9.0, 5.0, 15.0, 11.0, 8.0]


async def test_a_move_refuses_nothing_to_move_and_unknown_bodies(
    part: MockSolidWorksAdapter,
) -> None:
    assert (await part.move_copy_body(["Label1"], [0, 0, 0])).error.startswith(
        "Invalid request:"
    )
    assert (await part.move_copy_body(["Nope"], [1, 0, 0])).error.startswith(
        "Body(ies) not found: Nope."
    )


# ---- delete_keep_body ------------------------------------------------------------


async def test_delete_and_keep(part: MockSolidWorksAdapter) -> None:
    deleted = await part.delete_keep_body(["Label1"])

    assert deleted.data["removed"] == ["Label1"]
    assert deleted.data["feature"] == "Body-Delete/Keep1"
    kept = await part.delete_keep_body(["Lid"], keep=True)
    assert kept.data["removed"] == ["Label2"]
    assert await _names(part) == ["Lid"]


async def test_delete_refuses_keeping_all_and_unknown_bodies(
    part: MockSolidWorksAdapter,
) -> None:
    all_kept = await part.delete_keep_body(["Lid", "Label1", "Label2"], keep=True)

    assert all_kept.error.startswith("Invalid request: keeping every solid body")
    assert (await part.delete_keep_body(["Nope"])).error.startswith("Body(ies)")


# ---- split_body ------------------------------------------------------------------


async def test_a_default_plane_cuts_at_the_origin(part: MockSolidWorksAdapter) -> None:
    part._mock_bodies()["Lid"]["box"] = [-20.0, 0.0, -20.0, 20.0, 10.0, 20.0]

    result = await part.split_body("Front", body="Lid")

    assert result.data["tool"] == "Front Plane"
    assert [p["box_mm"] for p in result.data["pieces"]] == [
        [-20.0, 0.0, -20.0, 20.0, 10.0, 0.0],
        [-20.0, 0.0, 0.0, 20.0, 10.0, 20.0],
    ]
    assert result.data["pieces"][0]["volume_mm3"] == 8000.0


async def test_a_reference_plane_or_sketch_halves_the_longest_side(
    part: MockSolidWorksAdapter,
) -> None:
    await part.create_reference_plane("Top", offset=5)
    part._sketches["Sketch7"] = "Top"

    by_plane = await part.split_body("Plane1", body="Label1")
    by_sketch = await part.split_body(sketch="Sketch7", body="Label2")

    assert by_plane.data["pieces"][0]["box_mm"] == [5.0, 9.0, 5.0, 10.0, 11.0, 8.0]
    assert by_sketch.data["feature"] == "Split2"


async def test_split_refuses_what_it_cannot_find_or_cut(
    part: MockSolidWorksAdapter,
) -> None:
    assert (await part.split_body("Plane9")).error == "Plane not found: Plane9"
    assert (
        await part.split_body(sketch="Sketch9")
    ).error == "Sketch not found: Sketch9"
    assert (await part.split_body("Top", body="Nope")).error.startswith("Body(ies)")
    nothing = await part.split_body("Right")
    assert nothing.error.startswith("Right Plane splits no body")


# ---- body_clearance and make_mating_negative -------------------------------------


async def test_the_gap_between_two_bodies(part: MockSolidWorksAdapter) -> None:
    result = await part.body_clearance("Label1", "Label2")

    assert result.data["clearance_mm"] == 5.0 and result.data["touching"] is False
    assert result.data["point_a_mm"] == [15.0, 10.0, 6.5]


async def test_overlapping_bodies_touch(part: MockSolidWorksAdapter) -> None:
    result = await part.body_clearance("Label1", "Lid")

    assert result.data["touching"] is True
    assert result.data["overlap_mm3"] == 30.0


async def test_a_clearance_needs_two_known_bodies(part: MockSolidWorksAdapter) -> None:
    assert (await part.body_clearance("Lid", "Lid")).error.startswith("Invalid request")
    assert (await part.body_clearance("Lid", "Nope")).error.startswith("Body(ies)")


async def test_a_negative_removes_the_grown_overlap(
    part: MockSolidWorksAdapter,
) -> None:
    result = await part.make_mating_negative("Lid", ["Label1", "Label2"], 0.15)

    data = result.data
    one = 10.3 * 1.15 * 3.3  # the grown label inside the lid
    assert data["volume_removed_mm3"] == pytest.approx(2 * one)
    assert data["method"] == "shifted_copies" and data["copies_per_tool"] == 67
    assert data["features"] == ["Imported1", "Imported2", "Combine1"]
    assert data["folder"] == "Negative of Label1 and 1 more"
    after = await part.list_bodies()
    assert after.data["bodies"][0]["volume_mm3"] == pytest.approx(16000 - 2 * one)


async def test_an_indent_is_one_feature(part: MockSolidWorksAdapter) -> None:
    result = await part.make_mating_negative("Lid", ["Label1"], 0.1, method="indent")

    assert result.data["features"] == ["Indent1"]
    assert result.data["folder"] is None and result.data["copies_per_tool"] == 0


async def test_a_negative_needs_distinct_known_bodies(
    part: MockSolidWorksAdapter,
) -> None:
    assert (await part.make_mating_negative("Lid", ["Lid"], 0.1)).error.startswith(
        "Invalid request"
    )
    missing = await part.make_mating_negative("Lid", ["Nope"], 0.1)
    assert missing.error.startswith("Body(ies) not found: Nope.")
