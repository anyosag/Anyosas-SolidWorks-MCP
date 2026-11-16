"""A failed API snippet is its own category, before the COM rules."""

from __future__ import annotations

from solidworks_mcp.tools.error_classifier import API_SNIPPET_FAILED, ErrorClassifier


def test_a_snippet_failure_is_named_whatever_its_exception_says() -> None:
    classify = ErrorClassifier().classify

    for message in (
        "API snippet failed (run): COMException: Type mismatch (HRESULT 0x80020005)",
        "Error in run_api_snippet: API snippet failed (timeout): it ran past timeout_s",
        "API snippet failed (compile): 1 compile error(s); first at line 2",
    ):
        assert classify(message).error_type == API_SNIPPET_FAILED, message


def test_the_server_stopping_a_snippet_is_not_the_snippets_fault() -> None:
    message = (
        "Error in run_api_snippet: Stopped running the API snippet: the MCP client "
        "disconnected (pipe closed), so nothing would read the result."
    )

    assert ErrorClassifier().classify(message).error_type == "operation_stopped"
