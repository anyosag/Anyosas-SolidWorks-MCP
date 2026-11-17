"""The base adapter's defaults for ``add_sketch_text`` and ``pattern_linear``."""

from __future__ import annotations

from solidworks_mcp.adapters.base import (
    ExtrusionParameters,
    RevolveParameters,
    SolidWorksAdapter,
)
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_an_adapter_without_sketch_text_says_so() -> None:
    result = await SolidWorksAdapter.add_sketch_text(MockSolidWorksAdapter({}), "MAX")

    assert result.error == "add_sketch_text is not implemented by this adapter"


async def test_an_adapter_without_linear_patterns_says_so() -> None:
    result = await SolidWorksAdapter.pattern_linear(
        MockSolidWorksAdapter({}), ["Cut-Extrude1"], "x", 8, 12.0
    )

    assert result.error == "pattern_linear is not implemented by this adapter"


def test_the_new_parameters_default_to_what_was_built_before() -> None:
    extrusion = ExtrusionParameters(depth=5.0)

    assert (extrusion.start_offset, extrusion.draft_outward) == (0.0, False)
    assert RevolveParameters(angle=360.0).cut is False
