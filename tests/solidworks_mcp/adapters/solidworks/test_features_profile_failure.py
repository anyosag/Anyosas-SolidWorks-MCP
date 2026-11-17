"""A failed extrude or cut says when its sketch's contours cross, or builds their union.

The Dipper: overlapping rings gave a bare "Failed to create extrusion feature".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from solidworks_mcp.adapters.base import (
    AdapterResult,
    AdapterResultStatus,
    ExtrusionParameters,
    SolidWorksFeature,
)
from solidworks_mcp.adapters.solidworks import features, sketch_contours
from solidworks_mcp.tools.error_classifier import ErrorClassifier

from .test_features import _build_adapter, _FakeFeatureAdapter


def _failed(
    error: str = "Error in create_extrusion: Failed to create extrusion feature",
):
    return AdapterResult(status=AdapterResultStatus.ERROR, error=error)


def _built(**parameters: Any) -> AdapterResult:
    feature = SolidWorksFeature(
        name="Boss-Extrude1", type="Extrusion", parameters=dict(parameters)
    )
    return AdapterResult(status=AdapterResultStatus.SUCCESS, data=feature)


def _adapter(found: dict[str, Any] | Exception, last: str | None = "Sketch5") -> Any:
    adapter = _FakeFeatureAdapter()
    adapter.currentModel = SimpleNamespace()
    adapter._last_sketch_name = last
    adapter.asked: list[str] = []

    def union_check_in(_adapter: Any, name: str) -> dict[str, Any]:
        adapter.asked.append(name)
        if isinstance(found, Exception):
            raise found
        return found

    adapter.union_check_in = union_check_in
    return adapter


class _Builder:
    """Records each rebuild and answers with ``answer``."""

    def __init__(self, answer: AdapterResult | None = None) -> None:
        self.answer = answer or _built()
        self.calls: list[Any] = []

    async def __call__(self, params: Any) -> AdapterResult:
        self.calls.append(params)
        return self.answer


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch) -> Any:
    def install(adapter: Any) -> Any:
        monkeypatch.setattr(sketch_contours, "union_check_in", adapter.union_check_in)
        return adapter

    return install


_RINGS = ["the circle at (0, 0) r 18 and the circle ..."]


async def test_crossing_rings_with_a_hole_are_named_and_classified(
    patched: Any,
) -> None:
    adapter = patched(
        _adapter({"crossing": _RINGS, "refusal": "the circle a is inside b"})
    )
    build = _Builder()

    result = await features._union_or_explained(
        adapter, _failed(), SimpleNamespace(), build
    )

    assert adapter.asked == ["Sketch5"]
    assert build.calls == []
    assert result.error.startswith(
        "Error in create_extrusion: Failed to create extrusion feature. Sketch "
        "'Sketch5': sketch contours intersect (the circle at (0, 0) r 18"
    )
    assert result.error.endswith(
        "Building every region was not tried: the circle a is inside b."
    )
    assert ErrorClassifier().classify(result.error).error_type == (
        "sketch_contours_intersect"
    )


async def test_crossing_rings_are_built_as_their_union(patched: Any) -> None:
    adapter = patched(_adapter({"crossing": _RINGS, "refusal": None}))
    build = _Builder()
    params = ExtrusionParameters(depth=5, reverse_direction=True)

    result = await features._union_or_explained(adapter, _failed(), params, build)

    assert result is build.answer
    (again,) = build.calls
    assert again.regions == "all"
    assert again.sketch_name == "Sketch5"
    assert again.reverse_direction is True
    assert "reverse_direction" in again.model_fields_set
    assert "built from every region" in result.data.parameters["regions_note"]


async def test_a_union_of_plain_parameters_copies_them(patched: Any) -> None:
    adapter = patched(_adapter({"crossing": _RINGS, "refusal": None}))
    build = _Builder()

    await features._union_or_explained(
        adapter, _failed(), SimpleNamespace(sketch_name="Rings", depth=2), build
    )

    assert vars(build.calls[0]) == {
        "sketch_name": "Rings",
        "depth": 2,
        "regions": "all",
    }


async def test_a_union_that_fails_too_says_both(patched: Any) -> None:
    adapter = patched(_adapter({"crossing": _RINGS, "refusal": None}))
    build = _Builder(_failed("Error in create_cut_extrude: no regions"))

    result = await features._union_or_explained(
        adapter, _failed(), SimpleNamespace(), build
    )

    assert result.error.endswith("Building every region as one failed too: no regions")


async def test_a_union_asked_for_is_not_tried_again(patched: Any) -> None:
    adapter = patched(_adapter({"crossing": _RINGS, "refusal": None}))
    build = _Builder()

    result = await features._union_or_explained(
        adapter, _failed(), SimpleNamespace(regions="all"), build
    )

    assert build.calls == []
    assert "sketch contours intersect" in result.error


async def test_a_cut_that_built_nothing_is_classified_by_its_contours(
    patched: Any,
) -> None:
    adapter = patched(_adapter({"crossing": ["a and b"], "refusal": "x"}))
    cut = _failed("SolidWorks built no feature from sketch 'Rings' (tried: ...)")

    result = await features._union_or_explained(
        adapter, cut, SimpleNamespace(sketch_name="Rings"), _Builder()
    )

    assert adapter.asked == ["Rings"]
    assert ErrorClassifier().classify(result.error).error_type == (
        "sketch_contours_intersect"
    )


@pytest.mark.parametrize(
    ("found", "last"),
    [
        ({"crossing": [], "refusal": None}, "Sketch5"),
        (RuntimeError("COM"), "Sketch5"),
        ({"crossing": ["x"]}, None),
    ],
)
async def test_otherwise_the_failure_is_unchanged(
    patched: Any, found: Any, last: str | None
) -> None:
    adapter = patched(_adapter(found, last))

    result = await features._union_or_explained(
        adapter, _failed(), SimpleNamespace(), _Builder()
    )

    assert (
        result.error == "Error in create_extrusion: Failed to create extrusion feature"
    )


async def test_a_success_or_no_model_reads_nothing(patched: Any) -> None:
    adapter = patched(_adapter({"crossing": ["x"]}))
    done = AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)

    assert (
        await features._union_or_explained(adapter, done, SimpleNamespace(), None)
        is done
    )
    adapter.currentModel = None
    await features._union_or_explained(adapter, _failed(), SimpleNamespace(), None)
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
    monkeypatch.setattr(
        sketch_contours,
        "union_check_in",
        lambda *_a: {"crossing": ["a and b"], "refusal": None},
    )

    extrude = await adapter.create_extrusion(SimpleNamespace(sketch_name=None))
    cut = await adapter.create_cut_extrude(SimpleNamespace(sketch_name=None))

    assert "sketch contours intersect (a and b)" in extrude.error
    assert "Building every region as one failed too" in extrude.error
    assert "sketch contours intersect (a and b)" in cut.error
