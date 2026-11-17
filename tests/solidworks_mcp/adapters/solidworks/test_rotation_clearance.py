"""The rotation sweep on synthetic geometry: contacts, the closest pass, the edges."""

from __future__ import annotations

import math

import pytest

from solidworks_mcp.adapters.solidworks import rotation_clearance as rc
from solidworks_mcp.adapters.solidworks.clearance import InvalidRequest


def _hinge(
    stop: float = 160.3, close: float = 0.0, pass_at: float = 37.2
) -> tuple[rc.Evaluate, list[tuple[float, bool]]]:
    """A lid resting closed at 0 that hits a stop at ``stop`` and nears a latch.

    The gap is 0 at the start (faces resting), dips to 0.3 mm at ``pass_at``
    and closes to 0 at the stop; past the stop, or below ``close``, the two
    overlap.
    """
    calls: list[tuple[float, bool]] = []

    def evaluate(angle: float, measure: bool) -> rc.Sample:
        calls.append((angle, measure))
        overlap = 10.0 * (max(0.0, angle - stop) + max(0.0, close - angle))
        sample = rc.Sample(angle, overlap)
        if measure and not overlap:
            sample.clearance_mm = min(
                0.3 + 0.02 * abs(angle - pass_at), 0.05 * (stop - angle), 0.05 * angle
            )
            sample.point_a_mm = [angle, 0.0, 0.0]
            sample.point_b_mm = [angle, 1.0, 0.0]
        return sample

    return evaluate, calls


def test_a_hinge_stop_and_the_latch_it_passes() -> None:
    evaluate, calls = _hinge()

    found = rc.sweep(evaluate, [-30, 180], 5, 0.1, 50.0)

    assert found["positive"]["first_contact_deg"] == pytest.approx(160.3, abs=0.1)
    assert (
        found["positive"]["free_to_deg"]
        < 160.3
        < found["positive"]["first_contact_deg"] + 1e-9
    )
    assert found["negative"]["first_contact_deg"] == pytest.approx(0.0, abs=0.1)
    assert found["negative"]["free_to_deg"] == 0.0
    assert found["closest_pass"]["angle_deg"] == pytest.approx(37.2, abs=0.1)
    assert found["closest_pass"]["clearance_mm"] == pytest.approx(0.3, abs=0.01)
    assert found["closest_pass"]["point_moving_mm"][0] == pytest.approx(37.2, abs=0.1)
    # The resting start is the smallest gap measured, at 0.
    assert found["min_clearance_mm"] == 0.0
    assert found["min_clearance_deg"] == 0.0
    assert found["interfering_at_start"] is False
    assert found["clearance_measured"] is True
    assert found["travel_per_step_mm"] == pytest.approx(50 * math.radians(5), abs=1e-3)
    assert found["evaluations"] == len(calls) == found["samples_total"]
    # Each angle is evaluated once, and the bisections ask for overlap only.
    assert len({a for a, _m in calls}) == len(calls)
    assert any(not measure for _a, measure in calls)
    assert [s["angle_deg"] for s in found["samples"]] == sorted(
        s["angle_deg"] for s in found["samples"]
    )


def test_a_swing_that_never_touches() -> None:
    def evaluate(angle: float, measure: bool) -> rc.Sample:
        return rc.Sample(angle, 0.0, clearance_mm=2.0 + math.cos(math.radians(angle)))

    found = rc.sweep(evaluate, [0, 90], 10, 0.5, 10.0)

    assert found["positive"] == {
        "to_deg": 90.0,
        "first_contact_deg": None,
        "free_to_deg": 90.0,
    }
    assert found["negative"]["first_contact_deg"] is None
    # The gap falls all the way: the closest pass is the range's end.
    assert found["min_clearance_deg"] == 90.0
    assert found["min_clearance_mm"] == pytest.approx(2.0)
    assert found["closest_pass"]["angle_deg"] == 90.0
    assert found["evaluations"] >= 10


def test_faces_resting_at_the_start_are_no_closest_pass() -> None:
    evaluate, _calls = _hinge()

    found = rc.sweep(evaluate, [0, 180], 5, 0.1, 50.0)

    # The start touches (0 mm) with nothing swept below it: not a pass.
    assert found["closest_pass"]["angle_deg"] == pytest.approx(37.2, abs=0.1)
    assert found["negative"]["first_contact_deg"] is None


def test_an_overlap_where_it_starts_stops_the_sweep() -> None:
    calls: list[float] = []

    def evaluate(angle: float, measure: bool) -> rc.Sample:
        calls.append(angle)
        return rc.Sample(angle, 5.0)

    found = rc.sweep(evaluate, [-90, 90], 5, 0.1, 10.0)

    assert calls == [0.0]
    assert found["interfering_at_start"] is True
    assert found["start_overlap_mm3"] == 5.0
    assert found["positive"]["first_contact_deg"] == 0.0
    assert found["negative"]["free_to_deg"] is None
    assert found["min_clearance_mm"] is None
    assert found["closest_pass"] is None


def test_a_contact_hidden_between_two_steps_turns_up_while_refining() -> None:
    # A thin post at 21..21.3 degrees: the 5 degree steps land either side of
    # it, and the gap dips toward it, so refining the dip finds it.
    def evaluate(angle: float, measure: bool) -> rc.Sample:
        overlap = 1.0 if 21.0 <= angle <= 21.3 else 0.0
        gap = abs(angle - 21.15) * 0.1
        return rc.Sample(angle, overlap, clearance_mm=None if overlap else gap)

    found = rc.sweep(evaluate, [0, 60], 5, 0.05, 10.0)

    assert found["positive"]["first_contact_deg"] == pytest.approx(21.0, abs=0.06)
    assert found["closest_pass"] is None


def test_without_distances_only_contacts_are_found() -> None:
    def evaluate(angle: float, measure: bool) -> rc.Sample:
        return rc.Sample(angle, 2.0 if angle > 30 else 0.0)

    found = rc.sweep(evaluate, [0, 45], 5, 0.1, 10.0)

    assert found["clearance_measured"] is False
    assert found["min_clearance_mm"] is None
    assert found["positive"]["first_contact_deg"] == pytest.approx(30.0, abs=0.1)


def test_a_flat_gap_has_no_closest_pass() -> None:
    def evaluate(angle: float, measure: bool) -> rc.Sample:
        return rc.Sample(angle, 0.0, clearance_mm=0.25)

    found = rc.sweep(evaluate, [-20, 20], 5, 0.1, 10.0)

    assert found["closest_pass"] is None
    assert found["min_clearance_mm"] == 0.25
    assert found["min_clearance_deg"] == 0.0


def test_a_minimum_at_the_start_is_refined_both_ways() -> None:
    def evaluate(angle: float, measure: bool) -> rc.Sample:
        return rc.Sample(angle, 0.0, clearance_mm=1.0 + abs(angle - 0.7) * 0.01)

    found = rc.sweep(evaluate, [-10, 10], 5, 0.05, 10.0)

    assert found["closest_pass"]["angle_deg"] == pytest.approx(0.7, abs=0.05)


def test_the_sample_list_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "MAX_SAMPLES_LISTED", 3)

    def evaluate(angle: float, measure: bool) -> rc.Sample:
        return rc.Sample(angle, 0.0, clearance_mm=1.0)

    found = rc.sweep(evaluate, [0, 30], 5, 0.1, 10.0)

    assert len(found["samples"]) == 3
    assert found["samples_total"] == 7


def test_an_angle_is_evaluated_once_and_the_last_step_stops_at_the_end() -> None:
    calls: list[tuple[float, bool]] = []

    def evaluate(angle: float, measure: bool) -> rc.Sample:
        calls.append((angle, measure))
        return rc.Sample(angle, 0.0, clearance_mm=1.0)

    sweep = rc.Sweep(evaluate, 0, 12, 5, 0.1)
    first = sweep.at(3.0, measure=False)
    again = sweep.at(3.0, measure=False)
    measured = sweep.at(3.0)
    sweep.walk(1, 12.0)

    assert again is first and measured is not first
    assert calls == [(3.0, False), (3.0, True), (5.0, True), (10.0, True), (12.0, True)]
    assert rc._union([None]) is None


def test_each_evaluation_is_checked_first() -> None:
    seen: list[str] = []

    def evaluate(angle: float, measure: bool) -> rc.Sample:
        seen.append("evaluate")
        return rc.Sample(angle, 0.0, clearance_mm=1.0)

    rc.sweep(evaluate, [0, 10], 5, 0.1, 1.0, check=lambda: seen.append("check"))

    assert seen == ["check", "evaluate"] * 3


@pytest.mark.parametrize(
    ("span", "step", "tolerance", "fragment"),
    [
        ([10, 90], 5, 0.1, "at most 0 to at least 0"),
        ([0, 0], 5, 0.1, "at most 0 to at least 0"),
        ([-400, 400], 5, 0.1, "720"),
        ([0, 90], 5, 6, "tolerance_deg"),
        ([0, 90], 5, 0, "tolerance_deg"),
    ],
)
def test_a_range_that_cannot_be_swept(
    span: list[float], step: float, tolerance: float, fragment: str
) -> None:
    assert fragment in (rc.range_problem(span, step, tolerance) or "")
    with pytest.raises(InvalidRequest, match=fragment):
        rc.check_range(span, step, tolerance)


def test_a_good_range() -> None:
    assert rc.range_problem([-5, 5], 1, 1) is None
    rc.check_range([0, 180], 5, 0.1)


def test_the_reach_is_the_farthest_corner_from_the_axis() -> None:
    box = [0.04, -0.01, -0.01, 0.05, 0.01, 0.01]

    assert rc.axis_reach_mm(box, [0, 0, 1], [0, 0, 0]) == pytest.approx(
        math.hypot(50, 10)
    )
    assert rc.axis_reach_mm(None, [0, 0, 1], [0, 0, 0]) == 0.0


@pytest.mark.parametrize(
    ("raw", "placed", "expected"),
    [
        ([0.0, 0, 0, 0.01, 0.01, 0.01], [0.1, 0, 0, 0.11, 0.01, 0.01], "part"),
        ([0.1, 0, 0, 0.11, 0.01, 0.01], [0.2, 0, 0, 0.21, 0.01, 0.01], "assembly"),
        (None, None, "part"),
    ],
)
def test_the_frame_the_bodies_come_in(
    raw: list[float] | None, placed: list[float] | None, expected: str
) -> None:
    faces = [0.0998, -0.0002, -0.0002, 0.1102, 0.0102, 0.0102]

    assert rc.body_frame(raw, placed, faces) == expected


def test_bodies_that_lie_in_no_frame_are_refused() -> None:
    faces = [0.1, 0, 0, 0.11, 0.01, 0.01]

    with pytest.raises(RuntimeError, match="neither where its faces are"):
        rc.body_frame(
            [0.5, 0, 0, 0.51, 0.01, 0.01], [0.7, 0, 0, 0.71, 0.01, 0.01], faces
        )
    assert rc.body_frame([0, 0, 0, 1, 1, 1], None, None) == "part"


def test_boxes_swing_into_a_wall() -> None:
    # A 10 mm block 40..50 mm out on X turns about Z toward a wall at y 20.
    found = rc.box_sweep(
        [40.0, -5.0, -5.0, 50.0, 5.0, 5.0],
        [-100.0, 20.0, -10.0, 100.0, 30.0, 10.0],
        [0, 0, 1],
        [0, 0, 0],
        [0, 90],
        5,
        0.1,
    )

    # The box around the turned block first reaches y 20 near 15.9 degrees.
    hit = found["positive"]["first_contact_deg"]
    assert 14.0 < hit < 18.0
    assert found["axis"] == [0.0, 0.0, 1.0]
    assert found["start_clearance_mm"] == pytest.approx(15.0)
    assert found["min_points_mm"]["moving"] is not None


def test_a_placed_box_turns_from_its_placement() -> None:
    start = [1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0, 40.0, -5.0, -5.0, 1.0, 0, 0, 0]

    found = rc.box_sweep(
        [0.0, 0.0, 0.0, 10.0, 10.0, 10.0],
        [-100.0, 20.0, -10.0, 100.0, 30.0, 10.0],
        [0, 0, 1],
        [0, 0, 0],
        [0, 90],
        5,
        0.1,
        start,
    )

    assert found["start_clearance_mm"] == pytest.approx(15.0)
