"""The analysis methods of ``PyWin32Adapter`` from "The Dipper" build.

``rotation_clearance`` (``rotation_clearance.py``), ``volume_by_plane``
(``plane_volume.py``), ``set_density`` (``density.py``) and
``query_geometry`` (``geometry_query.py``). Each checks the
active document's type, then runs its work through ``_handle_com_operation``
(one STA thread, the settle pause, the time limit) and drops the
``Error in <op>:`` prefix from expected failures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from ..base import AdapterResult, AdapterResultStatus
from .com_utils import strip_op_prefix

#: ``swDocumentTypes_e``: part and assembly.
_PART, _ASSEMBLY = 1, 2
_KINDS = {1: "part", 2: "assembly", 3: "drawing"}

#: The default angle range of ``rotation_clearance``: both ways, half a turn.
DEFAULT_RANGE_DEG = (-180.0, 180.0)


class SolidWorksAnalysisMixin:
    """``rotation_clearance``, ``volume_by_plane``, ``set_density``, ``query_geometry``."""

    if TYPE_CHECKING:
        currentModel: Any

        def _handle_com_operation(
            self, operation_name: str, operation: Callable[..., Any], *args: Any
        ) -> AdapterResult[Any]: ...

    def _run_on(
        self,
        operation: str,
        allowed: dict[int, Callable[[], dict[str, Any]]],
    ) -> AdapterResult[dict[str, Any]]:
        """Run the work for the active document's type, or say why not."""
        from .io import _doc_type

        if not self.currentModel:
            return AdapterResult(
                status=AdapterResultStatus.ERROR, error="No active model"
            )
        doc_type = _doc_type(self)
        work = allowed.get(doc_type or 0)
        if work is None:
            wanted = " or ".join(_KINDS[t] for t in sorted(allowed))
            kind = _KINDS.get(doc_type or 0, "unknown")
            return AdapterResult(
                status=AdapterResultStatus.ERROR,
                error=(
                    f"{operation} needs an active {wanted} document (active "
                    f"document is {kind})."
                ),
            )
        result = cast(
            "AdapterResult[dict[str, Any]]",
            self._handle_com_operation(operation, work),
        )
        if not result.is_success:
            result.error = strip_op_prefix(result.error, operation)
        return result

    async def rotation_clearance(
        self,
        moving: str,
        other: str,
        axis: list[float],
        pivot_mm: list[float] | None = None,
        range_deg: list[float] | None = None,
        step_deg: float = 5.0,
        tolerance_deg: float = 0.1,
    ) -> AdapterResult[dict[str, Any]]:
        """Turn a body (part) or component (assembly) and find its clearance and contacts.

        See ``rotation_clearance.body_rotation_clearance`` and
        ``component_rotation_clearance``.
        """
        from .rotation_clearance import (
            body_rotation_clearance,
            component_rotation_clearance,
        )

        args = (
            moving,
            other,
            axis,
            list(pivot_mm or (0.0, 0.0, 0.0)),
            list(range_deg or DEFAULT_RANGE_DEG),
            step_deg,
            tolerance_deg,
        )
        return self._run_on(
            "rotation_clearance",
            {
                _PART: lambda: body_rotation_clearance(self, *args),
                _ASSEMBLY: lambda: component_rotation_clearance(self, *args),
            },
        )

    async def volume_by_plane(
        self,
        plane: str | None = None,
        point_mm: list[float] | None = None,
        normal: list[float] | None = None,
        bodies: list[str] | None = None,
        enclosed: bool = False,
        density_g_cm3: float | None = None,
    ) -> AdapterResult[dict[str, Any]]:
        """The active part's volume on each side of a plane (see ``plane_volume``)."""
        from .plane_volume import volume_by_plane

        return self._run_on(
            "volume_by_plane",
            {
                _PART: lambda: volume_by_plane(
                    self, plane, point_mm, normal, bodies, enclosed, density_g_cm3
                )
            },
        )

    async def set_density(
        self, density_g_cm3: float, remove_material: bool = False
    ) -> AdapterResult[dict[str, Any]]:
        """Give the active part a document density (see ``density.set_density``)."""
        from .density import set_density

        return self._run_on(
            "set_density",
            {_PART: lambda: set_density(self, density_g_cm3, remove_material)},
        )

    async def query_geometry(
        self,
        point_mm: list[float],
        radius_mm: float = 2.0,
        kinds: str = "both",
        limit: int = 10,
        read_all: bool = False,
    ) -> AdapterResult[dict[str, Any]]:
        """The active part's faces and edges near a point (see ``geometry_query``).

        A malformed query is refused before SolidWorks is called; the
        selection is left as it was.
        """
        from .geometry_query import query_geometry, query_problem

        problem = query_problem(point_mm, radius_mm, kinds, limit)
        if problem:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=problem)
        return self._run_on(
            "query_geometry",
            {
                _PART: lambda: query_geometry(
                    self, point_mm, radius_mm, kinds, limit, read_all
                )
            },
        )
