"""ComExecutor on the real ``pythoncom``: start/stop, exceptions, one worker thread.

These ran in ``tests/live/test_live_sw_regression.py`` although they never call
SolidWorks: they only need pywin32, for ``CoInitialize`` on the worker thread.
They skip where pywin32 isn't installed.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("pythoncom")


def test_com_executor_start_stop_idempotent() -> None:
    """ComExecutor.start() and stop() can be called repeatedly."""
    from solidworks_mcp.adapters.com_executor import ComExecutor

    ex = ComExecutor(name="test-idempotent")
    ex.start()
    ex.start()  # second call is a no-op
    assert ex._thread is not None and ex._thread.is_alive()
    ex.stop()
    ex.stop()  # safe to call again
    assert ex._thread is None


def test_com_executor_propagates_exceptions() -> None:
    """Exceptions raised inside a submitted callable reach the caller."""
    from solidworks_mcp.adapters.com_executor import ComExecutor

    with ComExecutor(name="test-exc") as ex:
        with pytest.raises(ZeroDivisionError):
            ex.run(lambda: 1 / 0)


def test_com_executor_runs_on_dedicated_thread() -> None:
    """All callables run on the same (non-caller) thread."""
    from solidworks_mcp.adapters.com_executor import ComExecutor

    caller = threading.current_thread().name
    with ComExecutor(name="test-thread") as ex:
        worker_name = ex.run(lambda: threading.current_thread().name)
        worker_name2 = ex.run(lambda: threading.current_thread().name)

    assert worker_name != caller, (
        "executor must run callables on a thread other than the caller"
    )
    assert worker_name == worker_name2, (
        "all callables must run on the same worker thread"
    )
