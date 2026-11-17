"""``check_printability`` for an adapter: export the part's mesh, then check it.

The part is exported to a temporary STL through the adapter's own
``export_stl_with_settings`` (fine resolution, mm, binary, in the model's
coordinates, so the points reported are the model's; the user's STL options are
put back), read, and checked by :func:`solidworks_mcp.utils.printability.analyse`.
The file is deleted afterwards. Change ``cellvis-assembly-and-print-checks``,
design D4.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from typing import Any, cast

from ...utils import printability
from ...utils.stl_mesh import read_triangles
from ..base import AdapterResult, AdapterResultStatus

#: The STL settings of the mesh the check reads.
MESH_SETTINGS: dict[str, Any] = {
    "resolution": "fine",
    "units": "mm",
    "binary": True,
    "translate_to_positive": False,
}


def _checks(
    triangles: Any,
    build_direction: str | Sequence[float] | None,
    support_angle_deg: float,
    min_bridge_mm: float,
    orientations: int,
    min_feature_mm: float | None,
) -> dict[str, Any]:
    """The checks asked, on the part's triangles (mm).

    With ``orientations``, the best of that many build directions is found
    (``best_orientation``) and the check is in it, unless ``build_direction``
    is given; with ``min_feature_mm``, the thin regions are added.
    """
    found = None
    if orientations:
        found = printability.best_orientation(
            triangles, orientations, support_angle_deg, min_bridge_mm
        )
    if build_direction is not None:
        data = printability.analyse(
            triangles, build_direction, support_angle_deg, min_bridge_mm
        )
    else:
        assert found is not None  # the caller asked for one of them
        data = dict(found["check"])
    data["orientation"] = "given" if build_direction is not None else "best"
    if found is not None:
        data["orientations"] = {k: v for k, v in found.items() if k != "check"}
    if min_feature_mm:
        data["thin_features"] = printability.thin_features(triangles, min_feature_mm)
    return data


async def check_part(
    adapter: Any,
    build_direction: str | Sequence[float] | None = None,
    support_angle_deg: float = 45.0,
    min_bridge_mm: float = 5.0,
    orientations: int = 0,
    min_feature_mm: float | None = None,
) -> AdapterResult[dict[str, Any]]:
    """Check the active part (the caller checked it is one) for printing.

    Args:
        adapter: An adapter with ``export_stl_with_settings``.
        build_direction: The direction the part grows from the plate; None
            to check in the best of ``orientations``.
        support_angle_deg: Down-facing surfaces under this angle need support.
        min_bridge_mm: Only longer bridges are listed.
        orientations: How many build directions to try (0: none, else at
            least 6); the best is ``orientations.best``.
        min_feature_mm: Report the regions thinner than this (None: don't).

    Returns:
        AdapterResult[dict[str, Any]]: The check (``analyse``), with
        ``orientation`` (``given`` or ``best``), ``orientations`` and
        ``thin_features`` when asked, plus ``mesh`` (the STL settings applied,
        and whether the user's came back).
    """
    if build_direction is None and not orientations:
        return AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="Give build_direction, or orientations to find the best one.",
        )
    try:
        if build_direction is not None:
            printability.direction_vector(build_direction)
        if orientations and orientations < 6:
            raise ValueError(
                "orientations must be at least 6 (the six axis directions)."
            )
    except ValueError as exc:
        return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))
    folder = tempfile.mkdtemp(prefix="sw_print_check_")
    path = os.path.join(folder, "part.stl")
    try:
        exported = await adapter.export_stl_with_settings(path, dict(MESH_SETTINGS))
        if not exported.is_success:
            return cast(AdapterResult[dict[str, Any]], exported)
        try:
            data = _checks(
                read_triangles(path, "mm"),
                build_direction,
                support_angle_deg,
                min_bridge_mm,
                orientations,
                min_feature_mm,
            )
        except (OSError, ValueError) as exc:
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=f"The part's mesh could not be checked: {exc}",
            )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    exported_data = exported.data if isinstance(exported.data, dict) else {}
    data["mesh"] = exported_data.get("mesh")
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS,
        data=data,
        execution_time=exported.execution_time,
    )
