"""Reading a task dialog's text with UI Automation (dialogs.read_accessible)."""

from __future__ import annotations

import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from solidworks_mcp.adapters.solidworks import dialogs
from solidworks_mcp.adapters.solidworks.dialogs import (
    UIA_BUTTON,
    UIA_TEXT,
    DialogWatchdog,
    accessible_names,
    describe_dialog,
    read_accessible,
)
from solidworks_mcp.adapters.solidworks.window import SolidWorksWindow

from .fake_user32 import FakeUser32

SW_PID = 9396
FRAME = 0x1000
DIALOG = 0x3000

#: What the "SOLIDWORKS Design" box of a Save Bodies call might say.
QUESTION = "Do you want to create an assembly of the saved bodies?"


def _task_dialog(user32: FakeUser32, *, buttons: bool = False) -> None:
    user32.add(
        DIALOG, SW_PID, title="SOLIDWORKS Design", class_name="#32770", owner=FRAME
    )
    user32.add(DIALOG + 1, SW_PID, class_name="DirectUIHWND", parent=DIALOG)
    if buttons:
        user32.add(DIALOG + 2, SW_PID, class_name="Button", parent=DIALOG, title="&Yes")


class _Element:
    def __init__(self, name: object, kind: int) -> None:
        self.CurrentName = name
        self.CurrentControlType = kind


class _Found:
    def __init__(self, elements: list[_Element]) -> None:
        self._elements = elements
        self.Length = len(elements)

    def GetElement(self, index: int) -> _Element:
        return self._elements[index]


class _Automation:
    """An IUIAutomation whose tree is ``elements``."""

    def __init__(self, elements: list[_Element]) -> None:
        self.elements = elements
        self.asked: list[int] = []

    def CreateTrueCondition(self) -> str:
        return "true"

    def ElementFromHandle(self, hwnd: int) -> SimpleNamespace:
        self.asked.append(hwnd)
        return SimpleNamespace(
            FindAll=lambda scope, condition: (
                _Found(self.elements)
                if (scope, condition) == (dialogs.UIA_DESCENDANTS, "true")
                else _Found([])
            )
        )


def test_accessible_names_keeps_texts_and_buttons_once() -> None:
    automation = _Automation(
        [
            _Element("SOLIDWORKS Design", UIA_TEXT),
            _Element(QUESTION, UIA_TEXT),
            _Element(QUESTION, UIA_TEXT),
            _Element("  ", UIA_TEXT),
            _Element(None, UIA_TEXT),
            _Element("&Yes", UIA_BUTTON),
            _Element("No", UIA_BUTTON),
            _Element("Pane", 50033),
            _Element("Details", dialogs.UIA_HYPERLINK),
        ]
    )

    texts, buttons = accessible_names(automation, DIALOG)

    assert automation.asked == [DIALOG]
    assert texts == ["SOLIDWORKS Design", QUESTION, "Details"]
    assert buttons == ["Yes", "No"]


def test_a_task_dialog_is_read_through_ui_automation() -> None:
    user32 = FakeUser32()
    _task_dialog(user32)
    asked: list[int] = []

    def reader(hwnd: int) -> tuple[list[str], list[str]]:
        asked.append(hwnd)
        return ["SOLIDWORKS Design", QUESTION], ["Yes", "No"]

    info = describe_dialog(user32, DIALOG, reader)

    assert asked == [DIALOG]
    assert info.task_dialog is True
    assert info.texts == [QUESTION]  # the title isn't repeated as text
    assert info.buttons == ["Yes", "No"]
    assert info.text_source == "ui_automation"
    assert info.summary() == f"'SOLIDWORKS Design': {QUESTION}"
    assert info.to_dict()["text_source"] == "ui_automation"


def test_win32_buttons_are_kept_and_an_unreadable_dialog_says_so() -> None:
    user32 = FakeUser32()
    _task_dialog(user32, buttons=True)

    read = describe_dialog(user32, DIALOG, lambda _h: ([QUESTION], ["Yes", "No"]))
    nothing = describe_dialog(user32, DIALOG, lambda _h: None)
    blank = describe_dialog(user32, DIALOG, lambda _h: ([], ["Yes"]))
    win32_only = describe_dialog(user32, DIALOG)

    assert read.buttons == ["Yes"]  # the Win32 caption wins
    assert (nothing.texts, nothing.text_source) == ([], None)
    assert (blank.texts, blank.text_source) == ([], None)
    assert win32_only.text_source is None


def test_a_dialog_with_readable_controls_is_not_read_again() -> None:
    user32 = FakeUser32()
    user32.add(DIALOG, SW_PID, class_name="#32770", owner=FRAME)
    user32.add(DIALOG + 1, SW_PID, class_name="DirectUIHWND", parent=DIALOG)
    user32.add(DIALOG + 2, SW_PID, class_name="Static", parent=DIALOG, title="Hi")

    def reader(_hwnd: int) -> None:
        raise AssertionError("not needed")

    info = describe_dialog(user32, DIALOG, reader)

    assert (info.texts, info.text_source) == (["Hi"], "controls")


def test_read_accessible_runs_on_its_own_thread_and_gives_up_in_time() -> None:
    caller = threading.get_ident()
    seen: list[int] = []

    def reader(hwnd: int) -> tuple[list[str], list[str]]:
        seen.append(threading.get_ident())
        return [str(hwnd)], []

    release = threading.Event()

    def stuck(_hwnd: int) -> tuple[list[str], list[str]]:
        release.wait(2)
        return ["late"], []

    assert read_accessible(7, reader=reader) == (["7"], [])
    assert seen and seen[0] != caller
    assert read_accessible(7, timeout=0.01, reader=stuck) is None
    release.set()


def test_the_watchdog_reads_a_task_dialog_before_cancelling_it() -> None:
    user32 = FakeUser32()
    user32.add(FRAME, SW_PID, title="SOLIDWORKS", enabled=False)
    _task_dialog(user32)
    now = [0.0]
    watchdog = DialogWatchdog(
        SolidWorksWindow(FRAME, user32),
        5,
        user32=user32,
        clock=lambda: now[0],
        read_text=lambda _h: ([QUESTION], ["Yes", "No"]),
    )

    watchdog.check()
    now[0] = 6.0
    watchdog.check()
    (info,) = watchdog.disarm()

    assert info.texts == [QUESTION]
    assert info.buttons == ["Yes", "No"]
    assert info.actions == ["cancel"]


def _fake_comtypes(monkeypatch: pytest.MonkeyPatch, automation: object) -> list[str]:
    """Install a ``comtypes`` stand-in; returns the calls it saw, in order."""
    calls: list[str] = []
    comtypes = ModuleType("comtypes")
    client = ModuleType("comtypes.client")
    uia = ModuleType("comtypes.gen.UIAutomationClient")
    comtypes.COINIT_MULTITHREADED = 0  # type: ignore[attr-defined]
    comtypes.CoInitializeEx = lambda flags: calls.append(f"init {flags}")  # type: ignore[attr-defined]
    comtypes.CoUninitialize = lambda: calls.append("uninit")  # type: ignore[attr-defined]
    client.GetModule = lambda name: calls.append(name)  # type: ignore[attr-defined]

    def create(clsid: object, interface: object) -> object:
        calls.append(f"create {clsid} {interface}")
        if isinstance(automation, Exception):
            raise automation
        return automation

    client.CreateObject = create  # type: ignore[attr-defined]
    uia.CUIAutomation = "CUIAutomation"  # type: ignore[attr-defined]
    uia.IUIAutomation = "IUIAutomation"  # type: ignore[attr-defined]
    comtypes.client = client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "comtypes.client", client)
    monkeypatch.setitem(sys.modules, "comtypes.gen.UIAutomationClient", uia)
    return calls


def test_uia_read_uses_its_own_apartment(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_comtypes(monkeypatch, _Automation([_Element(QUESTION, UIA_TEXT)]))

    assert dialogs._uia_read(DIALOG) == ([QUESTION], [])
    assert calls == [
        "init 0",
        "UIAutomationCore.dll",
        "create CUIAutomation IUIAutomation",
        "uninit",
    ]


def test_uia_read_failures_give_none(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_comtypes(monkeypatch, OSError("no UI Automation"))

    assert dialogs._uia_read(DIALOG) is None
    assert calls[-1] == "uninit"

    monkeypatch.setitem(sys.modules, "comtypes", None)
    assert dialogs._uia_read(DIALOG) is None


def test_uia_read_on_a_thread_that_has_an_apartment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _fake_comtypes(monkeypatch, _Automation([_Element(QUESTION, UIA_TEXT)]))

    def changed_mode(_flags: int) -> None:
        raise OSError(-2147417850, "Cannot change thread mode after it is set")

    sys.modules["comtypes"].CoInitializeEx = changed_mode  # type: ignore[attr-defined]

    assert dialogs._uia_read(DIALOG) == ([QUESTION], [])
    assert "uninit" not in calls  # the thread's own apartment is left alone
