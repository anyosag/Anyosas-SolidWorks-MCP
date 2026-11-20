"""``close_model`` says what it closed, and a name two documents share is refused.

On the Aggrewell enclosure v2 a part and an assembly were both titled
``Enclosure_v2_Tray_with_Stamps`` (Windows hiding extensions), and
``close_model`` with that ``document`` closed both.
"""

from __future__ import annotations

from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.modeling import _with_closed, register_modeling_tools


@pytest.fixture
async def tray(mcp_server) -> tuple[MockSolidWorksAdapter, dict[str, Any]]:
    mock = MockSolidWorksAdapter({})
    await mock.connect()
    mock.hide_extensions = True
    await mock.open_model("C:/work/Tray.SLDPRT")
    await mock.open_model("C:/work/Tray.SLDASM")
    await register_modeling_tools(mcp_server, mock, {})
    return mock, {t.name: t.fn for t in await mcp_server.list_tools()}


async def test_a_document_two_share_is_refused_before_anything_closes(tray) -> None:
    mock, tools = tray

    reply = await tools["close_model"]({}, document="Tray")

    assert reply["status"] == "error"
    assert reply["error_type"] == "invalid_input"
    assert "matches 2 open documents: Tray.SLDPRT, Tray.SLDASM" in reply["message"]
    assert len((await mock.list_open_documents()).data) == 2


async def test_a_close_names_the_one_document_it_closed(tray) -> None:
    mock, tools = tray

    reply = await tools["close_model"]({}, document="Tray.SLDASM")

    assert reply["status"] == "success", reply
    assert reply["closed"] == ["Tray.SLDASM"]
    assert "warning" not in reply
    assert [d["type"] for d in reply["open_documents"]] == ["Part"]
    assert [d["path"] for d in (await mock.list_open_documents()).data] == [
        "C:/work/Tray.SLDPRT"
    ]


def _result(closed: Any) -> AdapterResult[Any]:
    metadata = None if closed is None else {"closed": closed}
    return AdapterResult(status=AdapterResultStatus.SUCCESS, metadata=metadata)


@pytest.mark.parametrize(
    ("closed", "warning"),
    [
        (
            ["Tray.SLDASM", "Tray.SLDPRT"],
            "Closing Tray.SLDASM also closed Tray.SLDPRT.",
        ),
        ([], "SolidWorks closed no document: check list_open_documents."),
        (["Tray.SLDASM"], None),
    ],
)
def test_a_close_that_took_more_or_nothing_warns(
    closed: list[str], warning: str | None
) -> None:
    reply = _with_closed({"status": "success"}, _result(closed))

    assert reply["closed"] == closed
    assert reply.get("warning") == warning


def test_a_close_whose_documents_could_not_be_read_says_nothing() -> None:
    assert _with_closed({"status": "success"}, _result(None)) == {"status": "success"}
    assert _with_closed({"status": "success"}, None) == {"status": "success"}
