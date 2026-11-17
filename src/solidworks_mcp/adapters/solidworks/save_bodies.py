"""Save Bodies: split a multibody part into part files, and keep those files.

The API (SOLIDWORKS 2026 type library, gen_py DISPID 150; Save Bodies has no
``swFeatureNameID_e`` value, so ``CreateDefinition`` can't make one):

* ``IFeatureManager.CreateSaveBodyFeature(Bodies, FilePaths, AssemName,
  ConsumeBody, CopyCustomProperty)`` writes a part file per body and returns
  the feature. ``AssemName`` is the assembly to make of them; ``""`` asks for
  none.
* ``IFeature.GetDefinition`` gives an ``ISaveBodyFeatureData``:
  ``GetSaveBodies(Bodies, FilePaths, Flags)`` (three out-parameters; the
  flags say whether each body is consumed), ``AddSaveBodies(Bodies,
  FilePaths)`` ("adds the bodies and saves them as part documents on disk"),
  ``RemoveSaveBodies(Bodies)``, and the properties ``AssemblyPath``,
  ``ConsumeBody`` and ``CopyCustomProperties`` (get and put). Changing it
  takes ``AccessSelections(model, null)`` (the part rolls back) and then
  ``ModifyDefinition``, or ``ReleaseSelectionAccess`` to leave it unchanged.

What The Dipper build (SOLIDWORKS 2026 SP4.1, a 2-part resin dipper split into
derived parts and a 6-component assembly) met, every call of six:

* a "SOLIDWORKS Design" task dialog with Yes and No, whose text the watchdog
  couldn't read then (``dialogs.read_accessible`` reads it now); the
  watchdog cancelled it and the call returned ``solidworks_dialog``;
* yet the part file was written correctly, and an untitled assembly
  (Assem3, Assem5, Assem8, ...) was left open and active, although
  ``AssemName`` was ``""``. The older tutorial noted the same assembly.

So :meth:`SolidWorksSaveBodiesMixin.save_body_as_part` checks the file it
asked for (there, fresh, and holding a solid of the body's volume), closes
any untitled assembly that appeared during the call, makes the part active
again, and treats a dialog as a warning when that check passed. What the
dialog says, and whether another ``AssemName`` or ``CopyCustomProperty``
avoids it, is for ``tests/scripts/probe_save_bodies.py`` to settle.

Re-splitting into files that exist updates them in place: each derived part
is opened silently, rebuilt (it is built from the master's body, which is
open), saved silently and closed again, so the files keep their identity
and the assemblies that use them don't see a new file.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any, cast

from .. import sw_type_info as _sw_type_info
from ..base import AdapterResult, AdapterResultStatus
from . import loaded_documents as _loaded
from . import saving
from .com_utils import as_list
from .com_utils import flag as _flag

#: ``swBodyType_e.swSolidBody``.
SOLID_BODY = 0
#: A file holds the body when their volumes differ by at most this share.
VOLUME_TOLERANCE = 0.001
#: A file counts as written by the call when its time is no older than this
#: before the call started (file systems round modification times).
WRITE_SLACK_SECONDS = 2.0
#: ``GetTypeName2`` of a Save Bodies feature, lower case. Not measured yet:
#: the probe prints it; the feature's default name is matched as well.
SAVE_BODIES_TYPES = frozenset(
    {"savebodies", "savebody", "savebodyfeature", "saveoutbodies"}
)
SAVE_BODIES_NAME = "save bodies"
#: ``swOpenDocOptions_e``: Silent, and Silent | ReadOnly.
OPEN_SILENT = 1
OPEN_SILENT_READ_ONLY = 3
#: What ``edit_save_bodies`` can change in a Save Bodies feature.
SETTABLE = ("files", "assembly_path", "consume_body", "copy_custom_properties")

_DATA_METHODS = (
    "GetSaveBodies",
    "GetSaveBodiesCount",
    "AccessSelections",
    "ReleaseSelectionAccess",
    "AddSaveBodies",
    "RemoveSaveBodies",
)


class InvalidSaveBodiesRequest(ValueError):
    """Arguments that can't be used as given (classified ``invalid_input``)."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Invalid request: {detail}")


# ---- bodies and volumes ------------------------------------------------------


def _name(adapter: Any, body: Any) -> str | None:
    value = adapter._attempt(lambda: adapter._get_attr_or_call(body, "Name"))
    return str(value) if value is not None else None


def solid_bodies(adapter: Any, model: Any) -> list[tuple[str, Any]]:
    """``(name, IBody2)`` for each solid body of a part."""
    part = _sw_type_info.flagged(model, "IPartDoc") or model
    raw = adapter._attempt(lambda: part.GetBodies2(SOLID_BODY, False))
    found: list[tuple[str, Any]] = []
    for body in as_list(raw):
        flagged = _sw_type_info.flagged(body, "IBody2") or body
        name = _name(adapter, flagged)
        if name is not None:
            found.append((name, flagged))
    return found


def body_volume_m3(adapter: Any, body: Any) -> float | None:
    """A body's volume in m³ (``IBody2.GetMassProperties(1.0)``, index 3)."""
    _flag(adapter, body, "GetMassProperties")
    values = as_list(adapter._attempt(lambda: body.GetMassProperties(1.0)))
    if len(values) < 4 or not isinstance(values[3], (int, float)):
        return None
    return float(values[3])


def document_volume_m3(adapter: Any, document: Any) -> float | None:
    """The solid volume of a part document in m³; None when it has none."""
    volumes = [body_volume_m3(adapter, b) for _n, b in solid_bodies(adapter, document)]
    known = [v for v in volumes if v is not None]
    return sum(known) if known else None


def _mm3(volume_m3: float | None) -> float | None:
    return None if volume_m3 is None else round(volume_m3 * 1e9, 3)


def holds(volume_m3: float | None, expected_m3: float | None) -> bool | None:
    """Whether a file's volume is the body's; None when either isn't known."""
    if volume_m3 is None or not expected_m3:
        return None
    return abs(volume_m3 - expected_m3) <= VOLUME_TOLERANCE * abs(expected_m3)


# ---- documents ---------------------------------------------------------------


def _byref_int() -> Any:
    from .io import _byref_int as byref

    return byref()


def _loaded_by_path(adapter: Any, path: str) -> _loaded.LoadedDocument | None:
    key = _loaded.normalize(path)
    return next((d for d in _loaded.loaded_documents(adapter) if d.key == key), None)


def _open(adapter: Any, path: str, options: int) -> Any:
    """``OpenDoc6`` a part silently; ``None`` when SolidWorks won't."""
    app = adapter.swApp
    return adapter._attempt(
        lambda: app.OpenDoc6(
            path, _loaded.DOC_PART, options, "", _byref_int(), _byref_int()
        )
    )


def file_check(
    adapter: Any, path: str, expected_m3: float | None, since: float
) -> dict[str, Any]:
    """Read back a part file a Save Bodies call wrote.

    The document is read where SolidWorks already has it loaded (the stray
    assembly loads it); otherwise it is opened silently and read-only, and
    closed again.

    Returns:
        dict[str, Any]: ``file_path``, ``exists``, ``size_bytes``,
        ``written_now``, ``volume_mm3``, ``body_volume_mm3`` and
        ``holds_body`` (``None`` when a volume couldn't be read).
    """
    exists = os.path.isfile(path)
    check: dict[str, Any] = {
        "file_path": path,
        "exists": exists,
        "size_bytes": os.path.getsize(path) if exists else 0,
        "written_now": exists and os.path.getmtime(path) >= since - WRITE_SLACK_SECONDS,
    }
    volume = None
    if exists:
        loaded = _loaded_by_path(adapter, path)
        document = loaded.document if loaded is not None else None
        opened = False
        if document is None:
            document = _open(adapter, path, OPEN_SILENT_READ_ONLY)
            opened = document is not None
        if document is not None:
            volume = document_volume_m3(adapter, document)
        if opened:
            title = _loaded.describe(adapter, document).title
            adapter._attempt(lambda: adapter.swApp.CloseDoc(title))
    check["volume_mm3"] = _mm3(volume)
    check["body_volume_mm3"] = _mm3(expected_m3)
    check["holds_body"] = holds(volume, expected_m3)
    return check


def document_keys(adapter: Any) -> set[str]:
    """The keys of every loaded document, before a call that may open some."""
    return {d.key for d in _loaded.loaded_documents(adapter)}


def close_stray_assemblies(adapter: Any, before: set[str]) -> list[str]:
    """Close the untitled assemblies that appeared since ``before``.

    An untitled assembly has no file, and this call made it, so closing it
    loses nothing of the user's. One saved to a path stays.

    Returns:
        list[str]: The titles closed.
    """
    closed: list[str] = []
    for document in _loaded.loaded_documents(adapter):
        if (
            document.key in before
            or document.type != _loaded.DOC_ASSEMBLY
            or document.path
        ):
            continue
        title = document.title
        adapter._attempt(lambda t=title: adapter.swApp.CloseDoc(t))
        closed.append(title)
    return closed


def reactivate(adapter: Any, model: Any, title: str) -> None:
    """Make ``model`` (titled ``title``) the active document again."""
    from .io import activate_doc

    app = adapter.swApp
    active = adapter._attempt(lambda: app.ActiveDoc)
    if active is None or _loaded.describe(adapter, active).title != title:
        activated = activate_doc(adapter, app, title)
        if activated is not None:
            model = activated
    adapter._attempt(lambda: _sw_type_info.flag_doc(model, _loaded.DOC_PART), 0)
    adapter.currentModel = model


# ---- Save Bodies features ----------------------------------------------------


def _type_name(adapter: Any, feature: Any) -> str:
    _flag(adapter, feature, "GetTypeName2")
    value = adapter._attempt(lambda: feature.GetTypeName2())
    return str(value) if value is not None else ""


def is_save_bodies(adapter: Any, feature: Any) -> bool:
    """Whether a feature is a Save Bodies feature, by type or default name."""
    kind = _type_name(adapter, feature).replace(" ", "").lower()
    if kind in SAVE_BODIES_TYPES:
        return True
    name = str(adapter._attempt(lambda: feature.Name) or "")
    return name.lower().startswith(SAVE_BODIES_NAME)


def save_bodies_features(adapter: Any, model: Any) -> list[Any]:
    """The part's Save Bodies features, in tree order."""
    _flag(adapter, model, "FirstFeature")
    features: list[Any] = []
    feature = adapter._attempt(lambda: model.FirstFeature())
    while feature is not None:
        if is_save_bodies(adapter, feature):
            features.append(feature)
        current = feature
        _flag(adapter, current, "GetNextFeature")
        feature = adapter._attempt(lambda c=current: c.GetNextFeature())
    return features


def find_feature(adapter: Any, model: Any, name: str | None) -> list[Any]:
    """The named Save Bodies feature, or all of them when ``name`` is None.

    Raises:
        LookupError: No such feature, or the part has none (``not_found``).
        InvalidSaveBodiesRequest: The named feature isn't a Save Bodies one.
    """
    if not name:
        features = save_bodies_features(adapter, model)
        if not features:
            raise LookupError(
                "No Save Bodies feature found in the active part; "
                "save_body_as_part makes one."
            )
        return features
    feature = adapter._attempt(lambda: model.FeatureByName(name))
    if feature is None:
        raise LookupError(f"Feature {name!r} not found; list_save_bodies names them.")
    if not is_save_bodies(adapter, feature):
        raise InvalidSaveBodiesRequest(f"{name!r} is not a Save Bodies feature.")
    return [feature]


def _definition(adapter: Any, feature: Any) -> Any:
    _flag(adapter, feature, "GetDefinition", "ModifyDefinition")
    data = adapter._attempt(lambda: feature.GetDefinition())
    if data is None:
        raise Exception("SolidWorks gave no definition for the Save Bodies feature")
    _flag(adapter, data, *_DATA_METHODS)
    return data


def _out(value: Any) -> list[Any]:
    return as_list(getattr(value, "value", value))


def entries(adapter: Any, data: Any) -> list[tuple[Any, str, str, bool | None]]:
    """``(body, body name, file path, consumed)`` per body of a definition."""
    from .io import _byref_variant

    outs = (_byref_variant(), _byref_variant(), _byref_variant())
    returned = adapter._attempt(lambda: data.GetSaveBodies(*outs))
    if isinstance(returned, tuple) and len(returned) == 3:
        bodies, paths, flags = (as_list(r) for r in returned)
    else:
        bodies, paths, flags = (_out(o) for o in outs)
    found = []
    for index, body in enumerate(bodies):
        flagged = _sw_type_info.flagged(body, "IBody2") or body
        path = str(paths[index]) if index < len(paths) else ""
        consumed = flags[index] if index < len(flags) else None
        found.append(
            (
                flagged,
                _name(adapter, flagged) or "",
                path,
                bool(consumed) if consumed is not None else None,
            )
        )
    return found


def _null() -> Any:
    from .features import _null_callout

    return _null_callout()


def read_definition(adapter: Any, model: Any, feature: Any) -> dict[str, Any]:
    """A Save Bodies feature's bodies, files and options, unchanged.

    ``GetSaveBodies`` is read first as it is; only when that gives nothing
    are the selections accessed and released (``selections_accessed``).
    """
    data = _definition(adapter, feature)
    found = entries(adapter, data)
    accessed = False
    if not found:
        accessed = bool(adapter._attempt(lambda: data.AccessSelections(model, _null())))
        if accessed:
            found = entries(adapter, data)
            adapter._attempt(lambda: data.ReleaseSelectionAccess())
    return {
        "feature": str(adapter._attempt(lambda: feature.Name) or ""),
        "type": _type_name(adapter, feature),
        "bodies": [
            {
                "body": name,
                "file_path": path,
                "consumed": consumed,
                "exists": bool(path) and os.path.isfile(path),
                "lock_file": _loaded.lock_file(path),
            }
            for _body, name, path, consumed in found
        ],
        "assembly_path": adapter._attempt(lambda: data.AssemblyPath) or "",
        "consume_body": adapter._attempt(lambda: data.ConsumeBody),
        "copy_custom_properties": adapter._attempt(lambda: data.CopyCustomProperties),
        "selections_accessed": accessed,
        "settable": list(SETTABLE),
    }


def _check_new_path(path: str) -> str:
    target = os.path.abspath(path)
    if not target.lower().endswith(".sldprt"):
        raise InvalidSaveBodiesRequest(f"{path} is not a part file (.sldprt).")
    if not os.path.isdir(os.path.dirname(target)):
        raise InvalidSaveBodiesRequest(
            f"the folder of {target} does not exist; create it first."
        )
    if os.path.exists(target):
        raise InvalidSaveBodiesRequest(
            f"{target} already exists; give a new file name, or use "
            "resplit_bodies to update the files the feature already writes."
        )
    return target


def edit_definition(
    adapter: Any, model: Any, feature: Any, changes: dict[str, Any]
) -> dict[str, Any]:
    """Change a Save Bodies feature's files or options, then read it back.

    ``changes`` may hold ``files`` (body name -> new ``.sldprt`` path),
    ``assembly_path`` (``""`` for none), ``consume_body`` and
    ``copy_custom_properties``. A body's file changes by removing the body
    and adding it again with the new path; ``AddSaveBodies`` writes it.

    Raises:
        InvalidSaveBodiesRequest: An unknown body, or a path that can't be used.
        Exception: SolidWorks refused the change.
    """
    from .feature_editing import dispatch_array
    from .io import _variant_array, pythoncom

    files = {
        str(body): _check_new_path(str(path))
        for body, path in (changes.get("files") or {}).items()
    }
    data = _definition(adapter, feature)
    null = _null()
    if not data.AccessSelections(model, null):
        raise Exception(
            "SolidWorks would not open the Save Bodies feature for editing "
            "(AccessSelections returned False)"
        )
    try:
        by_name = {name: body for body, name, _p, _c in entries(adapter, data)}
        unknown = [name for name in files if name not in by_name]
        if unknown:
            raise InvalidSaveBodiesRequest(
                f"{', '.join(unknown)} not in the feature; its bodies are "
                + (", ".join(by_name) or "none")
                + "."
            )
        for name, path in files.items():
            body = dispatch_array([by_name[name]])
            data.RemoveSaveBodies(body)
            bstr = int(getattr(pythoncom, "VT_BSTR", 8))
            if not data.AddSaveBodies(body, _variant_array(bstr, [path])):
                raise Exception(f"SolidWorks would not save {name!r} to {path}")
        if "assembly_path" in changes:
            data.AssemblyPath = str(changes["assembly_path"] or "")
        if "consume_body" in changes:
            data.ConsumeBody = bool(changes["consume_body"])
        if "copy_custom_properties" in changes:
            data.CopyCustomProperties = bool(changes["copy_custom_properties"])
        modified = feature.ModifyDefinition(data, model, null)
    except Exception:
        adapter._attempt(lambda: data.ReleaseSelectionAccess())
        raise
    if not modified:
        raise Exception(
            "SolidWorks refused the change (ModifyDefinition returned False)"
        )
    adapter._attempt(lambda: adapter._get_attr_or_call(model, "EditRebuild3"))
    after = read_definition(adapter, model, feature)
    return {
        "feature": after["feature"],
        "changed": sorted(changes),
        "files_written": {path: os.path.isfile(path) for path in files.values()},
        "definition": after,
    }


def update_file(adapter: Any, body: Any, name: str, path: str) -> dict[str, Any]:
    """Bring one derived part up to date with its body, in place.

    Opens it silently (giving it a window when only an assembly had it
    loaded), rebuilds it from the master, saves it silently and closes the
    window again unless it had one.

    Returns:
        dict[str, Any]: ``body``, ``file_path`` and ``status``: ``updated``
        (with ``volume_mm3``, ``body_volume_mm3``, ``holds_body`` and
        ``written_now``), ``missing``, ``kept_modified`` or ``failed`` (with
        ``error``).
    """
    reply: dict[str, Any] = {"body": name, "file_path": path}
    if not os.path.isfile(path):
        return {**reply, "status": "missing"}
    loaded = _loaded_by_path(adapter, path)
    if loaded is not None and loaded.modified:
        return {**reply, "status": "kept_modified"}
    had_window = loaded is not None and loaded.visible is not False
    expected = body_volume_m3(adapter, body)
    before = os.path.getmtime(path)
    document = _open(adapter, path, OPEN_SILENT)
    if document is None:
        return {**reply, "status": "failed", "error": "SolidWorks would not open it"}
    try:
        _flag(adapter, document, "ForceRebuild3")
        document.ForceRebuild3(False)
        saving.save_document(adapter, document)
        volume = document_volume_m3(adapter, document)
    except Exception as exc:
        return {**reply, "status": "failed", "error": str(exc)}
    finally:
        if not had_window:
            title = _loaded.describe(adapter, document).title
            adapter._attempt(lambda: adapter.swApp.CloseDoc(title))
    return {
        **reply,
        "status": "updated",
        "volume_mm3": _mm3(volume),
        "body_volume_mm3": _mm3(expected),
        "holds_body": holds(volume, expected),
        "written_now": os.path.getmtime(path) > before,
    }


def resplit(adapter: Any, model: Any, feature_name: str | None) -> dict[str, Any]:
    """Update every file the part's Save Bodies features (or one) write."""
    features = find_feature(adapter, model, feature_name)
    files = []
    for feature in features:
        data = _definition(adapter, feature)
        found = entries(adapter, data)
        if not found and adapter._attempt(
            lambda d=data: d.AccessSelections(model, _null())
        ):
            found = entries(adapter, data)
            adapter._attempt(lambda d=data: d.ReleaseSelectionAccess())
        feature_label = str(adapter._attempt(lambda f=feature: f.Name) or "")
        for body, name, path, _consumed in found:
            files.append(
                {"feature": feature_label, **update_file(adapter, body, name, path)}
            )
    counts: dict[str, int] = {}
    for item in files:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"files": files, "counts": counts}


# ---- the mixin ---------------------------------------------------------------


def _dialog_warning(dialog: dict[str, Any], what: str) -> str:
    title = str(dialog.get("title") or "").strip()
    text = " ".join(str(t) for t in dialog.get("texts") or [] if t).strip()
    quoted = ": ".join(p for p in (f"'{title}'" if title else "", text) if p)
    outcome = (
        "could not cancel it and brought SolidWorks up to answer it"
        if dialog.get("handed_to_user")
        else "cancelled it"
    )
    return (
        f"SolidWorks showed a dialog during the call ({quoted or 'no text read'}); "
        f"the server {outcome}. {what}"
    )


def _tolerate_dialog(
    result: AdapterResult[Any], holder: dict[str, Any], what: str
) -> AdapterResult[Any]:
    """Keep a call's checked result when the only trouble was a dialog."""
    from .io import _dialog_of

    dialog = _dialog_of(result)
    if dialog is None or "data" not in holder:
        return result
    data = {**holder["data"], "dialog": dialog}
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data=data,
        execution_time=result.execution_time,
        metadata={"warning": _dialog_warning(dialog, what)},
    )


class SolidWorksSaveBodiesMixin:
    """Save Bodies: write bodies to part files, read and change the feature."""

    @staticmethod
    def _adapter(obj: Any) -> Any:
        return obj

    def _part_or_error(self) -> AdapterResult[Any] | None:
        adapter = self._adapter(self)
        sync = getattr(adapter, "_sync_current_model_from_active", None)
        if callable(sync):
            sync()
        if not adapter.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        if saving.document_type(adapter, adapter.currentModel) != _loaded.DOC_PART:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error="Save Bodies requires an active part document",
            )
        return None

    def _run_on_part(
        self,
        operation: str,
        work: Callable[[Any, Any], dict[str, Any]],
        what: str,
        close_strays: bool = True,
    ) -> AdapterResult[dict[str, Any]]:
        """Run ``work(model, holder)`` on the active part as one operation.

        Untitled assemblies that appear during it are closed (unless an
        assembly was asked for: ``close_strays`` False) and the part is made
        active again, whether it worked or not. A dialog the watchdog
        cancelled leaves a checked result standing, with a warning.
        """
        adapter = self._adapter(self)
        holder: dict[str, Any] = {}

        def _operation() -> dict[str, Any]:
            model = adapter.currentModel
            title = _loaded.describe(adapter, model).title
            before = document_keys(adapter)
            closed: list[str] = []
            try:
                data = work(model, holder)
            finally:
                if close_strays:
                    closed = close_stray_assemblies(adapter, before)
                reactivate(adapter, model, title)
            data["closed_documents"] = closed
            holder["data"] = data
            return data

        result = adapter._handle_com_operation(operation, _operation)
        return cast(
            AdapterResult[dict[str, Any]], _tolerate_dialog(result, holder, what)
        )

    async def save_body_as_part(
        self, body_name: str, file_path: str, assembly_path: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Save one solid body of the active part as a new part file (Save Bodies).

        Args:
            body_name: Name of a solid body in the active part.
            file_path: Absolute path for the new ``.sldprt``; its folder
                must exist. A file already there is replaced, unless
                SolidWorks has it loaded.
            assembly_path: Also make an assembly of the body at this
                ``.sldasm`` path; ``None`` makes none (an untitled assembly
                SolidWorks makes anyway is closed).

        Returns:
            AdapterResult[dict[str, Any]]: ``body``, ``file_path``,
            ``feature``, ``solid_bodies``, ``replaced``, ``file_check``
            (see :func:`file_check`), ``closed_documents`` and, when a dialog
            was cancelled, ``dialog`` with a ``warning`` in the metadata.
        """
        refused = self._part_or_error()
        if refused is not None:
            return refused
        if not str(body_name or "").strip():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="body_name is required"
            )
        target = str(file_path or "").strip()
        if not target:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="file_path is required"
            )
        target = os.path.abspath(target)
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"Parent directory does not exist: {parent}",
            )
        assembly = os.path.abspath(assembly_path) if assembly_path else None
        adapter = self._adapter(self)

        def _save(model: Any, _holder: dict[str, Any]) -> dict[str, Any]:
            from .io import _variant_array, pythoncom

            bodies = solid_bodies(adapter, model)
            if not bodies:
                raise Exception("The active part has no solid bodies")
            names = [name for name, _b in bodies]
            match = next((b for name, b in bodies if name == body_name), None)
            if match is None:
                raise LookupError(
                    f"Body {body_name!r} not found. Solid bodies: " + ", ".join(names)
                )
            manager = adapter._attempt(lambda: model.FeatureManager)
            if manager is None:
                raise Exception("Part has no FeatureManager")
            manager = _sw_type_info.flagged(manager, "IFeatureManager") or manager
            replaced = os.path.exists(target)
            if replaced:
                holder_doc = _loaded_by_path(adapter, target)
                if holder_doc is not None:
                    raise InvalidSaveBodiesRequest(
                        f"{target} is loaded in SolidWorks as {holder_doc.title!r}; "
                        "close it first, or use resplit_bodies to update the file "
                        "a Save Bodies feature already writes."
                    )
                os.remove(target)
            expected = body_volume_m3(adapter, match)
            started = time.time()
            feature = adapter._attempt(
                lambda: manager.CreateSaveBodyFeature(
                    _variant_array(int(getattr(pythoncom, "VT_DISPATCH", 9)), [match]),
                    _variant_array(int(getattr(pythoncom, "VT_BSTR", 8)), [target]),
                    assembly or "",
                    False,
                    True,
                )
            )
            adapter._attempt(lambda: adapter._get_attr_or_call(model, "EditRebuild3"))
            if not os.path.exists(target):
                raise Exception(
                    f"CreateSaveBodyFeature did not write a file to {target}; "
                    "the Save Bodies feature was rejected."
                )
            check = file_check(adapter, target, expected, started)
            if check["holds_body"] is False:
                raise Exception(
                    f"{target} was written, but holds {check['volume_mm3']} mm3, "
                    f"not the body's {check['body_volume_mm3']} mm3."
                )
            feature_name = (
                adapter._attempt(lambda: adapter._get_attr_or_call(feature, "Name"))
                if feature is not None
                else None
            )
            return {
                "body": body_name,
                "file_path": target,
                "feature": str(feature_name) if feature_name else None,
                "solid_bodies": names,
                "replaced": replaced,
                "assembly_path": assembly,
                "file_check": check,
            }

        return self._run_on_part(
            "save_body_as_part",
            _save,
            "The file was written and holds the body, so the save worked.",
            close_strays=assembly is None,
        )

    async def get_save_bodies(
        self, feature_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Read the active part's Save Bodies features (or one), unchanged.

        Returns:
            AdapterResult[dict[str, Any]]: ``features``, each as
            :func:`read_definition` gives it.
        """
        refused = self._part_or_error()
        if refused is not None:
            return refused
        adapter = self._adapter(self)

        def _read() -> dict[str, Any]:
            model = adapter.currentModel
            features = find_feature(adapter, model, feature_name)
            return {"features": [read_definition(adapter, model, f) for f in features]}

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("get_save_bodies", _read),
        )

    async def edit_save_bodies(
        self, feature_name: str, changes: dict[str, Any]
    ) -> AdapterResult[dict[str, Any]]:
        """Change a Save Bodies feature's output files or options.

        Args:
            feature_name: The feature, e.g. ``Save Bodies1``.
            changes: See :func:`edit_definition`.

        Returns:
            AdapterResult[dict[str, Any]]: ``feature``, ``changed``,
            ``files_written``, ``definition`` and ``closed_documents``.
        """
        refused = self._part_or_error()
        if refused is not None:
            return refused
        unknown = sorted(set(changes) - set(SETTABLE))
        if unknown or not changes:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    "Invalid request: give at least one of "
                    f"{', '.join(SETTABLE)}"
                    + (f"; not {', '.join(unknown)}" if unknown else "")
                ),
            )
        adapter = self._adapter(self)

        def _edit(model: Any, _holder: dict[str, Any]) -> dict[str, Any]:
            (feature,) = find_feature(adapter, model, feature_name)
            return edit_definition(adapter, model, feature, changes)

        asked_for = bool(changes.get("assembly_path"))
        return self._run_on_part(
            "edit_save_bodies",
            _edit,
            "The feature was changed; check `definition`.",
            close_strays=not asked_for,
        )

    async def resplit_bodies(
        self, feature_name: str | None = None
    ) -> AdapterResult[dict[str, Any]]:
        """Update the existing files the part's Save Bodies features write.

        Returns:
            AdapterResult[dict[str, Any]]: ``files`` (see :func:`update_file`,
            with the ``feature``), ``counts`` by status and
            ``closed_documents``.
        """
        refused = self._part_or_error()
        if refused is not None:
            return refused
        adapter = self._adapter(self)
        return self._run_on_part(
            "resplit_bodies",
            lambda model, _holder: resplit(adapter, model, feature_name),
            "Check each file's `status`.",
        )
