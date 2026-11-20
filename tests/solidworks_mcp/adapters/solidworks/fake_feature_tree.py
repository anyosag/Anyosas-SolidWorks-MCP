"""A fake part's feature tree, for ``delete_feature``: what is built on what.

Each feature knows the features built on it (``GetChildren``) and the
sketches it absorbed (``GetFirstSubFeature``, ``GetNextSubFeature``).
``DeleteSelection2`` deletes as SolidWorks 2026 was measured to:

* the selected features and every feature built on them, and theirs;
* with ``swDelete_Absorbed`` (2) also the sketches each of them absorbed,
  and what is built on those;
* a selected sketch a feature still uses is refused (False, nothing goes).

``GetFeatureCount`` counts what is left as the tree lists it, absorbed
sketches included. Every ``FeatureByName`` is noted in :attr:`FakeTree.lookups`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

SKETCH = "ProfileFeature"
ABSORBED = 2


class FakeFeature:
    """One feature of the tree.

    Attributes:
        children: The features built on it, in the order ``GetChildren`` gives.
        sketches: The sketches it absorbed, in sub-feature order.
    """

    def __init__(self, tree: FakeTree, name: str, kind: str) -> None:
        self.tree, self.Name, self.kind = tree, name, kind
        self.children: list[FakeFeature] = []
        self.sketches: list[FakeFeature] = []

    def GetTypeName2(self) -> str:  # noqa: N802 - COM name
        return self.kind

    def GetChildren(self) -> Any:  # noqa: N802 - COM name
        here = [c for c in self.children if c.Name in self.tree.present]
        return tuple(here) or None

    def GetFirstSubFeature(self) -> Any:  # noqa: N802 - COM name
        chain = None
        for sketch in reversed(self.sketches):
            chain = _Sub(sketch, chain)
        return chain

    def Select2(self, _append: bool, _mark: int) -> bool:  # noqa: N802 - COM name
        self.tree.selected.append(self)
        return True


class _Sub:
    """A sub-feature: the sketch itself, and the next one under the same feature."""

    def __init__(self, sketch: FakeFeature, after: _Sub | None) -> None:
        self._sketch, self._after = sketch, after

    def __getattr__(self, name: str) -> Any:
        return getattr(self._sketch, name)

    def GetNextSubFeature(self) -> _Sub | None:  # noqa: N802 - COM name
        return self._after


class FakeTree:
    """The part (``IModelDoc2``): its features in tree order.

    Attributes:
        present: The names still in the tree.
        lookups: Every name ``FeatureByName`` was asked for.
        miscount: Added to ``GetFeatureCount`` after a delete, for a count
            that doesn't match what went.
        unfindable: Names ``FeatureByName`` doesn't find once a delete has
            run, though they are still in the tree.
    """

    def __init__(self) -> None:
        self.features: dict[str, FakeFeature] = {}
        self.present: list[str] = []
        self.selected: list[FakeFeature] = []
        self.lookups: list[str] = []
        self.miscount = 0
        self.unfindable: set[str] = set()
        self.deleted = False
        self.Extension = SimpleNamespace(DeleteSelection2=self._delete_selection)
        self.FeatureManager = SimpleNamespace(GetFeatureCount=self._count)
        self.SelectionManager = SimpleNamespace(
            GetSelectedObjectCount2=lambda _mark: len(self.selected)
        )

    def add(
        self,
        name: str,
        kind: str = "Extrusion",
        on: tuple[str, ...] = (),
        sketch: str | None = None,
    ) -> FakeFeature:
        """Add a feature built on ``on``, absorbing ``sketch``; it goes last."""
        feature = FakeFeature(self, name, kind)
        for parent in on:
            self.features[parent].children.append(feature)
        if sketch is not None:
            feature.sketches.append(self.features[sketch])
            self.features[sketch].children.append(feature)
        self.features[name] = feature
        self.present.append(name)
        return feature

    def listing(self) -> list[dict[str, Any]]:
        """What ``list_features`` would give now."""
        return [{"name": name} for name in self.present]

    def FeatureByName(self, name: str) -> FakeFeature | None:  # noqa: N802 - COM name
        self.lookups.append(name)
        if name not in self.present or (self.deleted and name in self.unfindable):
            return None
        return self.features[name]

    def ClearSelection2(self, _all: bool) -> bool:  # noqa: N802 - COM name
        self.selected.clear()
        return True

    def EditRebuild3(self) -> bool:  # noqa: N802 - COM name
        return True

    def _count(self, _top_level: bool) -> int:
        return len(self.present) + (self.miscount if self.deleted else 0)

    def _delete_selection(self, options: int) -> bool:
        chosen = list(self.selected)
        if not chosen or any(
            f.kind == SKETCH and f.GetChildren() is not None for f in chosen
        ):
            return False
        going: list[FakeFeature] = []
        pending = list(chosen)
        while pending:
            feature = pending.pop()
            if feature in going or feature.Name not in self.present:
                continue
            going.append(feature)
            pending.extend(feature.children)
            if options & ABSORBED:
                pending.extend(feature.sketches)
        self.present = [n for n in self.present if self.features[n] not in going]
        self.selected.clear()
        self.deleted = True
        return True
