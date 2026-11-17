"""Collect rebuild errors/warnings from the active SolidWorks model (COM side).

Feeds :mod:`rebuild_diagnostics`, which turns each raw issue into a
description and fix hint. Two sources, in the order chosen from the live probe
(R2026x, 2026-10-13, see the #106 change's design.md):

1. ``IModelDocExtension::GetWhatsWrong`` with three byref VARIANT out-params
   - one call returns every flagged feature, its code and warning flag.
2. Fallback: walk the feature tree calling
   ``IFeature::GetErrorCode2(byref IsWarning)`` on each unsuppressed feature.

A cut that meets nothing (``swFeatureErrorCutNotIntersectModel``) also gets
the bodies it acts on, from its definition's ``FeatureScope``,
``AutoSelect`` and ``FeatureScopeBodies`` (read without ``AccessSelections``,
as ``get_feature_definition`` reads), and the part's solid bodies, read once.
"""

from __future__ import annotations

from typing import Any

from .com_utils import as_list as _as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .io import _byref_bool, _byref_variant
from .rebuild_diagnostics import (
    CUT_NOT_INTERSECT,
    BodyScope,
    ParentInfo,
    RawRebuildIssue,
)

_FEATURE_MEMBERS = (
    "GetTypeName2",
    "GetParents",
    "GetNextFeature",
    "GetFirstSubFeature",
    "GetNextSubFeature",
    "GetErrorCode2",
    "IsSuppressed",
    "GetSpecificFeature2",
)
_MAX_FEATURES = 10000  # runaway guard, same as the selector's tree walk


def _sketch_segment_count(adapter: Any, feature: Any) -> int | None:
    """Segment count of a sketch feature, or None when not readable."""
    sketch = _attempt(adapter, lambda: feature.GetSpecificFeature2())
    if sketch is None:
        return None
    _flag(adapter, sketch, "GetSketchSegments")
    segments = _attempt(adapter, lambda: sketch.GetSketchSegments(), default=False)
    if segments is False:
        return None
    return len(_as_list(segments))


def _parents(adapter: Any, feature: Any) -> list[ParentInfo]:
    """Describe the features/sketches ``feature`` depends on."""
    out = []
    for parent in _as_list(_attempt(adapter, lambda: feature.GetParents())):
        _flag(adapter, parent, *_FEATURE_MEMBERS)
        name = str(_attempt(adapter, lambda p=parent: p.Name, "") or "")
        kind = str(_attempt(adapter, lambda p=parent: p.GetTypeName2(), "") or "")
        segments = (
            _sketch_segment_count(adapter, parent) if kind == "ProfileFeature" else None
        )
        out.append(ParentInfo(name=name, type=kind, segment_count=segments))
    return out


#: What a property read gives when it raised.
_UNREAD = object()


class _PartBodies:
    """The part's solid body names, read the first time a cut needs them."""

    def __init__(self, adapter: Any, model: Any) -> None:
        self._adapter, self._model = adapter, model
        self._read = False
        self._names: list[str] | None = None

    def __call__(self) -> list[str] | None:
        if not self._read:
            from .bodies import solid_body_names

            self._names = solid_body_names(self._adapter, self._model)
            self._read = True
        return self._names


def _bool_or_none(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _scope(adapter: Any, feature: Any, part_bodies: _PartBodies) -> BodyScope:
    """The bodies a cut acts on (its definition) and the part's bodies."""
    from .bodies import _wrapped

    _flag(adapter, feature, "GetDefinition")
    data = _attempt(adapter, lambda: feature.GetDefinition())
    scoped = auto = None
    bodies: list[str] | None = None
    if data is not None:
        scoped = _bool_or_none(_attempt(adapter, lambda: data.FeatureScope))
        auto = _bool_or_none(_attempt(adapter, lambda: data.AutoSelect))
        raw = _attempt(adapter, lambda: data.FeatureScopeBodies, _UNREAD)
        if scoped and raw is not _UNREAD:
            bodies = [
                str(_attempt(adapter, lambda b=_wrapped(adapter, item): b.Name) or "")
                for item in _as_list(raw)
            ] or None
    return BodyScope(scoped, auto, bodies, part_bodies())


def _issue(
    adapter: Any,
    feature: Any,
    code: int,
    is_warning: bool,
    part_bodies: _PartBodies | None = None,
) -> RawRebuildIssue:
    _flag(adapter, feature, *_FEATURE_MEMBERS)
    scope = (
        _scope(adapter, feature, part_bodies)
        if part_bodies is not None and int(code) == CUT_NOT_INTERSECT
        else None
    )
    return RawRebuildIssue(
        feature=str(_attempt(adapter, lambda: feature.Name, "") or ""),
        type=str(_attempt(adapter, lambda: feature.GetTypeName2(), "") or ""),
        code=int(code),
        is_warning=bool(is_warning),
        parents=_parents(adapter, feature),
        scope=scope,
    )


def _from_whats_wrong(
    adapter: Any, model: Any, part_bodies: _PartBodies | None = None
) -> list[RawRebuildIssue] | None:
    """Source 1: one GetWhatsWrong call. None when the call is unusable."""
    ext = _attempt(adapter, lambda: model.Extension)
    if ext is None:
        return None
    _flag(adapter, ext, "GetWhatsWrongCount", "GetWhatsWrong")
    count = _attempt(adapter, lambda: ext.GetWhatsWrongCount())
    if isinstance(count, (int, float)) and int(count) == 0:
        return []
    features, codes, warnings = _byref_variant(), _byref_variant(), _byref_variant()
    if _attempt(adapter, lambda: ext.GetWhatsWrong(features, codes, warnings)) is None:
        return None
    feats = _as_list(features.value)
    errs = _as_list(codes.value)
    warns = _as_list(warnings.value)
    if not feats or len(errs) != len(feats):
        return None
    warns = (warns + [False] * len(feats))[: len(feats)]
    return [
        _issue(adapter, f, int(c), bool(w), part_bodies)
        for f, c, w in zip(feats, errs, warns, strict=True)
        if f is not None
    ]


def _check(
    adapter: Any,
    feature: Any,
    issues: list[RawRebuildIssue],
    part_bodies: _PartBodies | None = None,
) -> None:
    """Append ``feature``'s rebuild issue (if any) to ``issues``."""
    if _attempt(adapter, lambda: feature.IsSuppressed(), False):
        return
    is_warning = _byref_bool()
    code = _attempt(adapter, lambda: feature.GetErrorCode2(is_warning), 0)
    if isinstance(code, (int, float)) and int(code) != 0:
        issues.append(
            _issue(adapter, feature, int(code), bool(is_warning.value), part_bodies)
        )


def _from_tree_walk(
    adapter: Any, model: Any, part_bodies: _PartBodies | None = None
) -> list[RawRebuildIssue]:
    """Source 2: per-feature GetErrorCode2 over the unsuppressed tree.

    Visits each top-level feature and its sub-features (e.g. the sketch an
    extrude or cut absorbed), since a sketch error lives on the sketch.
    """
    issues: list[RawRebuildIssue] = []
    feature = _attempt(adapter, lambda: model.FirstFeature())
    guard = 0
    while feature is not None and guard < _MAX_FEATURES:
        guard += 1
        _flag(adapter, feature, *_FEATURE_MEMBERS)
        current = feature
        _check(adapter, current, issues, part_bodies)
        sub = _attempt(adapter, lambda f=current: f.GetFirstSubFeature())
        while sub is not None and guard < _MAX_FEATURES:
            guard += 1
            _flag(adapter, sub, *_FEATURE_MEMBERS)
            sub_current = sub
            _check(adapter, sub_current, issues, part_bodies)
            sub = _attempt(adapter, lambda s=sub_current: s.GetNextSubFeature())
        feature = _attempt(adapter, lambda f=current: f.GetNextFeature())
    return issues


def collect_rebuild_issues(adapter: Any, model: Any) -> list[RawRebuildIssue]:
    """Return every rebuild error/warning on ``model`` after a rebuild.

    Args:
        adapter: A connected adapter (for ``_attempt``).
        model: The active ``IModelDoc2``.

    Returns:
        list[RawRebuildIssue]: Empty when the model is clean. A cut that
        meets nothing carries its ``scope``.
    """
    part_bodies = _PartBodies(adapter, model)
    issues = _from_whats_wrong(adapter, model, part_bodies)
    if issues is None:
        issues = _from_tree_walk(adapter, model, part_bodies)
    return issues
