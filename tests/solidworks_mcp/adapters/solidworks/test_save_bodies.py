"""Save Bodies on a fake SolidWorks: save a body, read, change and re-split the feature."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import save_bodies as sb
from solidworks_mcp.adapters.solidworks.save_bodies import SolidWorksSaveBodiesMixin

from .fake_documents import FakeApp, FakeBody, FakeDocument, Harness

DIALOG = {
    "title": "SOLIDWORKS Design",
    "texts": ["Do you want to create an assembly?"],
    "buttons": ["Yes", "No"],
    "closed": True,
    "handed_to_user": False,
}


class _Adapter(Harness, SolidWorksSaveBodiesMixin):
    pass


def _values(variant: Any) -> list[Any]:
    return list(getattr(variant, "value", variant))


class FakeSaveBodiesData:
    """An ``ISaveBodyFeatureData`` holding ``(body, path)`` pairs."""

    def __init__(
        self, pairs: list[tuple[FakeBody, str]], *, needs_access: bool = False
    ) -> None:
        self.pairs = pairs
        self.needs_access = needs_access
        self.accessed = False
        self.released = 0
        self.grant_access = True
        self.add_works = True
        self.AssemblyPath = ""
        self.ConsumeBody = False
        self.CopyCustomProperties = True
        self.calls: list[str] = []

    def GetSaveBodies(self, bodies: Any, paths: Any, flags: Any) -> None:
        pairs = [] if self.needs_access and not self.accessed else self.pairs
        bodies.value = tuple(b for b, _p in pairs)
        paths.value = tuple(p for _b, p in pairs)
        flags.value = tuple(False for _ in pairs)

    def AccessSelections(self, _model: Any, _component: Any) -> bool:
        self.accessed = self.grant_access
        return self.grant_access

    def ReleaseSelectionAccess(self) -> None:
        self.released += 1
        self.accessed = False

    def RemoveSaveBodies(self, bodies: Any) -> bool:
        removed = _values(bodies)
        self.calls.append("remove")
        self.pairs = [(b, p) for b, p in self.pairs if b not in removed]
        return True

    def AddSaveBodies(self, bodies: Any, paths: Any) -> bool:
        self.calls.append("add")
        if not self.add_works:
            return False
        for body, path in zip(_values(bodies), _values(paths), strict=True):
            Path(path).write_bytes(b"part")
            self.pairs.append((body, path))
        return True


class FakeFeature:
    def __init__(
        self, model: FakeDocument, name: str, kind: str, data: Any = None
    ) -> None:
        self.model = model
        self.Name = name
        self.kind = kind
        self.data = data
        self.modify_works = True

    def GetTypeName2(self) -> str:
        return self.kind

    def GetDefinition(self) -> Any:
        return self.data

    def ModifyDefinition(self, _data: Any, _model: Any, _component: Any) -> bool:
        if self.modify_works:
            self.data.accessed = False
        return self.modify_works

    def GetNextFeature(self) -> Any:
        features = self.model.features
        index = features.index(self)
        return features[index + 1] if index + 1 < len(features) else None


class FakeFeatureManager:
    """``CreateSaveBodyFeature`` as The Dipper saw it: an untitled assembly too."""

    def __init__(self, app: FakeApp, model: FakeDocument) -> None:
        self.app = app
        self.model = model
        self.calls: list[tuple[Any, ...]] = []
        self.writes = True
        self.stray_assembly = True
        self.load_derived = True
        self.derived_volume: float | None = None

    def CreateSaveBodyFeature(
        self, bodies: Any, paths: Any, assembly: str, consume: bool, copy: bool
    ) -> Any:
        self.calls.append((assembly, consume, copy))
        bodies, paths = _values(bodies), _values(paths)
        for body, path in zip(bodies, paths, strict=True):
            if self.writes:
                Path(path).write_bytes(b"part")
            if self.load_derived and self.writes:
                volume = self.derived_volume or body.volume
                self.app.add(
                    Path(path).name,
                    path,
                    visible=False,
                    bodies=[FakeBody("Stock", volume)],
                )
        if self.stray_assembly:
            self.app.active = self.app.add("Assem3", "", 2)
        if assembly:
            self.app.add(Path(assembly).name, assembly, 2)
        data = FakeSaveBodiesData(list(zip(bodies, paths, strict=True)))
        feature = FakeFeature(self.model, "Save Bodies1", "SaveBodies", data)
        self.model.features.append(feature)
        return feature


def _master(tmp_path: Path) -> tuple[FakeApp, FakeDocument, FakeFeatureManager]:
    app = FakeApp()
    master = app.add(
        "TheDipper.SLDPRT",
        str(tmp_path / "TheDipper.SLDPRT"),
        bodies=[FakeBody("Comb", 2e-6), FakeBody("Stand", 3e-6)],
    )
    manager = FakeFeatureManager(app, master)
    master.FeatureManager = manager
    app.active = master
    return app, master, manager


# ---- save_body_as_part ---------------------------------------------------------


async def test_argument_checks(tmp_path: Path) -> None:
    app, master, _manager = _master(tmp_path)
    drawing = app.add("Sheet.SLDDRW", str(tmp_path / "Sheet.SLDDRW"), 3)
    target = str(tmp_path / "comb.sldprt")

    results = [
        await _Adapter(app).save_body_as_part("Comb", target),
        await _Adapter(app, drawing).save_body_as_part("Comb", target),
        await _Adapter(app, master).save_body_as_part(" ", target),
        await _Adapter(app, master).save_body_as_part("Comb", " "),
        await _Adapter(app, master).save_body_as_part(
            "Comb", str(tmp_path / "missing" / "comb.sldprt")
        ),
    ]

    assert [r.error for r in results] == [
        "No active model",
        "Save Bodies requires an active part document",
        "body_name is required",
        "file_path is required",
        f"Parent directory does not exist: {tmp_path / 'missing'}",
    ]


async def test_saves_the_body_and_closes_the_untitled_assembly(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    target = tmp_path / "comb.sldprt"
    adapter = _Adapter(app, master)

    result = await adapter.save_body_as_part("Comb", str(target))

    assert result.is_success, result.error
    data = result.data
    assert data["feature"] == "Save Bodies1"
    assert data["solid_bodies"] == ["Comb", "Stand"]
    # The derived part SolidWorks opened, and the untitled assembly.
    assert data["closed_documents"] == ["comb.sldprt", "Assem3"]
    assert data["replaced"] is False
    check = data["file_check"]
    assert check["holds_body"] is True
    assert check["volume_mm3"] == check["body_volume_mm3"] == 2000.0
    assert check["written_now"] is True
    assert manager.calls == [("", False, True)]
    assert app.closed == ["comb.sldprt", "Assem3"]
    assert app.active is master and adapter.currentModel is master
    assert result.metadata is None


async def test_a_cancelled_dialog_is_a_warning_when_the_file_holds_the_body(
    tmp_path: Path,
) -> None:
    app, master, _manager = _master(tmp_path)
    adapter = _Adapter(app, master)
    adapter.dialog = DIALOG

    result = await adapter.save_body_as_part("Comb", str(tmp_path / "comb.sldprt"))

    assert result.is_success
    assert result.data["dialog"] == DIALOG
    warning = result.metadata["warning"]
    assert "'SOLIDWORKS Design': Do you want to create an assembly?" in warning
    assert "cancelled it" in warning
    assert "holds the body" in warning


async def test_a_dialog_handed_to_the_user_or_without_text(tmp_path: Path) -> None:
    app, master, _manager = _master(tmp_path)
    adapter = _Adapter(app, master)
    adapter.dialog = {"title": "", "texts": [], "handed_to_user": True}

    result = await adapter.save_body_as_part("Comb", str(tmp_path / "comb.sldprt"))

    warning = result.metadata["warning"]
    assert "(no text read)" in warning
    assert "brought SolidWorks up" in warning


async def test_a_dialog_stays_an_error_when_nothing_was_written(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    manager.writes = False
    adapter = _Adapter(app, master)
    adapter.dialog = DIALOG

    result = await adapter.save_body_as_part("Comb", str(tmp_path / "comb.sldprt"))

    assert result.is_error
    assert "showed a dialog" in (result.error or "")
    assert app.closed == ["Assem3"]  # cleaned up all the same
    assert app.active is master


async def test_the_documents_left_open_are_listed_in_the_same_operation(
    tmp_path: Path,
) -> None:
    app, master, _manager = _master(tmp_path)
    adapter = _Adapter(app, master)

    result = await adapter.save_body_as_part(
        "Comb", str(tmp_path / "comb.sldprt"), list_documents=True
    )

    assert result.is_success, result.error
    assert adapter.operations == ["save_body_as_part"]
    # The derived part and the untitled assembly were closed first.
    assert result.metadata["open_documents"] == [
        {
            "title": "TheDipper.SLDPRT",
            "type": "Part",
            "active": True,
            "modified": False,
        }
    ]


async def test_a_cancelled_dialog_keeps_the_list(tmp_path: Path) -> None:
    app, master, _manager = _master(tmp_path)
    adapter = _Adapter(app, master)
    adapter.dialog = DIALOG

    result = await adapter.save_body_as_part(
        "Comb", str(tmp_path / "comb.sldprt"), list_documents=True
    )

    assert result.is_success
    assert "holds the body" in result.metadata["warning"]
    assert [d["title"] for d in result.metadata["open_documents"]] == [
        "TheDipper.SLDPRT"
    ]


async def test_failures(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    target = str(tmp_path / "comb.sldprt")

    missing = await _Adapter(app, master).save_body_as_part("Lid", target)
    manager.writes = False
    unwritten = await _Adapter(app, master).save_body_as_part("Comb", target)
    master.FeatureManager = None
    no_manager = await _Adapter(app, master).save_body_as_part("Comb", target)
    master.bodies = []
    no_bodies = await _Adapter(app, master).save_body_as_part("Comb", target)

    assert "Body 'Lid' not found. Solid bodies: Comb, Stand" in (missing.error or "")
    assert "did not write a file" in (unwritten.error or "")
    assert "Part has no FeatureManager" in (no_manager.error or "")
    assert "has no solid bodies" in (no_bodies.error or "")


async def test_a_file_that_holds_another_volume_is_an_error(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    manager.derived_volume = 5e-6

    result = await _Adapter(app, master).save_body_as_part(
        "Comb", str(tmp_path / "comb.sldprt")
    )

    assert "holds 5000.0 mm3, not the body's 2000.0 mm3" in (result.error or "")


async def test_an_existing_file_is_replaced_unless_it_is_loaded(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    manager.stray_assembly = False
    target = tmp_path / "comb.sldprt"
    target.write_bytes(b"old")

    replaced = await _Adapter(app, master).save_body_as_part("Comb", str(target))
    app.add("comb.sldprt", str(target))  # the user opens it
    loaded = await _Adapter(app, master).save_body_as_part("Comb", str(target))

    assert replaced.is_success and replaced.data["replaced"] is True
    assert replaced.data["closed_documents"] == ["comb.sldprt"]
    assert "is loaded in SolidWorks as 'comb.sldprt'" in (loaded.error or "")
    assert "Invalid request" in (loaded.error or "")


async def test_a_file_not_loaded_is_opened_read_only_to_check_it(
    tmp_path: Path,
) -> None:
    app, master, manager = _master(tmp_path)
    manager.load_derived = False
    target = str(tmp_path / "comb.sldprt")
    app.on_open[target] = FakeDocument(
        app, "comb.sldprt", target, bodies=[FakeBody("Stock", 2e-6)]
    )

    result = await _Adapter(app, master).save_body_as_part("Comb", target)

    assert result.data["file_check"]["holds_body"] is True
    assert app.opened == [(target, sb.OPEN_SILENT_READ_ONLY)]
    assert "comb.sldprt" in app.closed


async def test_an_unreadable_file_is_reported_unchecked(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    manager.load_derived = False

    result = await _Adapter(app, master).save_body_as_part(
        "Comb", str(tmp_path / "comb.sldprt")
    )

    assert result.is_success
    assert result.data["file_check"]["holds_body"] is None
    assert result.data["file_check"]["volume_mm3"] is None


async def test_an_assembly_asked_for_stays_open(tmp_path: Path) -> None:
    app, master, manager = _master(tmp_path)
    manager.stray_assembly = False
    assembly = str(tmp_path / "Dipper.SLDASM")

    result = await _Adapter(app, master).save_body_as_part(
        "Comb", str(tmp_path / "comb.sldprt"), assembly
    )

    assert result.data["assembly_path"] == assembly
    assert manager.calls == [(assembly, False, True)]
    assert app.closed == ["comb.sldprt"]  # the assembly stays


async def test_a_derived_part_with_changes_or_open_before_stays_open(
    tmp_path: Path,
) -> None:
    app, master, manager = _master(tmp_path)
    manager.stray_assembly = False
    target = tmp_path / "comb.sldprt"

    def changed(*args: object, **kwargs: object) -> object:
        document = FakeApp.add(app, *args, **kwargs)
        document.modified = True
        return document

    app.add = changed  # type: ignore[method-assign]
    result = await _Adapter(app, master).save_body_as_part("Comb", str(target))

    assert result.data["closed_documents"] == []
    assert sb.close_stray_assemblies(_Adapter(app, master), set(), (str(target),)) == []


def test_volume_helpers() -> None:
    adapter = Harness(FakeApp())
    odd = SimpleNamespace(GetMassProperties=lambda _d: (0.0, "x", 0.0, "volume"))

    assert sb.body_volume_m3(adapter, odd) is None
    assert sb.holds(None, 1.0) is None
    assert sb.holds(1.0, 0.0) is None
    assert sb.holds(1.0005, 1.0) is True
    assert sb.holds(1.01, 1.0) is False


def test_tolerate_dialog_needs_a_checked_result() -> None:
    result: AdapterResult[Any] = AdapterResult(
        status=AdapterResultStatus.ERROR,
        error="dialog",
        data={"dialog": DIALOG},
    )

    assert sb._tolerate_dialog(result, {}, "x") is result


def test_reactivate_keeps_the_model_when_activation_fails() -> None:
    app = FakeApp()
    other = app.add("Other.SLDPRT", "C:/o.SLDPRT")
    app.active = other
    adapter = Harness(app)
    gone = FakeDocument(app, "Gone.SLDPRT", "C:/g.SLDPRT")

    sb.reactivate(adapter, gone, "Gone.SLDPRT")

    assert adapter.currentModel is gone


def test_reactivate_activates_the_part_again() -> None:
    app = FakeApp()
    part = app.add("Part.SLDPRT", "C:/p.SLDPRT")
    app.active = app.add("Assem5", "", 2)
    adapter = Harness(app)

    sb.reactivate(adapter, object(), "Part.SLDPRT")

    assert app.active is part and adapter.currentModel is part


async def test_the_active_document_is_read_first(tmp_path: Path) -> None:
    app, master, _manager = _master(tmp_path)

    class _Synced(_Adapter):
        def _sync_current_model_from_active(self) -> None:
            self.currentModel = self.swApp.ActiveDoc

    result = await _Synced(app).resplit_bodies()
    drawing = app.add("Sheet.SLDDRW", "C:/s.SLDDRW", 3)
    app.active = drawing
    refused = await _Synced(app).resplit_bodies()

    assert "No Save Bodies feature found" in (result.error or "")
    assert refused.error == "Save Bodies requires an active part document"


# ---- list_save_bodies ------------------------------------------------------------


def _with_features(tmp_path: Path) -> tuple[FakeApp, FakeDocument, FakeSaveBodiesData]:
    app, master, _manager = _master(tmp_path)
    comb, stand = master.bodies
    comb_path, stand_path = (
        str(tmp_path / "comb.sldprt"),
        str(tmp_path / "stand.sldprt"),
    )
    Path(comb_path).write_bytes(b"part")
    (tmp_path / "~$comb.sldprt").write_text("lock")
    data = FakeSaveBodiesData([(comb, comb_path), (stand, stand_path)])
    master.features = [
        FakeFeature(master, "Origin", "OriginProfileFeature"),
        FakeFeature(master, "Save Bodies1", "SaveBodies", data),
        FakeFeature(
            master,
            "Save Bodies2",
            "Unknown",
            FakeSaveBodiesData([], needs_access=True),
        ),
        FakeFeature(master, "Boss-Extrude1", "Extrusion"),
    ]
    return app, master, data


async def test_list_save_bodies_reads_every_feature(tmp_path: Path) -> None:
    app, master, _data = _with_features(tmp_path)

    result = await _Adapter(app, master).get_save_bodies()

    assert result.is_success, result.error
    first, second = result.data["features"]
    assert first["feature"] == "Save Bodies1"
    assert first["bodies"] == [
        {
            "body": "Comb",
            "file_path": str(tmp_path / "comb.sldprt"),
            "consumed": False,
            "exists": True,
            "lock_file": str(tmp_path / "~$comb.sldprt"),
        },
        {
            "body": "Stand",
            "file_path": str(tmp_path / "stand.sldprt"),
            "consumed": False,
            "exists": False,
            "lock_file": None,
        },
    ]
    assert first["assembly_path"] == ""
    assert first["copy_custom_properties"] is True
    assert first["settable"] == list(sb.SETTABLE)
    assert first["selections_accessed"] is False
    assert second["feature"] == "Save Bodies2"  # found by its default name
    assert second["selections_accessed"] is True


async def test_list_save_bodies_errors(tmp_path: Path) -> None:
    app, master, _data = _with_features(tmp_path)
    adapter = _Adapter(app, master)

    named = await adapter.get_save_bodies("Save Bodies1")
    unknown = await adapter.get_save_bodies("Save Bodies9")
    not_one = await adapter.get_save_bodies("Boss-Extrude1")
    master.features = []
    none = await adapter.get_save_bodies()
    drawing = app.add("Sheet.SLDDRW", "C:/s.SLDDRW", 3)
    wrong = await _Adapter(app, drawing).get_save_bodies()

    assert [f["feature"] for f in named.data["features"]] == ["Save Bodies1"]
    assert "'Save Bodies9' not found" in (unknown.error or "")
    assert "Invalid request: 'Boss-Extrude1' is not a Save Bodies" in (
        not_one.error or ""
    )
    assert "No Save Bodies feature found" in (none.error or "")
    assert wrong.error == "Save Bodies requires an active part document"


def test_definitions_read_back_as_a_tuple_or_not_at_all() -> None:
    adapter = Harness(FakeApp())
    body = FakeBody("Comb")
    as_tuple = SimpleNamespace(
        GetSaveBodies=lambda *_outs: ((body,), ("C:/c.sldprt",), (True,))
    )
    short = SimpleNamespace(GetSaveBodies=lambda *_outs: ((body,), (), ()))
    no_definition = SimpleNamespace(GetDefinition=lambda: None)

    assert sb.entries(adapter, as_tuple) == [(body, "Comb", "C:/c.sldprt", True)]
    assert sb.entries(adapter, short) == [(body, "Comb", "", None)]
    with pytest.raises(Exception, match="gave no definition"):
        sb._definition(adapter, no_definition)


# ---- edit_save_bodies ------------------------------------------------------------


async def test_edit_sets_the_options_and_reads_them_back(tmp_path: Path) -> None:
    app, master, data = _with_features(tmp_path)

    result = await _Adapter(app, master).edit_save_bodies(
        "Save Bodies1",
        {
            "assembly_path": "",
            "consume_body": True,
            "copy_custom_properties": False,
        },
    )

    assert result.is_success, result.error
    assert result.data["changed"] == [
        "assembly_path",
        "consume_body",
        "copy_custom_properties",
    ]
    assert (data.ConsumeBody, data.CopyCustomProperties) == (True, False)
    assert result.data["definition"]["consume_body"] is True
    assert data.calls == []  # no body removed or added
    assert master.rebuilds == 1


async def test_a_new_file_for_a_body_is_refused_before_anything_changes(
    tmp_path: Path,
) -> None:
    app, master, data = _with_features(tmp_path)

    result = await _Adapter(app, master).edit_save_bodies(
        "Save Bodies1", {"files": {"Comb": str(tmp_path / "comb_v2.sldprt")}}
    )

    assert result.error == sb.FILES_REFUSED
    assert "delete_feature" in sb.FILES_REFUSED
    assert data.calls == [] and not data.accessed


async def test_an_option_solidworks_ignores_is_an_error(tmp_path: Path) -> None:
    app, master, data = _with_features(tmp_path)

    class Stubborn(type(data)):  # type: ignore[misc]
        def __setattr__(self, name: str, value: Any) -> None:
            if name == "ConsumeBody" and hasattr(self, "ConsumeBody"):
                return  # SolidWorks keeps its own value
            super().__setattr__(name, value)

    data.__class__ = Stubborn
    result = await _Adapter(app, master).edit_save_bodies(
        "Save Bodies1", {"consume_body": True}
    )

    assert "the feature reads otherwise: consume_body reads False" in (
        result.error or ""
    )


def test_unapplied_options_are_named() -> None:
    after = {"assembly_path": "", "consume_body": False}

    assert sb._unapplied(after, {"consume_body": False}) == []
    assert sb._unapplied(
        after, {"consume_body": True, "assembly_path": "C:/r.sldasm"}
    ) == ["assembly_path reads ''", "consume_body reads False"]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({}, "give at least one of"),
        ({"radius": 1}, "not radius"),
    ],
)
async def test_edit_refuses_what_cannot_be_used(
    tmp_path: Path, changes: dict, message: str
) -> None:
    app, master, data = _with_features(tmp_path)

    result = await _Adapter(app, master).edit_save_bodies("Save Bodies1", changes)

    assert message in (result.error or "")
    assert not data.accessed  # nothing left rolled back


async def test_edit_failures_release_the_feature(tmp_path: Path) -> None:
    app, master, data = _with_features(tmp_path)
    adapter = _Adapter(app, master)

    data.grant_access = False
    refused = await adapter.edit_save_bodies("Save Bodies1", {"consume_body": True})
    data.grant_access = True
    master.features[1].modify_works = False
    not_modified = await adapter.edit_save_bodies(
        "Save Bodies1", {"consume_body": True}
    )

    assert "AccessSelections returned False" in (refused.error or "")
    assert "ModifyDefinition returned False" in (not_modified.error or "")
    assert data.released == 1


async def test_edit_needs_a_part(tmp_path: Path) -> None:
    app = FakeApp()

    result = await _Adapter(app).edit_save_bodies("Save Bodies1", {"files": {}})

    assert result.error == "No active model"


# ---- resplit_bodies --------------------------------------------------------------


async def test_resplit_updates_each_file_in_place(tmp_path: Path) -> None:
    app, master, _manager = _master(tmp_path)
    comb, stand = master.bodies
    lid, peg, cap = FakeBody("Lid", 1e-6), FakeBody("Peg", 1e-6), FakeBody("Cap", 4e-6)
    paths = {
        name: tmp_path / f"{name}.sldprt"
        for name in ("comb", "stand", "lid", "peg", "cap", "gone")
    }
    for name in ("comb", "stand", "lid", "peg", "cap"):
        paths[name].write_bytes(b"old")
    # comb: only an assembly had it loaded; stand: open with a window;
    # lid: unsaved changes; peg: SolidWorks won't open it; cap: its save fails.
    app.on_open[str(paths["comb"])] = FakeDocument(
        app, "comb.sldprt", str(paths["comb"]), bodies=[FakeBody("Stock", 2e-6)]
    )
    stand_doc = app.add(
        "stand.sldprt", str(paths["stand"]), bodies=[FakeBody("S", 3e-6)]
    )
    app.add("lid.sldprt", str(paths["lid"]), visible=False, modified=True)
    cap_doc = app.add("cap.sldprt", str(paths["cap"]), visible=False)

    def broken_save(*_args: Any) -> bool:
        raise RuntimeError("swFileLockError")

    cap_doc.Save3 = broken_save  # type: ignore[method-assign]
    data = FakeSaveBodiesData(
        [
            (comb, str(paths["comb"])),
            (stand, str(paths["stand"])),
            (lid, str(paths["lid"])),
            (peg, str(paths["peg"])),
            (cap, str(paths["cap"])),
            (FakeBody("Gone"), str(paths["gone"])),
        ],
        needs_access=True,
    )
    master.features = [FakeFeature(master, "Save Bodies1", "SaveBodies", data)]

    result = await _Adapter(app, master).resplit_bodies()

    assert result.is_success, result.error
    by_body = {f["body"]: f for f in result.data["files"]}
    assert by_body["Comb"]["status"] == "updated"
    assert by_body["Comb"]["holds_body"] is True
    assert by_body["Comb"]["written_now"] is True
    assert by_body["Stand"]["status"] == "updated"
    assert stand_doc.saves == 1
    assert by_body["Lid"]["status"] == "kept_modified"
    assert by_body["Peg"] == {
        "feature": "Save Bodies1",
        "body": "Peg",
        "file_path": str(paths["peg"]),
        "status": "failed",
        "error": "SolidWorks would not open it",
    }
    assert by_body["Cap"]["status"] == "failed"
    assert "swFileLockError" in by_body["Cap"]["error"]
    assert by_body["Gone"]["status"] == "missing"
    assert result.data["counts"] == {
        "updated": 2,
        "kept_modified": 1,
        "failed": 2,
        "missing": 1,
    }
    # The windows this call opened are closed again; the open one stays.
    assert "comb.sldprt" in app.closed and "cap.sldprt" in app.closed
    assert "stand.sldprt" not in app.closed
    assert data.released == 1


async def test_resplit_needs_a_save_bodies_feature(tmp_path: Path) -> None:
    app, master, _manager = _master(tmp_path)

    result = await _Adapter(app, master).resplit_bodies("Save Bodies1")

    assert "'Save Bodies1' not found" in (result.error or "")
