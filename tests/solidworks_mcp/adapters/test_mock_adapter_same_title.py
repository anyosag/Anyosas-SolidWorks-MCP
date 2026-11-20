"""The mock refuses a name two documents answer to, and closes one document alone.

With ``hide_extensions`` it titles documents as SolidWorks does when Windows
hides file extensions, so a part and an assembly of one name share a title.
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def tray() -> MockSolidWorksAdapter:
    mock = MockSolidWorksAdapter({})
    await mock.connect()
    mock.hide_extensions = True
    await mock.open_model("C:/work/Tray.SLDPRT")
    await mock.open_model("C:/work/Tray.SLDASM")
    return mock


async def test_both_documents_are_titled_without_the_extension(tray) -> None:
    listed = await tray.list_open_documents()

    assert [d["title"] for d in listed.data] == ["Tray", "Tray"]


async def test_the_shared_title_is_refused_naming_both_files(tray) -> None:
    for chosen in (
        await tray.activate_document("Tray"),
        await tray.use_document("Tray"),
    ):
        assert chosen.is_error
        assert chosen.error == (
            "Invalid request: 'Tray' matches 2 open documents: Tray.SLDPRT, "
            "Tray.SLDASM. Pass one of those file names, with its extension, or "
            "the document's full path."
        )


async def test_a_file_name_picks_one_and_a_close_takes_only_it(tray) -> None:
    chosen = await tray.activate_document("Tray.SLDPRT")
    closed = await tray.close_model(list_documents=True)

    assert chosen.is_success
    assert closed.metadata["closed"] == ["Tray.SLDPRT"]
    assert [d["type"] for d in closed.metadata["open_documents"]] == ["Assembly"]
    assert (await tray.activate_document("Tray.SLDASM")).is_success
