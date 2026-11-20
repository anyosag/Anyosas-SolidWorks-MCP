"""A fake part with patterns, for ``pattern_linear``, ``pattern_circular`` and ``mirror_feature``.

Each feature has a type (``GetTypeName2``), its parents (``GetParents``) and,
for a pattern, the seed count its definition gives (``GetPatternFeatureCount``).
A pattern's *seeds* are what it really repeats: by default its parents that
aren't planes or axes, or given apart (a direction edge's feature is a parent,
not a seed). Any other feature repeats nothing.

The three feature calls build as SolidWorks 2026 was seen to (Aggrewell
enclosure lid): nothing when a selected feature is one another selected
pattern repeats, seeds of seeds included; a feature otherwise. The features
each call saw are in :attr:`FakePatternPart.calls`.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks.pattern_seeds import PATTERN_TYPES

#: Types the fake selects as directions, axes and planes, never as features.
PLACING = frozenset({"RefPlane", "RefAxis"})


class FakeFeature:
    """One feature of the part."""

    def __init__(
        self,
        part: FakePatternPart,
        name: str,
        kind: str,
        parents: tuple[str, ...],
        seed_count: Any,
        seeds: tuple[str, ...] | None,
    ) -> None:
        self.part, self.Name, self.kind = part, name, kind
        self.parents, self.seed_count = parents, seed_count
        if seeds is None and kind in PATTERN_TYPES:
            seeds = tuple(p for p in parents if part.features[p].kind not in PLACING)
        self.seeds = seeds or ()

    def GetTypeName2(self) -> str:  # noqa: N802 - COM name
        self.part.type_reads.append(self.Name)
        return self.kind

    def GetParents(self) -> Any:  # noqa: N802 - COM name
        return tuple(self.part.features[name] for name in self.parents) or None

    def GetDefinition(self) -> Any:  # noqa: N802 - COM name
        count = self.seed_count
        return SimpleNamespace(GetPatternFeatureCount=lambda: count)

    def Select2(self, append: bool, mark: int) -> bool:  # noqa: N802 - COM name
        if not append:
            self.part.selected.clear()
        self.part.selected.append((self.Name, mark))
        return True


class FakePatternPart:
    """A part's features, its selection, and the three pattern calls.

    Attributes:
        calls: The features selected (not planes or axes) at each call.
        type_reads: Every feature whose ``GetTypeName2`` was read.
        refuse_all: Make every call build nothing.
    """

    def __init__(self) -> None:
        self.features: dict[str, FakeFeature] = {}
        self.selected: list[tuple[str, int]] = []
        self.calls: list[list[str]] = []
        self.type_reads: list[str] = []
        self.refuse_all = False
        self.Extension = SimpleNamespace(SelectByID2=self._select_by_id)
        self.FeatureManager = SimpleNamespace(
            FeatureLinearPattern5=lambda *_a: self._build("LPattern"),
            FeatureCircularPattern5=lambda *_a: self._build("CirPattern"),
            InsertMirrorFeature=lambda *_a: self._build("Mirror"),
        )
        for plane in ("Front Plane", "Top Plane", "Right Plane"):
            self.add(plane, "RefPlane")

    def add(
        self,
        name: str,
        kind: str = "Cut",
        parents: tuple[str, ...] = (),
        seed_count: Any = None,
        seeds: tuple[str, ...] | None = None,
    ) -> FakePatternPart:
        """Add a feature; its parents must be added first."""
        self.features[name] = FakeFeature(self, name, kind, parents, seed_count, seeds)
        return self

    def FeatureByName(self, name: str) -> FakeFeature | None:  # noqa: N802 - COM name
        return self.features.get(name)

    def ClearSelection2(self, _all: bool) -> bool:  # noqa: N802 - COM name
        self.selected.clear()
        return True

    def _select_by_id(self, name: str, kind: str, *args: Any) -> bool:
        feature = self.features.get(name)
        if kind != "AXIS" or feature is None or feature.kind != "RefAxis":
            return False
        if not args[3]:
            self.selected.clear()
        self.selected.append((name, args[4]))
        return True

    def repeats(self, name: str) -> set[str]:
        """Everything ``name`` repeats, seeds of seeds included."""
        found: set[str] = set()
        for seed in self.features[name].seeds:
            found |= {seed} | self.repeats(seed)
        return found

    def _build(self, kind: str) -> Any:
        chosen = [
            n for n, _mark in self.selected if self.features[n].kind not in PLACING
        ]
        self.calls.append(chosen)
        refused = self.refuse_all or any(
            seed in chosen for name in chosen for seed in self.repeats(name)
        )
        return None if refused else SimpleNamespace(Name=f"{kind}{len(self.calls)}")


class FakeAdapter:
    """The adapter calls the pattern code makes, over a :class:`FakePatternPart`."""

    def __init__(self, part: FakePatternPart) -> None:
        self.currentModel = part

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error=f"Error in {name}: {exc}"
            )

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _attempt_with_error(self, callback: Any) -> tuple[Any, Exception | None]:
        try:
            return callback(), None
        except Exception as exc:
            return None, exc

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


def vent_lid() -> FakePatternPart:
    """The Aggrewell lid: a centre vent and two rings of vents, each a hole and its chamfer."""
    part = FakePatternPart()
    part.add("Ring_Axis", "RefAxis")
    part.add("Vent_Center_Hole").add(
        "Vent_Center_CS", "Chamfer", parents=("Vent_Center_Hole",)
    )
    for ring in ("Vent_Ring1", "Vent_Ring2"):
        part.add(f"{ring}_Hole").add(f"{ring}_CS", "Chamfer", parents=(f"{ring}_Hole",))
        part.add(
            f"{ring}_Pattern",
            "CirPattern",
            parents=(f"{ring}_Hole", f"{ring}_CS", "Ring_Axis"),
            seed_count=2,
        )
    return part


#: Everything the lid's linear pattern was asked to repeat.
LID_FEATURES = [
    "Vent_Center_Hole",
    "Vent_Center_CS",
    "Vent_Ring1_Hole",
    "Vent_Ring1_CS",
    "Vent_Ring1_Pattern",
    "Vent_Ring2_Hole",
    "Vent_Ring2_CS",
    "Vent_Ring2_Pattern",
]

#: What the rings already repeat, as ``left_out`` reports it.
LID_LEFT_OUT = [
    {"feature": "Vent_Ring1_Hole", "patterned_by": "Vent_Ring1_Pattern"},
    {"feature": "Vent_Ring1_CS", "patterned_by": "Vent_Ring1_Pattern"},
    {"feature": "Vent_Ring2_Hole", "patterned_by": "Vent_Ring2_Pattern"},
    {"feature": "Vent_Ring2_CS", "patterned_by": "Vent_Ring2_Pattern"},
]
