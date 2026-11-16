"""A file's references, read without opening it, and moving a file with them.

* :func:`list_references`: ``ISldWorks.GetDocumentDependencies2(path,
  traverse, False, False)`` gives ``[name, path, name, path, ...]`` for the
  files a document references, as saved in it (the search flag off: the
  search rules would also change SolidWorks' current folder). Each path is
  checked on disk, and a missing one is looked for beside the referencing
  file, where SolidWorks' search also looks.
* :func:`move_document`: ``ISldWorks.MoveDocument(source, destination,
  FromChildren, ToChildren, options)`` moves the file. Its children are the
  documents the *moved* file references (the API help), so it doesn't repoint
  the documents that use it. Those are found with ``GetDocumentDependencies2``
  on the given documents and the SolidWorks files of the search folders, and
  repointed with ``ISldWorks.ReplaceReferencedDocument(referencing, old,
  new)``, which works only on closed files. A dry run (the default) says all
  of that and changes nothing; a failed repoint puts everything back.

Enum values are from SolidWorks 2026's ``swconst`` (``swMoveCopyError_e``).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from typing import Any

from . import limits
from .com_utils import as_list
from .com_utils import flag as _flag

#: The SolidWorks document a file extension stands for.
DOCUMENT_TYPES = {".sldprt": "part", ".sldasm": "assembly", ".slddrw": "drawing"}

#: ``swMoveCopyError_e``.
MOVE_ERRORS = {
    0: "swMoveCopyErrorNone",
    1: "swMoveCopyErrorSourceDoesNotExist",
    2: "swMoveCopyErrorFail",
}

#: The most files one ``move_document`` call reads the references of.
MAX_SCANNED = 500

#: ``(name, path)`` pairs of a document's references, or None when unreadable.
ReferencesOf = Callable[[str], list[tuple[str, str]] | None]


def normalize(path: str) -> str:
    """A path in the form used to compare files (absolute, case-folded)."""
    return os.path.normcase(os.path.abspath(path))


def parse_dependencies(raw: Any) -> list[tuple[str, str]]:
    """``GetDocumentDependencies2``'s flat ``[name, path, ...]`` as pairs, once each.

    Args:
        raw: What the call returned: a tuple of strings, or None for a file
            that references nothing.

    Returns:
        list[tuple[str, str]]: ``(name, path)`` per referenced file, in the
        order given, each path once.
    """
    items = [str(item) for item in as_list(raw) if item is not None]
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index in range(0, len(items) - 1, 2):
        name, path = items[index], items[index + 1]
        if normalize(path) not in seen:
            seen.add(normalize(path))
            pairs.append((name, path))
    return pairs


def describe_reference(name: str, path: str, referencing: str) -> dict[str, Any]:
    """One reference: its name, saved path, type, and whether the file is there.

    Returns:
        dict[str, Any]: ``name``, ``path``, ``type`` (part, assembly,
        drawing or None), ``exists``; ``read_only`` when it exists, and
        ``found_beside`` (the same file name in the referencing file's
        folder, where SolidWorks would find it) when it doesn't.
    """
    exists = os.path.isfile(path)
    info: dict[str, Any] = {
        "name": name,
        "path": path,
        "type": DOCUMENT_TYPES.get(os.path.splitext(path)[1].lower()),
        "exists": exists,
    }
    if exists:
        info["read_only"] = not os.access(path, os.W_OK)
        return info
    beside = os.path.join(
        os.path.dirname(os.path.abspath(referencing)), os.path.basename(path)
    )
    if os.path.isfile(beside):
        info["found_beside"] = beside
    return info


def check_document(path: str) -> None:
    """Refuse a path that isn't a SolidWorks document on disk.

    Raises:
        ValueError: Not a .sldprt, .sldasm or .slddrw (``invalid_input``).
        LookupError: No such file (``not_found``).
    """
    if os.path.splitext(path)[1].lower() not in DOCUMENT_TYPES:
        raise ValueError(
            f"Invalid request: {path} is not a SolidWorks part, assembly or "
            "drawing (.sldprt, .sldasm or .slddrw)."
        )
    if not os.path.isfile(path):
        raise LookupError(f"File not found: {path}")


def resolve_move(source: str, destination: str) -> tuple[str, str]:
    """The absolute source and destination of a move, checked.

    A destination that is a folder takes the source's file name.

    Raises:
        ValueError: The destination is the source, has another extension, or
            exists already (``invalid_input``); nothing is overwritten.
        LookupError: The source isn't there.
    """
    src = os.path.abspath(source)
    check_document(src)
    dst = os.path.abspath(destination)
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    if normalize(dst) == normalize(src):
        raise ValueError("Invalid request: the destination is the source itself.")
    src_ext, dst_ext = os.path.splitext(src)[1], os.path.splitext(dst)[1]
    if dst_ext.lower() != src_ext.lower():
        raise ValueError(
            f"Invalid request: the destination must end in {src_ext} like the "
            f"source, not {dst_ext or 'no extension'}: a reference to it can't "
            "change the document's type."
        )
    if os.path.exists(dst):
        raise ValueError(
            f"Invalid request: {dst} already exists, and nothing is overwritten. "
            "Choose another name, or move that file first."
        )
    return src, dst


def candidate_files(
    folders: list[str], explicit: list[str], exclude: set[str]
) -> tuple[list[str], bool]:
    """The files to check for references: the given ones, then each folder's.

    Args:
        folders: Folders whose SolidWorks files are checked (not subfolders).
        explicit: Documents named by the caller, checked first.
        exclude: Normalized paths left out (the source and destination).

    Returns:
        tuple[list[str], bool]: The files, each once, and whether more than
        :data:`MAX_SCANNED` were found (the rest are left out).

    Raises:
        ValueError: A search folder isn't a folder (``invalid_input``).
    """
    found = list(explicit)
    for folder in folders:
        if not os.path.isdir(folder):
            raise ValueError(
                f"Invalid request: search folder {folder} is not a folder."
            )
        found += [
            os.path.join(folder, name)
            for name in sorted(os.listdir(folder))
            if os.path.splitext(name)[1].lower() in DOCUMENT_TYPES
        ]
    files: list[str] = []
    seen = set(exclude)
    for path in found:
        if normalize(path) in seen:
            continue
        seen.add(normalize(path))
        if len(files) == MAX_SCANNED:
            return files, True
        files.append(path)
    return files, False


def stored_reference(
    source: str, referencing: str, references: list[tuple[str, str]]
) -> str | None:
    """How ``referencing`` names ``source`` among its references, or None.

    A reference to ``source``'s own path, or, for a document in the source's
    folder, a reference to the same file name at a path that no longer
    exists (SolidWorks finds it beside the document).
    """
    target = normalize(source)
    for _name, path in references:
        if normalize(path) == target:
            return path
    if normalize(os.path.dirname(referencing)) != normalize(os.path.dirname(source)):
        return None
    for _name, path in references:
        same_name = os.path.basename(path).lower() == os.path.basename(source).lower()
        if same_name and not os.path.isfile(path):
            return path
    return None


def plan_move(
    source: str,
    destination: str,
    referencing: list[str] | None,
    search_folders: list[str] | None,
    references_of: ReferencesOf,
    open_documents: dict[str, bool],
) -> dict[str, Any]:
    """What moving ``source`` to ``destination`` would change, and what stops it.

    Args:
        source: The file to move.
        destination: Its new path, or a folder.
        referencing: Documents the caller knows use it.
        search_folders: Folders searched for more; the source's folder when
            None.
        references_of: A document's ``(name, path)`` references, or None
            when they can't be read.
        open_documents: Normalized path of each document open in
            SolidWorks -> whether it has unsaved changes.

    Returns:
        dict[str, Any]: ``source``, ``destination``, ``creates_folder``,
        ``search_folders``, ``scanned``, ``scan_truncated``, ``users`` (each
        ``path`` and ``references_as``, the path saved in it), ``not_using``
        (given documents that don't use it), ``unreadable``, ``not_found``
        and ``blocked`` (each ``path`` and ``reason``).

    Raises:
        ValueError: The move or a search folder is invalid.
        LookupError: The source isn't there.
    """
    src, dst = resolve_move(source, destination)
    folders = [os.path.abspath(f) for f in (search_folders or [os.path.dirname(src)])]
    explicit = [os.path.abspath(p) for p in (referencing or [])]
    candidates, truncated = candidate_files(
        folders, explicit, {normalize(src), normalize(dst)}
    )
    given = {normalize(p) for p in explicit}
    users: list[dict[str, str]] = []
    not_using: list[str] = []
    unreadable: list[str] = []
    not_found: list[str] = []
    for path in candidates:
        limits.check(f"looking for the documents that use {os.path.basename(src)}")
        if not os.path.isfile(path):
            not_found.append(path)
            continue
        references = references_of(path)
        if references is None:
            unreadable.append(path)
            continue
        stored = stored_reference(src, path, references)
        if stored is not None:
            users.append({"path": path, "references_as": stored})
        elif normalize(path) in given:
            not_using.append(path)
    return {
        "source": src,
        "destination": dst,
        "creates_folder": not os.path.isdir(os.path.dirname(dst)),
        "search_folders": folders,
        "scanned": len(candidates),
        "scan_truncated": truncated,
        "users": users,
        "not_using": not_using,
        "unreadable": unreadable,
        "not_found": not_found,
        "blocked": _blockers(src, users, open_documents),
    }


def _blockers(
    source: str, users: list[dict[str, str]], open_documents: dict[str, bool]
) -> list[dict[str, str]]:
    """What stops the move: the source or a user open, a user read-only."""
    blocked: list[dict[str, str]] = []
    if normalize(source) in open_documents:
        reason = (
            "has unsaved changes in SolidWorks; save or close it first"
            if open_documents[normalize(source)]
            else "is open in SolidWorks, which holds the file; close it first "
            "(close_model)"
        )
        blocked.append({"path": source, "reason": reason})
    for user in users:
        if normalize(user["path"]) in open_documents:
            blocked.append(
                {
                    "path": user["path"],
                    "reason": "uses it and is open in SolidWorks; its references "
                    "can be changed only while it is closed, so close it first",
                }
            )
        elif not os.access(user["path"], os.W_OK):
            blocked.append(
                {"path": user["path"], "reason": "uses it and is read-only on disk"}
            )
    return blocked


def blocked_message(plan: dict[str, Any]) -> str:
    """The error for a move something stops (``invalid_input``)."""
    reasons = "; ".join(f"{b['path']} {b['reason']}" for b in plan["blocked"])
    return f"Invalid request: nothing was moved: {reasons}."


def dry_run_reply(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan as the dry run's reply: what would change, and whether it can."""
    return {
        **plan,
        "dry_run": True,
        "moved": False,
        "would_update": [user["path"] for user in plan["users"]],
        "can_move": not plan["blocked"],
    }


# ---- COM -------------------------------------------------------------------------


def dependencies(adapter: Any, path: str, traverse: bool) -> list[tuple[str, str]]:
    """A file's references by ``GetDocumentDependencies2``, without opening it."""
    app = adapter.swApp
    _flag(adapter, app, "GetDocumentDependencies2")
    return parse_dependencies(
        app.GetDocumentDependencies2(path, bool(traverse), False, False)
    )


def open_documents(adapter: Any) -> dict[str, bool]:
    """Each document open in SolidWorks (also one loaded by an assembly):
    normalized path -> whether it has unsaved changes (``GetSaveFlag``).
    """
    from .io import _coerce_dispatch_sequence

    app = adapter.swApp
    documents = _coerce_dispatch_sequence(
        adapter._attempt(lambda: app.GetDocuments(), default=None)
    )
    states: dict[str, bool] = {}
    for document in documents:
        path = adapter._attempt(
            lambda d=document: adapter._get_attr_or_call(d, "GetPathName")
        )
        if not isinstance(path, str) or not path:
            continue  # never saved: it has no file to move
        dirty = adapter._attempt(
            lambda d=document: adapter._get_attr_or_call(d, "GetSaveFlag")
        )
        states[normalize(path)] = bool(dirty)
    return states


def list_references(adapter: Any, file_path: str, traverse: bool) -> dict[str, Any]:
    """A file's references, read without opening it.

    Args:
        adapter: A connected adapter.
        file_path: The part, assembly or drawing.
        traverse: Also list what the referenced files reference.

    Returns:
        dict[str, Any]: ``file``, ``traverse``, ``references`` (see
        :func:`describe_reference`), ``count`` and ``missing`` (the paths not
        on disk).

    Raises:
        ValueError: Not a SolidWorks document.
        LookupError: No such file.
    """
    resolved = os.path.abspath(file_path)
    check_document(resolved)
    references = [
        describe_reference(name, path, resolved)
        for name, path in dependencies(adapter, resolved, traverse)
    ]
    return {
        "file": resolved,
        "traverse": bool(traverse),
        "references": references,
        "count": len(references),
        "missing": [r["path"] for r in references if not r["exists"]],
    }


def _string_array(values: list[str]) -> Any:
    """``VARIANT(VT_ARRAY | VT_BSTR, values)``; the list itself without pywin32."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return list(values)
    return win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_BSTR, values)


def _move_file(adapter: Any, src: str, dst: str) -> str:
    """Move one file with ``MoveDocument`` and no children; say how it moved.

    A call late binding can't make (its children arguments are the open
    question ``tests/scripts/probe_file_references.py`` settles) falls back
    to a plain file move, which for one file without children is the same.

    Raises:
        RuntimeError: SolidWorks refused the move (an ``swMoveCopyError_e``),
            or the file isn't at the destination afterwards.
    """
    app = adapter.swApp
    _flag(adapter, app, "MoveDocument")
    try:
        code = app.MoveDocument(src, dst, _string_array([]), _string_array([]), 0)
    except Exception as exc:  # noqa: BLE001 - the probe settles the arguments
        shutil.move(src, dst)
        method = f"file move (MoveDocument raised {type(exc).__name__})"
    else:
        if code:
            raise RuntimeError(
                f"SolidWorks did not move {src}: MoveDocument returned {code} "
                f"({MOVE_ERRORS.get(code, 'unknown')}); nothing was changed."
            )
        method = "MoveDocument"
    if not os.path.isfile(dst):
        raise RuntimeError(
            f"{method} reported success, but {dst} isn't there; check {src}."
        )
    return method


def _repoint(adapter: Any, referencing: str, old: str, new: str) -> bool:
    """``ReplaceReferencedDocument``: whether SolidWorks repointed the file."""
    app = adapter.swApp
    _flag(adapter, app, "ReplaceReferencedDocument")
    return bool(
        adapter._attempt(
            lambda: app.ReplaceReferencedDocument(referencing, old, new), default=False
        )
    )


def _undo(adapter: Any, plan: dict[str, Any], repointed: list[dict[str, str]]) -> str:
    """Put a move back after a failed repoint; say how far that got."""
    src, dst = plan["source"], plan["destination"]
    unrestored = [
        u["path"]
        for u in repointed
        if not _repoint(adapter, u["path"], dst, u["references_as"])
    ]
    try:
        _move_file(adapter, dst, src)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return f"it could not be moved back ({exc}); it is at {dst}"
    if unrestored:
        return f"it was moved back, but {', '.join(unrestored)} still point to {dst}"
    return "it was moved back and every document points to it again"


def move_document(
    adapter: Any,
    source: str,
    destination: str,
    referencing: list[str] | None,
    search_folders: list[str] | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Move or rename a SolidWorks file and repoint the documents that use it.

    Args:
        adapter: A connected adapter.
        source: The file to move.
        destination: Its new path (same extension), or a folder.
        referencing: Documents known to use it.
        search_folders: Folders searched for more (the source's when None).
        dry_run: Only say what would change.

    Returns:
        dict[str, Any]: :func:`plan_move`'s plan with ``dry_run`` and
        ``moved``; a dry run adds ``would_update`` and ``can_move``, a move
        ``method`` and ``updated`` (each ``path`` and ``verified``, read back
        with ``GetDocumentDependencies2``).

    Raises:
        ValueError: An invalid move, or one something stops.
        LookupError: The source isn't there.
        RuntimeError: SolidWorks refused the move or a repoint (the move is
            then put back).
    """

    def references_of(path: str) -> list[tuple[str, str]] | None:
        return adapter._attempt(lambda: dependencies(adapter, path, False))

    plan = plan_move(
        source,
        destination,
        referencing,
        search_folders,
        references_of,
        open_documents(adapter),
    )
    if dry_run:
        return dry_run_reply(plan)
    if plan["blocked"]:
        raise ValueError(blocked_message(plan))
    src, dst = plan["source"], plan["destination"]
    if plan["creates_folder"]:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
    method = _move_file(adapter, src, dst)
    repointed: list[dict[str, str]] = []
    for user in plan["users"]:
        if not _repoint(adapter, user["path"], user["references_as"], dst):
            outcome = _undo(adapter, plan, repointed)
            raise RuntimeError(
                f"SolidWorks would not repoint {user['path']} to {dst} "
                f"(ReplaceReferencedDocument returned False), so the move was "
                f"undone: {outcome}."
            )
        repointed.append(user)
    updated = []
    for user in repointed:
        after = references_of(user["path"]) or []
        paths = {normalize(path) for _name, path in after}
        updated.append(
            {
                "path": user["path"],
                "verified": normalize(dst) in paths and normalize(src) not in paths,
            }
        )
    return {
        **plan,
        "dry_run": False,
        "moved": True,
        "method": method,
        "updated": updated,
    }
