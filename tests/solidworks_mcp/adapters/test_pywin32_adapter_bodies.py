"""``PyWin32Adapter`` reaches the bodies mixin, and paces its operations right.

A method left at module scope in a mixin file would fall through to the base
adapter's "not implemented" (see "Adapter wiring traps" in the COM pitfalls),
so the owning class is checked directly.
"""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters import pywin32_adapter

_METHODS = [
    "list_bodies",
    "insert_part",
    "combine_bodies",
    "move_copy_body",
    "delete_keep_body",
    "split_body",
    "body_clearance",
    "make_mating_negative",
]


@pytest.mark.parametrize("name", _METHODS)
def test_the_method_comes_from_the_bodies_mixin(name: str) -> None:
    owner = next(
        klass.__name__
        for klass in pywin32_adapter.PyWin32Adapter.__mro__
        if name in vars(klass)
    )

    assert owner == "SolidWorksBodiesMixin"


def test_reading_bodies_opens_no_settle_gap_and_inserting_a_part_a_long_one() -> None:
    assert {"list_bodies", "body_clearance"} <= pywin32_adapter._READ_ONLY_OPERATIONS
    assert "insert_part" in pywin32_adapter._DOCUMENT_OPERATIONS
    assert "make_mating_negative" not in pywin32_adapter._READ_ONLY_OPERATIONS
