"""The mock's document operations give the open documents as the real adapter does."""

from __future__ import annotations

from pathlib import Path

import pytest

from solidworks_mcp.adapters.base import ExtrusionParameters
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    return adapter


def _titles(result: object) -> list[tuple[str, bool]]:
    documents = result.metadata["open_documents"]  # type: ignore[attr-defined]
    return [(d["title"], d["active"]) for d in documents]


async def test_each_document_operation_lists_when_asked(
    mock: MockSolidWorksAdapter, tmp_path: Path
) -> None:
    part = await mock.create_part("Bracket", list_documents=True)
    assembly = await mock.create_assembly("Rack", list_documents=True)
    opened = await mock.open_model("C:/work/Lid.SLDPRT", list_documents=True)
    activated = await mock.activate_document("Bracket", list_documents=True)
    saved = await mock.save_file(str(tmp_path / "b.sldprt"), list_documents=True)
    body = await mock.save_body_as_part(
        "Boss-Extrude1", str(tmp_path / "boss.sldprt"), list_documents=True
    )

    assert _titles(part) == [("Bracket", True)]
    assert _titles(assembly) == [("Bracket", False), ("Rack", True)]
    assert _titles(opened)[-1] == ("Lid.SLDPRT", True)
    assert _titles(activated)[0] == ("Bracket", True)
    assert _titles(saved)[0] == ("Bracket", True)
    assert _titles(body)[0] == ("Bracket", True)
    assert part.metadata["open_documents"][0] == {
        "title": "Bracket",
        "type": "Part",
        "active": True,
        "modified": False,
    }


async def test_nothing_is_listed_unless_asked(mock: MockSolidWorksAdapter) -> None:
    results = [
        await mock.create_part("Bracket"),
        await mock.create_assembly("Rack"),
        await mock.open_model("C:/work/Lid.SLDPRT"),
        await mock.activate_document("Bracket"),
        await mock.close_model(),
    ]

    assert all(r.metadata is None for r in results[:-1])
    # A close always says what it closed, as live.
    assert results[-1].metadata == {"closed": ["Bracket.sldprt"]}


async def test_a_closed_document_leaves_the_list(mock: MockSolidWorksAdapter) -> None:
    await mock.create_part("Bracket")
    await mock.create_part("Lid")

    closed = await mock.close_model(list_documents=True)
    listed = await mock.list_open_documents()

    assert closed.metadata["open_documents"] == [
        {"title": "Bracket", "type": "Part", "active": False, "modified": False}
    ]
    assert [d["title"] for d in listed.data] == ["Bracket"]


async def test_open_model_summarises_the_tree_and_a_parts_bodies(
    mock: MockSolidWorksAdapter,
) -> None:
    await mock.create_part("Plate")
    await mock.create_sketch("Front Plane")
    await mock.add_rectangle(0, 0, 10, 10)
    await mock.exit_sketch()
    await mock.create_extrusion(ExtrusionParameters(depth=5))

    part = await mock.open_model("C:/work/Lid.SLDPRT", summarize=True)
    drawing = await mock.open_model("C:/work/Lid.SLDDRW", summarize=True)
    read_only = await mock.open_model_read_only(
        "C:/work/Rack.SLDASM", list_documents=True, summarize=True
    )

    contents = part.metadata["contents"]
    assert [row["type"] for row in contents["features"]] == ["Extrusion"]
    assert contents["bodies"] == ["Boss-Extrude1"]
    assert drawing.metadata["contents"]["bodies"] is None
    assert read_only.data.properties["read_only"] is True
    assert set(read_only.metadata) == {"contents", "open_documents"}
