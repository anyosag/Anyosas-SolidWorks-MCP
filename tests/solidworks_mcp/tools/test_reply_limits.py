"""Long reply lists come a page at a time, and a cut page says how to get more."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from solidworks_mcp.tools.reply_limits import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    limit_field,
    offset_field,
    page,
)


def test_a_short_list_is_returned_whole_with_nothing_added() -> None:
    assert page([1, 2, 3]) == ([1, 2, 3], {})


def test_a_long_list_is_cut_and_says_where_to_go_on() -> None:
    items = list(range(120))

    chunk, notes = page(items)

    assert chunk == list(range(DEFAULT_LIMIT))
    assert notes == {"truncated": True, "full_count": 120, "next_offset": 50}


def test_a_middle_page() -> None:
    chunk, notes = page(list(range(120)), limit=50, offset=50)

    assert chunk == list(range(50, 100))
    assert notes == {
        "truncated": True,
        "full_count": 120,
        "offset": 50,
        "next_offset": 100,
    }


def test_the_last_page_has_no_next_offset() -> None:
    chunk, notes = page(list(range(120)), limit=50, offset=100)

    assert chunk == list(range(100, 120))
    assert notes == {"full_count": 120, "offset": 100}


def test_an_offset_past_the_end_is_an_empty_page() -> None:
    assert page([1, 2], offset=5) == ([], {"full_count": 2, "offset": 5})


class _Paged(BaseModel):
    limit: int = limit_field()
    offset: int = offset_field()


def test_the_fields_default_and_bound() -> None:
    assert _Paged() == _Paged(limit=DEFAULT_LIMIT, offset=0)
    with pytest.raises(ValidationError):
        _Paged(limit=0)
    with pytest.raises(ValidationError):
        _Paged(limit=MAX_LIMIT + 1)
    with pytest.raises(ValidationError):
        _Paged(offset=-1)
