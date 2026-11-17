"""Hiding the FeatureManager pane while a picture is framed (``feature_pane``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks import feature_pane


class _Adapter:
    def _attempt(self, operation: Any, default: Any = None) -> Any:
        try:
            return operation()
        except Exception:  # noqa: BLE001 - mirror the real adapter
            return default


class _Extension:
    def __init__(self, works: bool = True) -> None:
        self.works, self.calls = works, []

    def HideFeatureManager(self, hide: bool) -> bool:  # noqa: N802
        self.calls.append(hide)
        return self.works


def _doc(width: Any, extension: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        GetFeatureManagerWidth=lambda: width,
        Extension=extension if extension is not None else _Extension(),
    )


def test_a_shown_pane_is_hidden_and_shown_again() -> None:
    doc = _doc(311)

    hidden = feature_pane.hide(_Adapter(), doc)
    feature_pane.show(_Adapter(), hidden)

    assert hidden is doc.Extension
    assert doc.Extension.calls == [True, False]


@pytest.mark.parametrize("width", [0, None, True, "311"])
def test_a_pane_that_isnt_shown_is_left_alone(width: Any) -> None:
    doc = _doc(width)

    hidden = feature_pane.hide(_Adapter(), doc)
    feature_pane.show(_Adapter(), hidden)

    assert hidden is None and doc.Extension.calls == []


def test_a_refused_or_missing_hide_changes_nothing() -> None:
    refused = _doc(311, _Extension(works=False))
    no_extension = SimpleNamespace(GetFeatureManagerWidth=lambda: 311)

    assert feature_pane.hide(_Adapter(), refused) is None
    assert feature_pane.hide(_Adapter(), no_extension) is None
    assert refused.Extension.calls == [True]
