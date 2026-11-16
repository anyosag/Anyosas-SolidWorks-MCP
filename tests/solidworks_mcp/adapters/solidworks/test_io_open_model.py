"""``open_model`` when SolidWorks shows a dialog while the file opens.

The Lid build (SolidWorks 2026 SP4.1) opened an assembly; SOLIDWORKS CAM
showed a dialog about lightweight parts, the watchdog cancelled it, and the
call came back as ``solidworks_dialog``, although the assembly and its four
parts were open. A document that is open after such a dialog now opens as
usual, with a ``warning`` quoting the dialog.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters import pywin32_adapter
from solidworks_mcp.adapters.pywin32_adapter import PyWin32Adapter
from solidworks_mcp.adapters.solidworks import io
from solidworks_mcp.adapters.solidworks import window as window_module
from solidworks_mcp.adapters.solidworks.dialogs import DialogInfo

RACK = os.path.abspath("C:/work/Rack.SLDASM")

_CAM = DialogInfo(
    hwnd=7,
    title="SOLIDWORKS CAM",
    texts=["Some components are lightweight.", "Resolve them to machine."],
    buttons=["OK"],
    actions=["cancel"],
    closed=True,
)


@pytest.fixture(autouse=True)
def _no_keepers() -> Iterator[None]:
    yield
    for keeper in list(window_module._KEEPERS.values()):
        keeper.stop()
    window_module._KEEPERS.clear()


class _Watchdog:
    """Says SolidWorks showed ``dialogs`` during the first operation only."""

    def __init__(self, dialogs: list[DialogInfo]) -> None:
        self._dialogs = list(dialogs)

    def disarm(self) -> list[DialogInfo]:
        dialogs, self._dialogs = self._dialogs, []
        return dialogs


class _Rack:
    """The assembly document, open at ``path``."""

    def __init__(self, path: str = RACK) -> None:
        self.path = path

    def GetPathName(self) -> str:  # noqa: N802 - COM name
        return self.path

    def GetActiveConfiguration(self) -> Any:  # noqa: N802 - COM name
        return SimpleNamespace(GetName=lambda: "Default")


class _App:
    def __init__(self, returned: Any, open_now: Any) -> None:
        self._returned, self._open_now = returned, open_now
        self.asked_for: list[str] = []

    def OpenDoc6(self, *_args: Any) -> Any:  # noqa: N802 - COM name
        return self._returned

    def GetOpenDocumentByName(self, name: str) -> Any:  # noqa: N802 - COM name
        self.asked_for.append(name)
        return self._open_now


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    returned: Any,
    open_now: Any,
    dialogs: list[DialogInfo] | None = None,
) -> PyWin32Adapter:
    monkeypatch.setattr(pywin32_adapter, "PYWIN32_AVAILABLE", True)
    monkeypatch.setattr(pywin32_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        pywin32_adapter, "pywintypes", SimpleNamespace(com_error=RuntimeError)
    )
    monkeypatch.setenv("SOLIDWORKS_MCP_KEEP_MINIMIZED", "0")
    monkeypatch.setattr(io, "activate_doc", lambda *_args: None)
    adapter = PyWin32Adapter({})
    adapter.swApp = _App(returned, open_now)
    adapter.constants = {"swDocPART": 1, "swDocASSEMBLY": 2, "swDocDRAWING": 3}
    monkeypatch.setattr(adapter, "is_connected", lambda: True)
    monkeypatch.setattr(adapter, "_read_model_title", lambda _model: "Rack.SLDASM")
    watchdog = _Watchdog([_CAM] if dialogs is None else dialogs)
    monkeypatch.setattr(adapter, "_arm_dialog_watchdog", lambda: watchdog)
    return adapter


@pytest.mark.parametrize("returned", [_Rack(), None], ids=["returned", "null"])
async def test_a_document_open_after_a_cancelled_dialog_opens(
    monkeypatch: pytest.MonkeyPatch, returned: Any
) -> None:
    """Whether OpenDoc6 handed the document back or not, it is open."""
    rack = _Rack()
    adapter = _adapter(monkeypatch, returned, rack)

    result = await adapter.open_model(RACK)

    assert result.is_success, result.error
    assert result.data.type == "Assembly"
    assert result.data.name == "Rack.SLDASM"
    assert adapter.currentModel is rack
    assert adapter.swApp.asked_for == [RACK]
    warning = result.metadata["warning"]
    assert warning.startswith(
        "SolidWorks showed a dialog while opening the file ('SOLIDWORKS CAM': "
        "Some components are lightweight. Resolve them to machine.); the server "
        "cancelled it, and the document is open."
    )
    assert result.metadata["dialog"]["title"] == "SOLIDWORKS CAM"


@pytest.mark.parametrize(
    "open_now",
    [None, _Rack(os.path.abspath("C:/other/Rack.SLDASM"))],
    ids=["not-open", "another-file"],
)
async def test_a_document_that_did_not_open_keeps_the_dialog_error(
    monkeypatch: pytest.MonkeyPatch, open_now: Any
) -> None:
    result = await _adapter(monkeypatch, None, open_now).open_model(RACK)

    assert result.is_error
    assert "SolidWorks showed a dialog during open_model" in (result.error or "")
    assert result.data["dialog"]["title"] == "SOLIDWORKS CAM"


async def test_a_dialog_handed_to_the_user_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stuck = DialogInfo(hwnd=8, title="", texts=[], handed_to_user=True)

    result = await _adapter(monkeypatch, _Rack(), _Rack(), [stuck]).open_model(RACK)

    assert result.is_success
    assert (
        "(no text); the server could not cancel it and brought SolidWorks up"
        in (result.metadata["warning"])
    )


async def test_without_a_dialog_nothing_is_looked_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch, _Rack(), _Rack(), dialogs=[])

    result = await adapter.open_model(RACK)

    assert result.is_success
    assert result.metadata is None
    assert adapter.swApp.asked_for == []


async def test_other_errors_are_not_looked_into(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch, None, _Rack(), dialogs=[])

    result = await adapter.open_model(RACK)

    assert result.is_error
    assert "Failed to open model" in (result.error or "")
    assert adapter.swApp.asked_for == []


# ---- the tools pass the warning on --------------------------------------------


async def _tool(adapter: Any, module: str, name: str) -> Any:
    mcp = FastMCP("open-after-dialog")
    if module == "modeling":
        from solidworks_mcp.tools.modeling import register_modeling_tools

        await register_modeling_tools(mcp, adapter, {})
    else:
        from solidworks_mcp.tools.file_management import (
            register_file_management_tools,
        )

        await register_file_management_tools(mcp, adapter, {})
    return next(t.fn for t in await mcp.list_tools() if t.name == name)


@pytest.mark.parametrize(
    ("module", "name"),
    [("modeling", "open_model"), ("file_management", "load_assembly")],
)
async def test_the_tools_reply_with_the_warning(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str
) -> None:
    adapter = _adapter(monkeypatch, None, _Rack())
    tool = await _tool(adapter, module, name)

    reply = await tool({"file_path": RACK})

    assert reply["status"] == "success", reply
    assert "SOLIDWORKS CAM" in reply["warning"]


async def test_a_part_loaded_without_a_dialog_has_no_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch, _Rack("C:/work/Lid.SLDPRT"), None, dialogs=[])
    tool = await _tool(adapter, "file_management", "load_part")

    reply = await tool({"file_path": "C:/work/Lid.SLDPRT"})

    assert reply["status"] == "success", reply
    # Only the tool's own support-level note, if any.
    assert "dialog" not in reply.get("warning", "")
