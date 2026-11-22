"""``_handle_com_operation`` holds the Stage Manager event around each outermost call.

The event (``automation_signal``) is taken before the operation runs and let
go when it ends, once per outermost call however deep they nest, for the
connected SolidWorks' process; disconnect closes it at once.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter

SW_PID = 9396


class _Recorder:
    """An ``AutomationSignal`` stand-in that records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def begin(self, pid: int) -> None:
        self.calls.append(("begin", pid))

    def end(self) -> None:
        self.calls.append(("end", None))

    def close(self) -> None:
        self.calls.append(("close", None))


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> PyWin32Adapter:
    """A ``PyWin32Adapter`` that thinks it runs on Windows, with a recorder."""
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter, "pywintypes", SimpleNamespace(com_error=RuntimeError)
    )
    monkeypatch.setenv("SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS", "0")
    made = PyWin32Adapter({})
    made._sw_process_id = SW_PID
    made._automation_signal = _Recorder()
    return made


def test_an_outermost_operation_holds_the_event_once(adapter: PyWin32Adapter) -> None:
    signal = adapter._automation_signal
    inner: list[Any] = []

    def outer() -> None:
        inner.append(adapter._handle_com_operation("add_line", lambda: None))

    result = adapter._handle_com_operation("add_sketch_entities", outer)

    assert result.is_success and inner[0].is_success
    assert signal.calls == [("begin", SW_PID), ("end", None)]


def test_the_event_is_let_go_when_the_operation_fails(adapter: PyWin32Adapter) -> None:
    def fails() -> None:
        raise RuntimeError("SolidWorks said no")

    result = adapter._handle_com_operation("add_line", fails)

    assert result.is_error
    assert adapter._automation_signal.calls == [("begin", SW_PID), ("end", None)]


def test_an_adapter_without_the_signal_still_runs(adapter: PyWin32Adapter) -> None:
    del adapter._automation_signal

    assert adapter._handle_com_operation("add_line", lambda: 1).is_success


def test_a_new_adapter_has_a_signal_that_holds_nothing_yet() -> None:
    signal = PyWin32Adapter({})._automation_signal

    assert not signal.held


@pytest.mark.asyncio
async def test_disconnect_closes_the_event_at_once(adapter: PyWin32Adapter) -> None:
    await adapter.disconnect()

    assert ("close", None) in adapter._automation_signal.calls


@pytest.mark.asyncio
async def test_disconnect_without_the_signal_still_disconnects(
    adapter: PyWin32Adapter,
) -> None:
    del adapter._automation_signal

    await adapter.disconnect()

    assert adapter.swApp is None
