"""``quiet.sounds_off``: event sounds off for a trial window, the user's value after."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import quiet


class _App:
    """``ISldWorks`` with the event-sounds toggle, recording every write."""

    def __init__(self, sounds: Any = True) -> None:
        self.sounds = sounds
        self.writes: list[bool] = []
        self.refuse_writes = False
        self.refuse_reads = False

    def GetUserPreferenceToggle(self, pref: int) -> Any:  # noqa: N802
        assert pref == quiet.EVENT_SOUNDS
        if self.refuse_reads:
            raise RuntimeError("no such preference")
        return self.sounds

    def SetUserPreferenceToggle(self, pref: int, value: bool) -> None:  # noqa: N802
        assert pref == quiet.EVENT_SOUNDS
        if self.refuse_writes:
            raise RuntimeError("refused")
        self.writes.append(value)
        self.sounds = value


class _Adapter:
    def __init__(self, app: _App | None, refuse: tuple[str, ...] = ()) -> None:
        self.swApp = app
        self.operations: list[str] = []
        self.refuse = refuse

    def _handle_com_operation(self, name: str, callback: Any) -> AdapterResult[Any]:
        self.operations.append(name)
        if name in self.refuse:
            return AdapterResult(status=AdapterResultStatus.ERROR, error="busy")
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _attempt_with_error(self, callback: Any) -> tuple[Any, Exception | None]:
        try:
            return callback(), None
        except Exception as exc:
            return None, exc


@pytest.fixture(autouse=True)
def _quiet_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(quiet.QUIET_TRIALS_ENV, raising=False)


@pytest.mark.parametrize("own_operation", [False, True])
def test_the_sounds_are_off_inside_and_back_after(own_operation: bool) -> None:
    app = _App()
    adapter = _Adapter(app)

    with quiet.sounds_off(adapter, own_operation=own_operation):
        assert app.sounds is False

    assert app.sounds is True
    assert app.writes == [False, True]
    expected = ["mute_trial_sounds", "restore_trial_sounds"] if own_operation else []
    assert adapter.operations == expected


@pytest.mark.parametrize("own_operation", [False, True])
def test_the_sounds_come_back_when_the_window_fails(own_operation: bool) -> None:
    app = _App()

    with pytest.raises(RuntimeError, match="trial failed"):
        with quiet.sounds_off(_Adapter(app), own_operation=own_operation):
            raise RuntimeError("trial failed")

    assert app.sounds is True


def test_an_inner_window_leaves_them_off_until_the_outer_ends() -> None:
    app = _App()
    adapter = _Adapter(app)

    with quiet.sounds_off(adapter, own_operation=True):
        with quiet.sounds_off(adapter):
            assert app.sounds is False
        assert app.sounds is False  # the inner one did not restore early
    assert app.sounds is True
    assert app.writes == [False, True]


@pytest.mark.parametrize("value", [False, None, 1])
def test_sounds_the_user_has_off_or_unreadable_are_not_touched(value: Any) -> None:
    app = _App(sounds=value)

    with quiet.sounds_off(_Adapter(app)):
        pass

    assert app.writes == []
    assert app.sounds == value


def test_a_read_that_raises_touches_nothing() -> None:
    app = _App()
    app.refuse_reads = True

    with quiet.sounds_off(_Adapter(app)):
        pass

    assert app.writes == []


def test_no_application_touches_nothing() -> None:
    adapter = _Adapter(None)

    with quiet.sounds_off(adapter):
        pass


def test_a_refused_mute_is_not_restored() -> None:
    app = _App()
    app.refuse_writes = True

    with quiet.sounds_off(_Adapter(app)):
        assert app.sounds is True

    assert app.writes == []


def test_a_refused_restore_operation_restores_directly() -> None:
    app = _App()
    adapter = _Adapter(app, refuse=("restore_trial_sounds",))

    with quiet.sounds_off(adapter, own_operation=True):
        assert app.sounds is False

    assert app.sounds is True


def test_a_refused_mute_operation_changes_nothing() -> None:
    app = _App()
    adapter = _Adapter(app, refuse=("mute_trial_sounds",))

    with quiet.sounds_off(adapter, own_operation=True):
        assert app.sounds is True

    assert app.writes == []
    assert adapter.operations == ["mute_trial_sounds"]


def test_a_restore_that_fails_is_logged() -> None:
    from loguru import logger

    app = _App()
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        with quiet.sounds_off(_Adapter(app)):
            app.refuse_writes = True
    finally:
        logger.remove(sink)

    assert app.sounds is False
    assert any("event sounds back on" in m for m in messages)


@pytest.mark.parametrize("value", ["0", "false", "No", " off "])
def test_the_env_var_turns_the_muting_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(quiet.QUIET_TRIALS_ENV, value)
    app = _App()
    adapter = _Adapter(app)

    with quiet.sounds_off(adapter, own_operation=True):
        assert app.sounds is True

    assert app.writes == []
    assert adapter.operations == []
    assert quiet.enabled() is False


def test_the_env_var_set_on_keeps_it_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(quiet.QUIET_TRIALS_ENV, "1")

    assert quiet.enabled() is True
