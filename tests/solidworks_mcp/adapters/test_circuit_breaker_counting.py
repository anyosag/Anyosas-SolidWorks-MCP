"""Which failed outcomes count toward an operation's circuit breaker."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.circuit_breaker import (
    CircuitBreakerAdapter,
    CircuitState,
    _counts_as_failure,
)

#: The Save Bodies call whose file was written but whose dialog was cancelled.
DIALOG = (
    "SolidWorks showed a dialog during save_body_as_part, 'SOLIDWORKS Design'; "
    "the server cancelled it."
)


def _error(message: str) -> AdapterResult[object]:
    return AdapterResult(status=AdapterResultStatus.ERROR, error=message)


@pytest.mark.parametrize(
    "message",
    [
        DIALOG,
        "Saving would overwrite 2 modified files that existed before this session",
        "The active document changed between calls: Assem3 is active",
        "SolidWorks is busy: it has not answered for 30 s",
        "The SolidWorks this server was connected to has exited, and the server "
        "has reconnected to the SolidWorks running now",
        "Not connected to SolidWorks",
        "save_body_as_part requires an active part document",
        "No active model",
        "Body 'X' not found. Solid bodies: Body1",
        "Invalid request: two names for one component",
        "Circuit breaker is open for save_body_as_part",
    ],
)
def test_answers_and_guards_do_not_count(message: str) -> None:
    assert _counts_as_failure(_error(message)) is False


@pytest.mark.parametrize(
    "message",
    [
        "COM error in create_extrusion: (-2147352567, 'Exception occurred.')",
        "Failed to create extrusion",
        "CreateSaveBodyFeature did not write a file",
    ],
)
def test_solidworks_failures_count(message: str) -> None:
    assert _counts_as_failure(_error(message)) is True


def test_a_timeout_still_counts() -> None:
    timeout = AdapterResult(status=AdapterResultStatus.TIMEOUT, error="took too long")

    assert _counts_as_failure(timeout) is True


async def test_dialogs_never_open_the_breaker() -> None:
    breaker = CircuitBreakerAdapter(failure_threshold=2)

    async def dialog() -> AdapterResult[object]:
        return _error(DIALOG)

    async def fails() -> AdapterResult[object]:
        return _error("Failed to save body")

    for _ in range(5):
        await breaker._execute_with_circuit_breaker("save_body_as_part", dialog)
    bucket = breaker._get_bucket("save_body_as_part")
    assert (bucket.state, bucket.failure_count) == (CircuitState.CLOSED, 0)

    await breaker._execute_with_circuit_breaker("save_body_as_part", fails)
    await breaker._execute_with_circuit_breaker("save_body_as_part", fails)
    assert bucket.state is CircuitState.OPEN
