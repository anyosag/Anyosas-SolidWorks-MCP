"""Turn SolidWorks rebuild error codes into descriptions and fix hints.

Pure functions, no COM: the adapter collects ``(feature, type, code,
is_warning, parents)`` from ``IModelDocExtension::GetWhatsWrong`` and this
module explains each one so an LLM caller can repair the model rather than
just learning that the rebuild "didn't work".

Hints are chosen most-specific first: an exact ``swFeatureError_e`` code, then
the feature type (``swFeatureErrorUnknown`` - code 1 - is what SolidWorks
reports for many common failures, e.g. an extrude whose sketch was emptied,
so the type is often the only useful signal), then a parent sketch that has
no geometry, then a generic fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..sw_feature_errors import SW_FEATURE_ERRORS

GENERIC_HINT = (
    "Inspect the parents listed for this feature: re-target any missing "
    "edge/face/sketch reference, fix the parent sketch, or suppress/delete the "
    "feature and recreate it."
)

# Exact-code hints. Keys MUST exist in SW_FEATURE_ERRORS (enforced by tests).
_CODE_HINTS: dict[int, tuple[str, str]] = {
    10: (
        "The fillet could not find a closed loop of edges.",
        "Re-select the fillet edges (e.g. select_face_edges then add_fillet "
        "with empty edge_names) or delete and recreate the fillet.",
    ),
    11: (
        "A face the fillet referenced no longer exists.",
        "Re-select the fillet's faces/edges with select_face_edges, or delete "
        "and recreate the fillet.",
    ),
    12: (
        "The fillet radius is invalid.",
        "Set a positive radius smaller than the adjacent faces allow.",
    ),
    13: (
        "An edge the fillet referenced no longer exists (often after an "
        "earlier dimension or feature change); as a warning, the fillet "
        "rounds only the edges it still finds.",
        "Select every edge it should round, the ones it still holds too "
        "(select_edges or select_face_edges), and call edit_feature with "
        "edges: 'selection': that replaces its edges in place. "
        "get_feature_definition gives its edge count.",
    ),
    15: (
        "The fillet radius is too small for the geometry.",
        "Increase the fillet radius.",
    ),
    18: (
        "The fillet radius is too big for the adjacent faces.",
        "Reduce the fillet radius below the thickness of the faces it joins.",
    ),
    19: (
        "The fillet radius is too big for the adjacent faces.",
        "Reduce the fillet radius below the thickness of the faces it joins.",
    ),
    30: (
        "The extrusion would create disjoint (disconnected) bodies.",
        "Change the extrude depth/end condition so the result touches the "
        "existing body, or enable merge-off / multibody intentionally.",
    ),
    31: (
        "The extrusion's end condition found no end (e.g. Up To Surface target "
        "missing).",
        "Re-select the up-to reference or switch the end condition to Blind "
        "with an explicit depth.",
    ),
    33: (
        "The cut profile mixes open and closed contours.",
        "Edit the cut's sketch so it contains only closed contours.",
    ),
    34: (
        "The cut profile is invalid (open, overlapping or self-intersecting).",
        "Edit the cut's sketch: close every contour and remove overlaps.",
    ),
    36: (
        "The boss profile mixes open and closed contours.",
        "Edit the boss sketch so it contains only closed contours.",
    ),
    37: (
        "The boss profile is invalid (open, overlapping or self-intersecting).",
        "Edit the boss sketch: close every contour and remove overlaps.",
    ),
    43: (
        "A mate references geometry that no longer exists (dangling).",
        "Delete the mate and re-mate the components to existing faces/edges.",
    ),
    46: (
        "The assembly is over-defined by mates.",
        "Delete or suppress the conflicting mate(s).",
    ),
    48: ("A mate is broken.", "Delete the mate and recreate it."),
    51: (
        "The sketch's external reference could not be resolved.",
        "Edit the sketch and repair or delete its dangling relations.",
    ),
    71: (
        "The cut does not intersect the model.",
        "Move the cut sketch onto/inside the body, flip the cut direction, or "
        "increase its depth.",
    ),
    72: (
        "The sketch contains a self-intersecting contour.",
        "Edit the sketch and remove the crossing segments.",
    ),
    73: (
        "Items the feature needs are missing.",
        "Re-select the feature's missing references or recreate the feature.",
    ),
}

# Feature-type hints, used when the code alone is not specific
# (``swFeatureErrorUnknown``) or has no curated hint. Keys are
# ``IFeature::GetTypeName2`` values.
_TYPE_HINTS: dict[str, str] = {
    "Extrusion": (
        "Check the boss's parent sketch: it must contain at least one closed "
        "contour, and the depth/end condition must reach geometry."
    ),
    "Cut": (
        "Check the cut's parent sketch has a closed contour that overlaps the "
        "body, and that the cut direction/depth actually removes material."
    ),
    # "ICE" is what GetTypeName2 reports for extrudes created through the
    # API - boss or cut (observed live on R2026x for a boss extrude).
    "ICE": (
        "Check the extrude's parent sketch: it must contain a closed contour, "
        "and the depth/end condition must reach (or, for a cut, overlap) the body."
    ),
    "Fillet": (
        "The fillet's edges/faces probably changed. Re-select them with "
        "select_face_edges and recreate the fillet, or reduce the radius."
    ),
    "Chamfer": (
        "The chamfer's edges probably changed. Re-select them with "
        "select_face_edges and recreate the chamfer, or reduce the distance."
    ),
    "ProfileFeature": (
        "The sketch itself has a problem (dangling or conflicting relations, "
        "or a reference to deleted geometry). Edit the sketch to repair it."
    ),
    "RefPlane": (
        "The reference plane lost the face/plane it was defined from. Redefine "
        "it from existing geometry."
    ),
}

#: Readable names for GetTypeName2 values used in descriptions.
_TYPE_LABELS: dict[str, str] = {
    "ICE": "Extrude",
    "Extrusion": "Boss-extrude",
    "Cut": "Cut-extrude",
    "ProfileFeature": "Sketch",
    "RefPlane": "Reference plane",
}

_EMPTY_SKETCH_HINT = (
    "Parent sketch '{name}' has no geometry. Add a closed profile to it (or "
    "delete this feature)."
)


@dataclass(frozen=True)
class ParentInfo:
    """A feature the failing feature depends on.

    Attributes:
        name: The parent feature or sketch name.
        type: Its ``GetTypeName2`` value (``""`` when unknown).
        segment_count: For sketches, how many segments it has; ``None`` when
            not a sketch or not readable.
    """

    name: str
    type: str = ""
    segment_count: int | None = None


#: ``swFeatureErrorCutNotIntersectModel``: the cut meets none of its bodies.
CUT_NOT_INTERSECT = 71


@dataclass(frozen=True)
class BodyScope:
    """Which bodies a feature acts on, and which the part has.

    Read for a cut that meets nothing (:data:`CUT_NOT_INTERSECT`): the
    Dipper's base sketch edit left an outline 2.5 mm short of the strip, the
    rebuild made it a second body, and a through-all cut scoped to the first
    body then failed, with nothing saying why.

    Attributes:
        feature_scope: True when the feature acts only on its listed bodies
            (``FeatureScope``), False for every body; None when unread.
        auto_select: Whether it picks those bodies itself (``AutoSelect``).
        bodies: The bodies it is scoped to (``FeatureScopeBodies``), by
            name; None when unread.
        part_bodies: The part's solid bodies now, by name; None when unread.
    """

    feature_scope: bool | None = None
    auto_select: bool | None = None
    bodies: list[str] | None = None
    part_bodies: list[str] | None = None


@dataclass(frozen=True)
class RawRebuildIssue:
    """One raw ``GetWhatsWrong`` entry as collected from COM."""

    feature: str
    type: str
    code: int
    is_warning: bool
    parents: list[ParentInfo] = field(default_factory=list)
    #: For a cut that meets nothing: its bodies and the part's.
    scope: BodyScope | None = None


def code_name(code: int) -> str:
    """Return the ``swFeatureError_e`` member name, or ``"code <n>"``."""
    return SW_FEATURE_ERRORS.get(code, f"code {code}")


def _names(names: list[str]) -> str:
    shown = [n or "(unnamed)" for n in names]
    return ", ".join(shown) if shown else "none"


def scope_hint(scope: BodyScope) -> str:
    """What a cut that meets nothing is scoped to, and the bodies the part has.

    Args:
        scope: The cut's scope, as read.

    Returns:
        str: Sentences for the hint; ``""`` when nothing was read.
    """
    parts: list[str] = []
    if scope.feature_scope and scope.bodies:
        parts.append(f"The cut acts only on {_names(scope.bodies)}.")
    elif scope.feature_scope:
        parts.append(
            "The cut acts only on the bodies it selected"
            + (" itself" if scope.auto_select else "")
            + ", which can't be read."
        )
    elif scope.feature_scope is False:
        parts.append("The cut acts on every body.")
    part = scope.part_bodies
    if part is not None:
        noun = "body" if len(part) == 1 else "bodies"
        parts.append(
            f"The part has {len(part)} solid {noun}: {_names(part)} (list_bodies)."
        )
    if part is not None and len(part) > 1:
        parts.append(
            "If an earlier edit split a body off (a sketch edit can leave a "
            "profile short of the body it should join), the body the cut acts "
            "on may no longer be where its sketch is: fix that edit so the "
            "bodies join again, or change the cut's bodies."
        )
    return " ".join(parts)


def _scope_entry(scope: BodyScope) -> dict[str, Any]:
    return {
        "feature_scope": scope.feature_scope,
        "auto_select": scope.auto_select,
        "bodies": scope.bodies,
        "part_bodies": scope.part_bodies,
    }


def body_counts(before: list[str] | None, after: list[str] | None) -> dict[str, Any]:
    """The solid-body counts a rebuild reports, from the names before and after.

    Args:
        before: The part's solid bodies before (None when unread).
        after: Its solid bodies after (None when unread).

    Returns:
        dict[str, Any]: ``solid_body_count_before`` and
        ``solid_body_count_after``, and ``solid_bodies_after`` (the names)
        when the count rose; empty unless both were read.
    """
    if before is None or after is None:
        return {}
    counts: dict[str, Any] = {
        "solid_body_count_before": len(before),
        "solid_body_count_after": len(after),
    }
    if len(after) > len(before):
        counts["solid_bodies_after"] = list(after)
    return counts


def body_count_warning(counts: dict[str, Any], what: str = "The rebuild") -> str | None:
    """A warning when a rebuild left the part more solid bodies than before.

    Args:
        counts: :func:`body_counts`' result (or a reply holding its keys).
        what: What rebuilt, to start the sentence.

    Returns:
        str | None: The warning, or None when the count didn't rise.
    """
    before = counts.get("solid_body_count_before")
    after = counts.get("solid_body_count_after")
    if not isinstance(before, int) or not isinstance(after, int) or after <= before:
        return None
    names = counts.get("solid_bodies_after")
    listed = f" ({_names(names)})" if isinstance(names, list) and names else ""
    return (
        f"{what} left the part {after} solid bodies where it had {before}"
        f"{listed}: a feature no longer meets the body it should join, often "
        "a sketch edit that left a profile short of it. A later feature "
        "scoped to one body, such as a cut, can then miss it. Check "
        "list_bodies, and fix the edit if the bodies should be one."
    )


def explain(issue: RawRebuildIssue) -> dict[str, Any]:
    """Build the caller-facing diagnostic entry for one rebuild issue.

    Args:
        issue: The raw entry collected from SolidWorks.

    Returns:
        dict[str, Any]: ``feature``, ``type``, ``code``, ``code_name``,
        ``severity``, ``description``, ``parents`` and ``hint``; and
        ``scope`` (the bodies the cut acts on and the part's), its sentences
        ahead of the hint, for a cut that meets nothing.
    """
    description, hint = _CODE_HINTS.get(issue.code, ("", ""))
    empty_parent = next(
        (
            p
            for p in issue.parents
            if p.type == "ProfileFeature" and p.segment_count == 0
        ),
        None,
    )
    if empty_parent is not None:
        hint = _EMPTY_SKETCH_HINT.format(name=empty_parent.name)
    elif not hint:
        hint = _TYPE_HINTS.get(issue.type, GENERIC_HINT)
    if not description:
        outcome = "rebuilt with a warning" if issue.is_warning else "failed to rebuild"
        description = (
            f"{_TYPE_LABELS.get(issue.type, issue.type or 'Feature')} "
            f"'{issue.feature}' {outcome} "
            f"({code_name(issue.code)})."
        )
    entry: dict[str, Any] = {
        "feature": issue.feature,
        "type": issue.type,
        "code": issue.code,
        "code_name": code_name(issue.code),
        "severity": "warning" if issue.is_warning else "error",
        "description": description,
        "parents": [
            {"name": p.name, "type": p.type}
            | (
                {"segment_count": p.segment_count}
                if p.segment_count is not None
                else {}
            )
            for p in issue.parents
        ],
        "hint": hint,
    }
    if issue.scope is not None:
        entry["scope"] = _scope_entry(issue.scope)
        said = scope_hint(issue.scope)
        if said:
            entry["hint"] = f"{said} {hint}"
    return entry


def summarize(entries: list[dict[str, Any]]) -> tuple[int, int, str]:
    """Count errors/warnings and build the one-line status message.

    Args:
        entries: Output of :func:`explain` for every issue.

    Returns:
        tuple[int, int, str]: ``(error_count, warning_count, message)``.
    """
    errors = [e["feature"] for e in entries if e["severity"] == "error"]
    warnings = [e["feature"] for e in entries if e["severity"] == "warning"]
    if errors:
        message = (
            f"Rebuild left {len(errors)} feature error(s): {', '.join(errors)}. "
            "See data.errors for each cause and hint."
        )
    elif warnings:
        message = f"Rebuilt with {len(warnings)} warning(s): {', '.join(warnings)}."
    else:
        message = "Rebuilt cleanly; no rebuild errors."
    return len(errors), len(warnings), message


def issues_warning(
    entries: list[dict[str, Any]], *, errors_too: bool = False
) -> str | None:
    """A reply's ``warning`` naming each feature SolidWorks flagged, and why.

    A fillet that lost some of its edges still rebuilds: SolidWorks reports
    it as a warning (``swFeatureErrorFilletNoEdge``), so it is said here
    rather than left in ``data.errors`` under a success.

    Args:
        entries: Output of :func:`explain`.
        errors_too: Name the errors as well (a call that succeeds with them,
            like moving the rollback bar), not only the warnings.

    Returns:
        str | None: The warning, or None when there is nothing to say.
    """
    shown = [e for e in entries if errors_too or e.get("severity") == "warning"]
    if not shown:
        return None
    parts = [
        f"{e['feature']} {'warns' if e['severity'] == 'warning' else 'fails'} "
        f"{e['code_name']}: {e['description']} {e['hint']}"
        for e in shown
    ]
    return "SolidWorks reports: " + " ".join(parts)
