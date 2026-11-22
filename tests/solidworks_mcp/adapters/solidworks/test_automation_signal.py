"""The Stage Manager event, held through each operation and 2 s after it.

``Local\\StageManager.Automation.<pid>`` tells the user's Stage Manager that
an MCP server is driving SolidWorks, so it keeps SolidWorks in its strip
(``automation_signal``, agreed with Stage Manager's H71).
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import automation_signal
from solidworks_mcp.adapters.solidworks.automation_signal import (
    GRACE_SECONDS,
    AutomationSignal,
    event_name,
    load_kernel32,
)

PID = 11572


class _Kernel32:
    """``CreateEventW`` and ``CloseHandle``, recorded."""

    def __init__(self, *handles: Any) -> None:
        self.created: list[tuple[Any, ...]] = []
        self.closed: list[Any] = []
        self._handles = iter(handles or (101, 102, 103))
        self.create_error: Exception | None = None
        self.close_error: Exception | None = None

    def CreateEventW(self, *args: Any) -> Any:  # noqa: N802 - Win32 name
        self.created.append(args)
        if self.create_error is not None:
            raise self.create_error
        return next(self._handles)

    def CloseHandle(self, handle: Any) -> bool:  # noqa: N802
        if self.close_error is not None:
            raise self.close_error
        self.closed.append(handle)
        return True


class _Timers:
    """``timer(seconds, action)``, started by hand: ``fire(i)`` runs one."""

    def __init__(self) -> None:
        self.started: list[SimpleNamespace] = []

    def __call__(self, seconds: float, action: Any) -> SimpleNamespace:
        timer = SimpleNamespace(seconds=seconds, action=action, cancelled=False)
        timer.cancel = lambda: setattr(timer, "cancelled", True)
        self.started.append(timer)
        return timer

    def fire(self, index: int = -1) -> None:
        self.started[index].action()


@pytest.fixture(autouse=True)
def _signal_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite turns the event off (conftest); these tests turn it on."""
    monkeypatch.setenv("SOLIDWORKS_MCP_AUTOMATION_SIGNAL", "1")


@pytest.fixture
def kernel32() -> _Kernel32:
    return _Kernel32()


@pytest.fixture
def timers() -> _Timers:
    return _Timers()


@pytest.fixture
def signal(kernel32: _Kernel32, timers: _Timers) -> AutomationSignal:
    return AutomationSignal(kernel32, timer=timers)


def test_the_name_is_the_prefix_and_the_decimal_pid() -> None:
    assert event_name(PID) == "Local\\StageManager.Automation.11572"


def test_an_operation_holds_the_event_until_the_grace_ends(
    signal: AutomationSignal, kernel32: _Kernel32, timers: _Timers
) -> None:
    signal.begin(PID)

    # Manual-reset, signalled, by name; no security attributes.
    assert kernel32.created == [(None, True, True, event_name(PID))]
    assert signal.held

    signal.end()

    assert [t.seconds for t in timers.started] == [GRACE_SECONDS] == [2.0]
    assert signal.held and kernel32.closed == []

    timers.fire()

    assert kernel32.closed == [101]
    assert not signal.held


def test_calls_within_the_grace_keep_one_event(
    signal: AutomationSignal, kernel32: _Kernel32, timers: _Timers
) -> None:
    signal.begin(PID)
    signal.end()
    signal.begin(PID)  # the next call, inside the grace

    assert timers.started[0].cancelled
    assert len(kernel32.created) == 1

    signal.end()
    timers.fire(0)  # the first timer, cancelled but run late: it does nothing

    assert signal.held and kernel32.closed == []

    timers.fire(1)

    assert kernel32.closed == [101]


def test_a_new_solidworks_gets_its_own_event(
    signal: AutomationSignal, kernel32: _Kernel32
) -> None:
    signal.begin(PID)
    signal.begin(PID + 1)  # SolidWorks restarted, the server reconnected

    assert kernel32.closed == [101]
    assert [args[3] for args in kernel32.created] == [
        event_name(PID),
        event_name(PID + 1),
    ]


def test_nothing_is_held_without_a_pid_or_when_turned_off(
    signal: AutomationSignal,
    kernel32: _Kernel32,
    timers: _Timers,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal.begin(0)
    signal.end()

    monkeypatch.setenv("SOLIDWORKS_MCP_AUTOMATION_SIGNAL", "off")
    signal.begin(PID)

    assert kernel32.created == [] and timers.started == []
    assert not automation_signal.enabled()


def test_a_failing_kernel32_never_fails_the_call(kernel32: _Kernel32) -> None:
    no_handle = AutomationSignal(_Kernel32(0))
    no_handle.begin(PID)

    kernel32.create_error = OSError("access denied")
    raising = AutomationSignal(kernel32)
    raising.begin(PID)

    assert not no_handle.held and not raising.held

    closing = AutomationSignal(_Kernel32())
    closing.begin(PID)
    assert closing._kernel32 is not None
    closing._kernel32.close_error = OSError("invalid handle")
    closing.close()

    assert not closing.held


def test_close_closes_at_once(
    signal: AutomationSignal, kernel32: _Kernel32, timers: _Timers
) -> None:
    signal.begin(PID)
    signal.end()

    signal.close()

    assert kernel32.closed == [101] and timers.started[0].cancelled
    signal.close()  # nothing left to close
    assert kernel32.closed == [101]


def test_kernel32_is_loaded_on_first_use() -> None:
    loaded = _Kernel32()
    signal = AutomationSignal(loader=lambda: loaded, timer=_Timers())
    nowhere = AutomationSignal(loader=lambda: None)

    signal.begin(PID)
    nowhere.begin(PID)

    assert loaded.created and signal.held
    assert not nowhere.held


def test_load_kernel32_types_its_two_functions() -> None:
    dll = SimpleNamespace(CreateEventW=SimpleNamespace(), CloseHandle=SimpleNamespace())
    opened: list[tuple[Any, ...]] = []

    def loader(*args: Any, **kwargs: Any) -> Any:
        opened.append((args, kwargs))
        return dll

    assert load_kernel32("linux") is None
    assert load_kernel32("win32", loader) is dll
    assert opened == [(("kernel32",), {"use_last_error": True})]
    assert len(dll.CreateEventW.argtypes) == 4
    assert dll.CreateEventW.restype is not None
    assert dll.CloseHandle.argtypes and dll.CloseHandle.restype is not None


def test_the_default_timer_runs_the_close() -> None:
    ran = threading.Event()

    timer = automation_signal._start_timer(0.01, ran.set)

    assert ran.wait(5.0)
    assert timer.daemon
