"""Hiding and showing assembly components (``component_visibility``) on fakes.

The Dipper session moved its cover 60 mm sideways and back to render the part
"cover off"; these are the calls that hide it instead.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import component_visibility as cv


class _Component:
    """An ``IComponent2``: ``Visible`` takes (or ignores) a new state."""

    def __init__(self, name: str, visible: int = 1, takes_visible: bool = True) -> None:
        self.Name2 = name
        self._visible = visible
        self.takes_visible = takes_visible
        self.selected = False

    @property
    def Visible(self) -> int:  # noqa: N802 - COM name
        return self._visible

    @Visible.setter
    def Visible(self, value: int) -> None:  # noqa: N802 - COM name
        if not self.takes_visible:
            raise RuntimeError("Member not found")
        self._visible = value

    def Select4(self, _append: bool, _data: Any, _callout: bool) -> bool:  # noqa: N802
        self.selected = True
        return True


class _Assembly:
    """An assembly document whose Hide/ShowComponent2 act on the selection."""

    def __init__(self, components: list[_Component], hide_works: bool = True) -> None:
        self.components = components
        self.hide_works = hide_works
        self.calls: list[str] = []
        self.SelectionManager = SimpleNamespace(CreateSelectData=lambda: "data")

    def GetComponents(self, top_level_only: bool) -> list[_Component]:  # noqa: N802
        assert top_level_only is False
        return self.components

    def ClearSelection2(self, _all: bool) -> None:  # noqa: N802
        self.calls.append("clear")
        for component in self.components:
            component.selected = False

    def _act(self, state: int) -> None:
        for component in self.components:
            if component.selected and self.hide_works:
                component._visible = state

    def HideComponent2(self) -> None:  # noqa: N802
        self.calls.append("HideComponent2")
        self._act(cv.HIDDEN)

    def ShowComponent2(self) -> None:  # noqa: N802
        self.calls.append("ShowComponent2")
        self._act(cv.VISIBLE)

    def GraphicsRedraw2(self) -> None:  # noqa: N802
        self.calls.append("redraw")


class _Adapter:
    def _attempt(self, fn: Any, default: Any = None) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        value = getattr(obj, name)
        return value() if callable(value) else value


@pytest.fixture(autouse=True)
def _plain_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cv, "_as_com", lambda _adapter, obj, _iface: obj)


def _assembly(**kwargs: Any) -> _Assembly:
    return _Assembly(
        [
            _Component("base-1"),
            _Component("cover-1", **kwargs),
            _Component("hinge-1/pin-1"),
        ]
    )


def test_components_are_found_at_every_level() -> None:
    model = _assembly()

    found = cv.find(_Adapter(), model, ["hinge-1/pin-1", "cover-1"])

    assert list(found) == ["hinge-1/pin-1", "cover-1"]
    assert found["cover-1"] is model.components[1]


def test_a_missing_component_lists_the_top_level_ones() -> None:
    with pytest.raises(LookupError) as raised:
        cv.find(_Adapter(), _assembly(), ["lid-1"])

    assert "Component(s) not found: lid-1" in str(raised.value)
    assert "base-1, cover-1 (list_components" in str(raised.value)


def test_unreadable_components_are_an_error() -> None:
    model = _assembly()
    model.GetComponents = lambda _top: None  # type: ignore[method-assign]

    with pytest.raises(LookupError, match="can't be read"):
        cv.find(_Adapter(), model, ["cover-1"])


def test_a_component_is_hidden_through_visible() -> None:
    model = _assembly()
    cover = model.components[1]

    state = cv.set_one(_Adapter(), model, "cover-1", cover, False)

    assert state == {
        "name": "cover-1",
        "before": True,
        "after": False,
        "method": "Visible",
    }
    assert cover.Visible == cv.HIDDEN
    assert "HideComponent2" not in model.calls


def test_when_visible_is_refused_the_tree_hide_is_used() -> None:
    model = _assembly(takes_visible=False)
    cover = model.components[1]

    state = cv.set_one(_Adapter(), model, "cover-1", cover, False)

    assert state["method"] == "HideComponent2"
    assert state["after"] is False
    assert model.calls == ["clear", "HideComponent2", "clear"]
    assert cover.selected is False


def test_a_component_already_hidden_is_left_alone() -> None:
    model = _assembly(visible=0)

    state = cv.set_one(_Adapter(), model, "cover-1", model.components[1], False)

    assert state == {"name": "cover-1", "before": False, "after": False, "method": None}


def test_a_component_solidworks_keeps_is_an_error() -> None:
    model = _Assembly([_Component("cover-1", takes_visible=False)], hide_works=False)

    with pytest.raises(RuntimeError, match="kept cover-1 shown"):
        cv.set_one(_Adapter(), model, "cover-1", model.components[0], False)


def test_an_unknown_state_is_none() -> None:
    adapter = _Adapter()

    assert cv.is_visible(adapter, _Component("x", visible=-1)) is None
    assert cv.is_visible(adapter, SimpleNamespace(Visible="?")) is None
    assert cv.is_visible(adapter, _Component("x", visible=1)) is True


def test_apply_records_each_one_and_restore_puts_them_back() -> None:
    model = _assembly()
    found = cv.find(_Adapter(), model, ["cover-1", "base-1"])
    done: list[dict[str, Any]] = []

    cv.apply(_Adapter(), model, found, False, done)

    assert [d["after"] for d in done] == [False, False]
    assert model.calls[-1] == "redraw"

    report = cv.restore(_Adapter(), model, found, done)

    assert report == [
        {"name": "cover-1", "before": True, "restored": True},
        {"name": "base-1", "before": True, "restored": True},
    ]
    assert all(c.Visible == cv.VISIBLE for c in model.components)


def test_a_failed_apply_leaves_what_it_hid_recorded() -> None:
    stuck = _Component("base-1", takes_visible=False)
    model = _Assembly([_Component("cover-1"), stuck], hide_works=False)
    found = {"cover-1": model.components[0], "base-1": stuck}
    done: list[dict[str, Any]] = []

    with pytest.raises(RuntimeError):
        cv.apply(_Adapter(), model, found, False, done)

    assert [d["name"] for d in done] == ["cover-1"]


def test_restore_reaches_every_component_and_says_which_stayed() -> None:
    model = _assembly()
    cover, base = model.components[0:2][::-1]
    found = {"cover-1": cover, "base-1": base}
    done: list[dict[str, Any]] = []
    cv.apply(_Adapter(), model, found, False, done)
    # The cover can no longer be shown, by either call.
    cover.takes_visible = False
    model.hide_works = False

    report = cv.restore(_Adapter(), model, found, done)

    assert report == [
        {"name": "cover-1", "before": True, "restored": False},
        {"name": "base-1", "before": True, "restored": True},
    ]
    assert base.Visible == cv.VISIBLE


def test_restore_leaves_one_that_was_already_hidden() -> None:
    model = _assembly(visible=0)
    found = {"cover-1": model.components[1]}
    done: list[dict[str, Any]] = []
    cv.apply(_Adapter(), model, found, False, done)

    report = cv.restore(_Adapter(), model, found, done)

    assert report == [{"name": "cover-1", "before": False, "restored": True}]
    assert model.components[1].Visible == cv.HIDDEN
