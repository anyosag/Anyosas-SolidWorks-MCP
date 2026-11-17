"""Check a part's mesh for printing in one orientation (``check_printability``).

Resin and FDM printers build a part layer by layer away from the build plate.
A surface facing the plate at a shallow angle needs supports; a region that
starts in mid-air (an island) needs them most; a flat overhang held at both
ends (a bridge) prints only over a short span; and a cavity that opens toward
the plate traps resin and pulls on each layer (a suction cup). This module
finds those on the part's STL mesh, with numpy and no COM. See the change
``cellvis-assembly-and-print-checks``, design D4.

Let ``d`` be the build direction (unit), a vertex's height ``h = v . d`` and
``h0`` the lowest height: the plate.

* **Plate faces**, every vertex within :data:`PLATE_MM` of ``h0``, are left
  out.
* **Down-facing**: ``n . d < 0``. Its angle from horizontal is
  ``acos(-n . d)``: 0 for a ceiling, 90 for a wall.
* **Regions**: down-facing triangles under the support angle, joined by
  shared edges. A boundary edge is *supported* when the triangle across it
  goes down (its third vertex is lower than the edge): a wall under it.
* **Islands**: a region with no supported edge, or a vertex lower than all its
  neighbours, above the plate.
* **Bridges**: a flat region (under :data:`FLAT_DEG`) supported along two or
  more separate runs of edges. Its span is twice the largest distance from a
  triangle's centre to the nearest support, across the build direction.
* **Suction cups**: a region (a ceiling) from under whose centre rays across
  the build direction hit the part all four ways, and a ray toward the plate
  hits nothing: the cavity opens onto the plate.

These are geometric checks on a mesh, not a slicer.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from typing import Any

import numpy as np

#: Faces within this distance of the lowest height lie on the plate, mm.
PLATE_MM = 0.01

#: A region within this many degrees of horizontal is flat (a bridge or not).
FLAT_DEG = 10.0

#: The angle bands, degrees from horizontal, that down-facing area is split in.
BANDS = ((0.0, 15.0), (15.0, 30.0), (30.0, 45.0), (45.0, 60.0), (60.0, 90.0))

#: Vertices this close (mm) are one vertex.
_WELD_MM = 1e-3

#: Heights closer than this (mm) are level.
_LEVEL_MM = 1e-4

#: The most entries each list gives; the count is always exact.
MAX_LISTED = 50

_AXES = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}


def direction_vector(value: str | Sequence[float]) -> np.ndarray:
    """The unit build direction for ``"x"``, ``"-z"`` and the like, or a vector.

    Raises:
        ValueError: An unknown name, a vector that isn't three numbers, or the
            zero vector.
    """
    if isinstance(value, str):
        name = value.strip().lower()
        sign = -1.0 if name.startswith("-") else 1.0
        axis = _AXES.get(name.lstrip("+-"))
        if axis is None:
            raise ValueError(
                f"build_direction must be x, y, z, -x, -y, -z or [x, y, z], not {value!r}"
            )
        return np.array(axis) * sign
    vector = np.asarray(list(value), dtype=float)
    if vector.shape != (3,):
        raise ValueError("build_direction as a vector must be [x, y, z]")
    length = float(np.linalg.norm(vector))
    if length < 1e-12:
        raise ValueError("build_direction must not be the zero vector")
    return vector / length


def _weld(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shared vertices and faces from per-triangle corners; drops slivers."""
    corners = triangles.reshape(-1, 3)
    keys = np.round(corners / _WELD_MM).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    vertices = unique.astype(float) * _WELD_MM
    faces = inverse.reshape(-1, 3)
    keep = (
        (faces[:, 0] != faces[:, 1])
        & (faces[:, 1] != faces[:, 2])
        & (faces[:, 0] != faces[:, 2])
    )
    return vertices, faces[keep]


def _edge_faces(faces: np.ndarray) -> dict[tuple[int, int], list[int]]:
    """Each undirected edge and the faces that use it."""
    table: dict[tuple[int, int], list[int]] = {}
    for index, (a, b, c) in enumerate(faces.tolist()):
        for p, q in ((a, b), (b, c), (c, a)):
            key = (p, q) if p < q else (q, p)
            table.setdefault(key, []).append(index)
    return table


def _components(members: np.ndarray, links: list[tuple[int, int]]) -> list[list[int]]:
    """Connected groups of ``members`` (indices) joined by ``links``."""
    parent = {int(m): int(m) for m in members}

    def root(item: int) -> int:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for a, b in links:
        if a in parent and b in parent:
            parent[root(a)] = root(b)
    groups: dict[int, list[int]] = {}
    for item in parent:
        groups.setdefault(root(item), []).append(item)
    return list(groups.values())


class _Topology:
    """A welded mesh: what doesn't depend on the build direction."""

    def __init__(self, triangles: np.ndarray) -> None:
        self.vertices, self.faces = _weld(triangles)
        a, b, c = (self.vertices[self.faces[:, i]] for i in range(3))
        cross = np.cross(b - a, c - a)
        doubled = np.linalg.norm(cross, axis=1)
        self.area = doubled / 2.0
        self.normal = cross / np.where(doubled > 0, doubled, 1.0)[:, None]
        self.centre = (a + b + c) / 3.0
        self.corners = (a, b, c)
        self.edges = _edge_faces(self.faces)


class _Mesh:
    """A welded mesh seen from one build direction."""

    def __init__(
        self, triangles: np.ndarray | _Topology, direction: np.ndarray
    ) -> None:
        topology = (
            triangles if isinstance(triangles, _Topology) else _Topology(triangles)
        )
        self.vertices, self.faces = topology.vertices, topology.faces
        self.area, self.normal = topology.area, topology.normal
        self.centre, self.corners = topology.centre, topology.corners
        self.edges = topology.edges
        self.d = direction
        self.h = self.vertices @ direction
        self.h0 = float(self.h.min()) if len(self.h) else 0.0
        facing = self.normal @ direction
        on_plate = (self.h[self.faces] <= self.h0 + PLATE_MM).all(axis=1)
        self.down = (facing < -1e-6) & ~on_plate
        self.angle = np.degrees(np.arccos(np.clip(-facing, -1.0, 1.0)))
        # Two directions across the build direction, for the side rays.
        helper = np.array([1.0, 0.0, 0.0])
        if abs(float(direction @ helper)) > 0.9:
            helper = np.array([0.0, 1.0, 0.0])
        self.e1 = np.cross(direction, helper)
        self.e1 /= np.linalg.norm(self.e1)
        self.e2 = np.cross(direction, self.e1)

    def regions(self, mask: np.ndarray) -> list[list[int]]:
        """The faces under ``mask``, grouped by shared edges."""
        members = np.flatnonzero(mask)
        chosen = set(members.tolist())
        links = [
            (users[0], users[1])
            for users in self.edges.values()
            if len(users) == 2 and users[0] in chosen and users[1] in chosen
        ]
        return _components(members, links)

    def supported_runs(self, region: list[int]) -> list[list[tuple[int, int]]]:
        """The region's supported boundary edges, chained into runs."""
        inside = set(region)
        supported: list[tuple[int, int]] = []
        for face in region:
            a, b, c = self.faces[face].tolist()
            for p, q in ((a, b), (b, c), (c, a)):
                key = (p, q) if p < q else (q, p)
                outside = [f for f in self.edges.get(key, []) if f not in inside]
                if outside and self._goes_down(outside[0], key):
                    supported.append(key)
        by_vertex: dict[int, list[int]] = {}
        for index, (p, q) in enumerate(supported):
            by_vertex.setdefault(p, []).append(index)
            by_vertex.setdefault(q, []).append(index)
        links = [
            (users[0], other) for users in by_vertex.values() for other in users[1:]
        ]
        groups = _components(np.arange(len(supported)), links)
        return [[supported[i] for i in group] for group in groups]

    def touches_plate(self, region: list[int]) -> bool:
        """Whether a region reaches down to the plate, which holds it."""
        return bool((self.h[self.faces[region]] <= self.h0 + PLATE_MM).any())

    def _goes_down(self, face: int, edge: tuple[int, int]) -> bool:
        """Whether ``face`` reaches below ``edge``: a wall under the edge."""
        third = [v for v in self.faces[face].tolist() if v not in edge]
        floor = min(self.h[edge[0]], self.h[edge[1]])
        return bool(third) and float(self.h[third[0]]) < floor - _LEVEL_MM

    def local_minima(self) -> list[int]:
        """Vertices above the plate lower than all their neighbours."""
        lowest: dict[int, float] = {}
        for p, q in self.edges:
            lowest[p] = min(lowest.get(p, math.inf), float(self.h[q]))
            lowest[q] = min(lowest.get(q, math.inf), float(self.h[p]))
        return [
            v
            for v, neighbour in lowest.items()
            if self.h[v] > self.h0 + PLATE_MM and self.h[v] < neighbour - _LEVEL_MM
        ]

    def hits(self, origin: np.ndarray, direction: np.ndarray) -> bool:
        """Whether a ray from ``origin`` hits the mesh (Moller-Trumbore)."""
        a, b, c = self.corners
        edge1, edge2 = b - a, c - a
        p = np.cross(direction, edge2)
        det = np.einsum("ij,ij->i", edge1, p)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        s = origin - a
        u = np.einsum("ij,ij->i", s, p) * inv
        q = np.cross(s, edge1)
        v = (q @ direction) * inv
        t = np.einsum("ij,ij->i", edge2, q) * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6)
        return bool(hit.any())


def _rounded(values: Any) -> list[float]:
    return [round(float(v), 3) + 0.0 for v in values]


def _span(mesh: _Mesh, region: list[int], runs: list[list[tuple[int, int]]]) -> float:
    """Twice the furthest a point of the region is from its nearest support.

    The points are each triangle's centre and edge midpoints: a long thin
    triangle's centre alone sits a third of the way along it.
    """
    across = np.eye(3) - np.outer(mesh.d, mesh.d)
    starts = np.array([mesh.vertices[p] for run in runs for p, _q in run]) @ across
    ends = np.array([mesh.vertices[q] for run in runs for _p, q in run]) @ across
    a, b, c = (corner[region] for corner in mesh.corners)
    centres = np.vstack([mesh.centre[region], (a + b) / 2, (b + c) / 2, (c + a) / 2])
    centres = centres @ across
    seg = ends - starts
    length2 = np.einsum("ij,ij->i", seg, seg)
    rel = centres[:, None, :] - starts[None, :, :]
    t = np.clip(
        np.einsum("kij,ij->ki", rel, seg) / np.where(length2 > 0, length2, 1.0),
        0.0,
        1.0,
    )
    nearest = starts[None, :, :] + t[:, :, None] * seg[None, :, :]
    distance = np.linalg.norm(centres[:, None, :] - nearest, axis=2).min(axis=1)
    return 2.0 * float(distance.max())


def _listed(entries: list[dict[str, Any]], key: str) -> dict[str, Any]:
    entries.sort(key=lambda e: -float(e[key]))
    return {"count": len(entries), "items": entries[:MAX_LISTED]}


def analyse(
    triangles: Sequence[Sequence[float]] | np.ndarray,
    build_direction: str | Sequence[float],
    support_angle_deg: float = 45.0,
    min_bridge_mm: float = 5.0,
) -> dict[str, Any]:
    """Check a mesh for printing with ``build_direction`` away from the plate.

    Args:
        triangles: Each triangle's three vertices, in mm (nine numbers).
        build_direction: The direction the part grows from the plate.
        support_angle_deg: Down-facing surfaces under this angle from
            horizontal need supports.
        min_bridge_mm: Only bridges longer than this are listed.

    Returns:
        dict[str, Any]: ``down_facing`` (area per band, ``needs_support_mm2``),
        ``islands``, ``bridges`` and ``suction_cups`` (each ``count`` and up
        to :data:`MAX_LISTED` ``items``), ``ok`` and ``triangles``.

    Raises:
        ValueError: An empty mesh or a bad build direction.
    """
    direction = direction_vector(build_direction)
    return _analyse_mesh(
        _Mesh(_corners(triangles), direction), support_angle_deg, min_bridge_mm
    )


def _corners(triangles: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
    """The triangles as an ``(n, 3, 3)`` array.

    Raises:
        ValueError: There are none.
    """
    corners = np.asarray(triangles, dtype=float).reshape(-1, 3, 3)
    if len(corners) == 0:
        raise ValueError("The mesh has no triangles.")
    return corners


def _analyse_mesh(
    mesh: _Mesh, support_angle_deg: float, min_bridge_mm: float
) -> dict[str, Any]:
    """:func:`analyse` on a mesh already seen from its build direction."""
    direction = mesh.d
    bands = [
        {
            "from_deg": low,
            "to_deg": high,
            "area_mm2": round(
                float(
                    mesh.area[
                        mesh.down
                        & (mesh.angle >= low)
                        & ((mesh.angle < high) if high < 90 else True)
                    ].sum()
                ),
                3,
            ),
        }
        for low, high in BANDS
    ]
    needs = mesh.down & (mesh.angle < support_angle_deg)

    islands: list[dict[str, Any]] = []
    cups: list[dict[str, Any]] = []
    in_islands: set[int] = set()
    for region in mesh.regions(needs):
        if not mesh.touches_plate(region) and not mesh.supported_runs(region):
            region_vertices = np.unique(mesh.faces[region])
            lowest = region_vertices[np.argmin(mesh.h[region_vertices])]
            in_islands.update(region_vertices.tolist())
            islands.append(
                {
                    "lowest_point_mm": _rounded(mesh.vertices[lowest]),
                    "area_mm2": round(float(mesh.area[region].sum()), 3),
                }
            )
        cup = _cup(mesh, region)
        if cup is not None:
            cups.append(cup)
    for vertex in mesh.local_minima():
        if vertex not in in_islands:
            islands.append(
                {"lowest_point_mm": _rounded(mesh.vertices[vertex]), "area_mm2": 0.0}
            )

    bridges: list[dict[str, Any]] = []
    for region in mesh.regions(mesh.down & (mesh.angle < FLAT_DEG)):
        runs = mesh.supported_runs(region)
        if len(runs) < 2:
            continue
        span = _span(mesh, region, runs)
        if span > min_bridge_mm:
            weights = mesh.area[region]
            centre = (mesh.centre[region] * weights[:, None]).sum(
                axis=0
            ) / weights.sum()
            bridges.append(
                {
                    "span_mm": round(span, 3),
                    "center_mm": _rounded(centre),
                    "area_mm2": round(float(weights.sum()), 3),
                }
            )

    needs_support = round(float(mesh.area[needs].sum()), 3)
    return {
        "build_direction": _rounded(direction),
        "support_angle_deg": support_angle_deg,
        "triangles": int(len(mesh.faces)),
        "down_facing": {"bands": bands, "needs_support_mm2": needs_support},
        "islands": _listed(islands, "area_mm2"),
        "bridges": _listed(bridges, "span_mm"),
        "suction_cups": _listed(cups, "opening_area_mm2"),
        "ok": not islands and not cups and not bridges and needs_support == 0.0,
    }


def _cup(mesh: _Mesh, region: list[int]) -> dict[str, Any] | None:
    """A ceiling region as a suction cup, or None when it vents or is closed."""
    weights = mesh.area[region]
    centre = (mesh.centre[region] * weights[:, None]).sum(axis=0) / weights.sum()
    start = centre - PLATE_MM * mesh.d
    sideways = (mesh.e1, -mesh.e1, mesh.e2, -mesh.e2)
    if not all(mesh.hits(start, ray) for ray in sideways):
        return None
    if mesh.hits(start, -mesh.d):
        return None  # closed toward the plate: an enclosed void, not a cup
    facing = np.abs(mesh.normal[region] @ mesh.d)
    return {
        "opening_center_mm": _rounded(centre),
        "opening_area_mm2": round(float((weights * facing).sum()), 3),
        "depth_mm": round(float(centre @ mesh.d) - mesh.h0, 3),
    }


# ---- orientations ------------------------------------------------------------------

#: What ranking orientations charges for each island and each suction cup, in
#: mm2 of support: an island needs a support tip, a cup on a resin printer can
#: fail the print (its opening area is added too).
ISLAND_COST_MM2 = 25.0
CUP_COST_MM2 = 500.0

#: Candidate directions closer than this many degrees are one.
_SAME_DIRECTION_DEG = 2.0

#: The most flat-face directions (a face put on the plate) tried.
_FLAT_CANDIDATES = 12

#: How many orientations ``ranked`` lists, the best first.
RANKED_LISTED = 5


def support_score(check: dict[str, Any]) -> float:
    """How much an orientation needs supports: the ranking's cost, in mm2.

    The area needing supports, plus :data:`ISLAND_COST_MM2` per island, plus
    :data:`CUP_COST_MM2` and the opening area per suction cup.
    """
    cups = check["suction_cups"]["items"]
    return (
        float(check["down_facing"]["needs_support_mm2"])
        + ISLAND_COST_MM2 * check["islands"]["count"]
        + CUP_COST_MM2 * check["suction_cups"]["count"]
        + sum(float(c["opening_area_mm2"]) for c in cups)
    )


def sphere_directions(count: int) -> np.ndarray:
    """``count`` unit vectors spread evenly over the sphere (a Fibonacci lattice)."""
    i = np.arange(count) + 0.5
    polar = np.arccos(1.0 - 2.0 * i / count)
    turn = math.pi * (1.0 + math.sqrt(5.0)) * i
    return np.stack(
        [np.cos(turn) * np.sin(polar), np.sin(turn) * np.sin(polar), np.cos(polar)],
        axis=1,
    )


def _flat_directions(topology: _Topology) -> list[np.ndarray]:
    """Build directions that put the biggest flat faces on the plate (``-normal``)."""
    keys = np.round(topology.normal * 200.0).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    areas = np.bincount(inverse, weights=topology.area, minlength=len(unique))
    out = []
    for group in np.argsort(-areas)[:_FLAT_CANDIDATES]:
        members = inverse == group
        normal = (topology.normal[members] * topology.area[members, None]).sum(axis=0)
        length = float(np.linalg.norm(normal))
        if length > 1e-12:
            out.append(-normal / length)
    return out


def candidate_directions(topology: _Topology, count: int) -> list[np.ndarray]:
    """The build directions tried, ``count`` in all, no two within 2 degrees.

    The six axes first, then the directions that put the biggest flat faces on
    the plate, then directions spread evenly over the sphere.
    """
    axes = [np.array(v) * s for v in _AXES.values() for s in (1.0, -1.0)]
    picked: list[np.ndarray] = []
    limit = math.cos(math.radians(_SAME_DIRECTION_DEG))
    pool = [*axes, *_flat_directions(topology), *sphere_directions(max(count, 6) * 2)]
    for direction in pool:
        if len(picked) >= count:
            break
        if all(float(direction @ p) < limit for p in picked):
            picked.append(np.asarray(direction, dtype=float))
    return picked


def euler_xyz_deg(rows: np.ndarray) -> list[float]:
    """``[rx, ry, rz]``: turns about X, then Y, then Z giving ``rows`` (``p . R``).

    The inverse of ``placement.rotation_rows_from_deg``: ``R[0][2] = -sin ry``,
    ``R[1][2] / R[2][2] = tan rx``, ``R[0][1] / R[0][0] = tan rz``.
    """
    ry = math.asin(max(-1.0, min(1.0, -float(rows[0][2]))))
    if abs(math.cos(ry)) > 1e-9:
        rx = math.atan2(float(rows[1][2]), float(rows[2][2]))
        rz = math.atan2(float(rows[0][1]), float(rows[0][0]))
    else:  # gimbal lock: the turn about Z is folded into the one about X
        rx, rz = math.atan2(-float(rows[2][1]), float(rows[1][1])), 0.0
    return [round(math.degrees(a), 6) + 0.0 for a in (rx, ry, rz)]


def rotation_onto(
    direction: np.ndarray, target: Sequence[float] = (0.0, 0.0, 1.0)
) -> dict[str, Any]:
    """The smallest turn that points ``direction`` along ``target``.

    Returns:
        dict[str, Any]: ``axis`` and ``angle_deg`` (right-hand rule),
        ``rotation_deg`` (``[rx, ry, rz]`` about X, then Y, then Z, as
        ``set_component_transform`` takes) and ``matrix`` (rows, ``p . R``).
    """
    d = np.asarray(direction, dtype=float) / float(np.linalg.norm(direction))
    t = np.asarray(target, dtype=float) / float(np.linalg.norm(target))
    cross = np.cross(d, t)
    sin, cos = float(np.linalg.norm(cross)), float(d @ t)
    if sin < 1e-12 and cos > 0:
        axis, angle = np.array([0.0, 0.0, 1.0]), 0.0
    elif sin < 1e-12:  # opposite: half a turn about an axis across it
        helper = np.array([1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
        axis = np.cross(d, helper)
        axis, angle = axis / np.linalg.norm(axis), math.pi
    else:
        axis, angle = cross / sin, math.atan2(sin, cos)
    skew = np.array(
        [[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]]
    )
    rows = (
        math.cos(angle) * np.eye(3)
        + (1 - math.cos(angle)) * np.outer(axis, axis)
        - math.sin(angle) * skew
    )
    return {
        "axis": _rounded(axis),
        "angle_deg": round(math.degrees(angle), 4) + 0.0,
        "rotation_deg": euler_xyz_deg(rows),
        "matrix": [[round(float(v), 9) + 0.0 for v in row] for row in rows],
    }


def _summary(
    direction: np.ndarray, check: dict[str, Any], height: float
) -> dict[str, Any]:
    return {
        "build_direction": _rounded(direction),
        "score_mm2": round(support_score(check), 3),
        "needs_support_mm2": check["down_facing"]["needs_support_mm2"],
        "islands": check["islands"]["count"],
        "bridges": check["bridges"]["count"],
        "suction_cups": check["suction_cups"]["count"],
        "height_mm": round(height, 3),
    }


def best_orientation(
    triangles: Sequence[Sequence[float]] | np.ndarray,
    count: int = 26,
    support_angle_deg: float = 45.0,
    min_bridge_mm: float = 5.0,
) -> dict[str, Any]:
    """Try ``count`` build directions and rank them by the supports they need.

    The mesh is checked from each direction (:func:`analyse`'s checks, on one
    welded mesh); the lowest :func:`support_score` wins, the lower part on a
    tie.

    Returns:
        dict[str, Any]: ``tried``; ``best`` (``build_direction``,
        ``score_mm2``, the counts, ``height_mm`` and ``rotate_to_z``, the turn
        that makes the build direction +Z, the build direction of most
        slicers); ``ranked`` (the best :data:`RANKED_LISTED`); ``score`` (how
        the cost is made); and ``check``, the best orientation's full check.

    Raises:
        ValueError: An empty mesh or fewer than 6 directions.
    """
    if count < 6:
        raise ValueError("orientations must be at least 6 (the six axis directions).")
    topology = _Topology(_corners(triangles))
    tried = []
    for direction in candidate_directions(topology, count):
        mesh = _Mesh(topology, direction)
        check = _analyse_mesh(mesh, support_angle_deg, min_bridge_mm)
        height = float(mesh.h.max() - mesh.h0)
        tried.append((support_score(check), height, direction, check))
    tried.sort(key=lambda entry: (round(entry[0], 6), round(entry[1], 6)))
    _score, height, direction, check = tried[0]
    best = _summary(direction, check, height)
    best["rotate_to_z"] = rotation_onto(direction)
    return {
        "tried": len(tried),
        "best": best,
        "ranked": [_summary(d, c, h) for _s, h, d, c in tried[:RANKED_LISTED]],
        "score": (
            f"needs_support_mm2 + {ISLAND_COST_MM2:g} per island + "
            f"{CUP_COST_MM2:g} and the opening area per suction cup; the lower "
            "part wins a tie"
        ),
        "check": check,
    }


# ---- thin features -----------------------------------------------------------------

#: Rays start this far (mm) inside the surface; nearer hits are the start face.
_RAY_START_MM = 1e-4

#: A triangle spanning more grid cells than this is checked by every ray.
_MAX_CELLS = 64

#: The most rays cast for thin features by default.
DEFAULT_THIN_RAYS = 5000


class _Grid:
    """Triangles by the grid cells their boxes cover, to find those near a ray."""

    def __init__(self, topology: _Topology, cell: float) -> None:
        a, b, c = topology.corners
        low = np.minimum(np.minimum(a, b), c)
        high = np.maximum(np.maximum(a, b), c)
        self.origin = low.min(axis=0)
        self.cell = cell
        first = np.floor((low - self.origin) / cell).astype(np.int64)
        last = np.floor((high - self.origin) / cell).astype(np.int64)
        spans = (last - first + 1).prod(axis=1)
        self.big = np.flatnonzero(spans > _MAX_CELLS)
        cells: dict[tuple[int, int, int], list[int]] = {}
        for t in np.flatnonzero(spans <= _MAX_CELLS).tolist():
            lo, hi = first[t].tolist(), last[t].tolist()
            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        cells.setdefault((x, y, z), []).append(t)
        self.cells = {key: np.array(v, dtype=np.int64) for key, v in cells.items()}

    def near(self, p: np.ndarray, q: np.ndarray) -> np.ndarray:
        """The triangles whose cells the segment ``p``-``q``'s box touches."""
        lo = np.floor((np.minimum(p, q) - self.origin) / self.cell).astype(np.int64)
        hi = np.floor((np.maximum(p, q) - self.origin) / self.cell).astype(np.int64)
        found = [self.big]
        for x in range(int(lo[0]), int(hi[0]) + 1):
            for y in range(int(lo[1]), int(hi[1]) + 1):
                for z in range(int(lo[2]), int(hi[2]) + 1):
                    hit = self.cells.get((x, y, z))
                    if hit is not None:
                        found.append(hit)
        return np.unique(np.concatenate(found))


def _ray_distances(
    topology: _Topology, which: np.ndarray, origin: np.ndarray, direction: np.ndarray
) -> np.ndarray:
    """How far along the ray each triangle in ``which`` is, leaving the solid.

    Moller-Trumbore. Only a triangle the ray leaves the solid through counts
    (its outward normal points along the ray). Misses are infinite.
    """
    a, b, c = (corner[which] for corner in topology.corners)
    edge1, edge2 = b - a, c - a
    p = np.cross(direction, edge2)
    det = np.einsum("ij,ij->i", edge1, p)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    s = origin - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, edge1)
    v = (q @ direction) * inv
    t = np.einsum("ij,ij->i", edge2, q) * inv
    leaving = topology.normal[which] @ direction > 0
    hit = ok & leaving & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > _RAY_START_MM)
    return np.where(hit, t, np.inf)


def _clusters(points: np.ndarray, link: float) -> list[list[int]]:
    """Groups of ``points`` joined when closer than ``link``."""
    keys = np.floor(points / link).astype(np.int64)
    by_cell: dict[tuple[int, ...], list[int]] = {}
    for index, key in enumerate(keys.tolist()):
        by_cell.setdefault(tuple(key), []).append(index)
    links = []
    for (x, y, z), members in by_cell.items():
        for dx, dy, dz in itertools.product((-1, 0, 1), repeat=3):
            for j in by_cell.get((x + dx, y + dy, z + dz), []):
                links.extend(
                    (i, j)
                    for i in members
                    if i < j and float(np.linalg.norm(points[i] - points[j])) <= link
                )
    return _components(np.arange(len(points)), links)


def thin_features(
    triangles: Sequence[Sequence[float]] | np.ndarray,
    min_feature_mm: float,
    max_rays: int = DEFAULT_THIN_RAYS,
) -> dict[str, Any]:
    """Regions of the mesh thinner than ``min_feature_mm``.

    From the centre of each sampled triangle a ray goes inward, against its
    normal; the distance to where it leaves the solid is the local thickness.
    Up to ``max_rays`` triangles are sampled, by area (a fixed seed, so a run
    repeats). The thin samples are grouped into regions: those closer than 2
    mm, 3 x ``min_feature_mm`` or 1.5 thin triangles' size are one.

    Returns:
        dict[str, Any]: ``min_feature_mm``, ``samples`` (rays cast),
        ``thin_samples``, ``thinnest_mm`` (None when nothing is thin),
        ``regions`` (``count`` and up to :data:`MAX_LISTED` ``items``, the
        thinnest first, each with ``min_thickness_mm``, ``at_mm`` and
        ``normal`` where it is thinnest, ``center_mm``, ``box_mm``,
        ``samples`` and ``sampled_area_mm2``) and ``ok``.

    Raises:
        ValueError: An empty mesh, or a size or ray count that isn't positive.
    """
    if min_feature_mm <= 0 or max_rays <= 0:
        raise ValueError("min_feature_mm and max_rays must be above 0.")
    topology = _Topology(_corners(triangles))
    usable = np.flatnonzero(topology.area > 0)
    if len(usable) > max_rays:
        weights = topology.area[usable] / topology.area[usable].sum()
        rng = np.random.default_rng(0)
        usable = np.sort(rng.choice(usable, size=max_rays, replace=False, p=weights))
    a, b, c = topology.corners
    extent = np.maximum(np.maximum(a, b), c) - np.minimum(np.minimum(a, b), c)
    typical = float(np.median(extent.max(axis=1)))
    grid = _Grid(topology, max(float(min_feature_mm), typical))
    thickness = np.full(len(usable), np.inf)
    for j, face in enumerate(usable.tolist()):
        direction = -topology.normal[face]
        origin = topology.centre[face] + _RAY_START_MM * direction
        near = grid.near(origin, origin + direction * min_feature_mm)
        near = near[near != face]
        t = float(_ray_distances(topology, near, origin, direction).min(initial=np.inf))
        if t <= min_feature_mm:
            thickness[j] = t + _RAY_START_MM
    thin = np.flatnonzero(np.isfinite(thickness))
    regions = []
    if len(thin):
        points = topology.centre[usable[thin]]
        spacing = float(np.median(extent[usable[thin]].max(axis=1)))
        link = max(2.0, 3.0 * float(min_feature_mm), 1.5 * spacing)
        for group in _clusters(points, link):
            members = thin[group]
            faces = usable[members]
            thinnest = members[int(np.argmin(thickness[members]))]
            weights = topology.area[faces]
            centre = (topology.centre[faces] * weights[:, None]).sum(
                axis=0
            ) / weights.sum()
            spots = topology.centre[faces]
            regions.append(
                {
                    "min_thickness_mm": round(float(thickness[thinnest]), 4),
                    "at_mm": _rounded(topology.centre[usable[thinnest]]),
                    "normal": _rounded(topology.normal[usable[thinnest]]),
                    "center_mm": _rounded(centre),
                    "box_mm": _rounded([*spots.min(axis=0), *spots.max(axis=0)]),
                    "samples": len(members),
                    "sampled_area_mm2": round(float(weights.sum()), 3),
                }
            )
    regions.sort(key=lambda r: r["min_thickness_mm"])
    return {
        "min_feature_mm": float(min_feature_mm),
        "samples": len(usable),
        "thin_samples": len(thin),
        "thinnest_mm": regions[0]["min_thickness_mm"] if regions else None,
        "regions": {"count": len(regions), "items": regions[:MAX_LISTED]},
        "ok": not regions,
    }
