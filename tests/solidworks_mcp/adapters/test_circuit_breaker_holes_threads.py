"""``create_thread`` and ``create_hole_wizard_hole`` pass through CircuitBreakerAdapter."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import CircuitBreakerAdapter, CircuitState


class _Stub:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.error = error

    def _record(self, name: str, *args: Any) -> AdapterResult[Any]:
        self.calls.append((name, args))
        if self.error:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=self.error)
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=name)

    async def create_thread(
        self,
        size,
        edge_point,
        length_mm,
        internal=False,
        diameter_offset_mm=None,
        start_offset_mm=None,
    ):
        return self._record(
            "create_thread",
            size,
            edge_point,
            length_mm,
            internal,
            diameter_offset_mm,
            start_offset_mm,
        )

    async def create_hole_wizard_hole(
        self,
        kind,
        size,
        face_point,
        side=None,
        depth_mm=None,
        fit=None,
        thread="cosmetic",
    ):
        return self._record(
            "create_hole_wizard_hole",
            kind,
            size,
            face_point,
            side,
            depth_mm,
            fit,
            thread,
        )


@pytest.mark.asyncio
async def test_both_are_forwarded_with_every_argument() -> None:
    stub = _Stub()
    breaker = CircuitBreakerAdapter(adapter=stub)

    assert (
        await breaker.create_thread("M30x1", [14.75, 1.77, 0], 5.5, False, -0.5, 0.5)
    ).is_success
    assert (
        await breaker.create_hole_wizard_hole(
            "tapped", "M3", [20, 10, 10], "+Z", 8.0, None, "modeled"
        )
    ).is_success
    assert stub.calls == [
        ("create_thread", ("M30x1", [14.75, 1.77, 0], 5.5, False, -0.5, 0.5)),
        (
            "create_hole_wizard_hole",
            ("tapped", "M3", [20, 10, 10], "+Z", 8.0, None, "modeled"),
        ),
    ]


@pytest.mark.asyncio
async def test_a_refused_diameter_does_not_trip_the_breaker() -> None:
    error = "Error in create_thread: Invalid request: an internal M10x1.5 thread needs a Ø8.376 hole"
    breaker = CircuitBreakerAdapter(adapter=_Stub(error), failure_threshold=2)

    for _ in range(3):
        assert (
            "Ø8.376"
            in (await breaker.create_thread("M10x1.5", [0, 0, 0], 5, True)).error
        )

    assert breaker._get_bucket("create_thread").state == CircuitState.CLOSED
