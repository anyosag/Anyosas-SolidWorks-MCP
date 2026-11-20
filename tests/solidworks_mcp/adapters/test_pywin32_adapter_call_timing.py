"""Where a tool call's server time goes: each COM operation's phases.

A reply's ``execution_time`` is the timed closure of one COM operation; the
settle gap, the checks before and after it, and a tool's other operations are
outside it (83 of 253 minutes of server round trips in the 2026-11-20
sample). The tool boundary times each call (``timed_call``) and the adapter
adds every outermost operation's phases to it, with no COM calls of its own.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.pywin32_adapter import (
    CallTiming,
    PyWin32Adapter,
    timed_call,
)


@pytest.fixture
def adapter(monkeypatch) -> PyWin32Adapter:
    """A ``PyWin32Adapter`` that thinks it runs on Windows with pywin32."""
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter,
        "pywintypes",
        SimpleNamespace(com_error=RuntimeError),
        raising=False,
    )
    return PyWin32Adapter({})


class _Clock:
    """``perf_counter``, ``monotonic`` and ``sleep`` on one clock.

    It moves only when slept on or told to, so each phase's seconds are exact.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def spend(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    """The default settle gaps, a fake clock, and a fresh pacer."""
    fake = _Clock()
    monkeypatch.setattr(pywin32_adapter.time, "perf_counter", fake.read)
    monkeypatch.setattr(pywin32_adapter.time, "monotonic", fake.read)
    monkeypatch.setattr(pywin32_adapter.time, "sleep", fake.sleep)
    monkeypatch.setenv("SOLIDWORKS_MCP_COM_SETTLE_SECONDS", "0.25")
    monkeypatch.setenv("SOLIDWORKS_MCP_DOC_SETTLE_SECONDS", "1.0")
    monkeypatch.delenv("SOLIDWORKS_MCP_SETTLE_MODE", raising=False)
    monkeypatch.setattr(pywin32_adapter, "_PACER", pywin32_adapter._ComPacer())
    return fake


def _totals(timing: CallTiming) -> dict[str, tuple[int, float, float]]:
    return {
        name: (totals.count, totals.closure_s, totals.total_s)
        for name, totals in timing.operations.items()
    }


# ---- _handle_com_operation ----------------------------------------------------------


def test_each_operation_adds_its_phases_to_the_call(adapter, clock) -> None:
    with timed_call("create_part") as timing:
        adapter._handle_com_operation("create_part", lambda: clock.spend(0.5))
        adapter._handle_com_operation("add_line", lambda: clock.spend(0.25))

    # add_line waited out create_part's 1.0 s document gap first.
    assert timing.phases == {
        "pace": 1.0,
        "pre_checks": 0.0,
        "closure": 0.75,
        "post_checks": 0.0,
    }
    assert _totals(timing) == {
        "create_part": (1, 0.5, 0.5),
        "add_line": (1, 0.25, 1.25),
    }


def test_the_checks_before_and_after_the_call_are_their_own_phases(
    adapter, clock, monkeypatch
) -> None:
    monkeypatch.setattr(adapter, "_refuse_while_busy", lambda _name: clock.spend(0.5))
    monkeypatch.setattr(adapter, "_remember_active_document", lambda: clock.spend(0.25))

    with timed_call("add_line") as timing:
        adapter._handle_com_operation("add_line", lambda: clock.spend(2.0))

    assert timing.phases == {
        "pace": 0.0,
        "pre_checks": 0.5,
        "closure": 2.0,
        "post_checks": 0.25,
    }


def test_an_operation_refused_by_a_check_counts_its_checks(
    adapter, clock, monkeypatch
) -> None:
    busy = AdapterResult(status=AdapterResultStatus.ERROR, error="busy")

    def refuse(_name: str) -> AdapterResult:
        clock.spend(30.0)
        return busy

    monkeypatch.setattr(adapter, "_refuse_while_busy", refuse)

    with timed_call("add_line") as timing:
        result = adapter._handle_com_operation("add_line", lambda: 1)

    assert result is busy
    assert timing.phases["pre_checks"] == 30.0
    assert _totals(timing) == {"add_line": (1, 0.0, 30.0)}


def test_a_failed_operation_is_timed_too(adapter, clock) -> None:
    def fail() -> None:
        clock.spend(0.5)
        raise ValueError("bad")

    with timed_call("open_model") as timing:
        result = adapter._handle_com_operation("open_model", fail)

    assert result.is_error
    assert _totals(timing) == {"open_model": (1, 0.5, 0.5)}


def test_an_operation_inside_another_is_part_of_its_closure(adapter, clock) -> None:
    def outer() -> None:
        clock.spend(0.5)
        adapter._handle_com_operation("get_model_info", lambda: clock.spend(0.25))

    with timed_call("export_stl") as timing:
        adapter._handle_com_operation("export_stl", outer)

    assert _totals(timing) == {"export_stl": (1, 0.75, 0.75)}


def test_after_mode_counts_its_sleep_as_pace(adapter, clock, monkeypatch) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_SETTLE_MODE", "after")

    with timed_call("add_line") as timing:
        adapter._handle_com_operation("add_line", lambda: clock.spend(0.5))

    assert timing.phases == {
        "pace": 0.25,
        "pre_checks": 0.0,
        "closure": 0.5,
        "post_checks": 0.0,
    }


def test_no_timing_is_kept_outside_a_tool_call(adapter, clock) -> None:
    result = adapter._handle_com_operation("add_line", lambda: 7)

    assert result.data == 7
    assert pywin32_adapter._CALL_TIMING.get() is None


def test_a_refused_cross_thread_call_is_not_timed(adapter, clock) -> None:
    adapter._com_thread_id = -1  # some other thread owns COM

    with timed_call("add_line") as timing:
        refused = adapter._handle_com_operation("add_line", lambda: 1)

    assert refused.is_error
    assert timing.operations == {}


def test_the_gap_before_an_adapter_method_counts_as_pace(adapter, clock) -> None:
    adapter._handle_com_operation("close_model", lambda: 1)

    with timed_call("get_model_info") as timing:
        adapter.pace_before_com()
        adapter._handle_com_operation("get_model_info", lambda: clock.spend(0.25))

    assert timing.phases["pace"] == 1.0
    assert _totals(timing) == {"get_model_info": (1, 0.25, 0.25)}


def test_the_gap_outside_a_tool_call_is_not_kept(adapter, clock) -> None:
    adapter._handle_com_operation("close_model", lambda: 1)

    adapter.pace_before_com()

    assert clock.now == 1001.0


# ---- CallTiming and timed_call -------------------------------------------------------


def _timing(tool: str = "export_stl", parent: CallTiming | None = None) -> CallTiming:
    timing = CallTiming(tool, parent)
    timing.add_operation(
        "export_file",
        {"pace": 0.25, "pre_checks": 0.0, "closure": 0.5, "post_checks": 0.25},
    )
    timing.add_operation(
        "set_stl_options",
        {"pace": 0.0, "pre_checks": 0.0, "closure": 0.25, "post_checks": 0.0},
    )
    timing.add_pace(0.5)
    return timing


def test_the_breakdown_lists_the_slowest_operation_first() -> None:
    assert _timing().breakdown(2.0) == {
        "server_s": 2.0,
        "com_operations": 2,
        "pace_s": 0.75,
        "pre_checks_s": 0.0,
        "closure_s": 0.75,
        "post_checks_s": 0.25,
        "other_s": 0.25,
        "operations": {
            "export_file": {"count": 1, "closure_s": 0.5, "total_s": 1.0},
            "set_stl_options": {"count": 1, "closure_s": 0.25, "total_s": 0.25},
        },
    }


def test_time_outside_the_phases_is_never_negative() -> None:
    assert _timing().breakdown(1.0)["other_s"] == 0.0


def test_the_log_line_sums_the_phases_and_names_the_operations() -> None:
    assert _timing().summary(2.0) == (
        "Timing export_stl: 2.000 s; pace 0.750, pre_checks 0.000, closure 0.750, "
        "post_checks 0.250, other 0.250 s; 2 COM operations: export_file x1 "
        "1.000 s, set_stl_options x1 0.250 s"
    )
    assert CallTiming("list_tools").summary(0.001) == (
        "Timing list_tools: 0.001 s; pace 0.000, pre_checks 0.000, closure 0.000, "
        "post_checks 0.000, other 0.001 s; 0 COM operations"
    )


def test_a_call_inside_another_says_so_and_adds_to_it() -> None:
    with timed_call("execute_workflow") as workflow:
        with timed_call("add_circle") as step:
            step.add_operation(
                "add_circle",
                {"pace": 0.25, "pre_checks": 0.0, "closure": 0.25, "post_checks": 0.0},
            )
            assert pywin32_adapter._CALL_TIMING.get() is step
        with timed_call("add_circle") as again:
            again.add_operation(
                "add_circle",
                {"pace": 0.25, "pre_checks": 0.0, "closure": 0.5, "post_checks": 0.0},
            )
        assert pywin32_adapter._CALL_TIMING.get() is workflow

    assert pywin32_adapter._CALL_TIMING.get() is None
    assert step.summary(0.5).startswith("Timing add_circle (in execute_workflow): ")
    assert workflow.phases["closure"] == 0.75
    assert _totals(workflow) == {"add_circle": (2, 0.75, 1.25)}
