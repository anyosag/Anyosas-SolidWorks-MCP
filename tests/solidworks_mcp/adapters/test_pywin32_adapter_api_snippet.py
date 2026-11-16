"""The live adapter runs snippets in their own process, through its COM guard.

The snippet runner is faked; no PowerShell, COM or SolidWorks is involved.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import api_snippets, limits
from solidworks_mcp.adapters.solidworks import window as window_module


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    limits.reset()
    yield
    limits.reset()
    for keeper in list(window_module._KEEPERS.values()):
        keeper.stop()
    window_module._KEEPERS.clear()


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> PyWin32Adapter:
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter, "pywintypes", SimpleNamespace(com_error=RuntimeError)
    )
    monkeypatch.setenv("SOLIDWORKS_MCP_DIALOG_TIMEOUT_SECONDS", "0")
    monkeypatch.setenv("SOLIDWORKS_MCP_KEEP_MINIMIZED", "0")
    return PyWin32Adapter({"solidworks_year": 2026})


class FakeRunner:
    """Returns a fixed outcome; records how it was called and checks ``should_stop``."""

    def __init__(self, outcome: dict[str, Any] | Exception) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, float, bool]] = []

    def run(
        self,
        code: str,
        timeout_s: float,
        compile_only: bool,
        should_stop: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((code, timeout_s, compile_only))
        assert should_stop is not None
        should_stop()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _use(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> list[int | None]:
    years: list[int | None] = []

    def for_install(year: int | None = None) -> FakeRunner:
        years.append(year)
        return runner

    monkeypatch.setattr(api_snippets.SnippetRunner, "for_install", for_install)
    return years


async def test_a_run_goes_through_the_com_guard(
    adapter: PyWin32Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner({"ok": True, "stage": "done", "result": 3})
    years = _use(monkeypatch, runner)
    guarded: list[str] = []
    original = adapter._handle_com_operation

    def guard(name: str, operation: Any, *args: Any, **kwargs: Any) -> Any:
        guarded.append(name)
        return original(name, operation, *args, **kwargs)

    monkeypatch.setattr(adapter, "_handle_com_operation", guard)

    result = await adapter.run_api_snippet("return 3;", 20.0)

    assert result.is_success
    assert result.data == {"ok": True, "stage": "done", "result": 3}
    assert guarded == ["run_api_snippet"]
    assert runner.calls == [("return 3;", 20.0, False)]
    assert years == [2026]


async def test_a_failed_run_keeps_its_diagnosis(
    adapter: PyWin32Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = {"ok": False, "stage": "entry", "error": "No entry point", "stdout": "x"}
    _use(monkeypatch, FakeRunner(outcome))

    result = await adapter.run_api_snippet("int x = 1;")

    assert result.is_error
    assert result.error == "API snippet failed (entry): No entry point"
    assert result.data == outcome


async def test_a_run_whose_client_has_gone_is_stopped_inside_the_guard(
    adapter: PyWin32Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner({"ok": True})
    _use(monkeypatch, runner)
    limits.mark_client_gone("test")

    result = await adapter.run_api_snippet("return 1;")

    assert result.is_error
    assert "the MCP client disconnected" in (result.error or "")


async def test_compiling_only_skips_the_com_guard(
    adapter: PyWin32Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner({"ok": True, "stage": "compiled"})
    _use(monkeypatch, runner)

    def no_guard(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("compile_only must not touch SolidWorks")

    monkeypatch.setattr(adapter, "_handle_com_operation", no_guard)

    result = await adapter.run_api_snippet("return 1;", compile_only=True)

    assert result.is_success
    assert result.execution_time is not None
    assert runner.calls == [("return 1;", 60.0, True)]


async def test_compiling_only_stops_when_the_client_goes(
    adapter: PyWin32Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, FakeRunner({"ok": True}))
    limits.mark_client_gone("test")

    result = await adapter.run_api_snippet("return 1;", compile_only=True)

    assert result.is_error
    assert "Stopped running the API snippet" in (result.error or "")


async def test_a_machine_without_the_interop_assemblies(
    adapter: PyWin32Adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(year: int | None = None) -> Any:
        raise api_snippets.SnippetSetupError("no DLLs here")

    monkeypatch.setattr(api_snippets.SnippetRunner, "for_install", missing)

    result = await adapter.run_api_snippet("return 1;")

    assert result.error == "API snippet failed (setup): no DLLs here"
