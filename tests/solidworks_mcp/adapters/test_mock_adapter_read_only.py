"""The mock opens a model read-only the way SolidWorks does."""

from __future__ import annotations

import pytest

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


@pytest.fixture
async def mock() -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
    await adapter.connect()
    return adapter


async def test_a_new_document_opens_read_only(mock) -> None:
    result = await mock.open_model_read_only("C:/a.sldprt")

    assert result.is_success
    assert result.data.properties["read_only"] is True


async def test_one_open_for_writing_stays_writable(mock) -> None:
    await mock.open_model("C:/a.sldprt")

    result = await mock.open_model_read_only("C:/a.sldprt")

    assert result.data.properties["read_only"] is False


async def test_one_open_read_only_stays_read_only(mock) -> None:
    await mock.open_model_read_only("C:/a.sldprt")

    result = await mock.open_model_read_only("C:/a.sldprt")

    assert result.data.properties["read_only"] is True


async def test_not_connected_is_the_open_error() -> None:
    adapter = MockSolidWorksAdapter({})

    result = await adapter.open_model_read_only("C:/a.sldprt")

    assert result.error == "Not connected to SolidWorks"
