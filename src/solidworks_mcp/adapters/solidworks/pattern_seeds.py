"""The features a listed pattern repeats already, left out of a new pattern or mirror.

A pattern or mirror of features repeats its own seeds with it. On the
Aggrewell enclosure lid (SOLIDWORKS 2026) a linear pattern of
``Vent_Ring1_Pattern`` alone, a circular pattern of a vent hole and its
chamfer, made 18 new vents, the seed vent's copies included (-109.25 mm³).
Listed beside those seeds, the same pattern made SolidWorks refuse the call:
``FeatureLinearPattern5`` and ``InsertMirrorFeature`` returned nothing.

So before anything is selected, ``pattern_linear``, ``pattern_circular`` and
``mirror_feature`` find the listed features that are patterns or mirrors
(``IFeature.GetTypeName2`` in :data:`PATTERN_TYPES`) and leave out every
other listed feature one of them repeats, following a pattern of a pattern
down to the inner pattern's seeds. A plain feature repeats nothing: a chamfer
listed with its hole keeps both.

A pattern's seeds are read from the pattern, without changing the model:

* ``IFeature.GetParents``: its direct parents, which are its seeds and what
  places it (its axis, a plane, the feature a direction edge or a mirror face
  belongs to). Reference geometry and sketches (:data:`NOT_SEEDS`) are never
  seeds.
* ``GetPatternFeatureCount`` on its definition: how many seeds it has. The
  help's mirror example reads it before ``AccessSelections``;
  ``PatternFeatureArray`` would name the seeds, but only after
  ``AccessSelections``, which rolls the model back
  (``ICircularPatternFeatureData.Axis`` reads nothing without it).

When no more parents are left than the pattern has seeds, they are its seeds,
and those listed are left out at once. With more (a direction edge's feature
is a parent too) it is unsure which are seeds: those listed stay in, and are
left out only when SolidWorks refuses the call with them
(:meth:`SeedPlan.attempts`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``IFeature.GetTypeName2`` of the features that repeat other features: the
#: linear, circular, curve-driven, sketch-driven, table-driven, fill
#: (``APattern``) and variable (``DimPattern``) patterns, and the mirror of
#: features (API help, ``GetTypeName2`` Remarks, Body group).
PATTERN_TYPES = frozenset(
    {
        "LPattern",
        "CirPattern",
        "MirrorPattern",
        "CurvePattern",
        "SketchPattern",
        "TablePattern",
        "APattern",
        "DimPattern",
    }
)

#: A body mirror. It repeats bodies, not features, so its parents aren't read
#: as seeds; listed, it can still be why SolidWorks refuses (:func:`refusal`).
BODY_MIRROR = "MirrorSolid"

#: SolidWorks' Thread feature (``create_thread``; ``IThreadFeatureData``). A
#: circular pattern of one made nothing on the Aggrewell tray's M5 posts, and
#: one ``create_thread`` per post worked, so a refusal names it.
THREAD = "SweepThread"

#: Parents that place a pattern and are never its seeds: the help's Reference
#: Geometry and Sketch groups (planes, axes, points, coordinate systems,
#: sketches and curves), the origin, a helix and a curve through points.
NOT_SEEDS = frozenset(
    {
        "BoundingBox",
        "CenterOfMass",
        "CoordSys",
        "GroundPlane",
        "RefAxis",
        "RefPlane",
        "RefPoint",
        "OriginProfileFeature",
        "ProfileFeature",
        "3DProfileFeature",
        "3DSplineCurve",
        "CompositeCurve",
        "ImportedCurve",
        "PLine",
        "RefCurve",
        "SketchBlockDef",
        "SketchBlockInst",
        "SketchBitmap",
        "Helix",
        "CurveInFile",
    }
)

#: The members read on each feature and parent.
_MEMBERS = ("GetTypeName2", "GetParents", "GetDefinition")


@dataclass(frozen=True)
class PatternParents:
    """A pattern's parents that can be seeds, and how many seeds it has.

    Attributes:
        candidates: Its parents' names, reference geometry and sketches left out.
        seed_count: ``GetPatternFeatureCount``; None when it can't be read.
    """

    candidates: tuple[str, ...]
    seed_count: int | None

    @property
    def sure(self) -> bool:
        """Whether every candidate is a seed: no more of them than seeds."""
        return self.seed_count is not None and len(self.candidates) <= self.seed_count


@dataclass
class SeedPlan:
    """What a call leaves out of the features it was given, and when.

    Attributes:
        listed: The features as given.
        sure: Each feature left out at once, with the listed pattern that
            repeats it.
        unsure: Each feature that may be a listed pattern's seed; left out
            only if SolidWorks refuses the call with it.
        patterns: The listed features that are patterns or mirrors.
        threads: The listed features that are Thread features (:data:`THREAD`).
    """

    listed: list[str]
    sure: dict[str, str] = field(default_factory=dict)
    unsure: dict[str, str] = field(default_factory=dict)
    patterns: list[str] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)

    def attempts(self) -> list[list[str]]:
        """The feature lists to try in turn: without the sure seeds, then without the unsure."""
        first = [name for name in self.listed if name not in self.sure]
        if not self.unsure:
            return [first]
        return [first, [name for name in first if name not in self.unsure]]

    def left_out(self, used: list[str]) -> list[dict[str, str]]:
        """``feature`` and ``patterned_by`` for each listed feature ``used`` lacks."""
        dropped = {**self.unsure, **self.sure}
        return [
            {"feature": name, "patterned_by": dropped[name]}
            for name in self.listed
            if name in dropped and name not in used
        ]

    def left_out_field(self, used: list[str]) -> dict[str, Any]:
        """``{"left_out": ...}`` for a reply when ``used`` left any out, else ``{}``."""
        left = self.left_out(used)
        return {"left_out": left} if left else {}

    def refusal(self, used: list[str]) -> str:
        """For SolidWorks making nothing: sentences naming the listed patterns
        and Thread features, or ``""``."""
        said = ""
        patterns = [name for name in self.patterns if name in used]
        if patterns:
            what = (
                "is a pattern or mirror"
                if len(patterns) == 1
                else "are patterns or mirrors"
            )
            said += (
                f" {', '.join(patterns)} {what}, and SolidWorks refuses one listed "
                "with features it already repeats."
            )
        threads = [name for name in self.threads if name in used]
        if threads:
            what = "is a Thread feature" if len(threads) == 1 else "are Thread features"
            said += (
                f" {', '.join(threads)} {what}, which SolidWorks made no pattern "
                "of: cut each thread with its own create_thread call."
            )
        return said


def plan(
    listed: list[str],
    kind_of: Callable[[str], str | None],
    parents_of: Callable[[str], PatternParents],
) -> SeedPlan:
    """Which listed features each listed pattern repeats, sure or unsure.

    Args:
        listed: The features to pattern or mirror, as given (``Name@Doc``
            matches a parent named ``Name``, as selection reads it).
        kind_of: A feature's ``GetTypeName2`` by name (None when unread).
        parents_of: A pattern's :class:`PatternParents` by name.

    Returns:
        SeedPlan: The plan. A feature repeated by two listed patterns names
        the first; one any of them repeats surely is sure.
    """
    repeating = PATTERN_TYPES | {BODY_MIRROR}
    result = SeedPlan(
        list(listed),
        patterns=[n for n in listed if kind_of(n) in repeating],
        threads=[n for n in listed if kind_of(n) == THREAD],
    )
    by_bare = {name.split("@", 1)[0]: name for name in listed}
    for pattern in listed:
        if kind_of(pattern) not in PATTERN_TYPES:
            continue
        for seed, sure in _repeated(pattern, kind_of, parents_of, {pattern}).items():
            name = by_bare.get(seed)
            if name is None or name in result.sure:
                continue
            if sure:
                result.unsure.pop(name, None)
                result.sure[name] = pattern
            else:
                result.unsure.setdefault(name, pattern)
    return result


def _repeated(
    pattern: str,
    kind_of: Callable[[str], str | None],
    parents_of: Callable[[str], PatternParents],
    seen: set[str],
) -> dict[str, bool]:
    """Every feature ``pattern`` repeats, nested patterns followed, and whether surely."""
    parents = parents_of(pattern)
    found: dict[str, bool] = {}
    for seed in parents.candidates:
        found[seed] = found.get(seed, False) or parents.sure
        if seed in seen or kind_of(seed) not in PATTERN_TYPES:
            continue
        seen.add(seed)
        for inner, sure in _repeated(seed, kind_of, parents_of, seen).items():
            found[inner] = found.get(inner, False) or (parents.sure and sure)
    return found


class _FeatureReader:
    """Reads each feature once: its type and, for a pattern, its parents."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self._features: dict[str, Any] = {}
        self._kinds: dict[str, str | None] = {}

    def _feature(self, name: str) -> Any:
        if name not in self._features:
            model = self._adapter.currentModel
            bare = name.split("@", 1)[0]
            self._features[name] = _attempt(
                self._adapter, lambda: model.FeatureByName(bare)
            )
        return self._features[name]

    def _type(self, feature: Any) -> str | None:
        _flag(self._adapter, feature, *_MEMBERS)
        kind = _attempt(self._adapter, lambda: feature.GetTypeName2())
        return kind if isinstance(kind, str) else None

    def kind(self, name: str) -> str | None:
        """The feature's ``GetTypeName2``; None when it isn't there or can't be read."""
        if name not in self._kinds:
            feature = self._feature(name)
            self._kinds[name] = None if feature is None else self._type(feature)
        return self._kinds[name]

    def parents(self, name: str) -> PatternParents:
        """The pattern's parents that can be seeds, and its seed count."""
        feature = self._feature(name)
        candidates: list[str] = []
        for parent in as_list(_attempt(self._adapter, lambda: feature.GetParents())):
            parent_name = _attempt(self._adapter, lambda p=parent: p.Name)
            if not isinstance(parent_name, str) or not parent_name:
                continue
            self._features.setdefault(parent_name, parent)
            if parent_name not in self._kinds:
                self._kinds[parent_name] = self._type(parent)
            if self._kinds[parent_name] not in NOT_SEEDS:
                candidates.append(parent_name)
        return PatternParents(tuple(candidates), self._seed_count(feature))

    def _seed_count(self, feature: Any) -> int | None:
        definition = _attempt(self._adapter, lambda: feature.GetDefinition())
        _flag(self._adapter, definition, "GetPatternFeatureCount")
        count = _attempt(self._adapter, lambda: definition.GetPatternFeatureCount())
        if isinstance(count, bool) or not isinstance(count, int | float):
            return None
        return int(count)


def plan_for(adapter: Any, names: list[str]) -> SeedPlan:
    """Read the listed features and plan what to leave out, before selecting.

    Args:
        adapter: A connected adapter with the part active, on the COM thread.
        names: The features to pattern or mirror.

    Returns:
        SeedPlan: See :func:`plan`.
    """
    reader = _FeatureReader(adapter)
    return plan(names, reader.kind, reader.parents)


def no_plan(names: list[str]) -> SeedPlan:
    """A plan that leaves nothing out: for bodies, which repeat nothing."""
    return SeedPlan(list(names))
