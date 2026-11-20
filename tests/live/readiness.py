"""Wait for a freshly started SolidWorks to be ready, instead of a fixed time.

The live suite used to sleep until SolidWorks was ``SW_TEST_WARMUP_SECONDS``
old (480 s by default): nearly every crash on SOLIDWORKS 2026 landed 2-5
minutes after launch, while 3DEXPERIENCE signed in and re-laid-out the
CommandManager tabs. :func:`wait_until_ready` polls instead and returns once
SolidWorks has been idle and answering for ``settle`` seconds in a row, with
the old fixed time kept as the upper bound.

Ready means, checked in this order so a busy SolidWorks is never called
through COM (a COM call to a busy STA blocks until it pumps messages):

1. its CPU use has settled (``GetProcessTimes``, no window, no COM);
2. every visible top-level window it owns answers a message
   (``SolidWorksWindow.is_responding``: ``IsHungAppWindow``, then ``WM_NULL``
   through ``SendMessageTimeoutW``, the server's own busy check);
3. a cheap COM read succeeds (``RevisionNumber`` on the running SolidWorks).

Everything is injectable, so ``tests/integration/test_live_readiness.py``
checks it with fakes and no SolidWorks.
"""

from __future__ import annotations

import ctypes
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: CPU use, in cores, below which SolidWorks counts as idle.
DEFAULT_IDLE_CORES = 0.2
#: How long SolidWorks must stay ready before the run starts.
DEFAULT_SETTLE_SECONDS = 20.0
#: Seconds between two checks.
DEFAULT_INTERVAL_SECONDS = 2.0
#: How long one window may take to answer ``WM_NULL``.
RESPOND_TIMEOUT_MS = 1000
GW_OWNER = 4


@dataclass(frozen=True)
class Readiness:
    """How the wait ended.

    Attributes:
        ready: True when SolidWorks stayed ready for the whole settle time.
        waited: Seconds spent waiting.
        reason: ``"ready"``, or why it was not ready when the budget ran out.
    """

    ready: bool
    waited: float
    reason: str


def wait_until_ready(
    check: Callable[[], str | None],
    *,
    budget: float,
    settle: float = DEFAULT_SETTLE_SECONDS,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Readiness:
    """Poll ``check`` until it reports ready for ``settle`` seconds in a row.

    Args:
        check: Returns ``None`` when SolidWorks is ready, else why it isn't.
        budget: Most seconds to wait (the old fixed warm-up time left).
        settle: Seconds ``check`` must keep returning ``None``.
        interval: Seconds between checks.
        clock: A monotonic clock (tests pass a fake).
        sleep: ``time.sleep`` (tests pass a fake).

    Returns:
        Readiness: ``ready`` False when the budget ran out first.
    """
    start = clock()
    ready_since: float | None = None
    while True:
        reason = check()
        now = clock()
        if reason is None:
            if ready_since is None:
                ready_since = now
            if now - ready_since >= settle:
                return Readiness(True, now - start, "ready")
            reason = f"ready for {now - ready_since:.0f} of {settle:.0f} s"
        else:
            ready_since = None
        if now - start >= budget:
            return Readiness(False, now - start, reason)
        sleep(max(0.0, min(interval, budget - (now - start))))


def warm_up(
    age: float | None,
    warmup: float,
    check: Callable[[], Callable[[], str | None]],
    *,
    fixed: bool = False,
    settle: float = DEFAULT_SETTLE_SECONDS,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Readiness | None:
    """Wait for a SolidWorks ``age`` seconds old, at most until it is ``warmup`` old.

    Args:
        age: The process' age in seconds; ``None`` when unknown (no wait).
        warmup: ``SW_TEST_WARMUP_SECONDS``: the age after which there is no
            wait, and the upper bound on it; ``0`` turns the wait off.
        check: Makes the readiness check (only called when polling).
        fixed: ``SW_TEST_WARMUP_FIXED``: sleep the whole time, as before.
        settle: Seconds SolidWorks must stay ready.
        log: Where progress lines go.
        sleep: ``time.sleep`` (tests pass a fake).
        clock: A monotonic clock (tests pass a fake).

    Returns:
        How the wait ended, or ``None`` when there was nothing to wait for.
    """
    if age is None or warmup <= 0 or age >= warmup:
        return None
    budget = warmup - age
    if fixed:
        log(
            f"[live] SolidWorks started {age:.0f}s ago; waiting {budget:.0f}s for it "
            "to finish warming up (SW_TEST_WARMUP_SECONDS, SW_TEST_WARMUP_FIXED)..."
        )
        sleep(budget)
        return Readiness(False, budget, "fixed wait")
    log(
        f"[live] SolidWorks started {age:.0f}s ago; waiting until it has been idle "
        f"and answering for {settle:.0f}s, at most {budget:.0f}s "
        "(SW_TEST_WARMUP_SECONDS)..."
    )
    result = wait_until_ready(
        check(), budget=budget, settle=settle, clock=clock, sleep=sleep
    )
    outcome = "ready" if result.ready else f"not ready ({result.reason}); starting"
    log(f"[live] SolidWorks {outcome} after {result.waited:.0f}s")
    return result


def process_cpu_seconds(pid: int, kernel32: Any = None) -> float | None:
    """CPU seconds (kernel + user) process ``pid`` has used, or ``None``."""
    from ctypes import wintypes

    kernel32 = kernel32 or ctypes.WinDLL("kernel32")
    handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        _created, _exited, kernel, user = times
        ticks = sum((t.dwHighDateTime << 32) | t.dwLowDateTime for t in (kernel, user))
        return ticks / 10_000_000
    finally:
        kernel32.CloseHandle(handle)


def process_windows(pid: int, user32: Any) -> list[int]:
    """The visible, unowned top-level windows process ``pid`` owns."""
    from solidworks_mcp.adapters.solidworks.dialogs import top_level_windows
    from solidworks_mcp.adapters.solidworks.window import SolidWorksWindow

    found = []
    for hwnd in top_level_windows(user32):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            continue
        if SolidWorksWindow(hwnd, user32).process_id() == pid:
            found.append(hwnd)
    return found


class SolidWorksReadiness:
    """The check :func:`wait_until_ready` polls for one SolidWorks process.

    Args:
        pid: The ``SLDWORKS.exe`` process id.
        com_read: Makes one cheap COM read; True when it answered.
        user32: A ``user32``-like object (``window.load_user32()`` by default).
        cpu_seconds: Returns the process' CPU seconds so far (or ``None``).
        clock: A monotonic clock.
        idle_cores: CPU use, in cores, below which it counts as idle.
    """

    def __init__(
        self,
        pid: int,
        com_read: Callable[[], bool],
        *,
        user32: Any = None,
        cpu_seconds: Callable[[], float | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_cores: float = DEFAULT_IDLE_CORES,
    ) -> None:
        """Bind to a process; nothing is read until the first call."""
        from solidworks_mcp.adapters.solidworks.window import load_user32

        self.pid = pid
        self._com_read = com_read
        self._user32 = user32 if user32 is not None else load_user32()
        self._cpu_seconds = cpu_seconds or (lambda: process_cpu_seconds(pid))
        self._clock = clock
        self._idle_cores = idle_cores
        self._last: tuple[float, float] | None = None

    def _cpu_cores(self) -> float | None:
        """Cores used since the previous call; ``None`` on the first one."""
        used, now = self._cpu_seconds(), self._clock()
        if used is None:
            self._last = None
            return None
        last, self._last = self._last, (now, used)
        if last is None or now <= last[0]:
            return None
        return (used - last[1]) / (now - last[0])

    def __call__(self) -> str | None:
        """``None`` when SolidWorks is ready, else why it isn't yet."""
        from solidworks_mcp.adapters.solidworks.window import SolidWorksWindow

        cores = self._cpu_cores()
        if cores is None:
            return "measuring CPU use"
        if cores > self._idle_cores:
            return f"busy: {cores:.2f} cores of CPU"
        if self._user32 is None:
            return "no user32 to ask the window"
        windows = process_windows(self.pid, self._user32)
        if not windows:
            return "no SolidWorks window yet"
        for hwnd in windows:
            if not SolidWorksWindow(hwnd, self._user32).is_responding(
                RESPOND_TIMEOUT_MS
            ):
                return "its window does not answer"
        try:
            answered = bool(self._com_read())
        except Exception as exc:  # noqa: BLE001 - not ready yet, whatever it raised
            return f"COM read failed: {exc}"
        return None if answered else "COM read failed"
