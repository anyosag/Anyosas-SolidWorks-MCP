"""``PyWin32Adapter`` reaches ``add_sketch_text`` and ``pattern_linear`` in their mixins.

A method left at module scope in a mixin file would fall through to the base
adapter's "not implemented" (see "Adapter wiring traps" in the COM pitfalls),
so the owning class is checked directly.
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters import pywin32_adapter


@pytest.mark.parametrize(
    ("name", "mixin"),
    [
        ("add_sketch_text", "SolidWorksSketchMixin"),
        ("pattern_linear", "SolidWorksFeaturesMixin"),
    ],
)
def test_the_method_comes_from_its_mixin(name: str, mixin: str) -> None:
    owner = next(
        klass.__name__
        for klass in pywin32_adapter.PyWin32Adapter.__mro__
        if name in vars(klass)
    )

    assert owner == mixin


def test_a_linear_pattern_leaves_nothing_selected() -> None:
    """Like every feature tool (pitfall #50): its seeds and directions are cleared."""
    assert "pattern_linear" in pywin32_adapter._SELECTION_CLEARING_OPERATIONS
