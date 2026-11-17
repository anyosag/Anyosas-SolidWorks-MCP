"""The mock's Save Bodies, loaded-document and assembly-part methods."""

from __future__ import annotations

from pathlib import Path

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def _part() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    return adapter


async def test_save_bodies_round_trip(tmp_path: Path) -> None:
    adapter = await _part()
    comb = tmp_path / "comb.sldprt"
    comb.write_text("old")

    saved = await adapter.save_body_as_part("Comb", str(comb), "C:/a.SLDASM")
    listed = await adapter.get_save_bodies()
    named = await adapter.get_save_bodies("Save Bodies1")
    moved = await adapter.edit_save_bodies(
        "Save Bodies1",
        {"files": {"Comb": str(tmp_path / "comb2.sldprt")}, "consume_body": True},
    )
    unknown = await adapter.edit_save_bodies(
        "Save Bodies1", {"files": {"Lid": str(tmp_path / "x.sldprt")}}
    )
    resplit = await adapter.resplit_bodies()
    (tmp_path / "comb2.sldprt").unlink()
    missing = await adapter.resplit_bodies("Save Bodies1")

    assert saved.data["replaced"] is True
    assert saved.data["assembly_path"] == "C:/a.SLDASM"
    (feature,) = listed.data["features"]
    assert feature["assembly_path"] == "C:/a.SLDASM"
    assert feature["bodies"][0]["exists"] is True
    assert named.is_success
    assert moved.data["files_written"] == {str(tmp_path / "comb2.sldprt"): True}
    assert moved.data["definition"]["consume_body"] is True
    assert "Lid not in the feature" in (unknown.error or "")
    assert resplit.data["counts"] == {"updated": 1}
    assert missing.data["counts"] == {"missing": 1}


async def test_save_bodies_refusals() -> None:
    adapter = await _part()

    none = await adapter.get_save_bodies()
    named = await adapter.resplit_bodies("Save Bodies9")
    edit = await adapter.edit_save_bodies("Save Bodies9", {"consume_body": True})
    await adapter.create_assembly()
    wrong = await adapter.get_save_bodies()

    assert "No Save Bodies feature found" in (none.error or "")
    assert "'Save Bodies9' not found" in (named.error or "")
    assert "'Save Bodies9' not found" in (edit.error or "")
    assert wrong.error == "Save Bodies requires an active part document"


async def test_loaded_documents_and_leftovers() -> None:
    adapter = await _part()
    adapter._hidden_documents = {
        "C:/d/Comb.SLDPRT": {"title": "Comb.SLDPRT"},
        "C:/d/Stand.SLDPRT": {"title": "Stand.SLDPRT", "modified": True},
        "C:/d/Holder.SLDPRT": {"title": "Holder.SLDPRT", "used_by": ["Rack"]},
    }

    listed = await adapter.list_loaded_documents()
    closed = await adapter.close_leftover_documents()
    after = await adapter.list_loaded_documents()

    assert listed.data["leftovers"] == ["Comb.SLDPRT", "Stand.SLDPRT"]
    assert listed.data["count"] == 4
    assert [d["title"] for d in closed.data["closed"]] == ["Comb.SLDPRT"]
    assert [d["title"] for d in closed.data["kept_modified"]] == ["Stand.SLDPRT"]
    assert after.data["leftovers"] == ["Stand.SLDPRT"]

    await adapter.disconnect()
    assert (await adapter.list_loaded_documents()).is_error
    assert (await adapter.close_leftover_documents()).is_error


async def test_assembly_parts_are_listed_once_and_reachable(tmp_path: Path) -> None:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    assert (await adapter.list_assembly_parts()).error == "No active model"
    await adapter.create_part()
    assert "requires an active assembly" in (
        (await adapter.list_assembly_parts()).error or ""
    )
    await adapter.create_assembly()
    holder = str(tmp_path / "Holder.SLDPRT")
    for path in (holder, holder, str(tmp_path / "Sub.SLDASM")):
        await adapter.insert_component(path)

    result = await adapter.list_assembly_parts()
    listed = await adapter.list_loaded_documents()
    activated = await adapter.activate_document(holder)

    (part,) = result.data["parts"]
    assert part["instances"] == 2
    assert part["title"] == "Holder.SLDPRT"
    holder_doc = next(d for d in listed.data["documents"] if d["path"] == holder)
    assert holder_doc["visible"] is False
    assert activated.is_success
