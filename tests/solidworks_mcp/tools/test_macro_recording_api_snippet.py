"""The ``run_api_snippet`` tool: off by default, and its replies when on."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ValidationError

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.macro_recording import register_macro_recording_tools


async def _call(adapter: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    mcp = FastMCP("snippets")
    await register_macro_recording_tools(mcp, adapter, {})
    result = await mcp.call_tool("run_api_snippet", {"input_data": arguments})
    return dict(result.structured_content or {})


@pytest.fixture
def allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_ALLOW_API_SNIPPETS", "1")


async def test_it_is_off_unless_the_environment_allows_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SOLIDWORKS_MCP_ALLOW_API_SNIPPETS", raising=False)
    adapter = MockSolidWorksAdapter({})
    adapter.run_api_snippet = AsyncMock()  # type: ignore[method-assign]

    reply = await _call(adapter, {"code": "return 1;"})

    assert reply["status"] == "error"
    assert reply["error_type"] == "not_supported"
    assert "SOLIDWORKS_MCP_ALLOW_API_SNIPPETS=1" in reply["message"]
    assert "Ask the user" in reply["hint"]
    adapter.run_api_snippet.assert_not_awaited()


async def test_a_run_returns_the_outcome(allowed: None) -> None:
    reply = await _call(MockSolidWorksAdapter({}), {"code": "return 1;"})

    assert reply["status"] == "success"
    assert reply["message"] == (
        "Snippet ran; it returned System.Collections.Generic.Dictionary"
    )
    assert reply["data"]["result"] == {"mock": True, "code_chars": 9}
    assert reply["warning"].startswith("Experimental:")


async def test_a_compile_only_run(allowed: None) -> None:
    reply = await _call(
        MockSolidWorksAdapter({}), {"code": "return 1;", "compile_only": True}
    )

    assert reply["message"] == "Compiled; entry point Snippet.Run(SldWorks)"


async def test_a_run_that_returns_nothing(allowed: None) -> None:
    adapter = MockSolidWorksAdapter({})
    adapter.run_api_snippet = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS, data=None, execution_time=0.1
        )
    )

    reply = await _call(adapter, {"code": "app.ActiveDoc.ToString();", "timeout_s": 5})

    assert reply["message"] == "Snippet ran; it returned null"
    adapter.run_api_snippet.assert_awaited_once_with(
        "app.ActiveDoc.ToString();", 5.0, False
    )


async def test_a_failed_snippet_comes_back_with_its_diagnosis(allowed: None) -> None:
    adapter = MockSolidWorksAdapter({})
    outcome = {
        "ok": False,
        "stage": "compile",
        "compile_errors": [{"line": 1, "code": "CS1002", "message": "; expected"}],
    }
    adapter.run_api_snippet = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.ERROR,
            error="API snippet failed (compile): 1 compile error(s)",
            data=outcome,
        )
    )

    reply = await _call(adapter, {"code": "int x = 1"})

    assert reply["status"] == "error"
    assert reply["error_type"] == "api_snippet_failed"
    assert reply["data"] == outcome
    assert "compile_only" in reply["hint"]


@pytest.mark.parametrize(
    "arguments",
    [{"code": ""}, {"code": "x();", "timeout_s": 601}],
    ids=["empty", "slow"],
)
async def test_an_empty_snippet_or_a_long_timeout_is_refused(
    allowed: None, arguments: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        await _call(MockSolidWorksAdapter({}), arguments)
