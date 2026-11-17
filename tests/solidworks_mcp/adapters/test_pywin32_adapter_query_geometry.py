"""``PyWin32Adapter`` reaches ``query_geometry`` in the analysis mixin, and keeps the selection.

The query picks (``SelectByID2``, ``SelectByRay``), so it is not a read for
the settle gap; and it must not clear the user's selection as it ends.
"""

from __future__ import annotations

from solidworks_mcp.adapters import pywin32_adapter


def test_the_method_comes_from_the_analysis_mixin() -> None:
    owner = next(
        klass.__name__
        for klass in pywin32_adapter.PyWin32Adapter.__mro__
        if "query_geometry" in vars(klass)
    )

    assert owner == "SolidWorksAnalysisMixin"


def test_it_neither_skips_the_gap_nor_clears_the_selection() -> None:
    assert "query_geometry" not in pywin32_adapter._READ_ONLY_OPERATIONS
    assert "query_geometry" not in pywin32_adapter._SELECTION_CLEARING_OPERATIONS
