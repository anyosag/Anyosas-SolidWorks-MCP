"""Choosing the print orientation needing the least support, on synthetic meshes."""

from __future__ import annotations

import math

import numpy as np
import pytest

from solidworks_mcp.adapters.solidworks.placement import rotation_rows_from_deg
from solidworks_mcp.utils import printability as pr

from .test_printability import _block, _cells_mesh, _pocket_box, _shelf


def _topology(triangles: list[list[float]]) -> pr._Topology:
    return pr._Topology(pr._corners(triangles))


def test_a_block_lies_flat_on_its_biggest_face() -> None:
    found = pr.best_orientation(_block(), 26)

    best = found["best"]
    assert found["tried"] == 26
    assert best["score_mm2"] == 0.0
    # Every axis needs no support; the lowest part (10 mm) wins the tie.
    assert best["height_mm"] == pytest.approx(10.0)
    assert abs(best["build_direction"][2]) == pytest.approx(1.0)
    assert len(found["ranked"]) == pr.RANKED_LISTED
    assert found["ranked"][0] == {k: v for k, v in best.items() if k != "rotate_to_z"}
    assert "per island" in found["score"]
    assert found["check"]["ok"] is True


def test_a_shelf_is_turned_so_it_needs_no_support() -> None:
    upright = pr.analyse(_shelf(), "z")

    found = pr.best_orientation(_shelf(), 26)

    assert upright["down_facing"]["needs_support_mm2"] > 0
    assert found["best"]["needs_support_mm2"] == 0.0
    assert found["best"]["score_mm2"] < pr.support_score(upright)


def test_a_pocket_open_to_the_plate_is_turned_over() -> None:
    found = pr.best_orientation(_pocket_box(), 6)

    best = found["best"]
    assert best["suction_cups"] == 0
    assert best["build_direction"] == [0.0, 0.0, -1.0]
    assert best["rotate_to_z"]["angle_deg"] == pytest.approx(180.0)
    upright = pr.analyse(_pocket_box(), "z")
    assert upright["suction_cups"]["count"] == 1
    assert pr.support_score(upright) >= pr.CUP_COST_MM2


def test_fewer_than_six_orientations_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 6"):
        pr.best_orientation(_block(), 5)


def test_the_candidates_start_with_the_axes_and_the_flat_faces() -> None:
    # A wedge: its sloped face, 45 degrees, is a flat-face candidate.
    triangles = _block() + [[0, 0, 10, 40, 0, 10, 40, 10, 20]]
    picked = pr.candidate_directions(_topology(triangles), 12)

    assert len(picked) == 12
    assert [list(p) for p in picked[:6]] == [
        [1.0, 0.0, 0.0],
        [-1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.0, -1.0],
    ]
    for i, a in enumerate(picked):
        for b in picked[i + 1 :]:
            assert float(a @ b) < math.cos(math.radians(2.0))


def test_the_sphere_directions_are_unit_and_spread() -> None:
    directions = pr.sphere_directions(50)

    assert directions.shape == (50, 3)
    assert np.allclose(np.linalg.norm(directions, axis=1), 1.0)
    assert abs(float(directions.sum(axis=0)[2])) < 1e-9


@pytest.mark.parametrize(
    "angles", [(30.0, -20.0, 75.0), (0.0, 0.0, 0.0), (-170.0, 45.0, 120.0)]
)
def test_the_euler_angles_undo_the_rows(angles: tuple[float, float, float]) -> None:
    rows = np.array(rotation_rows_from_deg(*angles))

    assert pr.euler_xyz_deg(rows) == pytest.approx(list(angles), abs=1e-6)


def test_the_euler_angles_at_gimbal_lock() -> None:
    rows = np.array(rotation_rows_from_deg(20.0, 90.0, 0.0))

    rx, ry, rz = pr.euler_xyz_deg(rows)
    assert ry == pytest.approx(90.0) and rz == 0.0
    assert np.allclose(rotation_rows_from_deg(rx, ry, rz), rows, atol=1e-9)


@pytest.mark.parametrize(
    ("direction", "angle"),
    [([0, 0, 1], 0.0), ([0, 0, -1], 180.0), ([0, 1, 0], 90.0), ([1, 1, 0], 90.0)],
)
def test_the_turn_points_the_build_direction_up(
    direction: list[float], angle: float
) -> None:
    turn = pr.rotation_onto(np.array(direction, dtype=float))

    d = np.array(direction, dtype=float) / np.linalg.norm(direction)
    assert turn["angle_deg"] == pytest.approx(angle)
    assert d @ np.array(turn["matrix"]) == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)
    assert np.allclose(rotation_rows_from_deg(*turn["rotation_deg"]), turn["matrix"])


def test_a_downward_x_direction_turns_over_another_axis() -> None:
    turn = pr.rotation_onto(np.array([0.0, 0.0, -1.0]), target=[0.0, 0.0, 1.0])
    sideways = pr.rotation_onto(np.array([-1.0, 0.0, 0.0]), target=[1.0, 0.0, 0.0])

    assert turn["axis"] == [0.0, -1.0, 0.0]
    assert sideways["axis"] == [0.0, 0.0, -1.0]


def test_the_score_charges_islands_and_cups() -> None:
    check = {
        "down_facing": {"needs_support_mm2": 10.0},
        "islands": {"count": 2},
        "suction_cups": {"count": 1, "items": [{"opening_area_mm2": 40.0}]},
    }

    assert pr.support_score(check) == pytest.approx(10 + 2 * 25 + 500 + 40)


def test_a_mesh_is_welded_once_for_every_direction() -> None:
    topology = _topology(_cells_mesh([0, 10], [0, 10], [0, 10], [(0, 0, 0)]))

    up = pr._Mesh(topology, np.array([0.0, 0.0, 1.0]))
    side = pr._Mesh(topology, np.array([1.0, 0.0, 0.0]))

    assert up.vertices is side.vertices and up.edges is side.edges
    assert up.h0 == 0.0 and side.h0 == 0.0
