"""A part and an assembly of one name: told apart by file name and path, never by title.

On the Aggrewell enclosure v2 (SOLIDWORKS 2026, Windows hiding extensions)
``Enclosure_v2_Tray_with_Stamps.SLDPRT`` and ``.SLDASM`` were both titled
``Enclosure_v2_Tray_with_Stamps``. ``close_model`` with that name closed both
(``CloseDoc`` closes every document of a title), and the next call with
``.SLDASM`` found nothing. The fake SolidWorks reads names the same way.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import io
from solidworks_mcp.adapters.solidworks import loaded_documents as loaded
from solidworks_mcp.adapters.solidworks.io import (
    SolidWorksIOMixin,
    activate_doc,
    ambiguous_document,
    document_label,
    window_documents,
)

from .fake_documents import FakeApp, Harness

PART = "C:/work/Tray.SLDPRT"
ASSEMBLY = "C:/work/Tray.SLDASM"


class _Session(Harness):
    """The adapter state ``use_document`` reads too."""

    def __init__(self, app: FakeApp, current: Any = None) -> None:
        super().__init__(app, current)
        self._last_active_document: Any = None

    @staticmethod
    def _document_key(doc: Any) -> tuple[str, str] | None:
        return None if doc is None else (doc.path.lower(), doc.title)


def _io(adapter: Any) -> SolidWorksIOMixin:
    mixin = SolidWorksIOMixin()
    mixin._adapter = lambda _self: adapter  # type: ignore[method-assign]
    return mixin


def _tray(active: str = "part") -> tuple[FakeApp, Any, Any]:
    """Both documents titled ``Tray``, as with Windows hiding extensions."""
    app = FakeApp()
    part = app.add("Tray", PART)
    assembly = app.add("Tray", ASSEMBLY, 2)
    app.active = part if active == "part" else assembly
    return app, part, assembly


# ---- naming and refusing ----------------------------------------------------------


def test_a_document_is_labelled_by_its_file_name() -> None:
    assert document_label("Tray", PART) == "Tray.SLDPRT"
    assert document_label("Part3", "") == "Part3 (not saved)"
    assert document_label(None, None) == "a document with no name"
    assert ambiguous_document("Tray", ["Tray.SLDPRT", "Tray.SLDASM"]) == (
        "Invalid request: 'Tray' matches 2 open documents: Tray.SLDPRT, "
        "Tray.SLDASM. Pass one of those file names, with its extension, or the "
        "document's full path."
    )


async def test_a_title_two_documents_have_is_refused() -> None:
    app, part, _assembly = _tray()
    harness = Harness(app)

    result = await _io(harness).activate_document("Tray")

    assert result.is_error
    assert "'Tray' matches 2 open documents: Tray.SLDPRT, Tray.SLDASM" in (
        result.error or ""
    )
    assert app.active is part  # nothing was activated


@pytest.mark.parametrize("name", ["Tray.SLDASM", "tray.sldasm", ASSEMBLY])
async def test_a_file_name_or_path_activates_that_document(name: str) -> None:
    app, _part, assembly = _tray()
    harness = Harness(app)

    result = await _io(harness).activate_document(name)

    assert result.is_success, result.error
    assert result.data["verified"] is True
    assert app.active is assembly and harness.currentModel is assembly


async def test_the_readback_compares_the_document_not_its_title() -> None:
    # SolidWorks takes neither the file name nor the path, and activates the
    # part: same title, other document.
    app, part, assembly = _tray()
    app.takes_paths = False
    app.ActivateDoc3 = lambda *_a: part  # type: ignore[method-assign]
    harness = Harness(app)

    result = await _io(harness).activate_document("Tray.SLDASM")

    assert result.is_error
    assert "Requested 'Tray.SLDASM' but ActiveDoc is 'Tray.SLDPRT'" in (
        result.error or ""
    )
    assert assembly in app.documents


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        (None, None),  # nothing active to read back
        ("Part3", True),  # never saved: its title is all there is
        ("Part4", False),
    ],
)
def test_a_document_never_saved_is_read_back_by_title(
    active: str | None, expected: bool | None
) -> None:
    app = FakeApp()
    doc = app.add(active) if active is not None else None

    assert io._is_document(Harness(app), doc, "Part3", "")[2] is expected


def test_an_unreadable_path_reads_back_as_unknown() -> None:
    app = FakeApp()
    doc = app.add("Tray", PART)
    doc.GetPathName = lambda: None  # type: ignore[method-assign]
    doc.GetTitle = lambda: None  # type: ignore[method-assign]

    assert io._is_document(Harness(app), doc, "Tray", PART)[2] is None
    assert io._is_document(Harness(app), doc, "Tray", "")[2] is None


# ---- activating with the file name ----------------------------------------------------


def test_a_shared_title_activates_by_file_name_then_path() -> None:
    app, _part, assembly = _tray()
    names: list[str] = []
    original = app.ActivateDoc3

    def record(name: str, *args: Any) -> Any:
        names.append(name)
        return original(name, *args) if name == ASSEMBLY else None

    app.ActivateDoc3 = record  # type: ignore[method-assign]

    activated = activate_doc(Harness(app), app, "Tray", document=assembly)

    assert names == ["Tray.SLDASM", ASSEMBLY]
    assert activated is assembly


def test_activating_the_other_document_of_the_title_counts_as_failing() -> None:
    app, part, assembly = _tray()
    app.ActivateDoc3 = lambda *_a: part  # type: ignore[method-assign]

    assert activate_doc(Harness(app), app, "Tray", document=assembly) is None


def test_a_title_nobody_else_has_is_given_as_it_is() -> None:
    app = FakeApp()
    lid = app.add("Lid", "C:/work/Lid.SLDPRT")
    names: list[str] = []
    app.ActivateDoc3 = lambda name, *_a: names.append(name) or lid  # type: ignore[method-assign]

    assert activate_doc(Harness(app), app, "Lid", document=lid) is lid
    assert names == ["Lid"]


# ---- use_document ------------------------------------------------------------------


async def test_a_shared_title_is_not_taken_as_the_active_document() -> None:
    app, part, _assembly = _tray()
    session = _Session(app, part)
    session._last_active_document = session._document_key(part)

    result = await _io(session).use_document("Tray")

    assert result.is_error
    assert "matches 2 open documents" in (result.error or "")


async def test_the_active_documents_file_name_still_skips_activation() -> None:
    app, part, _assembly = _tray()
    session = _Session(app, part)
    session._last_active_document = session._document_key(part)

    result = await _io(session).use_document("Tray.SLDPRT")

    assert result.data == {"activated": "Tray", "verified": True, "switched": False}
    assert session.operations == ["check_active_document"]


# ---- closing -------------------------------------------------------------------------


async def test_a_shared_title_closes_by_path_and_only_that_document() -> None:
    app, part, assembly = _tray(active="assembly")
    harness = Harness(app, assembly)

    result = await _io(harness).close_model(list_documents=True)

    assert result.is_success, result.error
    assert app.closed == [ASSEMBLY]
    assert app.documents == [part]
    assert result.metadata["closed"] == ["Tray.SLDASM"]
    assert [d["title"] for d in result.metadata["open_documents"]] == ["Tray"]


async def test_without_paths_the_active_document_is_closed_alone() -> None:
    app, part, assembly = _tray(active="assembly")
    app.takes_paths = False
    harness = Harness(app, assembly)

    result = await _io(harness).close_model()

    assert app.closed == [ASSEMBLY, ""]
    assert app.documents == [part]
    assert result.metadata == {"closed": ["Tray.SLDASM"]}


async def test_a_document_that_could_not_be_closed_alone_stays_and_says_so() -> None:
    app, part, assembly = _tray(active="part")
    app.takes_paths = False
    harness = Harness(app, assembly)

    result = await _io(harness).close_model()

    assert result.is_error
    assert "did not close Tray.SLDASM: another open document is titled 'Tray'" in (
        result.error or ""
    )
    assert app.documents == [part, assembly]
    assert harness.currentModel is assembly


async def test_a_close_names_every_document_it_took(monkeypatch) -> None:
    # Were the title not seen as shared, CloseDoc("Tray") would take both.
    app, part, assembly = _tray(active="assembly")
    monkeypatch.setattr(loaded, "title_shared", lambda *_a: False)
    harness = Harness(app, assembly)

    result = await _io(harness).close_model()

    assert app.closed == ["Tray"]
    assert result.metadata["closed"] == ["Tray.SLDASM", "Tray.SLDPRT"]
    assert part not in app.documents


async def test_closing_the_last_document_leaves_none() -> None:
    app = FakeApp()
    lid = app.add("Lid.SLDPRT", "C:/work/Lid.SLDPRT")
    app.active = lid
    real = app.GetDocuments
    app.GetDocuments = lambda: real() or None  # type: ignore[method-assign]

    result = await _io(Harness(app, lid)).close_model()

    assert result.metadata == {"closed": ["Lid.SLDPRT"]}


# ---- what is open, and what shares a title ----------------------------------------


def test_the_windowed_documents_and_the_active_one_are_listed() -> None:
    app = FakeApp()
    app.add("Rack.SLDASM", "C:/work/Rack.SLDASM", 2)
    app.add("Pin.SLDPRT", "C:/work/Pin.SLDPRT", visible=False)
    app.active = app.add("Hidden.SLDPRT", "C:/work/Hidden.SLDPRT", visible=False)
    app.add("Part9", "")

    found = window_documents(Harness(app))

    assert found == {
        loaded.normalize("C:/work/Rack.SLDASM"): "Rack.SLDASM",
        loaded.normalize("C:/work/Hidden.SLDPRT"): "Hidden.SLDPRT",
        "<Part9>": "Part9 (not saved)",
    }
    app.GetDocuments = lambda: None  # type: ignore[method-assign]
    assert window_documents(Harness(app)) is None


def test_only_another_path_of_the_title_is_sharing_it() -> None:
    app, _part, _assembly = _tray()
    harness = Harness(app)

    assert loaded.title_shared(harness, "Tray", PART) is True
    assert loaded.title_shared(harness, "tray", PART) is True
    assert loaded.title_shared(harness, "Tray", "") is False  # never saved
    assert loaded.close_name(harness, "Tray", PART) == PART

    alone = FakeApp()
    alone.add("Lid", "C:/work/Lid.SLDPRT")
    alone.add("Lid", "")  # an unsaved document of the same title shares it
    assert loaded.title_shared(Harness(alone), "Lid", "C:/work/Lid.SLDPRT") is True
    assert loaded.close_name(Harness(FakeApp()), "Lid", "C:/w/Lid.SLDPRT") == "Lid"


def test_a_title_with_its_extension_reads_nothing() -> None:
    app = FakeApp()
    reads: list[str] = []
    app.GetDocuments = lambda: reads.append("GetDocuments") or ()  # type: ignore[method-assign]

    assert loaded.title_shared(Harness(app), "Tray.SLDPRT", PART) is False
    assert reads == []


def test_a_document_is_the_file_at_its_path() -> None:
    app, part, _assembly = _tray()
    harness = Harness(app)

    assert loaded.same_path(harness, part, "c:\\work\\tray.sldprt") is True
    assert loaded.same_path(harness, part, ASSEMBLY) is False
    assert loaded.same_path(harness, part, "") is False
