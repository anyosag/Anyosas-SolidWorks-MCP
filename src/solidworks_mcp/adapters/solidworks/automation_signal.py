"""``Local\\StageManager.Automation.<pid>``: held while the server drives SolidWorks.

The user's Stage Manager, a window manager on this PC that parks apps in a
side strip, keeps SolidWorks off its stage while this named event exists.
Then only SolidWorks' own card, its taskbar button and the Alt+Tab, Win+Tab
and Win+number keys bring it up, even with Explorer, Start or the desktop in
front. Without it, a tool call that brings SolidWorks up (``NewDocument``,
``OpenDoc6``, ``ActivateDoc3``) while the user is in Explorer can land it on
the stage. Agreed with Stage Manager's author (its H71, 2026-11-22):

* the name is ``Local\\StageManager.Automation.`` and the SOLIDWORKS process
  id in decimal (:func:`event_name`);
* a manual-reset event, created signalled at the start of each outermost
  operation (``CreateEventW``); its existence is the signal;
* closed :data:`GRACE_SECONDS` after the operation ends, as SolidWorks can
  still bring itself up just after a call (the minimize keeper allows the same
  grace);
* every MCP server on one SolidWorks opens the same name, so the event exists
  while any of them is driving it.

Nothing in SolidWorks changes and no window is touched: Stage Manager releases
a parked window whose styles or visibility change. Win32 only (``kernel32``),
off with ``SOLIDWORKS_MCP_AUTOMATION_SIGNAL=0``. A failure is logged at debug
level and never fails a call.
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

from loguru import logger

#: The event's name, before the SOLIDWORKS process id.
PREFIX = "Local\\StageManager.Automation."
#: Seconds the event outlives the operation that held it.
GRACE_SECONDS = 2.0
_ENV = "SOLIDWORKS_MCP_AUTOMATION_SIGNAL"
_FALSY = frozenset({"0", "false", "no", "off"})


def enabled() -> bool:
    """Whether the event is held: on unless ``SOLIDWORKS_MCP_AUTOMATION_SIGNAL`` is off."""
    return os.getenv(_ENV, "1").strip().lower() not in _FALSY


def event_name(pid: int) -> str:
    """The event's name for SOLIDWORKS process ``pid``."""
    return f"{PREFIX}{int(pid)}"


def load_kernel32(platform: str | None = None, loader: Any = None) -> Any | None:
    """``kernel32`` with ``CreateEventW`` and ``CloseHandle`` typed, or ``None`` off Windows.

    Args:
        platform: ``sys.platform`` (tests pass another value).
        loader: ``ctypes.WinDLL`` (tests pass a fake).
    """
    if (platform or sys.platform) != "win32":
        return None
    dll = (loader or ctypes.WinDLL)("kernel32", use_last_error=True)
    dll.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    dll.CreateEventW.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    return dll


def _start_timer(seconds: float, action: Callable[[], None]) -> threading.Timer:
    """A daemon ``threading.Timer`` running ``action`` after ``seconds``, started."""
    timer = threading.Timer(seconds, action)
    timer.daemon = True
    timer.start()
    return timer


class AutomationSignal:
    """Holds the event through an operation and :data:`GRACE_SECONDS` after it.

    Args:
        kernel32: The DLL (tests pass a fake); loaded on first use otherwise.
        grace_seconds: How long the event outlives an operation.
        timer: Starts a timer: ``timer(seconds, action)`` returns an object
            with ``cancel()`` (tests pass a fake).
        loader: Loads ``kernel32`` when none was given.
    """

    def __init__(
        self,
        kernel32: Any = None,
        *,
        grace_seconds: float = GRACE_SECONDS,
        timer: Callable[[float, Callable[[], None]], Any] | None = None,
        loader: Callable[[], Any] | None = None,
    ) -> None:
        """Hold nothing yet."""
        self._kernel32 = kernel32
        self._loader = loader or load_kernel32
        self._grace = grace_seconds
        self._timer = timer or _start_timer
        self._lock = threading.Lock()
        self._handle: Any = None
        self._pid = 0
        self._closing: Any = None
        self._token: object | None = None

    @property
    def held(self) -> bool:
        """Whether the event is held now."""
        return self._handle is not None

    def begin(self, pid: int) -> None:
        """An operation on SOLIDWORKS process ``pid`` starts: hold the event."""
        if not pid or not enabled():
            return
        with self._lock:
            self._cancel_close()
            if self._handle is not None and self._pid == pid:
                return
            self._close_now()
            if self._kernel32 is None:
                self._kernel32 = self._loader()
            if self._kernel32 is None:
                return
            name = event_name(pid)
            try:
                handle = self._kernel32.CreateEventW(None, True, True, name)
            except Exception as exc:  # noqa: BLE001 - a signal never fails a call
                logger.debug(f"Could not create {name}: {exc}")
                return
            if not handle:
                logger.debug(f"CreateEventW gave no handle for {name}")
                return
            self._handle, self._pid = handle, pid

    def end(self) -> None:
        """The operation ended: close the event after the grace period."""
        with self._lock:
            if self._handle is None:
                return
            self._cancel_close()
            token = object()
            self._token = token
            self._closing = self._timer(
                self._grace, lambda: self._close_after_grace(token)
            )

    def close(self) -> None:
        """Close the event now (on disconnect)."""
        with self._lock:
            self._cancel_close()
            self._close_now()

    def _close_after_grace(self, token: object) -> None:
        """The timer's action: close, unless a later call took the event over."""
        with self._lock:
            if token is not self._token:
                return
            self._closing, self._token = None, None
            self._close_now()

    def _cancel_close(self) -> None:
        if self._closing is not None:
            self._closing.cancel()
        self._closing, self._token = None, None

    def _close_now(self) -> None:
        handle, self._handle, self._pid = self._handle, None, 0
        if handle is None:
            return
        try:
            self._kernel32.CloseHandle(handle)
        except Exception as exc:  # noqa: BLE001 - a signal never fails a call
            logger.debug(f"Could not close the automation event: {exc}")
