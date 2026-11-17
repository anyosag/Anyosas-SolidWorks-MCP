"""Regions thinner than a size, from rays cast inward on synthetic meshes."""

from __future__ import annotations

import pytest

from solidworks_mcp.utils import printability as pr

from .test_printability import _block, _cells_mesh


def _plate(thick: float = 0.5) -> list[list[float]]:
    return _cells_mesh([0, 40], [0, thick], [0, 30], [(0, 0, 0)])


def test_a_thin_plate_is_one_thin_region() -> None:
    found = pr.thin_features(_plate(), 0.8)

    assert found["ok"] is False
    assert found["thinnest_mm"] == pytest.approx(0.5, abs=1e-3)
    assert found["regions"]["count"] == 1
    region = found["regions"]["items"][0]
    assert region["min_thickness_mm"] == pytest.approx(0.5, abs=1e-3)
    assert region["normal"] in ([0.0, 1.0, 0.0], [0.0, -1.0, 0.0])
    assert region["box_mm"][1] >= 0.0 and region["box_mm"][4] <= 0.5
    assert found["thin_samples"] == region["samples"] <= found["samples"]
    assert found["min_feature_mm"] == 0.8


def test_a_thick_block_has_none() -> None:
    found = pr.thin_features(_block(), 0.8)

    assert found["ok"] is True
    assert found["thinnest_mm"] is None
    assert found["regions"] == {"count": 0, "items": []}
    assert found["samples"] == 12


def test_a_thin_fin_on_a_thick_block_is_found_where_it_is() -> None:
    # A 20 x 20 x 10 block and a 0.6 mm fin standing on it, x 9.7..10.3.
    filled = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (1, 0, 1)]
    mesh = _cells_mesh([0, 9.7, 10.3, 20], [0, 20], [0, 10, 25], filled)

    found = pr.thin_features(mesh, 1.0)

    assert found["regions"]["count"] == 1
    region = found["regions"]["items"][0]
    assert region["min_thickness_mm"] == pytest.approx(0.6, abs=1e-3)
    assert 9.6 <= region["center_mm"][0] <= 10.4
    assert region["center_mm"][2] > 10.0


def test_two_fins_far_apart_are_two_regions() -> None:
    filled = [
        (0, 0, 0),
        (1, 0, 0),
        (2, 0, 0),
        (3, 0, 0),
        (4, 0, 0),
        (1, 0, 1),
        (3, 0, 1),
    ]
    xs = [0, 9.7, 10.3, 49.7, 50.3, 60]
    mesh = _cells_mesh(xs, [0, 20], [0, 10, 25], filled)

    found = pr.thin_features(mesh, 1.0)

    assert found["regions"]["count"] == 2
    centres = sorted(r["center_mm"][0] for r in found["regions"]["items"])
    assert centres[0] == pytest.approx(10.0, abs=0.5)
    assert centres[1] == pytest.approx(50.0, abs=0.5)


def test_a_big_mesh_is_sampled_the_same_way_each_time() -> None:
    xs = [float(x) for x in range(0, 41)]
    mesh = _cells_mesh(xs, [0, 0.5], [0, 30], [(i, 0, 0) for i in range(40)])

    first = pr.thin_features(mesh, 0.8, max_rays=50)
    second = pr.thin_features(mesh, 0.8, max_rays=50)

    assert first == second
    assert first["samples"] == 50
    assert first["thinnest_mm"] == pytest.approx(0.5, abs=1e-3)


def test_triangles_much_bigger_than_the_rest_are_still_hit() -> None:
    # Fine 1 mm cells, then one 90 mm cell: its triangles span thousands of
    # grid cells, so every ray checks them.
    xs = [float(x) for x in range(11)] + [100.0]
    filled = [(i, 0, 0) for i in range(11)]
    mesh = _cells_mesh(xs, [0, 0.5], [0, 1], filled)

    found = pr.thin_features(mesh, 0.8)

    # Every top and bottom triangle (11 cells x 2 faces x 2), the big cell's
    # four among them; the sides and ends are thick along their rays.
    assert found["thin_samples"] == 44
    assert found["thinnest_mm"] == pytest.approx(0.5, abs=1e-3)


@pytest.mark.parametrize(("size", "rays"), [(0.0, 10), (1.0, 0)])
def test_a_size_or_count_that_is_not_positive(size: float, rays: int) -> None:
    with pytest.raises(ValueError, match="above 0"):
        pr.thin_features(_plate(), size, rays)


def test_an_empty_mesh_is_refused() -> None:
    with pytest.raises(ValueError, match="no triangles"):
        pr.thin_features([], 1.0)


def test_rays_count_only_a_face_they_leave_the_solid_by() -> None:
    topology = pr._Topology(pr._corners(_plate()))
    import numpy as np

    up = np.array([0.0, 1.0, 0.0])
    start = np.array([20.0, 0.25, 15.0])
    hits = pr._ray_distances(topology, np.arange(len(topology.faces)), start, up)
    down = pr._ray_distances(topology, np.arange(len(topology.faces)), start, -up)

    assert float(hits.min()) == pytest.approx(0.25)
    assert float(down.min()) == pytest.approx(0.25)
    outside = pr._ray_distances(
        topology, np.arange(len(topology.faces)), np.array([20.0, 5.0, 15.0]), -up
    )
    # From outside, the top face is entered, not left: it doesn't count.
    assert float(outside.min()) == pytest.approx(5.0)
