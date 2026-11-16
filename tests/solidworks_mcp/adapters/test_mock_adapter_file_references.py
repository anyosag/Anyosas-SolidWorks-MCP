"""The mock lists references and moves files the way the live adapter plans."""

from __future__ import annotations

from pathlib import Path

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def mock(tmp_path: Path) -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    for name in ("bracket.sldprt", "frame.sldasm", "top.sldasm"):
        (tmp_path / name).write_bytes(b"sw")
    adapter.document_references = {
        str(tmp_path / "top.sldasm"): [str(tmp_path / "frame.sldasm")],
        str(tmp_path / "frame.sldasm"): [
            str(tmp_path / "bracket.sldprt"),
            str(tmp_path / "gone.sldprt"),
        ],
    }
    return adapter


async def test_references_are_listed_and_checked_on_disk(mock, tmp_path) -> None:
    result = await mock.list_references(str(tmp_path / "frame.sldasm"))

    assert result.is_success
    assert result.data["count"] == 2
    assert result.data["missing"] == [str(tmp_path / "gone.sldprt")]
    assert result.data["references"][0]["name"] == "bracket"


async def test_traverse_goes_all_the_way_down(mock, tmp_path) -> None:
    shallow = await mock.list_references(str(tmp_path / "top.sldasm"))
    deep = await mock.list_references(str(tmp_path / "top.sldasm"), traverse=True)

    assert shallow.data["count"] == 1
    assert [r["name"] for r in deep.data["references"]] == ["frame", "bracket", "gone"]


async def test_a_file_that_is_not_there_is_not_found(mock, tmp_path) -> None:
    result = await mock.list_references(str(tmp_path / "none.sldprt"))

    assert result.error.startswith("File not found")


async def test_a_dry_run_changes_nothing(mock, tmp_path) -> None:
    result = await mock.move_document(
        str(tmp_path / "bracket.sldprt"), str(tmp_path / "plate.sldprt")
    )

    assert result.data["dry_run"] is True
    assert result.data["would_update"] == [str(tmp_path / "frame.sldasm")]
    assert (tmp_path / "bracket.sldprt").exists()


async def test_a_move_moves_the_file_and_repoints_its_users(mock, tmp_path) -> None:
    new = str(tmp_path / "parts" / "plate.sldprt")

    result = await mock.move_document(
        str(tmp_path / "bracket.sldprt"), new, dry_run=False
    )
    after = await mock.list_references(str(tmp_path / "frame.sldasm"))

    assert result.data["moved"] is True
    assert result.data["updated"] == [
        {"path": str(tmp_path / "frame.sldasm"), "verified": True}
    ]
    assert Path(new).exists()
    assert after.data["references"][0]["path"] == new


async def test_moving_an_assembly_keeps_what_it_references(mock, tmp_path) -> None:
    new = str(tmp_path / "frame2.sldasm")

    await mock.move_document(str(tmp_path / "frame.sldasm"), new, dry_run=False)
    moved = await mock.list_references(new)
    top = await mock.list_references(str(tmp_path / "top.sldasm"))

    assert moved.data["count"] == 2
    assert top.data["references"][0]["path"] == new


async def test_an_open_document_blocks_the_move(mock, tmp_path) -> None:
    await mock.open_model(str(tmp_path / "frame.sldasm"))

    dry = await mock.move_document(
        str(tmp_path / "bracket.sldprt"), str(tmp_path / "plate.sldprt")
    )
    real = await mock.move_document(
        str(tmp_path / "bracket.sldprt"), str(tmp_path / "plate.sldprt"), dry_run=False
    )

    assert dry.data["can_move"] is False
    assert real.error.startswith("Invalid request: nothing was moved:")
    assert (tmp_path / "bracket.sldprt").exists()


async def test_an_invalid_move_is_refused(mock, tmp_path) -> None:
    result = await mock.move_document(
        str(tmp_path / "bracket.sldprt"), str(tmp_path / "frame.sldasm")
    )

    assert result.error.startswith("Invalid request: the destination must end in")


async def test_not_connected_is_refused() -> None:
    adapter = MockSolidWorksAdapter({})

    assert (await adapter.list_references("C:/a.sldprt")).error == (
        "Not connected to SolidWorks"
    )
    assert (await adapter.move_document("C:/a.sldprt", "C:/b.sldprt")).error == (
        "Not connected to SolidWorks"
    )
