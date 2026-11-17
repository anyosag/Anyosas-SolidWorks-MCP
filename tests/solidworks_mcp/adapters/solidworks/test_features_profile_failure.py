"""A failed extrude or cut says when its sketch's contours cross.

The Dipper: overlapping rings gave a bare "Failed to create extrusion feature".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.solidworks import features, sketch_contours
from solidworks_mcp.tools.error_classifier import ErrorClassifier

from .test_features import _build_adapter, _FakeFeatureAdapter


def _failed(
    error: str = "Error in create_extrusion: Failed to create extrusion feature",
):
    return AdapterResult(status=AdapterResultStatus.ERROR, error=error)


def _adapter(found: list[str] | Exception, last: str | None = "Sketch5") -> Any:
    adapter = _FakeFeatureAdapter()
    adapter.currentModel = SimpleNamespace()
    adapter._last_sketch_name = last
    adapter.asked: list[str] = []

    def crossings_in(_adapter: Any, name: str) -> list[str]:
        adapter.asked.append(name)
        if isinstance(found, Exception):
            raise found
        return found

    adapter.crossings_in = crossings_in
    return adapter


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(adapter: Any) -> Any:
        monkeypatch.setattr(sketch_contours, "crossings_in", adapter.crossings_in)
        return adapter

    return install


def test_crossing_rings_are_named_and_classified(patched: Any) -> None:
    adapter = patched(_adapter(["the circle at (0, 0) r 18 and the circle ..."]))

    result = features._explain_profile_failure(adapter, _failed(), SimpleNamespace())

    assert adapter.asked == ["Sketch5"]
    assert result.error.startswith(
        "Error in create_extrusion: Failed to create extrusion feature. Sketch "
        "'Sketch5': sketch contours intersect (the circle at (0, 0) r 18"
    )
    assert ErrorClassifier().classify(result.error).error_type == (
        "sketch_contours_intersect"
    )


def test_a_cut_that_built_nothing_is_classified_by_its_contours(patched: Any) -> None:
    adapter = patched(_adapter(["the circle ... and the 4-line loop ..."]))
    cut = _failed("SolidWorks built no feature from sketch 'Rings' (tried: ...)")

    result = features._explain_profile_failure(
        adapter, cut, SimpleNamespace(sketch_name="Rings")
    )

    assert adapter.asked == ["Rings"]
    assert ErrorClassifier().classify(result.error).error_type == (
        "sketch_contours_intersect"
    )


@pytest.mark.parametrize(
    ("found", "last"),
    [([], "Sketch5"), (RuntimeError("COM"), "Sketch5"), (["x"], None)],
)
def test_otherwise_the_failure_is_unchanged(
    patched: Any, found: Any, last: str | None
) -> None:
    adapter = patched(_adapter(found, last))
    failed = _failed()

    result = features._explain_profile_failure(adapter, failed, SimpleNamespace())

    assert (
        result.error == "Error in create_extrusion: Failed to create extrusion feature"
    )


def test_a_success_or_no_model_reads_nothing(patched: Any) -> None:
    adapter = patched(_adapter(["x"]))
    done = AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)

    assert features._explain_profile_failure(adapter, done, SimpleNamespace()) is done
    adapter.currentModel = None
    features._explain_profile_failure(adapter, _failed(), SimpleNamespace())
    assert adapter.asked == []


@pytest.mark.asyncio
async def test_extrude_and_cut_failures_are_explained(monkeypatch) -> None:
    adapter = _build_adapter(monkeypatch)
    adapter.currentModel = SimpleNamespace()
    adapter._last_sketch_name = "Rings"
    monkeypatch.setattr(features, "_create_extrusion_impl", lambda *_a: _failed())

    async def no_cut(*_a: Any) -> AdapterResult:
        return _failed("SolidWorks built no feature from sketch 'Rings'")

    monkeypatch.setattr(features, "_create_cut_extrude_choosing_side", no_cut)
    monkeypatch.setattr(sketch_contours, "crossings_in", lambda *_a: ["a and b"])

    extrude = await adapter.create_extrusion(SimpleNamespace(sketch_name=None))
    cut = await adapter.create_cut_extrude(SimpleNamespace(sketch_name=None))

    assert "sketch contours intersect (a and b)" in extrude.error
    assert "sketch contours intersect (a and b)" in cut.error
