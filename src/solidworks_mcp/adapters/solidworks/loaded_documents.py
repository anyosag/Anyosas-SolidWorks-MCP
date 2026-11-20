"""Documents SolidWorks has loaded, with or without a window, and what uses them.

Closing an assembly in The Dipper build (SOLIDWORKS 2026 SP4.1) left two of
its parts loaded: their Office-style lock files (``~$TheDipper_LoadingComb.SLDPRT``)
stayed beside them, and ``list_open_documents`` gave the tools no way to see or
close them. This module:

* lists every loaded document, from ``ISldWorks.GetDocuments`` and the
  ``GetFirstDocument`` / ``IModelDoc2.GetNext`` walk together (they listed
  the same eleven documents on SOLIDWORKS 2026 SP4.1), with ``Visible``
  (``False``: no window of its own, pitfall #44), ``GetSaveFlag`` and the
  ``~$`` lock file;
* says which open documents use each one: an assembly's components at every
  level (``GetComponents(False)`` then ``GetModelDoc2``, so unsaved changes
  count) and a saved document's references on disk
  (``GetDocumentDependencies2`` with ``traverse`` on);
* closes the *leftovers*: loaded, no window, not active, used by no
  document that has one. ``ISldWorks.CloseDoc`` "closes the document without
  saving it" when it is modified (API help), so a leftover with unsaved
  changes is never closed here; it is listed instead.
* lists an assembly's parts once each (``list_assembly_parts``), for an
  export per part.

Everything runs on the COM thread inside ``_handle_com_operation``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, cast

from .. import sw_type_info as _sw_type_info
from ..base import AdapterResult, AdapterResultStatus
from .com_utils import as_list
from .com_utils import flag as _flag

#: ``swDocumentTypes_e``.
DOC_PART = 1
DOC_ASSEMBLY = 2
DOC_DRAWING = 3
DOC_TYPE_NAMES = {DOC_PART: "Part", DOC_ASSEMBLY: "Assembly", DOC_DRAWING: "Drawing"}
#: ``swComponentSuppressionState_e.swComponentSuppressed``.
COMPONENT_SUPPRESSED = 0
#: The most documents one walk reads (a guard against a cycle in ``GetNext``).
MAX_DOCUMENTS = 2000


def normalize(path: str) -> str:
    """A path in the form used to compare files (absolute, case-folded)."""
    return os.path.normcase(os.path.abspath(path))


def lock_file(path: str) -> str | None:
    """The ``~$`` lock file SolidWorks keeps beside a loaded file, if it is there."""
    if not path:
        return None
    candidate = os.path.join(os.path.dirname(path), "~$" + os.path.basename(path))
    return candidate if os.path.exists(candidate) else None


#: The files SolidWorks opens: a title ending in one of these names one file.
DOCUMENT_EXTENSIONS = (".sldprt", ".sldasm", ".slddrw")


def title_shared(adapter: Any, title: str, path: str) -> bool:
    """Whether another loaded document has the title ``title`` too.

    ``ISldWorks.CloseDoc`` and ``ActivateDoc3`` take a name. When Windows
    hides file extensions, a part and an assembly of one name are both titled
    ``Tray``, and ``CloseDoc("Tray")`` closed both (Aggrewell enclosure v2,
    SOLIDWORKS 2026). Nothing is read for a title with its extension, which
    names one file, or for a document never saved, which has no path to be
    told apart by.

    Args:
        adapter: A connected adapter, on the COM thread.
        title: The document's title.
        path: Its full path ("" when never saved).

    Returns:
        bool: True when a document with another path has the same title.
    """
    if not path or os.path.splitext(title)[1].lower() in DOCUMENT_EXTENSIONS:
        return False
    own = normalize(path)
    app = adapter.swApp
    for document in as_list(adapter._attempt(lambda: app.GetDocuments())):
        other = _text(adapter, document, "GetPathName")
        same_title = _text(adapter, document, "GetTitle").lower() == title.lower()
        if same_title and (not other or normalize(other) != own):
            return True
    return False


def same_path(adapter: Any, document: Any, path: str) -> bool:
    """Whether ``document`` is the file at ``path`` (its ``GetPathName``)."""
    own = _text(adapter, document, "GetPathName")
    return bool(own) and bool(path) and normalize(own) == normalize(path)


def close_name(adapter: Any, title: str, path: str) -> str:
    """What to give ``ISldWorks.CloseDoc`` to close one document.

    Its title, or its full path when another loaded document has the same
    title (:func:`title_shared`): ``CloseDoc`` closes every document of a
    title. Whether SolidWorks takes a path there is to be checked live.
    """
    return path if title_shared(adapter, title, path) else title


@dataclass
class LoadedDocument:
    """One document SolidWorks has loaded."""

    document: Any
    title: str
    path: str
    type: int
    visible: bool | None
    modified: bool | None
    used_by: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """The path when saved, else the title: one document each."""
        return normalize(self.path) if self.path else f"<{self.title}>"

    def describe(self, active_key: str | None) -> dict[str, Any]:
        """The document as plain data."""
        is_active = self.key == active_key
        return {
            "title": self.title,
            "path": self.path,
            "type": DOC_TYPE_NAMES.get(self.type, "Unknown"),
            "is_active": is_active,
            "visible": self.visible,
            "modified": self.modified,
            "used_by": list(self.used_by),
            "leftover": self.leftover(active_key),
            "lock_file": lock_file(self.path),
        }

    def leftover(self, active_key: str | None) -> bool:
        """Loaded with no window, not active, and used by no document with one."""
        return self.visible is False and not self.used_by and self.key != active_key


def _text(adapter: Any, obj: Any, name: str) -> str:
    value = adapter._attempt(lambda: adapter._get_attr_or_call(obj, name))
    return value.strip() if isinstance(value, str) else ""


def _bool(adapter: Any, obj: Any, name: str) -> bool | None:
    value = adapter._attempt(lambda: adapter._get_attr_or_call(obj, name))
    return value if isinstance(value, bool) else None


def describe(adapter: Any, document: Any) -> LoadedDocument:
    """Read one document's title, path, type, visibility and save flag."""
    doc_type = adapter._attempt(lambda: adapter._get_attr_or_call(document, "GetType"))
    visible = adapter._attempt(lambda: document.Visible)
    return LoadedDocument(
        document=document,
        title=_text(adapter, document, "GetTitle"),
        path=_text(adapter, document, "GetPathName"),
        type=int(doc_type) if isinstance(doc_type, (int, float)) else 0,
        visible=visible if isinstance(visible, bool) else None,
        modified=_bool(adapter, document, "GetSaveFlag"),
    )


def _walk(adapter: Any, app: Any) -> list[Any]:
    """``GetFirstDocument`` then ``IModelDoc2.GetNext``, as far as it goes."""
    _flag(adapter, app, "GetFirstDocument")
    found: list[Any] = []
    document = adapter._attempt(lambda: app.GetFirstDocument())
    while document is not None and len(found) < MAX_DOCUMENTS:
        found.append(document)
        current = document
        _flag(adapter, current, "GetNext")
        document = adapter._attempt(lambda c=current: c.GetNext())
    return found


def loaded_documents(adapter: Any) -> list[LoadedDocument]:
    """Every document SolidWorks has loaded, once each.

    ``GetDocuments`` first, then the ``GetFirstDocument`` walk for any it
    didn't list.
    """
    app = adapter.swApp
    listed = as_list(adapter._attempt(lambda: app.GetDocuments()))
    documents: dict[str, LoadedDocument] = {}
    for raw in [*listed, *_walk(adapter, app)]:
        if raw is None:
            continue
        loaded = describe(adapter, raw)
        documents.setdefault(loaded.key, loaded)
    return list(documents.values())


def active_key(adapter: Any) -> str | None:
    """The key of SolidWorks' active document, ``None`` when there is none."""
    app = adapter.swApp
    active = adapter._attempt(lambda: app.ActiveDoc)
    return describe(adapter, active).key if active is not None else None


def component_paths(adapter: Any, assembly: Any) -> list[str]:
    """The files an assembly's loaded components hold, at every level."""
    from .io import _as_com

    wrapped = _sw_type_info.flagged(assembly, "IAssemblyDoc") or assembly
    components = adapter._attempt(lambda: wrapped.GetComponents(False))
    paths: list[str] = []
    for component in as_list(components):
        comp = _as_com(adapter, component, "IComponent2") or component
        model = adapter._attempt(
            lambda c=comp: adapter._get_attr_or_call(c, "GetModelDoc2")
        )
        path = _text(adapter, model, "GetPathName") if model is not None else ""
        path = path or _text(adapter, comp, "GetPathName")
        if path:
            paths.append(path)
    return paths


def _references(adapter: Any, path: str) -> list[str]:
    from .references import dependencies

    pairs = adapter._attempt(lambda: dependencies(adapter, path, True)) or []
    return [p for _name, p in pairs]


def mark_users(adapter: Any, documents: list[LoadedDocument]) -> None:
    """Fill each document's ``used_by``: the documents with a window that use it.

    A document with a window is used by nothing here (it is the user's own);
    a hidden one is used by an open document that holds it as a component or
    references its file.
    """
    by_key = {d.key: d for d in documents}
    for user in documents:
        if user.visible is False:
            continue
        used: list[str] = []
        if user.type == DOC_ASSEMBLY:
            used += component_paths(adapter, user.document)
        if user.path:
            used += _references(adapter, user.path)
        for path in used:
            target = by_key.get(normalize(path))
            if (
                target is not None
                and target is not user
                and user.title not in target.used_by
            ):
                target.used_by.append(user.title)


def list_loaded(adapter: Any) -> dict[str, Any]:
    """Every loaded document described, and the titles of the leftovers."""
    documents = loaded_documents(adapter)
    mark_users(adapter, documents)
    active = active_key(adapter)
    described = [d.describe(active) for d in documents]
    return {
        "documents": described,
        "leftovers": [d["title"] for d in described if d["leftover"]],
        "count": len(described),
    }


def close_leftovers(adapter: Any) -> dict[str, Any]:
    """Close the leftovers that have no unsaved changes, and check they went.

    Returns:
        dict[str, Any]: ``closed`` (no longer loaded; with ``lock_file``, the
        lock file still there or ``None``), ``still_loaded`` (SolidWorks kept
        them: something it has loaded still uses them in memory), and
        ``kept_modified`` (unsaved changes, so not closed: save or close them
        yourself).
    """
    documents = loaded_documents(adapter)
    mark_users(adapter, documents)
    active = active_key(adapter)
    leftovers = [d for d in documents if d.leftover(active)]
    kept = [d for d in leftovers if d.modified is not False]
    tried = [d for d in leftovers if d.modified is False]
    app = adapter.swApp
    for document in tried:
        name = close_name(adapter, document.title, document.path)
        adapter._attempt(lambda n=name: app.CloseDoc(n))
    after = {d.key for d in loaded_documents(adapter)}
    closed = [d for d in tried if d.key not in after]
    remaining = [d for d in tried if d.key in after]
    return {
        "closed": [
            {"title": d.title, "path": d.path, "lock_file": lock_file(d.path)}
            for d in closed
        ],
        "still_loaded": [{"title": d.title, "path": d.path} for d in remaining],
        "kept_modified": [{"title": d.title, "path": d.path} for d in kept],
    }


def _configuration_name(adapter: Any, model: Any) -> str | None:
    config = adapter._attempt(lambda: model.GetActiveConfiguration())
    if config is None:
        return None
    _flag(adapter, config, "GetName")
    name = adapter._attempt(lambda: adapter._get_attr_or_call(config, "Name"))
    if not isinstance(name, str):
        name = adapter._attempt(lambda: config.GetName())
    return name if isinstance(name, str) else None


def assembly_parts(adapter: Any, assembly: Any) -> list[dict[str, Any]]:
    """The parts an assembly uses, at every level, once per file and configuration.

    Suppressed components and sub-assemblies are left out (a sub-assembly's
    parts are listed themselves).

    Returns:
        list[dict[str, Any]]: ``path``, ``title``, ``configuration`` (the
        one the components use), ``active_configuration`` (the part's own),
        ``instances``, ``components`` (up to five names) and ``virtual``.
    """
    from .io import _as_com

    wrapped = _sw_type_info.flagged(assembly, "IAssemblyDoc") or assembly
    parts: dict[tuple[str, str], dict[str, Any]] = {}
    for component in as_list(adapter._attempt(lambda: wrapped.GetComponents(False))):
        comp = _as_com(adapter, component, "IComponent2") or component
        if adapter._attempt(lambda c=comp: c.GetSuppression2()) == COMPONENT_SUPPRESSED:
            continue
        model = adapter._attempt(
            lambda c=comp: adapter._get_attr_or_call(c, "GetModelDoc2")
        )
        if model is None:
            continue
        loaded = describe(adapter, model)
        if loaded.type != DOC_PART:
            continue
        config = adapter._attempt(lambda c=comp: c.ReferencedConfiguration)
        config = config if isinstance(config, str) else ""
        name = _text(adapter, comp, "Name2") or loaded.title
        key = (loaded.key, config)
        entry = parts.get(key)
        if entry is None:
            entry = parts[key] = {
                "path": loaded.path,
                "title": loaded.title,
                "configuration": config or None,
                "active_configuration": _configuration_name(adapter, model),
                "instances": 0,
                "components": [],
                "virtual": bool(
                    adapter._attempt(
                        lambda c=comp: adapter._get_attr_or_call(c, "IsVirtual")
                    )
                ),
            }
        entry["instances"] += 1
        if len(entry["components"]) < 5:
            entry["components"].append(name)
    return list(parts.values())


class SolidWorksLoadedDocumentsMixin:
    """List and close loaded documents, and list an assembly's parts."""

    @staticmethod
    def _adapter(obj: Any) -> Any:
        return obj

    async def list_loaded_documents(self) -> AdapterResult[dict[str, Any]]:
        """Every loaded document, with or without a window, and the leftovers.

        Returns:
            AdapterResult[dict[str, Any]]: ``documents`` (``title``, ``path``,
            ``type``, ``is_active``, ``visible``, ``modified``, ``used_by``,
            ``leftover``, ``lock_file``), ``leftovers`` and ``count``.
        """
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation(
                "list_loaded_documents", lambda: list_loaded(adapter)
            ),
        )

    async def close_leftover_documents(self) -> AdapterResult[dict[str, Any]]:
        """Close loaded documents with no window that no open document uses.

        Returns:
            AdapterResult[dict[str, Any]]: See :func:`close_leftovers`.
        """
        adapter = self._adapter(self)
        if not adapter.is_connected():
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="Not connected to SolidWorks"
            )
        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation(
                "close_leftover_documents", lambda: close_leftovers(adapter)
            ),
        )

    async def list_assembly_parts(self) -> AdapterResult[dict[str, Any]]:
        """The active assembly's parts, once per file and configuration.

        Returns:
            AdapterResult[dict[str, Any]]: ``assembly`` (``title``, ``path``)
            and ``parts`` (see :func:`assembly_parts`).
        """
        adapter = self._adapter(self)
        model = adapter.currentModel
        if model is None:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )

        def _list() -> dict[str, Any]:
            loaded = describe(adapter, model)
            if loaded.type != DOC_ASSEMBLY:
                raise ValueError(
                    "list_assembly_parts requires an active assembly document"
                )
            return {
                "assembly": {"title": loaded.title, "path": loaded.path},
                "parts": assembly_parts(adapter, model),
            }

        return cast(
            AdapterResult[dict[str, Any]],
            adapter._handle_com_operation("list_assembly_parts", _list),
        )
