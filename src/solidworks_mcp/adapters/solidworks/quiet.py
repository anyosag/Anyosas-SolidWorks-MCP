"""SolidWorks event sounds off while the server tries a feature it may throw away.

A cut left without a direction is built on one side, measured, deleted and
built on the other; the side with no material is a rebuild error, and
SolidWorks plays its ``SLDWORKS_RebuildError`` sound for it although the
server deletes that trial at once. A build session of ``execute_workflow``
batches heard it over and over.

The sounds come from the system option "Enable sounds for SolidWorks events",
``swUserPreferenceToggle_e.swEnableSoundsForSolidWorksEvents`` (toggle 515).
:func:`sounds_off` turns it off for one trial window and puts the user's
value back afterwards, even when the window fails, as
``rectangle_check.inference_off`` does for sketch inference and
``stl_settings`` for the STL options:

* nothing is touched when the toggle can't be read, or is already off;
* nesting is safe: an inner window reads the toggle off and leaves it, so
  only the window that turned it off turns it back on;
* ``SOLIDWORKS_MCP_QUIET_TRIALS=0`` (or ``false``/``no``/``off``) leaves the
  sounds alone.

Only the trial windows are wrapped (the build-measure-delete of a cut's side,
an Indent tried before shifted copies), never a whole tool call, so a real
failure still sounds as the user set it.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger

from .com_utils import attempt as _attempt
from .com_utils import flag as _flag

#: ``swUserPreferenceToggle_e.swEnableSoundsForSolidWorksEvents``.
EVENT_SOUNDS = 515

#: The environment variable that turns the muting off.
QUIET_TRIALS_ENV = "SOLIDWORKS_MCP_QUIET_TRIALS"

_FALSY = {"0", "false", "no", "off"}


def enabled() -> bool:
    """Whether trial windows mute the event sounds (on unless the env var says no)."""
    return os.getenv(QUIET_TRIALS_ENV, "1").strip().lower() not in _FALSY


def _mute(adapter: Any) -> bool:
    """Turn the event sounds off when they are on; True when this did it."""
    app = getattr(adapter, "swApp", None)
    if app is None:
        return False
    _flag(adapter, app, "GetUserPreferenceToggle", "SetUserPreferenceToggle")
    was = _attempt(adapter, lambda: app.GetUserPreferenceToggle(EVENT_SOUNDS))
    if was is not True:
        # Off already (an outer window, or the user), or unreadable.
        return False
    _, error = adapter._attempt_with_error(
        lambda: app.SetUserPreferenceToggle(EVENT_SOUNDS, False)
    )
    return error is None


def _unmute(adapter: Any) -> None:
    """Put the user's event sounds back on."""
    app = adapter.swApp
    _, error = adapter._attempt_with_error(
        lambda: app.SetUserPreferenceToggle(EVENT_SOUNDS, True)
    )
    if error is not None:
        logger.warning(
            "Could not turn SolidWorks event sounds back on after a trial "
            f"feature: {error}. Tools > Options > System Options > General > "
            "'Enable sounds for SolidWorks events' turns them on."
        )


def _through_operation(adapter: Any, name: str, step: Callable[[], Any]) -> Any:
    """Run ``step`` as its own COM operation, so it waits out the settle gap.

    Returns:
        Any: What ``step`` returned, or None when the operation was refused.
    """
    result = adapter._handle_com_operation(name, step)
    return result.data if result.is_success else None


@contextmanager
def sounds_off(adapter: Any, *, own_operation: bool = False) -> Iterator[None]:
    """SolidWorks event sounds off for one trial window, the user's value after.

    Args:
        adapter: A connected adapter (``swApp``).
        own_operation: True between COM operations (the async layer): the
            toggle is read and set through ``_handle_com_operation`` so the
            thread, busy and reconnect checks run. False inside one (a COM
            closure), where it is called directly.

    Yields:
        None: The window; the sounds are back as they were when it ends,
        whether it returned or raised.
    """
    if not enabled():
        yield
        return
    if own_operation:
        muted = bool(
            _through_operation(adapter, "mute_trial_sounds", lambda: _mute(adapter))
        )
    else:
        muted = _mute(adapter)
    try:
        yield
    finally:
        if muted:
            restored = own_operation and _through_operation(
                adapter, "restore_trial_sounds", lambda: _unmute(adapter) or True
            )
            if not restored:
                # Inside an operation, or the restore operation was refused
                # (busy, another document active): never leave them off.
                _unmute(adapter)
