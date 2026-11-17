"""A revolve that removes material: ``FeatureRevolve2`` with ``IsCut`` on.

The Dipper build made each conical groove from two drafted cut-extrudes; the
revolve call's own cut form (``IsCut``, the 4th argument) does it in one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    RevolveParameters,
)
from solidworks_mcp.adapters.solidworks import features

_IS_THIN, _IS_CUT = 2, 3


class _Adapter:
    def __init__(self, revision: str, built: bool = True) -> None:
        self.calls: list[tuple] = []
        self.constants = {"swEndCondBlind": 0}
        self.swApp = SimpleNamespace(RevisionNumber=revision)
        self._last_sketch_name = "Sketch5"

        def revolve(*args: Any) -> Any:
            self.calls.append(args)
            return SimpleNamespace(Name="Cut-Revolve1") if built else None

        self.currentModel = SimpleNamespace(
            FeatureManager=SimpleNamespace(FeatureRevolve2=revolve)
        )

    def _handle_com_operation(self, _name: str, callback: Any) -> AdapterResult[Any]:
        try:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=callback())
        except Exception as exc:
            return AdapterResult(status=AdapterResultStatus.ERROR, error=str(exc))

    def _attempt(self, callback: Any, default: Any = None) -> Any:
        try:
            return callback()
        except Exception:
            return default

    def _get_attr_or_call(self, obj: Any, name: str) -> Any:
        return getattr(obj, name)

    def _get_feature_id(self, feature: Any) -> str:
        return str(feature.Name)


@pytest.mark.parametrize("revision", ["33.2.0", "34.4.1"])
def test_a_cut_revolve_sets_is_cut_and_is_never_thin(revision: str) -> None:
    adapter = _Adapter(revision)
    params = RevolveParameters(
        angle=360.0, cut=True, thin_feature=True, thin_thickness=1.0
    )

    result = features._create_revolve_impl(adapter, params)

    assert result.is_success, result.error
    args = adapter.calls[0]
    assert (args[_IS_THIN], args[_IS_CUT]) == (False, True)
    assert result.data.type == "Cut-Revolve"
    assert result.data.name == "Cut-Revolve1"
    assert result.data.parameters["cut"] is True
    assert result.data.parameters["thin_feature"] is False


@pytest.mark.parametrize("revision", ["33.2.0", "34.4.1"])
def test_a_boss_revolve_is_not_a_cut(revision: str) -> None:
    adapter = _Adapter(revision)

    result = features._create_revolve_impl(adapter, RevolveParameters(angle=90.0))

    assert adapter.calls[0][_IS_CUT] is False
    assert result.data.type == "Revolve"
    assert result.data.parameters["cut"] is False


def test_a_cut_that_builds_nothing_fails() -> None:
    adapter = _Adapter("34.4.1", built=False)

    result = features._create_revolve_impl(
        adapter, RevolveParameters(angle=360.0, cut=True)
    )

    assert not result.is_success
    assert "built no feature from sketch 'Sketch5'" in (result.error or "")


def test_on_sw2025_a_cut_with_nothing_returned_is_left_to_the_volume() -> None:
    """SW 2025 may return nothing for a revolve it built, as for a boss."""
    adapter = _Adapter("33.2.0", built=False)

    result = features._create_revolve_impl(
        adapter, RevolveParameters(angle=360.0, cut=True)
    )

    assert result.is_success
    assert result.data.name == "Cut-Revolve-Auto"
