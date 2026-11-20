"""Tests for the small COM helpers shared by the selection and rebuild code."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.solidworks.com_utils import (
    as_list,
    clear_selection,
    reads_as_circle,
    strip_op_prefix,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, []), ((1, 2), [1, 2]), ([3], [3]), ("scalar", ["scalar"])],
)
def test_as_list_normalises_com_returns(value: object, expected: list) -> None:
    assert as_list(value) == expected


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, None),
        ("", ""),
        ("Error in select_feature: Feature not found", "Feature not found"),
        ("Feature not found", "Feature not found"),
    ],
)
def test_strip_op_prefix(error: str | None, expected: str | None) -> None:
    assert strip_op_prefix(error, "select_feature") == expected


class _Adapter:
    def _attempt(self, fn: Any, default: Any = None) -> Any:
        try:
            return fn()
        except Exception:
            return default


def _refuses(_all: bool) -> bool:
    raise RuntimeError("RPC server is unavailable")


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (SimpleNamespace(ClearSelection2=lambda _all: True), True),
        (SimpleNamespace(ClearSelection2=_refuses), False),
        (SimpleNamespace(), False),
        (None, False),
    ],
)
def test_clear_selection_never_raises(model: Any, expected: bool) -> None:
    assert clear_selection(_Adapter(), model) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, True),
        (1.0, True),
        (True, True),
        (0, False),
        (False, False),
        (2, False),
        (None, False),
        ("1", False),
    ],
)
def test_reads_as_circle(value: Any, expected: bool) -> None:
    """``ISketchArc.IsCircle`` is a long: 1 for a full circle (pitfall #60)."""
    assert reads_as_circle(value) is expected
