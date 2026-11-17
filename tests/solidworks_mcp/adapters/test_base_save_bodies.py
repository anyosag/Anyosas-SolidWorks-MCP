"""The base adapter's stubs for Save Bodies, loaded documents and assembly parts."""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import SolidWorksAdapter


class _Bare(SolidWorksAdapter):
    """Only what SolidWorksAdapter leaves abstract."""

    def __init__(self) -> None:
        super().__init__({})


for _name in list(getattr(SolidWorksAdapter, "__abstractmethods__", ())):

    async def _abstract(self: Any, *_a: Any, **_k: Any) -> None:
        return None

    setattr(_Bare, _name, _abstract)
_Bare.__abstractmethods__ = frozenset()  # type: ignore[misc]


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("save_body_as_part", ("Comb", "C:/c.sldprt", None)),
        ("get_save_bodies", ()),
        ("edit_save_bodies", ("Save Bodies1", {"consume_body": True})),
        ("resplit_bodies", ()),
        ("list_loaded_documents", ()),
        ("close_leftover_documents", ()),
        ("list_assembly_parts", ()),
    ],
)
async def test_the_stubs_say_they_are_not_implemented(method: str, args: tuple) -> None:
    result = await getattr(_Bare(), method)(*args)

    assert result.is_error
    assert result.error == f"{method} is not implemented by this adapter"
