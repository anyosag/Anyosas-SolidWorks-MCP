"""The mock adapter answers ``run_api_snippet`` with a canned result."""

from __future__ import annotations

from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter


async def test_a_run_echoes_the_snippet() -> None:
    result = await MockSolidWorksAdapter({}).run_api_snippet("return 1;")

    assert result.is_success
    assert result.data is not None
    assert result.data["stage"] == "done"
    assert result.data["source_form"] == "body"
    assert result.data["result"] == {"mock": True, "code_chars": 9}
    assert result.data["mock"] is True


async def test_a_compile_only_run_returns_no_result() -> None:
    result = await MockSolidWorksAdapter({}).run_api_snippet(
        "public static object Run(SldWorks app) { return 1; }", compile_only=True
    )

    assert result.data is not None
    assert result.data["stage"] == "compiled"
    assert result.data["source_form"] == "method"
    assert "result" not in result.data
