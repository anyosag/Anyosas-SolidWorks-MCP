"""Select edges by rule: from a feature, a face or the whole part, then filtered.

``select_face_edges`` takes every edge of one face. Modelling a part with a
hundred edges needs more: "the edges the cut made", "the straight edges
along Y", "the edges inside this box". This module answers those in two
pieces:

* **Pure rules** (no COM), shared by the live and mock adapters:
  :func:`validate_edge_query` and :func:`filter_edges`, which work on the
  edge descriptions :func:`face_selection.describe_edge` returns (mm).
* **COM gathering** (:class:`EdgeRuleSelector`): the candidate edges for a
  source - a feature's faces (``IFeature.GetFaces``), one face, the edges two
  faces share, edges by persistent id, or every body's edges - each once,
  read through the part's :class:`edge_index.PartIndex`, which leaves out
  what ``inside_box`` rules out before reading it and keeps what it read
  until the next rebuild.

The selection itself is :meth:`FaceEdgeSelector.select`: all or nothing.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import flag as _flag
from .edge_convexity import CONVEXITIES, ConvexityReader
from .edge_index import (
    Box,
    IndexedBody,
    IndexedFace,
    PartIndex,
    box_from_mm,
    index_for,
    reaches,
)
from .face_selection import EDGE_TYPES, FaceEdgeSelector, side_vector
from .persist_ids import ids_problem, missing_ids_message, resolve

#: The sources an edge query may name; none of them means every edge.
EDGE_SOURCES = ("feature", "face_point", "side", "plane", "between_faces", "edge_ids")

#: Axis names ``parallel_to`` accepts.
AXIS_VECTORS: dict[str, tuple[float, float, float]] = {
    "x": (1.0, 0.0, 0.0),
    "y": (0.0, 1.0, 0.0),
    "z": (0.0, 0.0, 1.0),
}

#: The most edge descriptions a reply lists; the count is always exact.
MAX_DESCRIBED_EDGES = 50

#: How far an edge may stick out of ``inside_box`` and still be inside, mm.
_BOX_TOLERANCE_MM = 1e-3

#: A source with more edges than this, and an ``inside_box``, is narrowed
#: through the whole body's face boxes (``PartIndex.edges_in_reach``) before
#: its edges are read: a face box costs two calls, an edge about a dozen.
REACH_MIN_EDGES = 60


def axis_vector(value: str | list[float]) -> tuple[float, float, float]:
    """The unit vector for ``"x"``/``"y"``/``"z"`` or an ``[x, y, z]`` direction.

    Raises:
        ValueError: For another name, a vector that isn't three numbers, or
            a zero vector.
    """
    if isinstance(value, str):
        try:
            return AXIS_VECTORS[value.strip().lower()]
        except KeyError:
            raise ValueError(
                f"parallel_to must be 'x', 'y', 'z' or [x, y, z], not {value!r}"
            ) from None
    if len(value) != 3:
        raise ValueError("parallel_to as a direction must be [x, y, z]")
    length = math.sqrt(sum(float(c) ** 2 for c in value))
    if length < 1e-12:
        raise ValueError("parallel_to must not be the zero vector")
    x, y, z = (float(c) / length for c in value)
    return (x, y, z)


#: The keys one rule of ``any_of`` may give.
RULE_KEYS = (
    *EDGE_SOURCES,
    "body",
    "edge_type",
    "parallel_to",
    "parallel_tolerance_deg",
    "inside_box",
    "min_length",
    "max_length",
    "convexity",
)

#: Keys of a query that are options of the call, not of a rule.
OPTION_KEYS = ("with_ids",)

#: The most rules one ``any_of`` takes.
MAX_RULES = 20


def expand_query(query: dict[str, Any]) -> list[dict[str, Any]]:
    """The rules a query asks for: itself, or each ``any_of`` rule.

    Each ``any_of`` rule is laid over the query's own keys, so a source or
    filter given once applies to every rule that doesn't give its own.
    """
    shared = {k: v for k, v in query.items() if k != "any_of" and k not in OPTION_KEYS}
    rules = query.get("any_of")
    if not rules:
        return [shared]
    expanded = []
    for rule in rules:
        merged = {**shared, **rule}
        if rule.get("edge_type") is not None:
            merged["edge_type"] = str(rule["edge_type"]).strip().lower()
        expanded.append(merged)
    return expanded


def validate_edge_query(query: dict[str, Any]) -> str | None:
    """Check an edge query; return what is wrong with it, or None.

    Args:
        query: One source of :data:`EDGE_SOURCES` at most (none means every
            edge), ``body``, plus the filters ``edge_type``, ``parallel_to``,
            ``inside_box``, ``min_length`` and ``max_length`` (mm),
            ``parallel_tolerance_deg`` and ``convexity``. Or ``any_of``:
            rules with those keys, each laid over the query's own
            (:func:`expand_query`); an edge any of them matches is kept.

    Returns:
        str | None: The problem, in words an agent can act on.
    """
    rules = query.get("any_of")
    if rules is None:
        return _rule_problem(query)
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_RULES:
        return f"any_of must be a list of 1 to {MAX_RULES} rules."
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            return (
                f"any_of[{index}] must be a rule: an object of {', '.join(RULE_KEYS)}."
            )
        unknown = sorted(set(rule) - set(RULE_KEYS))
        if unknown:
            return (
                f"any_of[{index}] has unknown key(s) {', '.join(unknown)}; a rule "
                f"takes {', '.join(RULE_KEYS)}."
            )
    for index, rule in enumerate(expand_query(query)):
        problem = _rule_problem(rule)
        if problem:
            return f"any_of[{index}]: {problem}"
    return None


def _point_problem(name: str, point: Any) -> str | None:
    if not isinstance(point, list | tuple) or len(point) != 3:
        return f"{name} must be [x, y, z] in millimetres."
    return None


def _rule_problem(query: dict[str, Any]) -> str | None:
    """What is wrong with one rule (one source and its filters), or None."""
    sources = [s for s in EDGE_SOURCES if query.get(s) not in (None, "")]
    if len(sources) > 1:
        return (
            f"Give at most one of {', '.join(EDGE_SOURCES[:-1])} or "
            f"{EDGE_SOURCES[-1]} (got {', '.join(sources)})."
        )
    return _source_problem(query) or _filter_problem(query)


def _source_problem(query: dict[str, Any]) -> str | None:
    """What is wrong with the rule's source or ``body``, or None."""
    point = query.get("face_point")
    if point is not None:
        problem = _point_problem("face_point", point)
        if problem:
            return problem
    side = query.get("side")
    if side not in (None, ""):
        try:
            side_vector(str(side))
        except ValueError as exc:
            return str(exc)
    between = query.get("between_faces")
    if between is not None:
        if not isinstance(between, list) or len(between) != 2:
            return "between_faces must be two points, [[x, y, z], [x, y, z]] in mm."
        for index, corner in enumerate(between):
            problem = _point_problem(f"between_faces[{index}]", corner)
            if problem:
                return problem
    if query.get("edge_ids") is not None:
        problem = ids_problem(query["edge_ids"])
        if problem:
            return problem
        if query.get("body"):
            return "edge_ids name the edges themselves; leave body out."
    body = query.get("body")
    if body is not None and (not isinstance(body, str) or not body.strip()):
        return "body must be a solid body's name, as list_bodies gives it."
    return None


def _filter_problem(query: dict[str, Any]) -> str | None:
    """What is wrong with the rule's filters, or None."""
    edge_type = query.get("edge_type", "all")
    if edge_type not in EDGE_TYPES:
        return f"edge_type must be one of {', '.join(EDGE_TYPES)} (got {edge_type!r})."
    if query.get("parallel_to") is not None:
        try:
            axis_vector(query["parallel_to"])
        except ValueError as exc:
            return str(exc)
    box = query.get("inside_box")
    if box is not None:
        if len(box) != 2 or any(len(corner) != 3 for corner in box):
            return "inside_box must be [[xmin, ymin, zmin], [xmax, ymax, zmax]] in mm."
        if any(lo > hi for lo, hi in zip(box[0], box[1], strict=True)):
            return "inside_box's first corner must be its minimum on every axis."
    low, high = query.get("min_length"), query.get("max_length")
    if low is not None and high is not None and low > high:
        return "min_length must not be more than max_length."
    convexity = query.get("convexity")
    if convexity is not None and convexity not in CONVEXITIES:
        return f"convexity must be one of {', '.join(CONVEXITIES)} (got {convexity!r})."
    return None


def edge_direction(info: dict[str, Any]) -> tuple[float, float, float] | None:
    """A straight edge's unit direction from its end points, or None."""
    start, end = info.get("start_mm"), info.get("end_mm")
    if info.get("curve") != "linear" or not start or not end:
        return None
    delta = [b - a for a, b in zip(start, end, strict=True)]
    length = math.sqrt(sum(c * c for c in delta))
    if length < 1e-12:
        return None
    x, y, z = (c / length for c in delta)
    return (x, y, z)


def edge_length(info: dict[str, Any]) -> float | None:
    """An edge's length in mm: end to end, or a full circle's circumference."""
    if info.get("length_mm") is not None:
        return float(info["length_mm"])
    if info.get("curve") == "circular" and info.get("radius_mm") is not None:
        return 2 * math.pi * float(info["radius_mm"])
    return None


def edge_extent(info: dict[str, Any]) -> tuple[list[float], list[float]] | None:
    """The smallest box holding an edge's known points, mm, or None.

    A circular edge's box is its whole circle's (centre plus radius across
    the circle's plane), which holds any arc of it; a straight edge's is its
    two ends.
    """
    points = [p for p in (info.get("start_mm"), info.get("end_mm")) if p]
    center, radius, axis = (
        info.get("center_mm"),
        info.get("radius_mm"),
        info.get("axis"),
    )
    if center is not None and radius is not None and axis is not None:
        reach = [float(radius) * math.sqrt(max(0.0, 1.0 - float(a) ** 2)) for a in axis]
        points.append([c - r for c, r in zip(center, reach, strict=True)])
        points.append([c + r for c, r in zip(center, reach, strict=True)])
    if not points:
        return None
    low = [min(p[i] for p in points) for i in range(3)]
    high = [max(p[i] for p in points) for i in range(3)]
    return low, high


def _is_arc(info: dict[str, Any]) -> bool:
    """A circular edge with two different ends: part of its circle, not all."""
    start, end = info.get("start_mm"), info.get("end_mm")
    if info.get("curve") != "circular" or not start or not end:
        return False
    return math.dist(start, end) > 1e-6


def arc_extent(
    info: dict[str, Any], middle_mm: list[float]
) -> tuple[list[float], list[float]] | None:
    """The smallest box holding an arc, mm, from its ends and a point half way.

    ``edge_extent`` gives an arc its whole circle's box, which can stick out
    of a box the arc lies in: the two arcs where a 2 mm fillet meets a
    block's end faces were left out of an ``inside_box`` around the fillet
    (SOLIDWORKS 2026 SP4.1). The arc's box holds its ends and each point of
    its circle furthest along +-x, +-y and +-z that lies on the arc, which
    runs from the start through ``middle_mm`` to the end.
    """
    center, radius, axis = (
        info.get("center_mm"),
        info.get("radius_mm"),
        info.get("axis"),
    )
    if not _is_arc(info) or center is None or radius is None or axis is None:
        return None
    n = _unit3([float(a) for a in axis])
    c = [float(v) for v in center]
    start = [float(v) for v in info["start_mm"]]
    end = [float(v) for v in info["end_mm"]]
    u = _unit3([a - b for a, b in zip(start, c, strict=True)])
    if n is None or u is None:
        return None
    v = _cross3(n, u)

    def angle(point: list[float]) -> float:
        d = [a - b for a, b in zip(point, c, strict=True)]
        return math.atan2(_dot3(d, v), _dot3(d, u)) % (2 * math.pi)

    sweep, middle = angle(end), angle([float(m) for m in middle_mm])
    forward = middle <= sweep  # else it runs the other way round

    def on_arc(a: float) -> bool:
        if forward:
            return a <= sweep + 1e-9
        return a >= sweep - 1e-9 or a <= 1e-9

    points = [start, end]
    for i in range(3):
        along = _unit3([(1.0 if j == i else 0.0) - n[i] * n[j] for j in range(3)])
        if along is None:
            continue
        for sign in (1.0, -1.0):
            point = [c[j] + sign * float(radius) * along[j] for j in range(3)]
            if on_arc(angle(point)):
                points.append(point)
    low = [min(p[i] for p in points) for i in range(3)]
    high = [max(p[i] for p in points) for i in range(3)]
    return low, high


def _dot3(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _cross3(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def _unit3(v: list[float]) -> list[float] | None:
    length = math.sqrt(_dot3(v, v))
    return None if length < 1e-12 else [x / length for x in v]


def _is_parallel(
    info: dict[str, Any], axis: tuple[float, float, float], tolerance_deg: float
) -> bool:
    direction = edge_direction(info)
    if direction is None:
        return False
    cosine = abs(sum(a * b for a, b in zip(direction, axis, strict=True)))
    return cosine >= math.cos(math.radians(tolerance_deg))


def _holds(extent: Any, box: list[list[float]]) -> bool:
    if extent is None:
        return False
    low, high = extent
    return all(
        low[i] >= box[0][i] - _BOX_TOLERANCE_MM
        and high[i] <= box[1][i] + _BOX_TOLERANCE_MM
        for i in range(3)
    )


#: ``(edge, description) -> a point half way along an arc (mm) | None``.
ArcMiddle = Callable[[Any, dict[str, Any]], list[float] | None]


def _is_inside(
    edge: Any,
    info: dict[str, Any],
    box: list[list[float]],
    arc_middle: ArcMiddle | None = None,
) -> bool:
    """Whether the edge lies in ``box``; an arc whose circle doesn't is measured.

    The point half way round (``arc_middle``) is read only for an arc whose
    whole circle sticks out of the box while its ends lie in it, and kept in
    the description (``arc_middle_mm``).
    """
    if _holds(edge_extent(info), box):
        return True
    if not _is_arc(info):
        return False
    pairs = list(zip(info["start_mm"], info["end_mm"], strict=True))
    if not _holds(([min(p) for p in pairs], [max(p) for p in pairs]), box):
        return False
    middle = info.get("arc_middle_mm")
    if middle is None and arc_middle is not None:
        middle = arc_middle(edge, info)
        if middle is not None:
            info["arc_middle_mm"] = middle
    return middle is not None and _holds(arc_extent(info, middle), box)


def _length_ok(info: dict[str, Any], low: float | None, high: float | None) -> bool:
    length = edge_length(info)
    if length is None:
        return False
    return (low is None or length >= low - 1e-6) and (
        high is None or length <= high + 1e-6
    )


#: ``(edge, description) -> "convex" | "concave" | "tangent" | None``.
Classify = Callable[[Any, dict[str, Any]], str | None]


def filter_edges(
    edges: list[tuple[Any, dict[str, Any]]],
    query: dict[str, Any],
    classify: Classify | None = None,
    arc_middle: ArcMiddle | None = None,
) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, int]]:
    """Keep the edges every rule in ``query`` allows.

    Args:
        edges: ``(handle, description)`` pairs, descriptions in mm. An
            :class:`EdgeList` a box narrowed also gives ``faces_left_out``.
        query: A query :func:`validate_edge_query` accepted.
        classify: Reads an edge's convexity, for ``convexity``; only the
            edges every other rule kept are read. Without it, the
            description's ``convexity`` is used.
        arc_middle: Reads a point half way along an arc, for ``inside_box``
            (:func:`_is_inside`); without it an arc needs its whole circle
            inside the box.

    Returns:
        tuple: The kept pairs, and how many were left after each rule that
        was given (``candidates`` first), so an empty result says which rule
        removed them.
    """
    counts = {"candidates": len(edges)}
    if getattr(edges, "pruned", False):
        counts["faces_left_out"] = int(getattr(edges, "faces_left_out", 0))
    kept = list(edges)
    edge_type = query.get("edge_type", "all")
    if edge_type != "all":
        kept = [e for e in kept if e[1].get("curve") == edge_type]
        counts["edge_type"] = len(kept)
    if query.get("parallel_to") is not None:
        axis = axis_vector(query["parallel_to"])
        tolerance = float(query.get("parallel_tolerance_deg") or 1.0)
        kept = [e for e in kept if _is_parallel(e[1], axis, tolerance)]
        counts["parallel_to"] = len(kept)
    if query.get("inside_box") is not None:
        box = query["inside_box"]
        kept = [e for e in kept if _is_inside(e[0], e[1], box, arc_middle)]
        counts["inside_box"] = len(kept)
    low, high = query.get("min_length"), query.get("max_length")
    if low is not None or high is not None:
        kept = [e for e in kept if _length_ok(e[1], low, high)]
        counts["length"] = len(kept)
    wanted = query.get("convexity")
    if wanted is not None:
        kinds = [
            classify(edge, info) if classify else info.get("convexity")
            for edge, info in kept
        ]
        unread = sum(1 for kind in kinds if kind is None)
        kept = [e for e, kind in zip(kept, kinds, strict=True) if kind == wanted]
        counts["convexity"] = len(kept)
        if unread:
            counts["convexity_unread"] = unread
    return kept, counts


def describe_source(query: dict[str, Any]) -> str:
    """The source in words, for messages: ``"feature 'Cut-Extrude2'"`` etc."""
    body = query.get("body")
    on = f" on body {body!r}" if body else ""
    if query.get("feature"):
        return f"feature {query['feature']!r}{on}"
    if query.get("face_point") is not None:
        return f"the face at {list(query['face_point'])} mm{on}"
    if query.get("side"):
        return f"the {query['side']} face{on}"
    if query.get("plane"):
        return f"the face(s) on {query['plane']!r}{on}"
    if query.get("between_faces") is not None:
        first, second = (list(p) for p in query["between_faces"])
        return f"the join of the faces at {first} mm and {second} mm{on}"
    if query.get("edge_ids"):
        return f"{len(query['edge_ids'])} edge id(s)"
    return f"body {body!r}" if body else "the part"


def describe_sources(rules: list[dict[str, Any]]) -> str:
    """The sources of the rules in words, each once."""
    named = list(dict.fromkeys(describe_source(rule) for rule in rules))
    return " and ".join(named)


def _steps(query: dict[str, Any], counts: dict[str, int]) -> str:
    near = ""
    if "faces_left_out" in counts:
        near = (
            f" near inside_box ({counts['faces_left_out']} face(s) left out by "
            "their boxes)"
        )
    steps = [f"{describe_source(query)} has {counts['candidates']} edge(s){near}"]
    for rule, count in counts.items():
        if rule == "convexity_unread":
            steps.append(f"{count} whose convexity didn't read")
        elif rule not in ("candidates", "faces_left_out"):
            steps.append(f"{count} pass {rule}")
    return "; ".join(steps)


def no_match_message(query: dict[str, Any], counts: dict[str, int]) -> str:
    """Why nothing matched: the source's edge count, then each rule's."""
    return f"No matching edges found: {_steps(query, counts)}."


def no_match_message_rules(
    rules: list[dict[str, Any]], counts: list[dict[str, int]]
) -> str:
    """Why no rule matched: :func:`no_match_message` for each."""
    if len(rules) == 1:
        return no_match_message(rules[0], counts[0])
    parts = [
        f"rule {index}: {_steps(rule, rule_counts)}"
        for index, (rule, rule_counts) in enumerate(zip(rules, counts, strict=True))
    ]
    return "No matching edges found for any rule: " + " | ".join(parts) + "."


def _frozen(value: Any) -> Any:
    """``value`` with its lists made tuples, so it can be a key."""
    if isinstance(value, list | tuple):
        return tuple(_frozen(v) for v in value)
    return value


def _source_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    """Equal for rules that read the same edges.

    A rule's ``body`` and ``inside_box`` narrow what is read, so they are
    part of its source.
    """
    return tuple(
        (s, _frozen(rule.get(s)))
        for s in (*EDGE_SOURCES, "body", "inside_box")
        if rule.get(s) not in (None, "")
    )


def match_rules(
    rules: list[dict[str, Any]],
    candidates: Any,
    classify: Classify | None = None,
    arc_middle: ArcMiddle | None = None,
) -> tuple[list[tuple[Any, dict[str, Any]]], list[dict[str, int]]]:
    """The edges any rule keeps, each once, reading each source once.

    Reading the edges is what takes the time (about a dozen COM calls an
    edge: 11 s for a 134-edge part), and six calls that each picked one box
    of the same part read them six times (Cellvis DishRack). Rules with the
    same source share one reading.

    Args:
        rules: From :func:`expand_query`.
        candidates: ``(rule) -> [(handle, description)]``, the source's edges.
        classify: Reads an edge's convexity (:func:`filter_edges`).
        arc_middle: Reads a point half way along an arc (:func:`filter_edges`).

    Returns:
        tuple: The kept pairs in rule order, and each rule's counts
        (:func:`filter_edges`).
    """
    read: dict[tuple[Any, ...], list[tuple[Any, dict[str, Any]]]] = {}
    kept_all: list[tuple[Any, dict[str, Any]]] = []
    seen: set[Any] = set()
    every_count = []
    for rule in rules:
        source = _source_key(rule)
        if source not in read:
            read[source] = candidates(rule)
        kept, counts = filter_edges(read[source], rule, classify, arc_middle)
        every_count.append(counts)
        for edge, info in kept:
            key = edge_key(info)
            identity = key if key is not None else ("edge", id(info))
            if identity in seen:
                continue
            seen.add(identity)
            kept_all.append((edge, info))
    return kept_all, every_count


def edge_key(info: dict[str, Any]) -> tuple[Any, ...] | None:
    """A key equal for the same edge reached through two of its faces.

    No two edges of a body share their curve type, end points and circle,
    so the geometry (rounded to 1 nm) identifies the edge. An edge with
    neither end points nor a circle (a closed spline) has no key: None,
    and is never merged with another.
    """

    def _rounded(point: list[float] | None) -> tuple[float, ...] | None:
        return None if point is None else tuple(round(float(c), 6) for c in point)

    ends = sorted(
        (
            p
            for p in (_rounded(info.get("start_mm")), _rounded(info.get("end_mm")))
            if p
        ),
    )
    if not ends and info.get("center_mm") is None:
        return None
    return (
        info.get("curve"),
        tuple(ends),
        _rounded(info.get("center_mm")),
        None if info.get("radius_mm") is None else round(float(info["radius_mm"]), 6),
    )


class EdgeList(list[tuple[Any, dict[str, Any]]]):
    """A source's ``(edge, description)`` pairs.

    Attributes:
        pruned: An ``inside_box`` narrowed them before they were read.
        faces_left_out: How many faces that box left out unread.
    """

    pruned = False
    faces_left_out = 0


class EdgeRuleSelector:
    """Gather the candidate edges for an edge query on the active part.

    Args:
        adapter: A connected ``PyWin32Adapter``.
        index: The part's index; by default the one kept on the adapter
            while the part is unchanged (:func:`edge_index.index_for`).
    """

    def __init__(self, adapter: Any, index: PartIndex | None = None) -> None:
        self._adapter = adapter
        self._faces = FaceEdgeSelector(adapter)
        self.index = index if index is not None else index_for(adapter)
        self._convexity = ConvexityReader(adapter, self.index.flag_once)
        # (feature, body) -> its faces: rules with other boxes share one read.
        self._feature_reads: dict[tuple[str, str | None], list[IndexedFace]] = {}

    def candidates(self, query: dict[str, Any]) -> EdgeList:
        """The source's edges, each once, with their descriptions.

        Raises:
            LookupError: The feature, face, plane, body or an edge id isn't
                there, or the part has no edges.
        """
        body = self.index.body_named(str(query["body"])) if query.get("body") else None
        bodies = None if body is None else [body]
        box = (
            box_from_mm(query["inside_box"])
            if query.get("inside_box") is not None
            else None
        )
        if query.get("edge_ids"):
            return self._listed(self._by_ids(list(query["edge_ids"])))
        if query.get("feature"):
            key = (str(query["feature"]), None if body is None else body.name)
            if key not in self._feature_reads:
                faces = self._feature_faces(key[0], body)
                self._feature_reads[key] = [IndexedFace(face) for face in faces]
            return self._from_faces(self._feature_reads[key], box, bodies)
        if query.get("between_faces") is not None:
            return self._listed(self._between(query["between_faces"], bodies))
        if query.get("face_point") is not None:
            face = self.index.face_at(query["face_point"], bodies)
            return self._from_faces([face], box, bodies)
        if any(query.get(s) not in (None, "") for s in ("side", "plane")):
            resolved = self._faces.resolve(
                None,
                query.get("side"),
                query.get("plane"),
                None if body is None else [body.handle],
            )
            return self._from_faces(
                [IndexedFace(face.handle) for face in resolved], box, bodies
            )
        return self._whole(bodies, box)

    def convexity(self, edge: Any, info: dict[str, Any]) -> str | None:
        """An edge's convexity (``edge_convexity``), read once and kept in ``info``."""
        return self._convexity.read(edge, info)

    def arc_middle(self, edge: Any, info: dict[str, Any]) -> list[float] | None:
        """A point half way along an arc, mm (``ConvexityReader.point_and_direction``)."""
        located = self._convexity.point_and_direction(edge, info)
        if located is None:
            return None
        return [round(c * 1000.0, 9) for c in located[0]]

    def _whole(self, bodies: list[IndexedBody] | None, box: Box | None) -> EdgeList:
        """Every edge of the part (or ``bodies``), or those a box can hold."""
        every = self.index.bodies() if bodies is None else bodies
        if not every:
            raise LookupError("The active part has no solid body edges.")
        if box is None:
            handles = [edge for body in every for edge in self.index.body_edges(body)]
            if not handles:
                raise LookupError("The active part has no solid body edges.")
            return self._listed(handles)
        reach = self.index.edges_in_reach(box, every)
        out = self._listed(reach.edges)
        out.pruned, out.faces_left_out = True, reach.faces_left_out
        return out

    def _from_faces(
        self,
        faces: list[IndexedFace],
        box: Box | None,
        bodies: list[IndexedBody] | None,
    ) -> EdgeList:
        """The edges of ``faces``; with a box, of those whose box reaches it.

        A source with more than :data:`REACH_MIN_EDGES` edges is narrowed
        further through the body's faces (``PartIndex.edges_in_reach``).
        """
        left_out = 0
        if box is not None:
            near = [face for face in faces if reaches(self.index.face_box(face), box)]
            left_out = len(faces) - len(near)
            faces = near
        self.index.read_edges(faces)
        handles = _once([edge for face in faces for edge in face.edges or []])
        if box is None:
            return self._listed(handles)
        if len(handles) > REACH_MIN_EDGES and self.index.edge_identity:
            reach = {id(e) for e in self.index.edges_in_reach(box, bodies).edges}
            handles = [edge for edge in handles if id(edge) in reach]
        out = self._listed(handles)
        out.pruned, out.faces_left_out = True, left_out
        return out

    def _listed(self, handles: list[Any]) -> EdgeList:
        """``(edge, description)`` for each handle, the same edge kept once."""
        out = EdgeList()
        seen: set[tuple[Any, ...]] = set()
        for edge in _once(handles):
            info = self.index.describe(edge)
            key = edge_key(info)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            out.append((edge, info))
        return out

    def _feature_faces(self, name: str, body: IndexedBody | None) -> list[Any]:
        model = self._adapter.currentModel
        feature = _attempt(self._adapter, lambda: model.FeatureByName(name))
        if feature is None:
            raise LookupError(
                f"Feature {name!r} not found; list_features names the features."
            )
        _flag(self._adapter, feature, "GetFaces")
        faces = as_list(_attempt(self._adapter, lambda: feature.GetFaces()))
        if not faces:
            raise LookupError(
                f"Feature {name!r} has no faces in the body (a sketch, plane or "
                "suppressed feature has none)."
            )
        if body is None:
            return faces
        wanted = self.index.body_name(body)
        names = [self._body_name_of(face) for face in faces]
        on = [face for face, named in zip(faces, names, strict=True) if named == wanted]
        if not on:
            made = sorted({n for n in names if n}) or ["no body that reads"]
            raise LookupError(
                f"Feature {name!r} made no faces on body {wanted!r}; its faces are "
                f"on {', '.join(made)}. A feature renames the bodies it changes, "
                "so list_bodies names them as they are now."
            )
        return on

    def _body_name_of(self, face: Any) -> str | None:
        """The name of the body a face is on (``IFace2.GetBody``, ``IBody2.Name``)."""
        self.index.flag_once(face, "GetBody")
        body = _attempt(self._adapter, lambda: face.GetBody())
        name = _attempt(self._adapter, lambda: body.Name)
        return str(name) if name else None

    def _between(
        self, points: list[list[float]], bodies: list[IndexedBody] | None
    ) -> list[Any]:
        """The edges both faces through ``points`` bound."""
        first = self.index.face_at(points[0], bodies)
        second = self.index.face_at(points[1], bodies)
        if first is second:
            raise LookupError(
                f"Both points of between_faces are on one face (at {list(points[0])} "
                "mm); give a point on each of the two faces."
            )
        a, b = self.index.face_edges(first), self.index.face_edges(second)
        if self.index.edge_identity:
            in_b = {id(edge) for edge in b}
            return [edge for edge in a if id(edge) in in_b]
        keys = {edge_key(self.index.describe(edge)) for edge in b} - {None}
        return [edge for edge in a if edge_key(self.index.describe(edge)) in keys]

    def _by_ids(self, ids: list[str]) -> list[Any]:
        found, missing = resolve(self._adapter, self._adapter.currentModel, ids)
        if missing:
            raise LookupError(missing_ids_message(missing))
        return [self.index.canonical(edge) for edge in found]

    def select(
        self, edges: list[tuple[Any, dict[str, Any]]], append: bool
    ) -> int | None:
        """Select every edge, all or nothing (see ``FaceEdgeSelector.select``)."""
        return self._faces.select(edges, append)


def _once(handles: list[Any]) -> list[Any]:
    """``handles`` without repeats of one handle, in order."""
    seen: set[int] = set()
    out = []
    for handle in handles:
        if id(handle) not in seen:
            seen.add(id(handle))
            out.append(handle)
    return out
