"""``check_part`` with ``orientations`` and ``min_feature_mm`` (``print_check``)."""

from __future__ import annotations

import struct
from pathlib import Path

from solidworks_mcp.adapters.solidworks import print_check
from tests.solidworks_mcp.utils.test_printability import _cells_mesh

from .test_print_check import _Adapter


def _plate_stl(path: str) -> None:
    """A 40 x 0.5 x 30 plate: thin everywhere."""
    triangles = _cells_mesh([0, 40], [0, 0.5], [0, 30], [(0, 0, 0)])
    data = b"plate".ljust(80, b" ") + struct.pack("<I", len(triangles))
    for t in triangles:
        data += struct.pack("<12fH", 0.0, 0.0, 0.0, *t, 0)
    Path(path).write_bytes(data)


async def test_the_best_orientation_is_checked_without_a_direction() -> None:
    result = await print_check.check_part(_Adapter(), None, orientations=6)

    assert result.is_success, result.error
    data = result.data
    assert data["orientation"] == "best"
    assert data["orientations"]["tried"] == 6
    assert "check" not in data["orientations"]
    assert data["build_direction"] == data["orientations"]["best"]["build_direction"]
    assert data["mesh"]["restored"] is True


async def test_a_given_direction_is_checked_and_the_best_reported() -> None:
    result = await print_check.check_part(_Adapter(), "x", orientations=8)

    assert result.data["orientation"] == "given"
    assert result.data["build_direction"] == [1.0, 0.0, 0.0]
    assert result.data["orientations"]["tried"] == 8


async def test_thin_features_are_added() -> None:
    result = await print_check.check_part(
        _Adapter(write=_plate_stl), "y", min_feature_mm=0.8
    )

    thin = result.data["thin_features"]
    assert thin["regions"]["count"] == 1
    assert thin["thinnest_mm"] == 0.5
    assert "orientations" not in result.data


async def test_neither_a_direction_nor_orientations_is_refused() -> None:
    adapter = _Adapter()

    result = await print_check.check_part(adapter, None)

    assert result.error == "Give build_direction, or orientations to find the best one."
    assert adapter.exports == []


async def test_too_few_orientations_is_refused_before_the_export() -> None:
    adapter = _Adapter()

    result = await print_check.check_part(adapter, "z", orientations=3)

    assert "at least 6" in (result.error or "")
    assert adapter.exports == []
