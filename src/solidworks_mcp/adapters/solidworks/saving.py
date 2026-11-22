"""Save documents without SolidWorks dialogs, and ask before overwriting.

Saving an assembly whose parts have unsaved changes made SolidWorks show its
modal "Save Modified Documents" dialog, which blocks the COM thread until
someone answers it (measured on SOLIDWORKS 2026 SP4.1 with
``tests/scripts/probe_minimized.py --saves``: both an in-place save and
``close_model(save=True)``). The old save paths weren't silent:

* ``Save3(1, None, None)`` can't marshal its by-ref out-parameters under late
  binding, so it fell back to the legacy ``Save()``, which shows dialogs;
* Save As passed no options;
* ``close_model(save=True)`` called ``Save``.

:func:`save_document` saves with ``swSaveAsOptions_Silent`` (and
``SaveReferenced`` for an assembly, so its modified parts are written too),
passes real by-ref out-parameters, and turns the error and warning flags into
names.

Before an assembly save writes its modified parts, they are sorted:

* never saved, virtual, or created or first saved by this server session:
  saved with the assembly;
* saved by this session before, and unchanged on disk since: its unsaved
  changes are this session's own edits (the Cellvis dish rack's parts were
  the user's files, and every assembly save after the first asked again);
* a file this session wrote, since renamed or moved on its drive and
  unchanged: the same file id (``st_dev``, ``st_ino``), size and modified
  time as just after the session's save. The Dipper's part, made this
  session and renamed with ``mv``, was asked about as the user's. A move
  ``move_document`` made carries the records to the new path
  (:func:`follow_move`). A file system without file ids, or a file copied
  to another drive, gives no match, and the save asks;
* a file this server wrote before it restarted (a context compaction can
  restart it mid-conversation), found in the ledger (``file_ledger``) and
  unchanged on disk since, in the same SolidWorks process (its id and start
  time): one the server created is its own; one it only saved counts only
  when the document's ``GetUpdateStamp`` is still the one read just after
  that save (:func:`ledger_owns`). On SOLIDWORKS 2026 SP4.1 a save left the
  stamp alone, while a rebuild, a model edit and a dimension change each
  moved it (pitfall #57), so a saved file rebuilt since asks: the safe side.
  ``NeedsRebuild2`` can't tell a rebuild from an edit either;
* any other modified file - the user's own work - is not overwritten unless
  the caller passed ``overwrite_modified_references``. The save returns
  :class:`NeedsConfirmation` instead, and the tool reports
  ``error_type: confirmation_required`` with the files.

The option values come from the SW 2026 constants type library
(``swconst.tlb``): ``swSaveAsOptions_Silent`` is 1, ``Copy`` 2,
``SaveReferenced`` 4.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from ..base import AdapterResult, AdapterResultStatus

# swSaveAsOptions_e
SAVE_SILENT = 1
SAVE_COPY = 2
SAVE_REFERENCED = 4

# swDocumentTypes_e
DOC_PART = 1
DOC_ASSEMBLY = 2
DOC_DRAWING = 3

#: The tool argument that confirms overwriting the user's modified files.
CONFIRM_FLAG = "overwrite_modified_references"

# swFileSaveError_e
SAVE_ERRORS = {
    1: "swGenericSaveError",
    2: "swReadOnlySaveError",
    4: "swFileNameEmpty",
    8: "swFileNameContainsAtSign",
    16: "swFileLockError",
    32: "swFileSaveFormatNotAvailable",
    64: "swFileSaveWithRebuildError",
    128: "swFileSaveAsDoNotOverwrite",
    256: "swFileSaveAsInvalidFileExtension",
    512: "swFileSaveAsNoSelection",
    1024: "swFileSaveAsBadEDrawingsVersion",
    2048: "swFileSaveAsNameExceedsMaxPathLength",
    4096: "swFileSaveAsNotSupported",
    8192: "swFileSaveRequiresSavingReferences",
    16384: "swFileSaveAsDetachedDrawingsNotSupported",
    32768: "swFileSaveDoNotUpgradeError",
    65536: "swFileSaveIncompatibleItemsError",
}

# swFileSaveWarning_e
SAVE_WARNINGS = {
    1: "swFileSaveWarning_RebuildError",
    2: "swFileSaveWarning_NeedsRebuild",
    4: "swFileSaveWarning_ViewsNeedUpdate",
    8: "swFileSaveWarning_AnimatorNeedToSolve",
    16: "swFileSaveWarning_AnimatorFeatureEdits",
    32: "swFileSaveWarning_EdrwingsBadSelection",
    64: "swFileSaveWarning_AnimatorLightEdits",
    128: "swFileSaveWarning_AnimatorCameraViews",
    256: "swFileSaveWarning_AnimatorSectionViews",
    512: "swFileSaveWarning_MissingOLEObjects",
    1024: "swFileSaveWarning_OpenedViewOnly",
    2048: "swFileSaveWarning_XmlInvalid",
}


def flag_names(value: Any, names: dict[int, str]) -> list[str]:
    """The names of the bits set in a SolidWorks flags value."""
    try:
        bits = int(getattr(value, "value", value) or 0)
    except (TypeError, ValueError):
        return []
    return [name for bit, name in names.items() if bits & bit]


def normalize(path: str) -> str:
    """A path in the form used to compare files (absolute, case-folded)."""
    return os.path.normcase(os.path.abspath(path))


def session_paths(adapter: Any) -> set[str]:
    """The files this server session created or saved for the first time."""
    paths = getattr(adapter, "_session_paths", None)
    if paths is None:
        paths = set()
        adapter._session_paths = paths
    return paths


def remember(adapter: Any, path: str) -> None:
    """Record a file this session wrote for the first time."""
    session_paths(adapter).add(normalize(path))
    _record_identity(adapter, path)


#: A file's id on its volume: ``(st_dev, st_ino)``. A rename or a move on the
#: same drive keeps it.
FileId = tuple[int, int]


def _identity(path: str) -> tuple[FileId, tuple[int, int]] | None:
    """A file's id and its ``(size, modified time in ns)``; None when unknown."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    if not status.st_ino:
        return None  # a file system without file ids: no way to tell
    return (status.st_dev, status.st_ino), (status.st_size, status.st_mtime_ns)


def session_files(adapter: Any) -> dict[FileId, tuple[int, int]]:
    """Each file this session wrote, by id: its size and modified time just after."""
    files = getattr(adapter, "_session_files", None)
    if files is None:
        files = {}
        adapter._session_files = files
    return files


def _record_identity(adapter: Any, path: str) -> None:
    found = _identity(path)
    if found is not None:
        session_files(adapter)[found[0]] = found[1]


def moved_session_file(adapter: Any, path: str) -> bool:
    """Whether ``path`` is a file this session wrote, renamed or moved since, unchanged.

    Its id, size and modified time must all be those recorded just after
    the session's save; anything else is not known to be the session's.
    """
    found = _identity(path)
    return found is not None and session_files(adapter).get(found[0]) == found[1]


def follow_move(adapter: Any, source: str, destination: str) -> None:
    """Carry this session's records of ``source`` to ``destination`` after a move.

    ``move_document`` calls it, whether SolidWorks or a plain file move moved
    the file. A move to another drive copies the file (a new id), so the id
    is recorded again when the source was the session's.
    """
    old, new = normalize(source), normalize(destination)
    paths, saves = session_paths(adapter), session_saves(adapter)
    owned = old in paths
    if owned:
        paths.discard(old)
        paths.add(new)
    if old in saves:
        recorded = saves.pop(old)
        # A rename keeps the modified time; anything else is not the session's.
        saves[new] = recorded if recorded == _modified_time(destination) else None
    if owned or saves.get(new) is not None:
        _record_identity(adapter, destination)
    ledger = _ledger(adapter)
    if ledger is not None:
        ledger.follow_move(source, destination)


def _modified_time(path: str) -> float | None:
    """The file's modification time, or None when it can't be read."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def session_saves(adapter: Any) -> dict[str, float | None]:
    """The files this session saved, with their modification time just after."""
    saves = getattr(adapter, "_session_saves", None)
    if saves is None:
        saves = {}
        adapter._session_saves = saves
    return saves


def record_save(adapter: Any, path: str, doc: Any = None) -> None:
    """Record that this session saved ``path`` (and when the file then changed).

    The ledger gets it too, with the SolidWorks process, the document's
    update stamp (``doc``, when given) and whether the session created it.
    """
    session_saves(adapter)[normalize(path)] = _modified_time(path)
    _record_identity(adapter, path)
    ledger = _ledger(adapter)
    if ledger is not None:
        ledger.record(
            path,
            created=normalize(path) in session_paths(adapter),
            solidworks=solidworks_process(adapter),
            update_stamp=update_stamp(adapter, doc),
        )


def _ledger(adapter: Any) -> Any:
    """The adapter's :class:`file_ledger.FileLedger`, or None without one."""
    return getattr(adapter, "_file_ledger", None)


def solidworks_process(adapter: Any) -> tuple[int, int] | None:
    """The connected SolidWorks: its process id and start time, or None.

    The start time tells it from a later SolidWorks that got the same id.
    It is read once per process id.
    """
    from .sessions import process_started

    pid = int(getattr(adapter, "_sw_process_id", 0) or 0)
    if not pid:
        return None
    known = getattr(adapter, "_sw_process_started", None)
    if known and known[0] == pid:
        return (pid, int(known[1]))
    started = process_started(pid)
    if started is None:
        return None
    adapter._sw_process_started = (pid, started)
    return (pid, started)


def update_stamp(adapter: Any, doc: Any) -> int | None:
    """``IModelDoc2.GetUpdateStamp``: a counter of the model's changes, or None."""
    if doc is None:
        return None
    value = adapter._attempt(lambda: adapter._get_attr_or_call(doc, "GetUpdateStamp"))
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def ledger_owns(adapter: Any, path: str, doc: Any) -> bool:
    """Whether the ledger shows ``path`` as this server's own, after a restart.

    All of: an entry whose file is unchanged on disk since the server's
    save; the same SolidWorks process (id and start time) then and now; and
    the server created the file, or the document's update stamp is the one
    read just after that save. Anything unknown is False: the guard asks.
    """
    ledger = _ledger(adapter)
    if ledger is None:
        return False
    entry = ledger.unchanged_entry(path)
    process = solidworks_process(adapter)
    if entry is None or process is None:
        return False
    if [entry.get("solidworks_pid"), entry.get("solidworks_started")] != list(process):
        return False
    if entry.get("created"):
        return True
    stamp = update_stamp(adapter, doc)
    return stamp is not None and stamp == entry.get("update_stamp")


def close_holder(adapter: Any, target: str) -> list[str]:
    """Close the open document whose file is ``target``; the names closed.

    Before a save to ``target``, a document still holding that file open is
    closed. Only one at that very path: ``CloseDoc`` takes a name, and with
    Windows hiding extensions a part and an assembly of one name are both
    titled ``X``, so ``CloseDoc("X.SLDASM")`` before saving an assembly as
    ``X.SLDASM`` closed the open part ``X`` without saving it (Aggrewell
    probe, SOLIDWORKS 2026). The name given is the one
    :func:`loaded_documents.close_name` picks: the path when the title is
    shared.
    """
    from .com_utils import as_list
    from .loaded_documents import _text, close_name

    key = normalize(target)
    app = adapter.swApp
    closed: list[str] = []
    for document in as_list(adapter._attempt(lambda: app.GetDocuments())):
        path = _text(adapter, document, "GetPathName")
        if not path or normalize(path) != key:
            continue
        name = close_name(adapter, _text(adapter, document, "GetTitle"), path)
        adapter._attempt(lambda n=name: app.CloseDoc(n))
        closed.append(name)
    return closed


def saved_by_session(adapter: Any, path: str) -> bool:
    """Whether this session saved ``path`` and nobody has saved it since."""
    key = normalize(path)
    saves = session_saves(adapter)
    if key not in saves:
        return False
    recorded, now = saves[key], _modified_time(path)
    return recorded is not None and now == recorded


@dataclass
class Reference:
    """A document an assembly uses that has unsaved changes."""

    path: str
    #: Never saved, virtual, written by this session, or saved by it and
    #: unchanged on disk since: saved without asking.
    new: bool
    #: Its ``IModelDoc2``, for its update stamp after the save.
    doc: Any = field(default=None, compare=False, repr=False)


@dataclass
class NeedsConfirmation:
    """A save that would overwrite the user's modified files; nothing written."""

    files: list[str]


def document_type(adapter: Any, model: Any) -> int:
    """``swDocumentTypes_e`` of ``model`` (0 when it can't be read)."""
    value = adapter._attempt(lambda: adapter._get_attr_or_call(model, "GetType"))
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def modified_references(adapter: Any, assembly: Any) -> list[Reference]:
    """The documents ``assembly`` uses, at every level, with unsaved changes.

    Components that aren't loaded (suppressed, lightweight) have no model
    document and can't have unsaved changes, so they are skipped.
    """
    from .io import _as_com

    components = adapter._attempt(lambda: assembly.GetComponents(False), default=None)
    references: dict[str, Reference] = {}
    for component in components or ():
        wrapped = _as_com(adapter, component, "IComponent2") or component
        doc = adapter._attempt(
            lambda w=wrapped: adapter._get_attr_or_call(w, "GetModelDoc2")
        )
        if doc is None:
            continue
        dirty = adapter._attempt(
            lambda d=doc: adapter._get_attr_or_call(d, "GetSaveFlag")
        )
        if not dirty:
            continue
        path = str(
            adapter._attempt(lambda d=doc: adapter._get_attr_or_call(d, "GetPathName"))
            or ""
        )
        virtual = bool(
            adapter._attempt(
                lambda w=wrapped: adapter._get_attr_or_call(w, "IsVirtual")
            )
        )
        new = (
            not path
            or virtual
            or not os.path.exists(path)
            or normalize(path) in session_paths(adapter)
            or saved_by_session(adapter, path)
            or moved_session_file(adapter, path)
            or ledger_owns(adapter, path, doc)
        )
        key = normalize(path) if path else f"<unsaved {len(references)}>"
        references.setdefault(key, Reference(path=path, new=new, doc=doc))
    return list(references.values())


def confirmation_message(files: list[str]) -> str:
    """The error text for a save that needs the user's confirmation."""
    count = len(files)
    noun = "file" if count == 1 else "files"
    return (
        f"Saving would overwrite {count} modified {noun} that existed before "
        f"this session: {', '.join(files)}. Nothing was saved."
    )


def confirmation_result(
    files: list[str], execution_time: float | None = None
) -> AdapterResult[Any]:
    """The error result for :class:`NeedsConfirmation`."""
    return AdapterResult(
        status=AdapterResultStatus.ERROR,
        error=confirmation_message(files),
        data={"files": files, "confirm_with": CONFIRM_FLAG},
        execution_time=execution_time,
    )


def _failure(action: str, errors: list[str]) -> Exception:
    detail = f" ({', '.join(errors)})" if errors else ""
    return Exception(f"{action}{detail}")


def save_document(
    adapter: Any,
    model: Any,
    file_path: str | None = None,
    overwrite_modified_references: bool = False,
) -> NeedsConfirmation | dict[str, Any]:
    """Save ``model`` in place, or as ``file_path``, without a dialog.

    Runs on the COM thread, inside ``_handle_com_operation``.

    Args:
        adapter: The connected adapter.
        model: The ``IModelDoc2`` to save.
        file_path: A new path to save as; ``None`` (or the document's own
            path) saves in place.
        overwrite_modified_references: Save the user's modified files that
            the assembly uses, after the user agreed.

    Returns:
        :class:`NeedsConfirmation` when the user's files would be
        overwritten (nothing is written); otherwise ``file_path``,
        ``saved_references`` (the other files written) and ``warnings``.

    Raises:
        Exception: SolidWorks refused the save; the message names its errors.
    """
    from .io import _byref_int

    doc_type = document_type(adapter, model)
    references: list[Reference] = []
    options = SAVE_SILENT
    if doc_type == DOC_ASSEMBLY:
        references = modified_references(adapter, model)
        user_files = [r.path for r in references if not r.new]
        if user_files and not overwrite_modified_references:
            return NeedsConfirmation(user_files)
        options |= SAVE_REFERENCED

    current = str(
        adapter._attempt(lambda: adapter._get_attr_or_call(model, "GetPathName")) or ""
    )
    target = os.path.abspath(file_path) if file_path else current
    if not target:
        raise Exception(
            "The document has never been saved; give a file_path to save it to"
        )
    in_place = bool(current) and normalize(current) == normalize(target)

    warnings: list[str] = []
    if in_place:
        errors_out, warnings_out = _byref_int(), _byref_int()
        saved = model.Save3(options, errors_out, warnings_out)
        errors = flag_names(errors_out, SAVE_ERRORS)
        warnings = flag_names(warnings_out, SAVE_WARNINGS)
        if not saved or errors:
            raise _failure("Failed to save file", errors)
    else:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # A *different* document may be holding the target path open: close
        # that one only, never the document being saved (its path isn't the
        # target here) and never one that merely shares a name.
        if adapter.swApp:
            close_holder(adapter, target)
        # SaveAs3 returns 0 on success, else swFileSaveError_e flags (fakes
        # may return a bool). Deliberately no os.remove before it: SaveAs3
        # overwrites, and deleting first meant a failed save lost the old file.
        code = model.SaveAs3(target, 0, options)
        failed = code is False or (not isinstance(code, bool) and bool(code))
        if failed or not os.path.exists(target):
            errors = [] if isinstance(code, bool) else flag_names(code, SAVE_ERRORS)
            raise _failure(f"Failed to save as: {target}", errors)
        if not current or normalize(current) != normalize(target):
            remember(adapter, target)

    if not os.path.exists(target):
        raise Exception(f"File not written after save: {target}")
    record_save(adapter, target, model)
    for reference in references:
        if reference.path and os.path.exists(reference.path):
            record_save(adapter, reference.path, reference.doc)
    return {
        "file_path": target,
        "saved_references": [r.path for r in references if r.path],
        "warnings": warnings,
    }
