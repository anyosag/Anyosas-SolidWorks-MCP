"""A fake SolidWorks with documents loaded with and without windows.

Enough of ``ISldWorks`` and ``IModelDoc2`` for the Save Bodies and loaded
document code: ``GetDocuments`` / ``GetFirstDocument`` / ``GetNext``,
``ActiveDoc``, ``CloseDoc``, ``OpenDoc6``, ``ActivateDoc3`` and
``GetDocumentDependencies2``; documents with bodies, components, features and
``Save3``. ``CloseDoc`` and ``ActivateDoc3`` read a name as
:meth:`FakeApp.named` does: every document of a title, as SolidWorks 2026
closed a part and an assembly both titled ``Tray``.
"""

from __future__ import annotations

import os
from typing import Any

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus


class FakeBody:
    def __init__(self, name: str, volume_m3: float = 1e-6) -> None:
        self.Name = name
        self.volume = volume_m3

    def GetMassProperties(self, _density: float) -> tuple[float, ...]:
        return (0.0, 0.0, 0.0, self.volume, 0.0)


class FakeComponent:
    def __init__(
        self,
        name: str,
        document: FakeDocument | None,
        *,
        config: str = "Default",
        suppressed: bool = False,
        virtual: bool = False,
    ) -> None:
        self.Name2 = name
        self.document = document
        self.ReferencedConfiguration = config
        self.suppressed = suppressed
        self.virtual = virtual

    def GetModelDoc2(self) -> FakeDocument | None:
        return self.document

    def GetSuppression2(self) -> int:
        return 0 if self.suppressed else 2

    def IsVirtual(self) -> bool:
        return self.virtual

    def GetPathName(self) -> str:
        return self.document.path if self.document else ""


class FakeConfiguration:
    def __init__(self, name: str) -> None:
        self.Name = name


class FakeDocument:
    def __init__(
        self,
        app: FakeApp,
        title: str,
        path: str = "",
        doc_type: int = 1,
        *,
        visible: bool = True,
        modified: bool = False,
        bodies: list[FakeBody] | None = None,
        components: list[FakeComponent] | None = None,
        config: str = "Default",
    ) -> None:
        self.app = app
        self.title = title
        self.path = path
        self.doc_type = doc_type
        self.Visible = visible
        self.modified = modified
        self.bodies = bodies or []
        self.components = components or []
        self.config = FakeConfiguration(config)
        self.features: list[Any] = []
        self.saves = 0
        self.rebuilds = 0
        self.FeatureManager: Any = None

    def GetTitle(self) -> str:
        return self.title

    def GetPathName(self) -> str:
        return self.path

    def GetType(self) -> int:
        return self.doc_type

    def GetSaveFlag(self) -> bool:
        return self.modified

    def GetNext(self) -> FakeDocument | None:
        docs = self.app.walk_documents()
        index = docs.index(self)
        return docs[index + 1] if index + 1 < len(docs) else None

    def GetBodies2(self, _kind: int, _visible_only: bool) -> list[FakeBody]:
        return list(self.bodies)

    def GetComponents(self, _top_only: bool) -> list[FakeComponent]:
        return list(self.components)

    def GetActiveConfiguration(self) -> FakeConfiguration:
        return self.config

    def EditRebuild3(self) -> bool:
        self.rebuilds += 1
        return True

    def ForceRebuild3(self, _top_only: bool) -> bool:
        self.rebuilds += 1
        return True

    def Save3(self, _options: int, _errors: Any, _warnings: Any) -> bool:
        self.saves += 1
        if self.path:
            with open(self.path, "ab") as handle:
                handle.write(b"saved")
        return True

    def FirstFeature(self) -> Any:
        return self.features[0] if self.features else None

    def FeatureByName(self, name: str) -> Any:
        return next((f for f in self.features if f.Name == name), None)


class FakeApp:
    """Documents in load order; ``listed`` says which ``GetDocuments`` returns."""

    def __init__(self) -> None:
        self.documents: list[FakeDocument] = []
        self.unlisted: set[str] = set()
        self.active: FakeDocument | None = None
        self.closed: list[str] = []
        self.opened: list[tuple[str, int]] = []
        self.dependencies: dict[str, list[str]] = {}
        #: Titles CloseDoc leaves loaded (something else still uses them).
        self.sticky: set[str] = set()
        #: What OpenDoc6 makes of a path that isn't loaded.
        self.on_open: dict[str, FakeDocument] = {}
        #: Whether CloseDoc and ActivateDoc3 take a full path as a name.
        self.takes_paths = True

    def named(self, name: str) -> list[FakeDocument]:
        """The documents ``name`` names, as SolidWorks reads a name.

        Every document of that title (``CloseDoc("Tray")`` closed a part and an
        assembly both titled ``Tray``); else the one whose file name, with its
        extension, it is; else, with :attr:`takes_paths`, the one at that path.
        """
        titled = [d for d in self.documents if d.title == name]
        by_file = [
            d
            for d in self.documents
            if d.path and os.path.basename(d.path).lower() == name.lower()
        ]
        at_path = [
            d
            for d in self.documents
            if self.takes_paths
            and d.path
            and os.path.normcase(d.path) == os.path.normcase(name)
        ]
        return titled or by_file or at_path

    def add(self, *args: Any, **kwargs: Any) -> FakeDocument:
        document = FakeDocument(self, *args, **kwargs)
        self.documents.append(document)
        return document

    def walk_documents(self) -> list[FakeDocument]:
        return list(self.documents)

    def GetDocuments(self) -> tuple[FakeDocument, ...]:
        return tuple(d for d in self.documents if d.title not in self.unlisted)

    def GetFirstDocument(self) -> FakeDocument | None:
        return self.documents[0] if self.documents else None

    @property
    def ActiveDoc(self) -> FakeDocument | None:
        return self.active

    def CloseDoc(self, title: str) -> None:
        self.closed.append(title)
        if title in self.sticky:
            for document in self.documents:
                if document.title == title:
                    document.Visible = False
            return
        # "" closes the active document (API help).
        gone = [self.active] if title == "" else self.named(title)
        self.documents = [d for d in self.documents if d not in gone]
        if self.active is not None and self.active in gone:
            self.active = self.documents[0] if self.documents else None

    def OpenDoc6(
        self, path: str, _kind: int, options: int, _config: str, _e: Any, _w: Any
    ) -> FakeDocument | None:
        self.opened.append((path, options))
        for document in self.documents:
            if os.path.normcase(document.path) == os.path.normcase(path):
                document.Visible = True
                return document
        document = self.on_open.get(path)
        if document is not None:
            self.documents.append(document)
        return document

    def ActivateDoc3(self, title: str, _prefs: bool, _option: int, _e: Any) -> Any:
        found = self.named(title)
        if found:
            self.active = found[0]
            return found[0]
        return None

    def GetDocumentDependencies2(
        self, path: str, _traverse: bool, _search: bool, _read_only: bool
    ) -> tuple[str, ...] | None:
        refs = self.dependencies.get(path)
        if not refs:
            return None
        flat: list[str] = []
        for ref in refs:
            flat += [os.path.basename(ref), ref]
        return tuple(flat)


class Harness:
    """The adapter methods the mixins call, around a :class:`FakeApp`."""

    def __init__(self, app: FakeApp | None, current: Any = None) -> None:
        self.swApp = app
        self.currentModel = current
        self.operations: list[str] = []
        self.dialog: dict[str, Any] | None = None

    def is_connected(self) -> bool:
        return self.swApp is not None

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        self.operations.append(name)
        try:
            data = callback()
        except Exception as exc:
            result: AdapterResult[Any] = AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )
        else:
            result = AdapterResult(status=AdapterResultStatus.SUCCESS, data=data)
        if self.dialog is not None:
            # What PyWin32Adapter._dialog_result returns after a cancelled dialog.
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"SolidWorks showed a dialog during {name}, 'x'; the server "
                "cancelled it.",
                data={"dialog": self.dialog},
            )
        return result
