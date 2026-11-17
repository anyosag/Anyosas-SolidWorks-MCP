"""A file's references, and moving a file with them (``references``)."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import references as refs


class _App:
    """``ISldWorks``: references by path; moves and repoints on disk."""

    def __init__(self, table: dict[str, list[str]]) -> None:
        self.table = {refs.normalize(k): list(v) for k, v in table.items()}
        self.documents: list[Any] = []
        self.move_code = 0
        self.move_raises: Exception | None = None
        self.repoint_refuses: set[str] = set()
        self.unreadable: set[str] = set()
        self.calls: list[tuple[Any, ...]] = []

    def GetDocumentDependencies2(self, path, traverse, search, read_only):  # noqa: N802
        self.calls.append(("deps", path, traverse, search, read_only))
        if refs.normalize(path) in self.unreadable:
            raise RuntimeError("file is corrupt")
        found = self.table.get(refs.normalize(path))
        if not found:
            return None
        flat: list[str] = []
        for item in found:
            flat += [Path(item).stem, item]
        return tuple(flat)

    def GetDocuments(self) -> list[Any]:  # noqa: N802
        return self.documents

    def MoveDocument(self, src, dst, from_children, to_children, option):  # noqa: N802
        self.calls.append(("move", src, dst, option))
        if self.move_raises is not None:
            raise self.move_raises
        if self.move_code == 0:
            shutil.move(src, dst)
        return self.move_code

    def ReplaceReferencedDocument(self, referencing, old, new):  # noqa: N802
        self.calls.append(("repoint", referencing, old, new))
        if refs.normalize(referencing) in self.repoint_refuses:
            return False
        key = refs.normalize(referencing)
        self.table[key] = [
            new if refs.normalize(p) == refs.normalize(old) else p
            for p in self.table.get(key, [])
        ]
        return True


class _Adapter:
    def __init__(self, app: _App) -> None:
        self.swApp = app

    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


def _doc(path: str, dirty: bool = False) -> Any:
    return SimpleNamespace(GetPathName=lambda: path, GetSaveFlag=lambda: dirty)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    for name in ("bracket.sldprt", "frame.sldasm", "frame.slddrw", "other.sldprt"):
        (tmp_path / name).write_bytes(b"sw")
    (tmp_path / "notes.txt").write_text("not a document", encoding="utf-8")
    return tmp_path


def _uses(folder: Path) -> dict[str, list[str]]:
    part = str(folder / "bracket.sldprt")
    return {
        str(folder / "frame.sldasm"): [part, str(folder / "other.sldprt")],
        str(folder / "frame.slddrw"): [str(folder / "frame.sldasm"), part],
    }


# ---- pure parts ------------------------------------------------------------------


def test_dependencies_come_in_pairs_each_path_once() -> None:
    raw = ("A", "C:/x/A.sldprt", "B", "C:/x/B.sldprt", "a", "c:/X/a.SLDPRT", "odd")

    assert refs.parse_dependencies(raw) == [
        ("A", "C:/x/A.sldprt"),
        ("B", "C:/x/B.sldprt"),
    ]
    assert refs.parse_dependencies(None) == []


def test_a_reference_says_whether_its_file_is_there(folder: Path) -> None:
    referencing = str(folder / "frame.sldasm")
    there = refs.describe_reference(
        "bracket", str(folder / "bracket.sldprt"), referencing
    )
    moved = refs.describe_reference(
        "bracket", str(folder / "old" / "bracket.sldprt"), referencing
    )
    gone = refs.describe_reference(
        "gone", str(folder / "old" / "gone.sldprt"), referencing
    )

    assert there == {
        "name": "bracket",
        "path": str(folder / "bracket.sldprt"),
        "type": "part",
        "exists": True,
        "read_only": False,
    }
    assert moved["exists"] is False
    assert moved["found_beside"] == str(folder / "bracket.sldprt")
    assert "found_beside" not in gone


def test_only_solidworks_documents_on_disk_are_taken(folder: Path) -> None:
    with pytest.raises(ValueError, match="is not a SolidWorks part"):
        refs.check_document(str(folder / "notes.txt"))
    with pytest.raises(LookupError, match="File not found"):
        refs.check_document(str(folder / "none.sldprt"))


def test_a_move_is_checked_before_anything_happens(folder: Path) -> None:
    part = str(folder / "bracket.sldprt")
    sub = folder / "parts"
    sub.mkdir()

    assert refs.resolve_move(part, str(sub)) == (part, str(sub / "bracket.sldprt"))
    with pytest.raises(ValueError, match="the destination is the source itself"):
        refs.resolve_move(part, part.upper())
    with pytest.raises(ValueError, match=r"must end in \.sldprt like the source"):
        refs.resolve_move(part, str(folder / "bracket.sldasm"))
    with pytest.raises(ValueError, match="no extension"):
        refs.resolve_move(part, str(folder / "bracket"))
    with pytest.raises(ValueError, match="already exists, and nothing is overwritten"):
        refs.resolve_move(part, str(folder / "other.sldprt"))


def test_candidates_are_the_given_files_then_each_folders_documents(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = str(folder.parent / "top.sldasm")
    exclude = {refs.normalize(str(folder / "bracket.sldprt"))}

    files, truncated = refs.candidate_files([str(folder)], [elsewhere], exclude)
    monkeypatch.setattr(refs, "MAX_SCANNED", 2)
    few, cut = refs.candidate_files([str(folder)], [], exclude)

    assert files == [
        elsewhere,
        str(folder / "frame.sldasm"),
        str(folder / "frame.slddrw"),
        str(folder / "other.sldprt"),
    ]
    assert truncated is False
    assert len(few) == 2 and cut is True
    with pytest.raises(ValueError, match="is not a folder"):
        refs.candidate_files([str(folder / "notes.txt")], [], set())


def test_a_reference_by_its_old_path_counts_only_beside_the_source(
    folder: Path,
) -> None:
    part = str(folder / "bracket.sldprt")
    old = [("bracket", "D:/old/bracket.sldprt")]

    assert refs.stored_reference(part, str(folder / "a.sldasm"), [("b", part)]) == part
    assert refs.stored_reference(part, str(folder / "a.sldasm"), old) == old[0][1]
    assert refs.stored_reference(part, "E:/elsewhere/a.sldasm", old) is None
    assert (
        refs.stored_reference(part, str(folder / "a.sldasm"), [("x", "D:/x.sldprt")])
        is None
    )


def test_a_plan_finds_the_users_and_what_stops_the_move(folder: Path) -> None:
    app = _App(_uses(folder))
    part, drawing = str(folder / "bracket.sldprt"), str(folder / "frame.slddrw")
    app.unreadable.add(refs.normalize(str(folder / "other.sldprt")))

    def references_of(path: str) -> Any:
        return _Adapter(app)._attempt(
            lambda: refs.dependencies(_Adapter(app), path, False)
        )

    plan = refs.plan_move(
        part,
        str(folder / "new" / "plate.sldprt"),
        [str(folder / "gone.sldasm"), str(folder.parent / "lonely.sldasm")],
        None,
        references_of,
        {refs.normalize(drawing): False},
    )

    assert plan["destination"] == str(folder / "new" / "plate.sldprt")
    assert plan["creates_folder"] is True
    assert plan["search_folders"] == [str(folder)]
    assert [u["path"] for u in plan["users"]] == [str(folder / "frame.sldasm"), drawing]
    assert plan["users"][0]["references_as"] == part
    assert plan["unreadable"] == [str(folder / "other.sldprt")]
    assert plan["not_found"] == [
        str(folder / "gone.sldasm"),
        str(folder.parent / "lonely.sldasm"),
    ]
    assert plan["blocked"] == [
        {
            "path": drawing,
            "reason": "uses it and is open in SolidWorks; its references can be "
            "changed only while it is closed, so close it first",
        }
    ]


def test_a_given_document_that_does_not_use_it_is_named(folder: Path) -> None:
    other = str(folder / "other.sldprt")

    plan = refs.plan_move(
        str(folder / "bracket.sldprt"),
        str(folder / "plate.sldprt"),
        [other],
        [str(folder)],
        lambda _path: [],
        {},
    )

    assert plan["not_using"] == [other]
    assert plan["users"] == []


@pytest.mark.parametrize(
    ("dirty", "reason"),
    [
        (True, "has unsaved changes in SolidWorks; save or close it first"),
        (False, "is open in SolidWorks, which holds the file; close it first"),
    ],
)
def test_an_open_source_is_blocked(folder: Path, dirty: bool, reason: str) -> None:
    part = str(folder / "bracket.sldprt")

    plan = refs.plan_move(
        part,
        str(folder / "plate.sldprt"),
        None,
        None,
        lambda _p: [],
        {refs.normalize(part): dirty},
    )

    assert plan["blocked"][0]["path"] == part
    assert plan["blocked"][0]["reason"].startswith(reason)
    assert refs.blocked_message(plan).startswith(
        f"Invalid request: nothing was moved: {part} "
    )


def test_a_read_only_user_is_blocked(folder: Path) -> None:
    assembly = folder / "frame.sldasm"
    assembly.chmod(stat.S_IREAD)
    try:
        plan = refs.plan_move(
            str(folder / "bracket.sldprt"),
            str(folder / "plate.sldprt"),
            None,
            None,
            lambda path: (
                [("b", str(folder / "bracket.sldprt"))] if path == str(assembly) else []
            ),
            {},
        )
    finally:
        assembly.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert plan["blocked"] == [
        {"path": str(assembly), "reason": "uses it and is read-only on disk"}
    ]


def test_a_source_another_program_holds_and_a_closed_folder_are_blocked(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = str(folder / "bracket.sldprt")
    monkeypatch.setattr(
        refs.file_access,
        "move_blocker",
        lambda path: "is held" if path == part else None,
    )
    monkeypatch.setattr(refs.file_access, "unwritable_folder", lambda _f: "is closed")

    plan = refs.plan_move(
        part, str(folder / "out" / "plate.sldprt"), None, None, lambda _p: [], {}
    )

    assert plan["blocked"] == [
        {"path": part, "reason": "is held"},
        {"path": str(folder / "out"), "reason": "is closed"},
    ]
    assert refs.dry_run_reply(plan)["can_move"] is False


def test_a_source_open_in_solidworks_is_not_also_reported_held(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = str(folder / "bracket.sldprt")
    monkeypatch.setattr(refs.file_access, "move_blocker", lambda _p: "is held")

    plan = refs.plan_move(
        part,
        str(folder / "plate.sldprt"),
        None,
        None,
        lambda _p: [],
        {refs.normalize(part): False},
    )

    assert [b["path"] for b in plan["blocked"]] == [part]
    assert plan["blocked"][0]["reason"].startswith("is open in SolidWorks")


def test_a_move_in_a_sync_folder_is_warned_about_once(tmp_path: Path) -> None:
    drive = tmp_path / "Shared drives" / "Team"
    drive.mkdir(parents=True)
    (drive / "dipper.sldprt").write_bytes(b"sw")
    (drive / "old").mkdir()

    plan = refs.plan_move(
        str(drive / "dipper.sldprt"),
        str(drive / "old" / "dipper.sldprt"),
        None,
        None,
        lambda _p: [],
        {},
    )
    local = refs.sync_warnings(str(tmp_path / "a.sldprt"), str(tmp_path / "b"))

    assert len(plan["warnings"]) == 1
    assert plan["warnings"][0].startswith(
        f"{drive / 'dipper.sldprt'} is in a Google Drive folder."
    )
    assert "falls back to a plain file move" in plan["warnings"][0]
    assert plan["blocked"] == []
    assert local == []


def test_moving_out_of_a_sync_folder_names_each_client(tmp_path: Path) -> None:
    warnings = refs.sync_warnings(
        str(tmp_path / "Dropbox" / "a.sldprt"),
        str(tmp_path / "OneDrive" / "parts" / "a.sldprt"),
    )

    assert [w.split(" is in a ")[1].split(" folder")[0] for w in warnings] == [
        "Dropbox",
        "OneDrive",
    ]


# ---- through SolidWorks ----------------------------------------------------------


def test_references_are_listed_without_opening_the_file(folder: Path) -> None:
    app = _App(
        {str(folder / "frame.slddrw"): [str(folder / "frame.sldasm"), "D:/x.sldprt"]}
    )

    data = refs.list_references(_Adapter(app), str(folder / "frame.slddrw"), True)

    assert data["count"] == 2 and data["traverse"] is True
    assert data["missing"] == ["D:/x.sldprt"]
    assert data["references"][0]["type"] == "assembly"
    assert app.calls == [("deps", str(folder / "frame.slddrw"), True, False, False)]


def test_open_documents_skip_one_never_saved() -> None:
    app = _App({})
    app.documents = [_doc("C:/a.sldprt", True), _doc(""), _doc("C:/b.SLDASM")]

    assert refs.open_documents(_Adapter(app)) == {
        refs.normalize("C:/a.sldprt"): True,
        refs.normalize("C:/b.SLDASM"): False,
    }


def test_a_dry_run_changes_nothing(folder: Path) -> None:
    app = _App(_uses(folder))

    data = refs.move_document(
        _Adapter(app),
        str(folder / "bracket.sldprt"),
        str(folder / "plate.sldprt"),
        None,
        None,
        True,
    )

    assert data["dry_run"] is True and data["moved"] is False
    assert data["can_move"] is True
    assert data["would_update"] == [
        str(folder / "frame.sldasm"),
        str(folder / "frame.slddrw"),
    ]
    assert (folder / "bracket.sldprt").exists()
    assert not [c for c in app.calls if c[0] != "deps"]


def test_a_blocked_move_moves_nothing(folder: Path) -> None:
    app = _App(_uses(folder))
    app.documents = [_doc(str(folder / "bracket.sldprt"), True)]

    with pytest.raises(ValueError, match="nothing was moved"):
        refs.move_document(
            _Adapter(app),
            str(folder / "bracket.sldprt"),
            str(folder / "plate.sldprt"),
            None,
            None,
            False,
        )
    assert (folder / "bracket.sldprt").exists()


def test_a_move_repoints_every_user_and_reads_them_back(folder: Path) -> None:
    app = _App(_uses(folder))
    new = str(folder / "parts" / "plate.sldprt")

    data = refs.move_document(
        _Adapter(app), str(folder / "bracket.sldprt"), new, None, None, False
    )

    assert data["moved"] is True and data["method"] == "MoveDocument"
    assert Path(new).exists() and not (folder / "bracket.sldprt").exists()
    assert data["updated"] == [
        {"path": str(folder / "frame.sldasm"), "verified": True},
        {"path": str(folder / "frame.slddrw"), "verified": True},
    ]
    move = next(c for c in app.calls if c[0] == "move")
    assert move == ("move", str(folder / "bracket.sldprt"), new, 0)


def test_a_move_late_binding_cannot_make_falls_back_to_a_file_move(
    folder: Path,
) -> None:
    app = _App({})
    app.move_raises = TypeError("Type mismatch")

    data = refs.move_document(
        _Adapter(app),
        str(folder / "bracket.sldprt"),
        str(folder / "plate.sldprt"),
        None,
        None,
        False,
    )

    assert data["method"] == "file move (MoveDocument raised TypeError)"
    assert (folder / "plate.sldprt").exists()


def test_a_closed_file_solidworks_refuses_is_moved_as_a_file(folder: Path) -> None:
    # The Dipper: MoveDocument returned 2 for a closed part on Google Drive.
    app = _App(_uses(folder))
    app.move_code = 2
    new = str(folder / "parts" / "plate.sldprt")

    data = refs.move_document(
        _Adapter(app), str(folder / "bracket.sldprt"), new, None, None, False
    )

    assert data["method"] == "file move (MoveDocument returned 2 (swMoveCopyErrorFail))"
    assert Path(new).read_bytes() == b"sw"
    assert not (folder / "bracket.sldprt").exists()
    assert [u["verified"] for u in data["updated"]] == [True, True]


def test_a_refusal_that_moved_the_file_anyway_is_a_move(folder: Path) -> None:
    app = _App({})

    def move(src: str, dst: str, *_a: Any) -> int:
        shutil.move(src, dst)
        return 2

    app.MoveDocument = move  # type: ignore[method-assign]

    data = refs.move_document(
        _Adapter(app),
        str(folder / "bracket.sldprt"),
        str(folder / "plate.sldprt"),
        None,
        None,
        False,
    )

    assert data["method"] == (
        "MoveDocument (returned 2 (swMoveCopyErrorFail), yet the file moved)"
    )


def test_a_file_solidworks_holds_open_is_not_moved_behind_its_back(
    folder: Path,
) -> None:
    app = _App(_uses(folder))
    app.move_code = 2
    part = str(folder / "bracket.sldprt")
    original = app.MoveDocument

    def move(*args: Any) -> int:
        app.documents = [_doc(part)]  # opened since the plan was made
        return original(*args)

    app.MoveDocument = move  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="it has the file open") as raised:
        refs.move_document(
            _Adapter(app), part, str(folder / "plate.sldprt"), None, None, False
        )

    assert "returned 2 (swMoveCopyErrorFail)" in str(raised.value)
    assert Path(part).exists() and not (folder / "plate.sldprt").exists()
    assert not [c for c in app.calls if c[0] == "repoint"]


class _Locked(PermissionError):
    winerror = 32


def test_a_file_a_sync_client_holds_says_so(
    folder: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _App(_uses(folder))
    app.move_code = 2

    def locked(*_a: Any) -> None:
        raise _Locked(13, "The process cannot access the file")

    monkeypatch.setattr(refs.shutil, "move", locked)

    with pytest.raises(RuntimeError) as raised:
        refs.move_document(
            _Adapter(app),
            str(folder / "bracket.sldprt"),
            str(folder / "plate.sldprt"),
            None,
            None,
            False,
        )

    text = str(raised.value)
    assert "MoveDocument returned 2 (swMoveCopyErrorFail), and a plain file" in text
    assert "is open in another program" in text and "sync client" in text
    assert text.endswith("Nothing was changed.")
    assert not [c for c in app.calls if c[0] == "repoint"]


@pytest.mark.parametrize(("removable", "left"), [(True, False), (False, True)])
def test_a_copy_left_by_a_failed_move_to_another_drive_is_removed(
    folder: Path, monkeypatch: pytest.MonkeyPatch, removable: bool, left: bool
) -> None:
    app = _App({})
    app.move_code = 2
    dst = folder / "plate.sldprt"

    def copy_then_fail(src: str, to: str) -> None:
        shutil.copy2(src, to)  # the copy worked; deleting the source didn't
        raise _Locked(13, "in use")

    def remove(path: str) -> None:
        if not removable:
            raise PermissionError("in use")
        os.unlink(path)

    monkeypatch.setattr(refs.shutil, "move", copy_then_fail)
    monkeypatch.setattr(refs.os, "remove", remove)

    with pytest.raises(RuntimeError) as raised:
        refs.move_document(
            _Adapter(app),
            str(folder / "bracket.sldprt"),
            str(dst),
            None,
            None,
            False,
        )

    assert dst.exists() is left
    assert (folder / "bracket.sldprt").exists()
    assert str(raised.value).endswith(
        f"A copy was left at {dst}." if left else "Nothing was changed."
    )


def test_a_move_that_left_no_file_is_an_error(folder: Path) -> None:
    app = _App({})
    app.MoveDocument = lambda *_a: 0  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="reported success, but"):
        refs.move_document(
            _Adapter(app),
            str(folder / "bracket.sldprt"),
            str(folder / "plate.sldprt"),
            None,
            None,
            False,
        )


def test_a_refused_repoint_puts_everything_back(folder: Path) -> None:
    app = _App(_uses(folder))
    app.repoint_refuses.add(refs.normalize(str(folder / "frame.slddrw")))
    part = str(folder / "bracket.sldprt")

    with pytest.raises(RuntimeError) as raised:
        refs.move_document(
            _Adapter(app), part, str(folder / "plate.sldprt"), None, None, False
        )

    assert "so the move was undone: it was moved back and every document points" in str(
        raised.value
    )
    assert Path(part).exists()
    assert part in app.table[refs.normalize(str(folder / "frame.sldasm"))]


def test_an_undo_that_cannot_restore_a_user_says_which(folder: Path) -> None:
    app = _App(_uses(folder))
    drawing = refs.normalize(str(folder / "frame.slddrw"))
    assembly = refs.normalize(str(folder / "frame.sldasm"))
    original = app.ReplaceReferencedDocument

    def repoint(referencing: str, old: str, new: str) -> bool:
        if refs.normalize(referencing) == drawing:
            return False
        if refs.normalize(referencing) == assembly and "plate" in old:
            return False  # the way back is refused
        return original(referencing, old, new)

    app.ReplaceReferencedDocument = repoint  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match=r"still point to .*plate\.sldprt"):
        refs.move_document(
            _Adapter(app),
            str(folder / "bracket.sldprt"),
            str(folder / "plate.sldprt"),
            None,
            None,
            False,
        )


def test_an_undo_that_cannot_move_back_says_where_the_file_is(folder: Path) -> None:
    app = _App(_uses(folder))
    app.repoint_refuses.add(refs.normalize(str(folder / "frame.sldasm")))
    moves = {"count": 0}
    original = app.MoveDocument

    def move(*args: Any) -> int:
        moves["count"] += 1
        if moves["count"] == 1:
            return original(*args)
        app.documents = [_doc(str(folder / "plate.sldprt"))]  # SolidWorks has it
        return 2

    app.MoveDocument = move  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="could not be moved back"):
        refs.move_document(
            _Adapter(app),
            str(folder / "bracket.sldprt"),
            str(folder / "plate.sldprt"),
            None,
            None,
            False,
        )


def test_a_repoint_that_does_not_read_back_is_unverified(folder: Path) -> None:
    app = _App(_uses(folder))
    app.ReplaceReferencedDocument = lambda *_a: True  # type: ignore[method-assign]

    data = refs.move_document(
        _Adapter(app),
        str(folder / "bracket.sldprt"),
        str(folder / "plate.sldprt"),
        [str(folder / "frame.sldasm")],
        [str(folder.parent)],
        False,
    )

    assert data["updated"] == [
        {"path": str(folder / "frame.sldasm"), "verified": False}
    ]


def test_the_children_arrays_are_bstr_safearrays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform == "win32":
        import pythoncom

        array = refs._string_array([])
        assert array.varianttype == pythoncom.VT_ARRAY | pythoncom.VT_BSTR
    monkeypatch.setitem(sys.modules, "pythoncom", None)

    assert refs._string_array(["a"]) == ["a"]


def test_paths_compare_without_case_or_relative_parts() -> None:
    assert refs.normalize("C:/A/b.SLDPRT") == refs.normalize(
        os.path.join("c:/a", "B.sldprt")
    )
