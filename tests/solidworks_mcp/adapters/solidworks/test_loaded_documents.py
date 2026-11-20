"""Loaded documents with and without windows, their users, and the leftovers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from solidworks_mcp.adapters.solidworks import loaded_documents as loaded
from solidworks_mcp.adapters.solidworks.loaded_documents import (
    SolidWorksLoadedDocumentsMixin,
)

from .fake_documents import FakeApp, FakeComponent, Harness


class _Adapter(Harness, SolidWorksLoadedDocumentsMixin):
    pass


def _dipper(tmp_path: Path) -> tuple[FakeApp, dict[str, object]]:
    """The master open, an assembly open, and two parts the closed assembly left."""
    app = FakeApp()
    master = app.add("TheDipper.SLDPRT", str(tmp_path / "TheDipper.SLDPRT"))
    used = app.add("Holder.SLDPRT", str(tmp_path / "Holder.SLDPRT"), visible=False)
    assembly = app.add(
        "Rack.SLDASM",
        str(tmp_path / "Rack.SLDASM"),
        2,
        components=[FakeComponent("Holder-1", used)],
    )
    comb = app.add(
        "TheDipper_LoadingComb.SLDPRT",
        str(tmp_path / "TheDipper_LoadingComb.SLDPRT"),
        visible=False,
    )
    stand = app.add(
        "CoverslipStandIns.SLDPRT",
        str(tmp_path / "CoverslipStandIns.SLDPRT"),
        visible=False,
        modified=True,
    )
    drawn = app.add("Drawn.SLDPRT", str(tmp_path / "Drawn.SLDPRT"), visible=False)
    drawing = app.add("Sheet.SLDDRW", str(tmp_path / "Sheet.SLDDRW"), 3)
    app.dependencies[drawing.path] = [drawn.path]
    app.unlisted = {comb.title}  # only the GetFirstDocument walk finds it
    app.active = master
    (tmp_path / "~$TheDipper_LoadingComb.SLDPRT").write_text("lock")
    return app, {
        "master": master,
        "used": used,
        "assembly": assembly,
        "comb": comb,
        "stand": stand,
        "drawn": drawn,
    }


def test_lock_file_is_found_beside_the_file(tmp_path: Path) -> None:
    (tmp_path / "~$a.SLDPRT").write_text("lock")

    assert loaded.lock_file(str(tmp_path / "a.SLDPRT")) == str(tmp_path / "~$a.SLDPRT")
    assert loaded.lock_file(str(tmp_path / "b.SLDPRT")) is None
    assert loaded.lock_file("") is None


async def test_list_loaded_documents_finds_the_leftovers(tmp_path: Path) -> None:
    app, docs = _dipper(tmp_path)

    result = await _Adapter(app).list_loaded_documents()

    assert result.is_success
    by_title = {d["title"]: d for d in result.data["documents"]}
    assert result.data["count"] == 7
    # GetDocuments' order first; the walk adds the one it left out.
    assert result.data["leftovers"] == [
        "CoverslipStandIns.SLDPRT",
        "TheDipper_LoadingComb.SLDPRT",
    ]
    comb = by_title["TheDipper_LoadingComb.SLDPRT"]
    assert comb["visible"] is False
    assert comb["lock_file"] == str(tmp_path / "~$TheDipper_LoadingComb.SLDPRT")
    assert by_title["Holder.SLDPRT"]["used_by"] == ["Rack.SLDASM"]
    assert by_title["Drawn.SLDPRT"]["used_by"] == ["Sheet.SLDDRW"]
    assert by_title["TheDipper.SLDPRT"]["is_active"] is True
    assert by_title["TheDipper.SLDPRT"]["leftover"] is False
    assert by_title["CoverslipStandIns.SLDPRT"]["modified"] is True
    assert by_title["Rack.SLDASM"]["type"] == "Assembly"


async def test_close_leftovers_keeps_modified_ones(tmp_path: Path) -> None:
    app, _docs = _dipper(tmp_path)

    result = await _Adapter(app).close_leftover_documents()

    assert result.is_success
    assert app.closed == ["TheDipper_LoadingComb.SLDPRT"]
    assert result.data["closed"] == [
        {
            "title": "TheDipper_LoadingComb.SLDPRT",
            "path": str(tmp_path / "TheDipper_LoadingComb.SLDPRT"),
            # SolidWorks removes it; the fake doesn't.
            "lock_file": str(tmp_path / "~$TheDipper_LoadingComb.SLDPRT"),
        }
    ]
    assert result.data["kept_modified"] == [
        {
            "title": "CoverslipStandIns.SLDPRT",
            "path": str(tmp_path / "CoverslipStandIns.SLDPRT"),
        }
    ]
    assert result.data["still_loaded"] == []


async def test_a_leftover_sharing_a_title_is_closed_by_its_path() -> None:
    # Windows hides the extensions: the leftover part and the open assembly
    # are both "Tray", and CloseDoc("Tray") would close the assembly too.
    app = FakeApp()
    app.active = app.add("Tray", "C:/work/Tray.SLDASM", 2)
    app.add("Tray", "C:/work/Tray.SLDPRT", visible=False)

    result = await _Adapter(app).close_leftover_documents()

    assert app.closed == ["C:/work/Tray.SLDPRT"]
    assert [d.path for d in app.documents] == ["C:/work/Tray.SLDASM"]
    assert [d["path"] for d in result.data["closed"]] == ["C:/work/Tray.SLDPRT"]


async def test_a_leftover_solidworks_keeps_is_reported(tmp_path: Path) -> None:
    app, _docs = _dipper(tmp_path)
    app.sticky = {"TheDipper_LoadingComb.SLDPRT"}

    result = await _Adapter(app).close_leftover_documents()

    assert result.data["closed"] == []
    assert [d["title"] for d in result.data["still_loaded"]] == [
        "TheDipper_LoadingComb.SLDPRT"
    ]


async def test_not_connected() -> None:
    adapter = _Adapter(None)

    assert (
        await adapter.list_loaded_documents()
    ).error == "Not connected to SolidWorks"
    assert (
        await adapter.close_leftover_documents()
    ).error == "Not connected to SolidWorks"


def test_an_unreadable_document_is_described_as_unknown() -> None:
    adapter = Harness(FakeApp())
    odd = SimpleNamespace(
        GetTitle=lambda: None,
        GetPathName=lambda: 7,
        GetType=lambda: None,
        Visible=1,
        GetSaveFlag=lambda: "yes",
    )

    described = loaded.describe(adapter, odd)

    assert (described.title, described.path, described.type) == ("", "", 0)
    assert (described.visible, described.modified) == (None, None)
    assert described.key == "<>"
    assert described.describe(None)["type"] == "Unknown"


def test_no_active_document_and_an_empty_session() -> None:
    app = FakeApp()
    app.GetDocuments = lambda: (None,)  # type: ignore[method-assign]
    adapter = Harness(app)

    assert loaded.active_key(adapter) is None
    assert loaded.loaded_documents(adapter) == []


def test_a_walk_that_never_ends_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FakeApp()
    doc = app.add("Loop.SLDPRT", "C:/x/Loop.SLDPRT")
    doc.GetNext = lambda: doc  # type: ignore[method-assign]
    monkeypatch.setattr(loaded, "MAX_DOCUMENTS", 3)

    assert len(loaded._walk(Harness(app), app)) == 3


# ---- an assembly's parts --------------------------------------------------------


def _rack(tmp_path: Path) -> tuple[FakeApp, object]:
    app = FakeApp()
    holder = app.add("Holder.SLDPRT", str(tmp_path / "Holder.SLDPRT"), visible=False)
    peg = app.add(
        "Peg.SLDPRT", str(tmp_path / "Peg.SLDPRT"), visible=False, config="Long"
    )
    sub = app.add("Sub.SLDASM", str(tmp_path / "Sub.SLDASM"), 2, visible=False)
    components = [FakeComponent(f"Holder-{i}", holder) for i in range(1, 7)]
    components += [
        FakeComponent("Peg-1", peg, config="Short"),
        FakeComponent("Peg-2", peg, suppressed=True),
        FakeComponent("Sub-1", sub),
        FakeComponent("Ghost-1", None),
        FakeComponent("Virtual-1", peg, config="Long", virtual=True),
    ]
    assembly = app.add(
        "Rack.SLDASM", str(tmp_path / "Rack.SLDASM"), 2, components=components
    )
    app.active = assembly
    return app, assembly


async def test_list_assembly_parts_once_per_file_and_configuration(
    tmp_path: Path,
) -> None:
    app, assembly = _rack(tmp_path)

    result = await _Adapter(app, assembly).list_assembly_parts()

    assert result.is_success
    assert result.data["assembly"] == {
        "title": "Rack.SLDASM",
        "path": str(tmp_path / "Rack.SLDASM"),
    }
    holder, short_peg, long_peg = result.data["parts"]
    assert holder["instances"] == 6
    assert holder["components"] == [f"Holder-{i}" for i in range(1, 6)]
    assert (holder["configuration"], holder["active_configuration"]) == (
        "Default",
        "Default",
    )
    assert (short_peg["configuration"], short_peg["active_configuration"]) == (
        "Short",
        "Long",
    )
    assert long_peg["virtual"] is True


async def test_list_assembly_parts_needs_an_assembly(tmp_path: Path) -> None:
    app = FakeApp()
    part = app.add("Holder.SLDPRT", str(tmp_path / "Holder.SLDPRT"))

    no_model = await _Adapter(app).list_assembly_parts()
    a_part = await _Adapter(app, part).list_assembly_parts()

    assert no_model.error == "No active model"
    assert "requires an active assembly document" in (a_part.error or "")


def test_configuration_names_from_either_accessor() -> None:
    adapter = Harness(FakeApp())
    by_method = SimpleNamespace(
        GetActiveConfiguration=lambda: SimpleNamespace(Name=None, GetName=lambda: "A")
    )
    none = SimpleNamespace(GetActiveConfiguration=lambda: None)
    unreadable = SimpleNamespace(
        GetActiveConfiguration=lambda: SimpleNamespace(Name=None, GetName=lambda: 3)
    )

    assert loaded._configuration_name(adapter, by_method) == "A"
    assert loaded._configuration_name(adapter, none) is None
    assert loaded._configuration_name(adapter, unreadable) is None
