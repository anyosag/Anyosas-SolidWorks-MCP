"""``PyWin32Adapter`` reaches the analysis mixin, and paces its methods right.

A method left at module scope in a mixin file would fall through to the base
adapter's "not implemented" (see "Adapter wiring traps" in the COM pitfalls),
so the owning class is checked directly.
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters import pywin32_adapter


@pytest.mark.parametrize(
    "name", ["rotation_clearance", "volume_by_plane", "set_density"]
)
def test_the_method_comes_from_the_analysis_mixin(name: str) -> None:
    owner = next(
        klass.__name__
        for klass in pywin32_adapter.PyWin32Adapter.__mro__
        if name in vars(klass)
    )

    assert owner == "SolidWorksAnalysisMixin"


def test_only_the_plane_volume_is_a_read() -> None:
    reads = pywin32_adapter._READ_ONLY_OPERATIONS

    assert "volume_by_plane" in reads
    # Turning a component moves it, and a density changes the part.
    assert not {"rotation_clearance", "set_density"} & reads
    assert "rotation_clearance" not in pywin32_adapter._SELECTION_CLEARING_OPERATIONS
