"""The live suite's warm-up wait (``tests/live/readiness.py``), with fakes only.

No SolidWorks: the clock, the sleep, the CPU counter, ``user32`` and the COM
read are all fakes. ``tests/live/`` is not ``src/``, so this lives here rather
than under ``tests/solidworks_mcp/``.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable

import pytest

from tests.live import readiness
from tests.solidworks_mcp.adapters.solidworks.fake_user32 import FakeUser32

SW_PID = 4242


class _Clock:
    """A fake monotonic clock that the fake sleep moves forward."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _checks(answers: Iterable[str | None]) -> Callable[[], str | None]:
    """A check that gives ``answers`` in turn, then keeps the last one."""
    queue = list(answers)

    def check() -> str | None:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return check


# ---- wait_until_ready ----------------------------------------------------------


def test_ready_once_the_check_holds_for_the_settle_time() -> None:
    clock = _Clock()

    result = readiness.wait_until_ready(
        _checks(["busy", None]),
        budget=400,
        settle=10,
        interval=2,
        clock=clock,
        sleep=clock.sleep,
    )

    # Ready from t=2; it has to hold through t=12.
    assert result == readiness.Readiness(True, 12.0, "ready")
    assert clock.now < 400


def test_a_relapse_starts_the_settle_time_again() -> None:
    clock = _Clock()

    result = readiness.wait_until_ready(
        _checks([None, None, "busy", None]),
        budget=400,
        settle=4,
        interval=2,
        clock=clock,
        sleep=clock.sleep,
    )

    # Ready at 0 and 2, busy at 4, ready again from 6 until 10.
    assert result.ready is True
    assert result.waited == pytest.approx(10.0)


def test_the_fixed_time_stays_the_upper_bound() -> None:
    clock = _Clock()

    result = readiness.wait_until_ready(
        _checks(["its window does not answer"]),
        budget=7,
        settle=4,
        interval=2,
        clock=clock,
        sleep=clock.sleep,
    )

    assert result == readiness.Readiness(False, 7.0, "its window does not answer")
    assert clock.sleeps == [2, 2, 2, 1]  # never past the budget


def test_ready_but_not_settled_when_the_budget_ends() -> None:
    clock = _Clock()

    result = readiness.wait_until_ready(
        _checks(["busy", None]),
        budget=6,
        settle=20,
        interval=2,
        clock=clock,
        sleep=clock.sleep,
    )

    assert result.ready is False
    assert result.reason == "ready for 4 of 20 s"


# ---- warm_up -------------------------------------------------------------------


def _warm_up(age: float | None, warmup: float, **kwargs):  # noqa: ANN202
    clock = _Clock()
    lines: list[str] = []
    made: list[int] = []

    def factory() -> Callable[[], str | None]:
        made.append(1)
        return lambda: None

    result = readiness.warm_up(
        age,
        warmup,
        factory,
        settle=4,
        log=lines.append,
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )
    return result, clock, lines, made


@pytest.mark.parametrize(("age", "warmup"), [(None, 480), (500, 480), (10, 0)])
def test_no_wait_for_an_old_solidworks_or_with_the_wait_off(age, warmup) -> None:
    result, clock, lines, made = _warm_up(age, warmup)

    assert result is None
    assert clock.sleeps == [] and lines == [] and made == []


def test_a_young_solidworks_is_polled_not_slept_for() -> None:
    result, clock, lines, made = _warm_up(36, 480)

    assert result is not None and result.ready is True
    assert clock.now == pytest.approx(4.0)  # the settle time, not 444 s
    assert made == [1]
    assert "at most 444s" in lines[0]
    assert lines[-1] == "[live] SolidWorks ready after 4s"


def test_the_fixed_wait_is_still_there_when_asked() -> None:
    result, clock, lines, made = _warm_up(36, 480, fixed=True)

    assert clock.sleeps == [444]
    assert made == []
    assert "waiting 444s" in lines[0]
    assert result is not None and result.ready is False


# ---- SolidWorksReadiness -------------------------------------------------------


class _Cpu:
    """CPU seconds used, advanced by hand."""

    def __init__(self) -> None:
        self.used: float | None = 0.0

    def __call__(self) -> float | None:
        return self.used


def _probe(user32: FakeUser32, com: Callable[[], bool] = lambda: True):  # noqa: ANN202
    clock, cpu = _Clock(), _Cpu()
    probe = readiness.SolidWorksReadiness(
        SW_PID, com, user32=user32, cpu_seconds=cpu, clock=clock
    )
    return probe, clock, cpu


def _idle_tick(clock: _Clock, cpu: _Cpu, cores: float = 0.01) -> None:
    clock.now += 2
    cpu.used = (cpu.used or 0.0) + 2 * cores


def _desktop(**frame) -> FakeUser32:  # noqa: ANN003
    user32 = FakeUser32()
    user32.add(1, SW_PID, title="SOLIDWORKS", **frame)
    user32.add(2, 999, title="someone else's window", hung=True)
    return user32


def test_the_first_call_only_measures_cpu() -> None:
    probe, _clock, _cpu = _probe(_desktop())

    assert probe() == "measuring CPU use"


def test_busy_cpu_is_not_ready() -> None:
    probe, clock, cpu = _probe(_desktop())
    probe()
    _idle_tick(clock, cpu, cores=1.5)

    assert probe() == "busy: 1.50 cores of CPU"


def test_idle_answering_solidworks_is_ready() -> None:
    probe, clock, cpu = _probe(_desktop())
    probe()
    _idle_tick(clock, cpu)

    # Another process' hung window is not SolidWorks'.
    assert probe() is None


def test_no_window_yet_is_not_ready() -> None:
    user32 = FakeUser32()
    user32.add(1, SW_PID, visible=False)
    user32.add(3, SW_PID, owner=7)  # an owned window, not the frame
    probe, clock, cpu = _probe(user32)
    probe()
    _idle_tick(clock, cpu)

    assert probe() == "no SolidWorks window yet"


def test_a_hung_window_is_not_ready_and_com_is_not_called() -> None:
    calls: list[int] = []
    probe, clock, cpu = _probe(
        _desktop(hung=True), com=lambda: bool(calls.append(1)) or True
    )
    probe()
    _idle_tick(clock, cpu)

    assert probe() == "its window does not answer"
    assert calls == []


def test_a_failed_com_read_is_not_ready() -> None:
    def broken() -> bool:
        raise OSError("RPC server unavailable")

    probe, clock, cpu = _probe(_desktop(), com=broken)
    probe()
    _idle_tick(clock, cpu)
    assert probe() == "COM read failed: RPC server unavailable"

    probe, clock, cpu = _probe(_desktop(), com=lambda: False)
    probe()
    _idle_tick(clock, cpu)
    assert probe() == "COM read failed"


def test_unreadable_cpu_times_start_the_measurement_again() -> None:
    probe, clock, cpu = _probe(_desktop())
    probe()
    _idle_tick(clock, cpu)
    cpu.used = None

    assert probe() == "measuring CPU use"
    cpu.used = 10.0
    assert probe() == "measuring CPU use"
    _idle_tick(clock, cpu)
    assert probe() is None


def test_no_user32_is_not_ready() -> None:
    probe, clock, cpu = _probe(_desktop())
    probe._user32 = None
    probe()
    _idle_tick(clock, cpu)

    assert probe() == "no user32 to ask the window"


def test_process_cpu_seconds_adds_kernel_and_user_time() -> None:
    class _Kernel32:
        def __init__(self, handle: int, ok: bool = True) -> None:
            self.handle, self.ok, self.closed = handle, ok, False

        def OpenProcess(self, access, inherit, pid):  # noqa: ANN001, ANN202, N802
            return self.handle

        def GetProcessTimes(self, handle, *times):  # noqa: ANN001, ANN202, N802
            if not self.ok:
                return 0
            # kernel 1.5 s, user 2.5 s, in 100 ns ticks.
            times[2]._obj.dwLowDateTime = 15_000_000
            times[3]._obj.dwLowDateTime = 25_000_000
            return 1

        def CloseHandle(self, handle):  # noqa: ANN001, ANN202, N802
            self.closed = True

    kernel32 = _Kernel32(handle=5)
    assert readiness.process_cpu_seconds(SW_PID, kernel32) == pytest.approx(4.0)
    assert kernel32.closed
    assert readiness.process_cpu_seconds(SW_PID, _Kernel32(handle=0)) is None
    failing = _Kernel32(handle=5, ok=False)
    assert readiness.process_cpu_seconds(SW_PID, failing) is None
    assert failing.closed


@pytest.mark.skipif(sys.platform != "win32", reason="GetProcessTimes is Win32")
def test_process_cpu_seconds_reads_this_process() -> None:
    used = readiness.process_cpu_seconds(os.getpid())

    assert used is not None and used > 0
