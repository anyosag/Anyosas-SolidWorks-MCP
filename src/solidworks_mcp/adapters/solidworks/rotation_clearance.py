"""Clearance through a rotation: turn a body or a component and find where it hits.

A build on SOLIDWORKS 2026 SP4.1 ("The Dipper", a two-part resin lab part with
a hinge) hand-computed its hinge's open stop (about 160 degrees) and a
pin-flat lift-off window (+/-11 degrees). ``rotation_clearance`` turns one
body of a part, or one component of an assembly, about any axis through a
pivot over an angle range, and reports the smallest gap on the way and the
first angle in each direction where the two overlap.

The sweep (:class:`Sweep`, pure, tested with synthetic geometry):

* coarse steps from the start (0 degrees) out to each end of the range; a
  step whose turned part overlaps the other one is a contact, and the last
  clear step and it are bisected down to ``tolerance_deg`` (overlap only, the
  cheaper check);
* the clearance's local minima away from a contact are refined the same way,
  narrowing a bracket around each (:data:`REFINED_MINIMA` of them, smallest
  first). A contact found while refining moves that direction's contact.

A contact is an *overlap* (a shared volume over :data:`bodies.OVERLAP_MM3`),
not a zero distance, so faces resting on each other at the start, or a
line-to-line pin turning in its hole, don't stop the sweep. Between two steps
a point of the turned part moves at most ``travel_per_step_mm``: a wall
thinner than that can be passed unseen.

The COM, per step, as ``tests/scripts/probe_dipper_analysis.py`` ran it on
SOLIDWORKS 2026 SP4.1 (first contact 42.11 degrees where 42.09 was
computed, the start gap 33.0 mm, both ways):

* **bodies** (a part): ``IBody2.Copy`` of the turned body, then
  ``ApplyTransform`` of the turn (``IMathUtility.CreateTransform``, flagged:
  pitfall #41), so nothing changes in the part; the overlap is
  ``bodies.intersect_mm3`` on copies, and the gap ``IModelDoc2.ClosestDistance``
  between the temporary body's faces and the other body's, nearest face boxes
  first (``clearance.nearest_pair``);
* **components** (an assembly): ``IComponent2.Transform2`` set to the turned
  placement and read back (when it differs the sweep stops). A coincident
  mate of the two Front Planes did not hold it: Transform2 turned the arm
  30 degrees through the mate, as nothing rebuilds the mates between steps.
  So the reply counts the moving component's mates (``IComponent2.GetMates``)
  and the tool warns that the sweep ignored them. The gap is
  ``ClosestDistance`` between the faces' assembly
  twins (pitfall #46); the overlap is ``intersect_mm3`` on copies of each
  component's bodies placed in the assembly. The original transform (and the
  fixed state) is put back at the end, whatever happened.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from loguru import logger

from .. import sw_type_info as _sw_type_info
from . import limits
from .bodies import (
    OVERLAP_MM3,
    _box_m,
    _faces,
    _math_utility,
    _wrapped,
    find_bodies,
    intersect_mm3,
    temp_copy,
)
from .clearance import (
    TOUCHING_MM,
    InvalidRequest,
    _closest,
    _solid_bodies,
    box_distance,
    nearest_pair,
    transform_box,
)
from .com_utils import as_list
from .com_utils import attempt as _attempt
from .com_utils import double_array as _double_array
from .com_utils import flag as _flag
from .placement import (
    _is_fixed,
    _matches,
    _set_fixed,
    _transform_data,
    turn_data,
    turned,
    unit_vector,
)

#: How many local minima of the clearance are refined, smallest first.
REFINED_MINIMA = 3
#: The most samples the reply lists (``samples_total`` counts them all).
MAX_SAMPLES_LISTED = 400
#: The most bracket halvings one refinement makes.
_MAX_HALVINGS = 40
#: How far apart (mm) the placed body and face boxes' centres may be.
_FRAME_TOLERANCE_MM = 1.0

Box = list[float]


@dataclass
class Sample:
    """What one angle gave.

    Attributes:
        angle_deg: The turn from the start.
        overlap_mm3: The volume the two share there.
        measured: The gap was asked for and the two don't overlap.
        clearance_mm: The gap, when measured and SolidWorks gave one.
        point_a_mm: The nearest point on the turned part.
        point_b_mm: The nearest point on the other one.
    """

    angle_deg: float
    overlap_mm3: float = 0.0
    measured: bool = False
    clearance_mm: float | None = None
    point_a_mm: list[float] | None = None
    point_b_mm: list[float] | None = None

    @property
    def blocked(self) -> bool:
        """The two overlap: the turn can't reach this angle."""
        return self.overlap_mm3 > OVERLAP_MM3

    @property
    def gap(self) -> float:
        """The clearance, or infinity when there is none to compare."""
        return math.inf if self.clearance_mm is None else self.clearance_mm


#: ``evaluate(angle_deg, measure)``: the sample at an angle; ``measure`` False
#: asks for the overlap only.
Evaluate = Callable[[float, bool], Sample]


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(float(value), digits) + 0.0


class Sweep:
    """The adaptive sweep over ``[from_deg, to_deg]`` (0, the start, inside it).

    Args:
        evaluate: Gives the sample at an angle (see :data:`Evaluate`).
        from_deg: The range's low end, at most 0.
        to_deg: Its high end, at least 0.
        step_deg: The coarse step.
        tolerance_deg: How finely contacts and minima are narrowed.
        check: Called before each evaluation (``limits.check``).
    """

    def __init__(
        self,
        evaluate: Evaluate,
        from_deg: float,
        to_deg: float,
        step_deg: float,
        tolerance_deg: float,
        check: Callable[[], None] = lambda: None,
    ) -> None:
        self.evaluate = evaluate
        self.low, self.high = float(from_deg), float(to_deg)
        self.step = float(step_deg)
        self.tolerance = float(tolerance_deg)
        self.check = check
        self.samples: dict[float, Sample] = {}

    def at(self, angle: float, measure: bool = True) -> Sample:
        """The sample at ``angle``, evaluated once (again only to add the gap)."""
        key = round(float(angle), 9) + 0.0
        known = self.samples.get(key)
        if known is not None and (known.measured or known.blocked or not measure):
            return known
        self.check()
        sample = self.evaluate(key, measure)
        sample.angle_deg = key
        sample.measured = measure and not sample.blocked
        self.samples[key] = sample
        return sample

    def walk(self, sign: int, end: float) -> None:
        """Coarse steps from 0 toward ``end`` until a step overlaps."""
        angle = 0.0
        while sign * (end - angle) > 1e-9:
            angle = angle + sign * self.step
            if sign * (angle - end) > 0:
                angle = end
            if self.at(angle).blocked:
                return

    def contact(self, sign: int) -> tuple[float, float] | None:
        """``(free_to, first_contact)`` toward ``sign``, bisected; None without one."""
        ordered = sorted((a for a in self.samples if sign * a > 0 or a == 0.0), key=abs)
        hit = next((a for a in ordered if self.samples[a].blocked), None)
        if hit is None or hit == 0.0:
            return None
        lo = max((a for a in ordered if abs(a) < abs(hit)), key=abs)
        hi = hit
        while abs(hi - lo) > self.tolerance:
            middle = (lo + hi) / 2.0
            if self.at(middle, measure=False).blocked:
                hi = middle
            else:
                lo = middle
        return lo, hi

    def _known(self) -> list[float]:
        """The angles with a gap measured or a contact, in order."""
        return sorted(
            a
            for a, s in self.samples.items()
            if s.blocked or (s.measured and s.clearance_mm is not None)
        )

    def minima(self, low: float, high: float) -> list[float]:
        """Local minima of the gap in ``[low, high]``, smallest first.

        A sample next to a contact is the approach to it (the gap closes to 0
        there), and a touching one (faces resting together) is a contact
        itself, so neither is a minimum. A flat run of equal gaps has none.
        """
        known = self._known()
        found = []
        for i, angle in enumerate(known):
            sample = self.samples[angle]
            if sample.blocked or not low - 1e-9 <= angle <= high + 1e-9:
                continue
            if sample.gap <= TOUCHING_MM:
                continue
            near = [
                self.samples[known[j]] for j in (i - 1, i + 1) if 0 <= j < len(known)
            ]
            if not near or any(n.blocked for n in near):
                continue
            if all(sample.gap <= n.gap for n in near) and any(
                sample.gap < n.gap for n in near
            ):
                found.append(angle)
        return sorted(found, key=lambda a: self.samples[a].gap)

    def refine(self, angle: float) -> float | None:
        """Narrow the minimum at ``angle``; its angle, or None if a contact turned up."""
        known = self._known()
        i = known.index(angle)
        a = known[i - 1] if i > 0 else angle
        c = known[i + 1] if i + 1 < len(known) else angle
        b = angle
        for _ in range(_MAX_HALVINGS):
            if c - a <= 2.0 * self.tolerance:
                break
            m1 = (a + b) / 2.0 if b - a > self.tolerance else None
            m2 = (b + c) / 2.0 if c - b > self.tolerance else None
            for m in (m1, m2):
                if m is not None and self.at(m).blocked:
                    return None
            best = min(
                (x for x in (b, m1, m2) if x is not None),
                key=lambda x: self.samples[round(x, 9) + 0.0].gap,
            )
            if best == m1:
                c, b = b, best
            elif best == m2:
                a, b = b, best
            else:
                a, c = (m1 if m1 is not None else a), (m2 if m2 is not None else c)
        return round(b, 9) + 0.0

    def free_range(self) -> tuple[float, float]:
        """The angles the turn reaches: the range, cut at each contact."""
        positive, negative = self.contact(1), self.contact(-1)
        return (
            negative[0] if negative else self.low,
            positive[0] if positive else self.high,
        )

    def run(self) -> dict[str, Any]:
        """The sweep; see :func:`report` for what it returns."""
        start = self.at(0.0)
        if start.blocked:
            return self.report()
        for sign, end in ((1, self.high), (-1, self.low)):
            if end != 0.0:
                self.walk(sign, end)
        done: set[float] = set()
        for _ in range(REFINED_MINIMA + 2):
            low, high = self.free_range()
            todo = [a for a in self.minima(low, high) if a not in done]
            if not todo or len(done) >= REFINED_MINIMA:
                break
            done.add(todo[0])
            settled = self.refine(todo[0])
            if settled is not None:
                done.add(settled)
        return self.report()

    def report(self) -> dict[str, Any]:
        """The sweep's findings.

        Returns:
            dict[str, Any]: ``interfering_at_start`` and ``start_overlap_mm3``;
            ``start_clearance_mm``; ``positive`` and ``negative`` (``to_deg``,
            ``first_contact_deg`` (None: no contact) and ``free_to_deg``);
            ``min_clearance_mm`` and ``min_clearance_deg`` (the smallest gap
            measured, the approach to a contact included) with its points;
            ``closest_pass`` (the smallest refined minimum away from a contact,
            or None); ``samples`` and ``samples_total``; ``evaluations``;
            ``clearance_measured`` (SolidWorks gave a gap at any angle).
        """
        start = self.samples[0.0]
        directions = {}
        for key, sign, end in (("positive", 1, self.high), ("negative", -1, self.low)):
            found = None if start.blocked else self.contact(sign)
            directions[key] = {
                "to_deg": end,
                "first_contact_deg": _round(found[1], 4) if found else None,
                "free_to_deg": _round(found[0], 4) if found else end,
            }
            if start.blocked:
                directions[key].update(first_contact_deg=0.0, free_to_deg=None)
        measured = [s for s in self.samples.values() if s.clearance_mm is not None]
        best = min(measured, key=lambda s: (s.gap, abs(s.angle_deg)), default=None)
        low, high = (self.low, self.high) if start.blocked else self.free_range()
        minima = [] if start.blocked else self.minima(low, high)
        closest = self.samples[minima[0]] if minima else None
        ordered = [self.samples[a] for a in sorted(self.samples)]
        return {
            "interfering_at_start": start.blocked,
            "start_overlap_mm3": _round(start.overlap_mm3, 6),
            "start_clearance_mm": _round(start.clearance_mm, 6),
            **directions,
            "min_clearance_mm": _round(best.clearance_mm, 6) if best else None,
            "min_clearance_deg": _round(best.angle_deg, 4) if best else None,
            "min_points_mm": (
                {"moving": best.point_a_mm, "other": best.point_b_mm} if best else None
            ),
            "closest_pass": (
                {
                    "angle_deg": _round(closest.angle_deg, 4),
                    "clearance_mm": _round(closest.clearance_mm, 6),
                    "point_moving_mm": closest.point_a_mm,
                    "point_other_mm": closest.point_b_mm,
                }
                if closest
                else None
            ),
            "clearance_measured": bool(measured),
            "evaluations": len(self.samples),
            "samples_total": len(ordered),
            "samples": [
                {
                    "angle_deg": _round(s.angle_deg, 4),
                    "clearance_mm": _round(s.clearance_mm, 6),
                    "overlap_mm3": _round(s.overlap_mm3, 6),
                    "contact": s.blocked,
                }
                for s in ordered[:MAX_SAMPLES_LISTED]
            ],
        }


def range_problem(
    range_deg: Sequence[float], step_deg: float, tolerance_deg: float
) -> str | None:
    """What is wrong with a range, step or tolerance, or None.

    The range must hold the start (0); the tolerance can't exceed the step.
    """
    low, high = (float(v) for v in range_deg)
    if not low <= 0.0 <= high or low == high:
        return (
            f"range_deg [{low:g}, {high:g}] must run from at most 0 to at least 0: "
            "0 is where the part is now."
        )
    if high - low > 720.0:
        return "range_deg spans at most 720 degrees."
    if not 0.0 < tolerance_deg <= step_deg:
        return (
            f"tolerance_deg ({tolerance_deg:g}) must be above 0 and at most "
            f"step_deg ({step_deg:g})."
        )
    return None


def check_range(
    range_deg: Sequence[float], step_deg: float, tolerance_deg: float
) -> None:
    """Refuse what :func:`range_problem` finds.

    Raises:
        InvalidRequest: Saying what to change.
    """
    problem = range_problem(range_deg, step_deg, tolerance_deg)
    if problem:
        raise InvalidRequest(problem)


def axis_reach_mm(
    box_m: Sequence[float] | None, axis: Sequence[float], pivot_m: Sequence[float]
) -> float:
    """How far (mm) the box reaches from the axis: the radius its points turn on."""
    if box_m is None:
        return 0.0
    k = unit_vector(axis)
    reach = 0.0
    for i in range(8):
        corner = [box_m[a + 3] if (i >> a) & 1 else box_m[a] for a in range(3)]
        d = [corner[a] - float(pivot_m[a]) for a in range(3)]
        along = sum(d[a] * k[a] for a in range(3))
        reach = max(reach, math.sqrt(max(0.0, sum(v * v for v in d) - along * along)))
    return reach * 1000.0


def sweep(
    evaluate: Evaluate,
    range_deg: Sequence[float],
    step_deg: float,
    tolerance_deg: float,
    reach_mm: float,
    check: Callable[[], None] = lambda: None,
) -> dict[str, Any]:
    """Run :class:`Sweep` and add ``travel_per_step_mm`` (``reach_mm`` x step)."""
    check_range(range_deg, step_deg, tolerance_deg)
    found = Sweep(
        evaluate, range_deg[0], range_deg[1], step_deg, tolerance_deg, check
    ).run()
    found["travel_per_step_mm"] = round(reach_mm * math.radians(step_deg), 4) + 0.0
    return found


#: A placement that keeps everything where it is.
IDENTITY = [
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,
]


def box_sweep(
    moving_box_mm: Sequence[float],
    other_box_mm: Sequence[float],
    axis: Sequence[float],
    pivot_mm: Sequence[float],
    range_deg: Sequence[float],
    step_deg: float,
    tolerance_deg: float,
    start: Sequence[float] | None = None,
) -> dict[str, Any]:
    """The sweep on boxes (mm): the mock adapter's geometry.

    ``moving_box_mm`` is placed by ``start`` (16 values, translation in mm;
    unmoved when None) turned about the axis; each angle's part is the box
    around it, so the mock's contacts come a little early on a turned box.
    """
    from .bodies import nearest_points, overlap_volume

    k = unit_vector(axis)
    placement = list(start or IDENTITY)

    def evaluate(angle: float, measure: bool) -> Sample:
        data = turned(placement, k, pivot_mm, angle)
        rows = [data[0:3], data[3:6], data[6:9]]
        box = transform_box(moving_box_mm, rows, data[9:12])
        sample = Sample(angle, overlap_volume(box, other_box_mm))
        if sample.blocked or not measure:
            return sample
        point_a, point_b = nearest_points(box, other_box_mm)
        sample.clearance_mm = round(box_distance(box, other_box_mm), 6) + 0.0
        sample.point_a_mm = [round(v, 4) + 0.0 for v in point_a]
        sample.point_b_mm = [round(v, 4) + 0.0 for v in point_b]
        return sample

    rows0 = [placement[0:3], placement[3:6], placement[6:9]]
    placed = transform_box(moving_box_mm, rows0, placement[9:12])
    reach = axis_reach_mm(
        [v / 1000.0 for v in placed], k, [v / 1000.0 for v in pivot_mm]
    )
    return {
        **_axis_facts(k, pivot_mm, range_deg, step_deg, tolerance_deg),
        **sweep(evaluate, range_deg, step_deg, tolerance_deg, reach),
    }


# ---- COM: bodies of a part --------------------------------------------------------


def _mm(values: Sequence[float]) -> list[float]:
    return [round(float(v) * 1000.0, 4) + 0.0 for v in values]


def _turn_transform(adapter: Any, utility: Any, values: Sequence[float]) -> Any:
    """``IMathUtility.CreateTransform`` of 16 values.

    Raises:
        RuntimeError: SolidWorks made no transform.
    """
    xform = _attempt(
        adapter, lambda: utility.CreateTransform(_double_array(list(values)))
    )
    if xform is None:
        raise RuntimeError("IMathUtility.CreateTransform gave nothing for the turn.")
    return xform


def _placed_copy(
    adapter: Any, utility: Any, body: Any, values: Sequence[float] | None, label: str
) -> Any:
    """A temporary copy of ``body`` moved by the transform ``values`` (None: kept).

    Raises:
        RuntimeError: The copy couldn't be made or moved.
    """
    piece = temp_copy(adapter, body)
    if piece is None:
        raise RuntimeError(f"IBody2.Copy gave nothing for {label}.")
    if values is not None:
        xform = _turn_transform(adapter, utility, values)
        if not _attempt(adapter, lambda: piece.ApplyTransform(xform), False):
            raise RuntimeError(
                f"A copy of {label} could not be turned (IBody2.ApplyTransform)."
            )
    return piece


def _gap(
    adapter: Any,
    model: Any,
    faces_a: Sequence[tuple[Any, Box]],
    faces_b: Sequence[tuple[Any, Box]],
    sample: Sample,
) -> Sample:
    """``sample`` with the gap between two sets of (face, box), nearest boxes first."""
    nearest = nearest_pair(
        [box for _f, box in faces_a],
        [box for _f, box in faces_b],
        lambda i, j: _closest(adapter, model, faces_a[i][0], faces_b[j][0]),
        touching=TOUCHING_MM / 1000.0,
        check=lambda: limits.check("measuring the clearance while turning"),
    )
    if nearest is None:
        return sample
    gap = nearest.distance * 1000.0
    sample.clearance_mm = 0.0 if gap <= TOUCHING_MM else round(gap, 6)
    sample.point_a_mm = _mm(nearest.point_a)
    sample.point_b_mm = _mm(nearest.point_b)
    return sample


def body_rotation_clearance(
    adapter: Any,
    moving: str,
    other: str,
    axis: Sequence[float],
    pivot_mm: Sequence[float],
    range_deg: Sequence[float],
    step_deg: float,
    tolerance_deg: float,
) -> dict[str, Any]:
    """Turn body ``moving`` of the active part and measure it against ``other``.

    Every step works on a temporary copy, so the part is not changed.

    Raises:
        InvalidRequest: The same body twice, or a range that can't be swept.
        LookupError: A body isn't in the part.
        RuntimeError: A copy couldn't be made, turned or measured.
    """
    if moving == other:
        raise InvalidRequest(
            f"moving and other are both {moving!r}; give two different bodies."
        )
    check_range(range_deg, step_deg, tolerance_deg)
    model = adapter.currentModel
    found = find_bodies(adapter, model, [moving, other])
    k = unit_vector(axis)
    pivot_m = [float(c) / 1000.0 for c in pivot_mm]
    utility = _math_utility(adapter)
    other_box = _box_m(adapter, found[other])
    other_faces = _faces(adapter, other, found[other])
    _flag(adapter, model, "ClosestDistance")

    def evaluate(angle: float, measure: bool) -> Sample:
        values = turn_data(k, pivot_m, angle) if angle else None
        piece = _placed_copy(adapter, utility, found[moving], values, moving)
        box = _box_m(adapter, piece)
        sample = Sample(angle)
        if box is None or other_box is None or box_distance(box, other_box) == 0.0:
            sample.overlap_mm3 = intersect_mm3(adapter, piece, found[other])
        if sample.blocked or not measure:
            return sample
        return _gap(adapter, model, _faces(adapter, moving, piece), other_faces, sample)

    reply = sweep(
        evaluate,
        range_deg,
        step_deg,
        tolerance_deg,
        axis_reach_mm(_box_m(adapter, found[moving]), k, pivot_m),
        check=lambda: limits.check(f"turning {moving} through the angle range"),
    )
    return {
        "kind": "bodies",
        "moving": moving,
        "other": other,
        **_axis_facts(k, pivot_mm, range_deg, step_deg, tolerance_deg),
        **reply,
    }


def _axis_facts(
    axis: Sequence[float],
    pivot_mm: Sequence[float],
    range_deg: Sequence[float],
    step_deg: float,
    tolerance_deg: float,
) -> dict[str, Any]:
    return {
        "axis": [round(float(v), 9) + 0.0 for v in axis],
        "pivot_mm": [float(v) for v in pivot_mm],
        "range_deg": [float(v) for v in range_deg],
        "step_deg": float(step_deg),
        "tolerance_deg": float(tolerance_deg),
    }


# ---- COM: components of an assembly -----------------------------------------------


#: One face of a component: its assembly twin, its box in the part's
#: coordinates, and which of the component's bodies it is on.
PartFace = tuple[Any, Box, int]


def _part_faces(adapter: Any, component: Any, label: str) -> list[PartFace]:
    """Each face's assembly twin, its box in the *part's* coordinates, its body.

    Raises:
        InvalidRequest: The component is suppressed or holds no solid body.
        RuntimeError: No face of it could be read in the assembly.
    """
    faces: list[PartFace] = []
    _flag(adapter, component, "GetCorrespondingEntity")
    for owner, body in enumerate(_solid_bodies(adapter, component, label)):
        _flag(adapter, body, "GetFaces")
        for face in as_list(_attempt(adapter, lambda b=body: b.GetFaces())):
            limits.check("reading faces for the clearance")
            _flag(adapter, face, "GetBox")
            box = _attempt(adapter, lambda f=face: f.GetBox())
            twin = _attempt(adapter, lambda f=face: component.GetCorrespondingEntity(f))
            if twin is None or not isinstance(box, (list, tuple)) or len(box) < 6:
                continue
            faces.append((twin, [float(v) for v in box[:6]], owner))
    if not faces:
        raise RuntimeError(
            f"No face of {label} could be read in the assembly "
            "(GetCorrespondingEntity or GetBox gave nothing)."
        )
    return faces


def _placed_faces(
    faces: Sequence[PartFace], data: Sequence[float]
) -> list[tuple[Any, Box]]:
    """The twins with their boxes placed by ``data`` (assembly coordinates)."""
    rows = [list(data[0:3]), list(data[3:6]), list(data[6:9])]
    return [(twin, transform_box(box, rows, data[9:12])) for twin, box, _o in faces]


def _frame_of(
    faces: Sequence[PartFace],
    bodies: Sequence[tuple[Any, Box | None]],
    data: Sequence[float],
) -> str:
    """:func:`body_frame` for the first body with faces: its box against theirs."""
    owner = faces[0][2]
    raw = bodies[owner][1] if owner < len(bodies) else None
    rows = [list(data[0:3]), list(data[3:6]), list(data[6:9])]
    placed = transform_box(raw, rows, data[9:12]) if raw else None
    own = _placed_faces([f for f in faces if f[2] == owner], data)
    return body_frame(raw, placed, _union([b for _t, b in own]))


def _union(boxes: Sequence[Box | None]) -> Box | None:
    real = [b for b in boxes if b is not None]
    if not real:
        return None
    return [min(b[k] for b in real) for k in range(3)] + [
        max(b[k + 3] for b in real) for k in range(3)
    ]


def _centre(box: Sequence[float]) -> list[float]:
    return [(box[k] + box[k + 3]) / 2.0 for k in range(3)]


def body_frame(
    raw_box: Box | None, placed_box: Box | None, faces_box: Box | None
) -> str:
    """Whether a component's bodies come in the part's or the assembly's coordinates.

    ``faces_box`` is around the faces placed in the assembly (part boxes turned
    by the transform, pitfall #46). The bodies' own box, as read (``raw_box``)
    or placed by the same transform (``placed_box``), lies on it in one frame.

    Returns:
        str: ``part`` (place copies by the component's transform) or
        ``assembly`` (they are placed already).

    Raises:
        RuntimeError: Neither lies on the faces.
    """
    if faces_box is None or (raw_box is None and placed_box is None):
        return "part"
    target = _centre(faces_box)

    def off(box: Box | None) -> float:
        return math.inf if box is None else math.dist(_centre(box), target) * 1000.0

    placed, raw = off(placed_box), off(raw_box)
    if min(placed, raw) > _FRAME_TOLERANCE_MM:
        raise RuntimeError(
            "The component's bodies lie neither where its faces are in the assembly "
            f"nor in its part's coordinates ({min(placed, raw):.3f} mm off), so its "
            "overlap can't be measured."
        )
    return "part" if placed <= raw else "assembly"


def _mate_count(adapter: Any, component: Any) -> int | None:
    """How many mates the component has (``IComponent2.GetMates``), or None."""
    flagged = _sw_type_info.flagged(component, "IComponent2")
    raw = _attempt(adapter, lambda: flagged.GetMates(), _UNREAD_MATES)
    return None if raw is _UNREAD_MATES else len(as_list(raw))


#: What ``GetMates`` gives when it raised.
_UNREAD_MATES = object()


class _ComponentTurn:
    """Turning one component and putting it back (``IComponent2.Transform2``)."""

    def __init__(
        self,
        adapter: Any,
        model: Any,
        assembly: Any,
        component: Any,
        label: str,
        utility: Any,
    ) -> None:
        self.adapter, self.model, self.assembly = adapter, model, assembly
        self.component, self.label, self.utility = component, label, utility
        data = _transform_data(adapter, component)
        if data is None:
            raise RuntimeError(
                f"The placement of {label} could not be read (Transform2)."
            )
        self.start = data
        self.was_fixed = _is_fixed(adapter, component)
        self.moved = False

    def put(self, values: Sequence[float]) -> None:
        """Place the component at ``values`` and check SolidWorks kept it there.

        Raises:
            RuntimeError: The placement read back isn't the one asked (its mates
                or a pattern hold it).
        """
        if self.was_fixed and not self.moved:
            _set_fixed(self.adapter, self.model, self.assembly, self.component, False)
        self.moved = True
        xform = _turn_transform(self.adapter, self.utility, values)
        self.component.Transform2 = xform
        after = _transform_data(self.adapter, self.component)
        rows = [list(values[0:3]), list(values[3:6]), list(values[6:9])]
        if after is None or not _matches(after, values[9:12], rows):
            mates = as_list(
                _attempt(
                    self.adapter,
                    lambda: _sw_type_info.flagged(
                        self.component, "IComponent2"
                    ).GetMates(),
                )
            )
            raise RuntimeError(
                f"SolidWorks kept {self.label} where it was when it was turned"
                + (
                    f": its {len(mates)} mate(s) likely hold it. Suppress the mate "
                    "that fixes its angle (keep the hinge's concentric one) and try "
                    "again."
                    if mates
                    else "; check that a pattern or an in-context reference doesn't hold it."
                )
            )

    def restore(self) -> bool:
        """Put the original placement and fixed state back; True when it is back.

        Never raises: it runs while another error may be on its way out.
        """
        if not self.moved:
            return True

        def put_back() -> None:
            self.component.Transform2 = _turn_transform(
                self.adapter, self.utility, self.start
            )

        _attempt(self.adapter, put_back)
        if self.was_fixed:
            _attempt(
                self.adapter,
                lambda: _set_fixed(
                    self.adapter, self.model, self.assembly, self.component, True
                ),
            )
        after = _transform_data(self.adapter, self.component)
        rows = [self.start[0:3], self.start[3:6], self.start[6:9]]
        back = after is not None and _matches(after, self.start[9:12], rows)
        if not back:
            logger.warning(
                "rotation_clearance could not put {} back at its placement", self.label
            )
        return back


def _component_bodies(
    adapter: Any, component: Any, label: str
) -> list[tuple[Any, Box | None]]:
    """The component's solid bodies (wrapped, flagged) and their boxes as read."""
    bodies = []
    for body in _solid_bodies(adapter, component, label):
        wrapped = _wrapped(adapter, body, "Copy", "GetBodyBox")
        bodies.append((wrapped, _box_m(adapter, wrapped)))
    return bodies


def component_rotation_clearance(
    adapter: Any,
    moving: str,
    other: str,
    axis: Sequence[float],
    pivot_mm: Sequence[float],
    range_deg: Sequence[float],
    step_deg: float,
    tolerance_deg: float,
) -> dict[str, Any]:
    """Turn component ``moving`` of the active assembly and measure it against ``other``.

    The component is placed at each angle and put back at the end, even when
    the sweep fails or is stopped.

    Raises:
        InvalidRequest: The same component twice, one suppressed or without a
            solid body, or a range that can't be swept.
        Exception: A component isn't in the assembly.
        RuntimeError: Mates hold the component, or nothing could be measured.
    """
    from .io import _find_components

    if moving == other:
        raise InvalidRequest(
            f"moving and other are both {moving!r}; give two different components."
        )
    check_range(range_deg, step_deg, tolerance_deg)
    model = adapter.currentModel
    assembly = _sw_type_info.flagged(model, "IAssemblyDoc")
    found = _find_components(adapter, assembly, [moving, other])
    k = unit_vector(axis)
    pivot_m = [float(c) / 1000.0 for c in pivot_mm]
    utility = _math_utility(adapter)
    label_m, label_o = f"moving {moving!r}", f"other {other!r}"
    turn = _ComponentTurn(adapter, model, assembly, found[moving], label_m, utility)
    mates = _mate_count(adapter, found[moving])
    other_data = _transform_data(adapter, found[other])
    if other_data is None:
        raise RuntimeError(
            f"The placement of {label_o} could not be read (Transform2)."
        )
    moving_faces = _part_faces(adapter, found[moving], label_m)
    other_faces = _placed_faces(_part_faces(adapter, found[other], label_o), other_data)
    moving_bodies = _component_bodies(adapter, found[moving], label_m)
    faces_box = _union([b for _t, b in _placed_faces(moving_faces, turn.start)])
    frame = _frame_of(moving_faces, moving_bodies, turn.start)
    others = []
    for body, _box in _component_bodies(adapter, found[other], label_o):
        piece = _placed_copy(
            adapter, utility, body, other_data if frame == "part" else None, other
        )
        others.append((piece, _box_m(adapter, piece)))
    _flag(adapter, model, "ClosestDistance")

    def evaluate(angle: float, measure: bool) -> Sample:
        values = turned(turn.start, k, pivot_m, angle)
        if angle or turn.moved:
            turn.put(values)
        sample = Sample(angle)
        if frame == "part":
            placement: list[float] | None = values
        else:
            placement = turn_data(k, pivot_m, angle) if angle else None
        for body, _box in moving_bodies:
            piece = _placed_copy(adapter, utility, body, placement, moving)
            box = _box_m(adapter, piece)
            for target, target_box in others:
                if (
                    box is None
                    or target_box is None
                    or not box_distance(box, target_box)
                ):
                    sample.overlap_mm3 += intersect_mm3(adapter, piece, target)
        if sample.blocked or not measure:
            return sample
        placed = _placed_faces(moving_faces, values)
        return _gap(adapter, model, placed, other_faces, sample)

    reach = axis_reach_mm(faces_box, k, pivot_m)
    try:
        reply = sweep(
            evaluate,
            range_deg,
            step_deg,
            tolerance_deg,
            reach,
            check=lambda: limits.check(f"turning {moving} through the angle range"),
        )
    finally:
        restored = turn.restore()
    return {
        "kind": "components",
        "moving": moving,
        "other": other,
        **_axis_facts(k, pivot_mm, range_deg, step_deg, tolerance_deg),
        **reply,
        "body_frame": frame,
        "restored": restored,
        "mates": mates,
    }
