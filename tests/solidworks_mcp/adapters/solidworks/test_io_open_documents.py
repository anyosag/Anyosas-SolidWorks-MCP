"""Document operations read the open documents in their own operation.

Agents called ``list_open_documents`` right after opening, making, activating,
saving or closing a document (speed report, section 3). The document
operations now read the list inside their own COM operation when asked
(``list_documents``), so the reply carries it with no second operation and no
second settle gap; ``open_model`` also reads the document's own tree and
bodies (``summarize``). These fakes count every COM member read.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.pywin32_adapter import _FeatureSelectionService
from solidworks_mcp.adapters.solidworks import io
from solidworks_mcp.adapters.solidworks.io import SolidWorksIOMixin

from .fake_documents import FakeApp, FakeBody, FakeDocument, Harness


class CountingDocument(FakeDocument):
    """A document that records each COM member read on it (capitalised names)."""

    def __getattribute__(self, name: str) -> Any:
        if name[:1].isupper():
            object.__getattribute__(self, "app").reads.append(name)
        return object.__getattribute__(self, name)

    def SaveAs3(self, path: str, _version: int, _options: int) -> int:
        Path(path).write_bytes(b"saved as")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "title", Path(path).name)
        return 0


class CountingApp(FakeApp):
    """A SolidWorks whose documents count their COM reads into ``reads``."""

    def __init__(self) -> None:
        super().__init__()
        self.reads: list[str] = []
        self.made: list[CountingDocument] = []

    def add(self, *args: Any, **kwargs: Any) -> CountingDocument:
        document = CountingDocument(self, *args, **kwargs)
        self.documents.append(document)
        return document

    def GetDocuments(self) -> tuple[FakeDocument, ...]:
        self.reads.append("GetDocuments")
        return super().GetDocuments()

    @property
    def ActiveDoc(self) -> FakeDocument | None:
        self.reads.append("ActiveDoc")
        return self.active

    def NewPart(self) -> CountingDocument:
        self.active = self.add(f"Part{len(self.made) + 1}")
        self.made.append(self.active)
        return self.active

    def NewAssembly(self) -> CountingDocument:
        self.active = self.add(f"Assem{len(self.made) + 1}", "", 2)
        self.made.append(self.active)
        return self.active


class _Harness(Harness):
    constants = {"swDocPART": 1, "swDocASSEMBLY": 2, "swDocDRAWING": 3}


def _io(adapter: Any) -> SolidWorksIOMixin:
    """The I/O mixin bound to a fake adapter."""
    mixin = SolidWorksIOMixin()
    mixin._adapter = lambda _self: adapter  # type: ignore[method-assign]
    return mixin


def _session() -> tuple[CountingApp, CountingDocument, CountingDocument]:
    """An active modified part, an open assembly and a part it loaded hidden."""
    app = CountingApp()
    part = app.add("Bracket.SLDPRT", "C:/work/Bracket.SLDPRT", modified=True)
    rack = app.add("Rack.SLDASM", "C:/work/Rack.SLDASM", 2)
    app.add("Pin.SLDPRT", "C:/work/Pin.SLDPRT", visible=False)
    app.active = part
    return app, part, rack


BRACKET = {"title": "Bracket.SLDPRT", "type": "Part", "active": True, "modified": True}
RACK = {"title": "Rack.SLDASM", "type": "Assembly", "active": False, "modified": False}


# ---- the list ---------------------------------------------------------------


def test_the_list_has_each_document_with_a_window_and_what_it_cost() -> None:
    app, _part, _rack = _session()

    documents = io.open_documents(_Harness(app))

    assert documents == [BRACKET, RACK]
    # GetDocuments and ActiveDoc; three reads per document listed; Visible of
    # each other document (Pin's says it has no window, so it is left there).
    assert app.reads == [
        "GetDocuments",
        "ActiveDoc",
        "GetTitle",
        "GetType",
        "GetSaveFlag",
        "Visible",
        "GetTitle",
        "GetType",
        "GetSaveFlag",
        "Visible",
    ]


def test_the_active_document_is_listed_even_without_a_window() -> None:
    app = CountingApp()
    app.active = app.add("Hidden.SLDPRT", "C:/work/Hidden.SLDPRT", visible=False)

    documents = io.open_documents(_Harness(app))

    assert [d["title"] for d in documents] == ["Hidden.SLDPRT"]
    assert "Visible" not in app.reads


def test_what_cannot_be_read_is_unknown() -> None:
    class _Odd(CountingDocument):
        def GetType(self) -> int:
            raise RuntimeError("no type")

        def GetSaveFlag(self) -> Any:
            return "yes"

    app = CountingApp()
    odd = _Odd(app, "")
    app.documents.append(odd)

    assert io.open_documents(_Harness(app)) == [
        {"title": None, "type": "Unknown", "active": False, "modified": None}
    ]


def test_no_documents_is_an_empty_list() -> None:
    class _Broken(CountingApp):
        def GetDocuments(self) -> Any:
            raise RuntimeError("gone")

    assert io.open_documents(_Harness(_Broken())) == []


def test_metadata_is_added_to_a_success_only() -> None:
    ok: AdapterResult[Any] = AdapterResult(status=AdapterResultStatus.SUCCESS)
    failed: AdapterResult[Any] = AdapterResult(status=AdapterResultStatus.ERROR)
    kept: AdapterResult[Any] = AdapterResult(
        status=AdapterResultStatus.SUCCESS, metadata={"warning": "w"}
    )

    assert io.with_metadata(ok, {"open_documents": []}).metadata == {
        "open_documents": []
    }
    assert io.with_metadata(failed, {"open_documents": []}).metadata is None
    assert io.with_metadata(kept, {"a": 1}).metadata == {"warning": "w", "a": 1}
    assert io.with_metadata(kept, {}).metadata == {"warning": "w", "a": 1}


# ---- the operations read it in their own operation ---------------------------------


async def test_open_model_lists_and_summarises_in_one_operation(
    tmp_path: Path,
) -> None:
    app, _part, _rack = _session()
    path = str(tmp_path / "Lid.SLDPRT")
    lid = CountingDocument(app, "Lid.SLDPRT", path, bodies=[FakeBody("Lid")])
    lid.features = [
        _Feature(lid, "Origin", "OriginProfileFeature"),
        _Feature(lid, "Sketch1", "ProfileFeature"),
        _Feature(lid, "Boss-Extrude1", "Extrusion"),
    ]
    app.on_open[path] = lid
    harness = _Harness(app)
    harness._feature_selector = _FeatureSelectionService(harness)

    result = await _io(harness).open_model(path, list_documents=True, summarize=True)

    assert result.is_success, result.error
    assert harness.operations == ["open_model"]
    listed = result.metadata["open_documents"]
    assert listed[-1] == {
        "title": "Lid.SLDPRT",
        "type": "Part",
        "active": True,
        "modified": False,
    }
    contents = result.metadata["contents"]
    assert [(r["name"], r["type"]) for r in contents["features"]] == [
        ("Origin", "OriginProfileFeature"),
        ("Sketch1", "ProfileFeature"),
        ("Boss-Extrude1", "Extrusion"),
    ]
    assert contents["bodies"] == ["Lid"]


@pytest.mark.parametrize("read_only", [False, True])
async def test_open_model_reads_nothing_more_unless_asked(
    tmp_path: Path, read_only: bool
) -> None:
    app, _part, _rack = _session()
    path = str(tmp_path / "Lid.SLDPRT")
    app.on_open[path] = CountingDocument(app, "Lid.SLDPRT", path)
    mixin = _io(_Harness(app))

    opened = (
        await mixin.open_model_read_only(path)
        if read_only
        else await mixin.open_model(path)
    )

    assert opened.is_success, opened.error
    assert opened.metadata is None
    assert "GetDocuments" not in app.reads


async def test_a_read_only_open_lists_too(tmp_path: Path) -> None:
    app, _part, _rack = _session()
    path = str(tmp_path / "Lid.SLDPRT")
    app.on_open[path] = CountingDocument(app, "Lid.SLDPRT", path)

    opened = await _io(_Harness(app)).open_model_read_only(path, list_documents=True)

    assert [d["title"] for d in opened.metadata["open_documents"]][-1] == "Lid.SLDPRT"
    assert "contents" not in opened.metadata


async def test_a_document_open_after_a_dialog_keeps_the_list(tmp_path: Path) -> None:
    app, _part, _rack = _session()
    path = str(tmp_path / "Rig.SLDASM")
    rig = CountingDocument(app, "Rig.SLDASM", path, 2)
    app.documents.append(rig)  # open, though OpenDoc6 handed nothing back
    harness = _Harness(app)
    harness.dialog = {"title": "SOLIDWORKS CAM", "texts": ["Lightweight"]}

    calls: list[str] = []

    def _first_call_only(name: str, callback: Any) -> AdapterResult[Any]:
        calls.append(name)
        if len(calls) > 1:
            harness.dialog = None
        return Harness._handle_com_operation(harness, name, callback)

    harness._handle_com_operation = _first_call_only  # type: ignore[method-assign]
    app.GetOpenDocumentByName = lambda _path: rig  # type: ignore[attr-defined]

    opened = await _io(harness).open_model(path, list_documents=True)

    assert opened.is_success, opened.error
    assert "SOLIDWORKS CAM" in opened.metadata["warning"]
    assert [d["title"] for d in opened.metadata["open_documents"]][-1] == "Rig.SLDASM"


async def test_new_documents_list_in_their_own_operation() -> None:
    app, _part, _rack = _session()
    harness = _Harness(app)
    mixin = _io(harness)

    part = await mixin.create_part(list_documents=True)
    assembly = await mixin.create_assembly(list_documents=True)
    plain = await mixin.create_part()

    assert harness.operations == ["create_part", "create_assembly", "create_part"]
    assert part.metadata["open_documents"][-1] == {
        "title": "Part1",
        "type": "Part",
        "active": True,
        "modified": False,
    }
    titles = [d["title"] for d in assembly.metadata["open_documents"]]
    assert titles == ["Bracket.SLDPRT", "Rack.SLDASM", "Part1", "Assem2"]
    assert plain.metadata is None


async def test_close_model_lists_what_is_left() -> None:
    app, part, _rack = _session()
    harness = _Harness(app, part)

    closed = await _io(harness).close_model(list_documents=True)

    assert closed.is_success, closed.error
    assert harness.operations == ["close_model"]
    assert app.closed == ["Bracket.SLDPRT"]
    # SolidWorks activated the next window (the fake: the first left).
    assert closed.metadata["open_documents"] == [{**RACK, "active": True}]


async def test_a_close_whose_save_needs_confirming_closes_and_lists_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solidworks_mcp.adapters.solidworks import saving

    app, part, _rack = _session()
    monkeypatch.setattr(
        saving,
        "save_document",
        lambda *_args: saving.NeedsConfirmation(["C:/work/rail.SLDPRT"]),
    )

    result = await _io(_Harness(app, part)).close_model(save=True, list_documents=True)

    assert result.is_error
    assert result.data["files"] == ["C:/work/rail.SLDPRT"]
    assert app.closed == []
    assert "GetDocuments" not in app.reads


async def test_activate_document_lists_after_switching() -> None:
    app, _part, _rack = _session()
    harness = _Harness(app)

    result = await _io(harness).activate_document("Rack.SLDASM", list_documents=True)

    assert result.is_success, result.error
    assert harness.operations == ["activate_document"]
    assert result.metadata["open_documents"] == [
        {**BRACKET, "active": False},
        {**RACK, "active": True},
    ]


async def test_use_document_never_lists() -> None:
    app, _part, _rack = _session()
    harness = _Harness(app)
    harness._document_key = lambda _doc: None  # type: ignore[attr-defined]
    harness._last_active_document = None

    result = await _io(harness).use_document("Rack.SLDASM")

    assert result.is_success, result.error
    assert result.metadata is None


async def test_save_as_lists_the_renamed_document(tmp_path: Path) -> None:
    app, part, _rack = _session()
    harness = _Harness(app, part)
    target = tmp_path / "Bracket-v2.SLDPRT"

    saved = await _io(harness).save_file(str(target), list_documents=True)

    assert saved.is_success, saved.error
    assert harness.operations == ["save_file"]
    assert saved.metadata["open_documents"][0]["title"] == "Bracket-v2.SLDPRT"


async def test_a_save_waiting_for_confirmation_lists_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from solidworks_mcp.adapters.solidworks import saving

    app, part, _rack = _session()
    monkeypatch.setattr(
        saving,
        "save_document",
        lambda *_args: saving.NeedsConfirmation(["C:/work/rail.SLDPRT"]),
    )

    result = await _io(_Harness(app, part)).save_file(
        "C:/work/x.SLDASM", list_documents=True
    )

    assert result.is_error
    assert result.metadata is None or "open_documents" not in result.metadata
    assert "GetDocuments" not in app.reads


# ---- the tree and bodies for the summary -------------------------------------------


class _Feature:
    """An ``IFeature`` in a fake document's tree."""

    def __init__(
        self, model: FakeDocument, name: str, kind: str, suppressed: bool = False
    ) -> None:
        self.model = model
        self.Name = name
        self.kind = kind
        self.suppressed = suppressed

    def GetTypeName2(self) -> str:
        return self.kind

    def IsSuppressed(self) -> bool:
        return self.suppressed

    def GetNextFeature(self) -> Any:
        features = self.model.features
        index = features.index(self)
        return features[index + 1] if index + 1 < len(features) else None


def test_contents_are_the_own_tree_with_suppressed_ones_and_a_parts_bodies() -> None:
    app = CountingApp()
    part = app.add("P.SLDPRT", bodies=[FakeBody("A"), FakeBody("B")])
    part.features = [
        _Feature(part, "Cut-Extrude1", "Cut", suppressed=True),
        _Feature(part, "Fillet1", "Fillet"),
    ]
    harness = _Harness(app)
    harness._feature_selector = _FeatureSelectionService(harness)

    contents = io.model_contents(harness, part)

    assert contents is not None
    assert [(r["name"], r["suppressed"]) for r in contents["features"]] == [
        ("Cut-Extrude1", True),
        ("Fillet1", False),
    ]
    assert contents["bodies"] == ["A", "B"]


def test_an_assembly_has_no_bodies_and_no_walk_means_no_contents() -> None:
    app = CountingApp()
    assembly = app.add("A.SLDASM", "", 2)
    harness = _Harness(app)
    harness._feature_selector = _FeatureSelectionService(harness)

    assert io.model_contents(harness, assembly) == {"features": [], "bodies": None}
    assert io.model_contents(_Harness(app), assembly) is None
    assert io.model_contents(harness, None) is None
