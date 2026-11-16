"""``open_model_read_only``: ``OpenDoc6`` with Silent | ReadOnly, read back."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks.io import (
    OPEN_READ_ONLY,
    OPEN_SILENT,
    _opened_read_only,
)


def _adapter(monkeypatch: pytest.MonkeyPatch, read_only: Any) -> PyWin32Adapter:
    monkeypatch.setattr(
        "solidworks_mcp.adapters.pywin32_adapter.PYWIN32_AVAILABLE", True
    )
    monkeypatch.setattr(
        "solidworks_mcp.adapters.pywin32_adapter.platform.system", lambda: "Windows"
    )
    monkeypatch.setattr(
        "solidworks_mcp.adapters.pywin32_adapter.pywintypes",
        SimpleNamespace(com_error=RuntimeError),
        raising=False,
    )
    adapter = PyWin32Adapter({})
    com = MagicMock()
    com._thread.is_alive.return_value = True
    com.run = lambda fn, timeout=None: fn()
    adapter._com = com
    doc = SimpleNamespace(
        GetTitle=Mock(return_value="bracket.SLDPRT"),
        GetActiveConfiguration=Mock(return_value=None),
        GetSaveTime=Mock(return_value="now"),
        IsOpenedReadOnly=Mock(return_value=read_only),
    )
    adapter.swApp = SimpleNamespace(
        OpenDoc6=Mock(return_value=doc), ActivateDoc3=Mock(return_value=doc)
    )
    return adapter


def test_the_options_are_the_swconst_values() -> None:
    assert (OPEN_SILENT, OPEN_READ_ONLY) == (1, 2)


@pytest.mark.asyncio
async def test_a_read_only_open_passes_silent_and_read_only(monkeypatch) -> None:
    adapter = _adapter(monkeypatch, True)

    result = await adapter.open_model_read_only("C:/parts/bracket.SLDPRT")

    assert result.is_success, result.error
    assert adapter.swApp.OpenDoc6.call_args.args[2] == 3
    assert result.data.properties["read_only"] is True


@pytest.mark.asyncio
async def test_a_plain_open_stays_silent_and_reports_the_state(monkeypatch) -> None:
    adapter = _adapter(monkeypatch, False)

    result = await adapter.open_model("C:/parts/bracket.SLDPRT")

    assert result.is_success, result.error
    assert adapter.swApp.OpenDoc6.call_args.args[2] == 1
    assert result.data.properties["read_only"] is False


@pytest.mark.asyncio
async def test_a_state_that_cannot_be_read_is_none(monkeypatch) -> None:
    adapter = _adapter(monkeypatch, "maybe")

    result = await adapter.open_model_read_only("C:/parts/bracket.SLDPRT")

    assert result.data.properties["read_only"] is None


def test_opened_read_only_reads_a_property_or_a_method() -> None:
    adapter = SimpleNamespace(
        _attempt=lambda fn, default=None: fn(),
        _get_attr_or_call=lambda obj, name: getattr(obj, name),
    )

    assert _opened_read_only(adapter, SimpleNamespace(IsOpenedReadOnly=True)) is True
    assert _opened_read_only(adapter, SimpleNamespace(IsOpenedReadOnly=0)) is None


def test_the_method_lives_on_the_io_mixin() -> None:
    owner = next(
        k.__name__ for k in PyWin32Adapter.__mro__ if "open_model_read_only" in vars(k)
    )

    assert owner == "SolidWorksIOMixin"
