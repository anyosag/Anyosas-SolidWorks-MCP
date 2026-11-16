"""The real snippet host compiles, in Windows PowerShell, against the installed interop.

``compile_only`` never attaches to SolidWorks: it compiles the host and the
snippet and looks for ``Run``. It needs Windows PowerShell and the SolidWorks
interop assemblies on disk (SolidWorks installed, not running), so it is
skipped wherever they aren't.
"""

from __future__ import annotations

import re

import pytest

from solidworks_mcp.adapters.solidworks.api_snippets import (
    SnippetRunner,
    SnippetSetupError,
    prepare_source,
)
from solidworks_mcp.skills import SKILL_DIR

_REFERENCE = SKILL_DIR / "references" / "api-escape-hatches.md"


def list_bodies_snippet() -> str:
    """The C# example in the skill's API reference."""
    text = _REFERENCE.read_text(encoding="utf-8")
    match = re.search(r"```csharp\n(.*?)```", text, re.DOTALL)
    assert match is not None, "the reference lost its C# example"
    return match.group(1)


def test_the_documented_example_is_a_run_body() -> None:
    code = list_bodies_snippet()

    assert prepare_source(code).form == "body"
    assert "GetBodies2" in code


def _runner() -> SnippetRunner:
    try:
        return SnippetRunner.for_install()
    except SnippetSetupError as exc:
        pytest.skip(f"no snippet runner here: {exc}")


@pytest.mark.parametrize(
    "code",
    [
        'ModelDoc2 doc = (ModelDoc2)app.ActiveDoc;\nreturn doc == null ? "none" : doc.GetTitle();',
        "public static object Run(SldWorks app) { return app.RevisionNumber(); }",
    ],
    ids=["body", "method"],
)
def test_a_snippet_compiles_and_its_run_is_found(code: str) -> None:
    outcome = _runner().run(code, timeout_s=120, compile_only=True)

    assert outcome["ok"] is True, outcome
    assert outcome["stage"] == "compiled"
    assert outcome["entry_point"] == "Snippet.Run(SldWorks)"
    assert "compile_warnings" not in outcome


def test_the_documented_example_compiles() -> None:
    outcome = _runner().run(list_bodies_snippet(), timeout_s=120, compile_only=True)

    assert outcome["ok"] is True, outcome
    assert outcome["source_form"] == "body"


def test_a_compile_error_names_its_line() -> None:
    outcome = _runner().run(
        'int x = 1;\nreturn $"{x}";', timeout_s=120, compile_only=True
    )

    assert outcome["stage"] == "compile"
    assert outcome["compile_errors"][0]["line"] == 2
