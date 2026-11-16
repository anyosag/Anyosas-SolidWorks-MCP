"""The C# snippet runner, with the PowerShell subprocess faked.

Nothing here starts PowerShell, compiles C# or touches SolidWorks: a fake
``Popen`` writes the result file the real host would write.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.adapters.base import AdapterResultStatus
from solidworks_mcp.adapters.solidworks import api_snippets
from solidworks_mcp.adapters.solidworks.api_snippets import (
    InteropAssemblies,
    SnippetRunner,
    SnippetSetupError,
    failure_message,
    find_interop_assemblies,
    find_powershell,
    prepare_source,
    snippet_result,
    snippets_allowed,
)
from solidworks_mcp.tools.error_classifier import ErrorClassifier

ASSEMBLIES = InteropAssemblies(Path("C:/SW/sldworks.dll"), Path("C:/SW/swconst.dll"))


class FakeProcess:
    """A process that exits after ``polls`` waits, or never until killed."""

    def __init__(
        self, polls: int, returncode: int, hang: bool, stuck_after_kill: bool = False
    ) -> None:
        self.polls = polls
        self.returncode: int | None = None
        self._code = returncode
        self.hang = hang
        self.pid = 4242
        self.killed = False
        self.stuck_after_kill = stuck_after_kill

    def wait(self, timeout: float | None = None) -> int:
        if self.killed and not self.stuck_after_kill:
            self.returncode = -9
            return -9
        if self.hang or self.polls > 0:
            self.polls -= 1
            raise subprocess.TimeoutExpired("powershell", timeout or 0)
        self.returncode = self._code
        return self._code

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class FakePopen:
    """``subprocess.Popen`` that writes what the host would, then hands back a process."""

    def __init__(
        self,
        reply: dict[str, Any] | None,
        stdout: bytes = b"",
        stderr: bytes = b"",
        polls: int = 0,
        hang: bool = False,
        returncode: int = 0,
        stuck_after_kill: bool = False,
    ) -> None:
        self.stuck_after_kill = stuck_after_kill
        self.reply = reply
        self.out = stdout
        self.err = stderr
        self.polls = polls
        self.hang = hang
        self.returncode = returncode
        self.calls: list[dict[str, Any]] = []
        self.process: FakeProcess | None = None

    def __call__(self, command: list[str], **kwargs: Any) -> FakeProcess:
        work = Path(kwargs["cwd"])
        self.calls.append(
            {
                "command": command,
                "kwargs": kwargs,
                "script": base64.b64decode(command[-1]).decode("utf-16-le"),
                "source": (work / "snippet.cs").read_text(encoding="utf-8"),
                "host": (work / "host.cs").read_text(encoding="utf-8"),
                "work": work,
            }
        )
        if self.reply is not None:
            text = json.dumps(self.reply).replace(
                "<WORK>", str(work).replace("\\", "\\\\")
            )
            (work / "result.json").write_text(text, encoding="utf-8")
        kwargs["stdout"].write(self.out)
        kwargs["stderr"].write(self.err)
        self.process = FakeProcess(
            self.polls, self.returncode, self.hang, self.stuck_after_kill
        )
        return self.process


def _runner(popen: FakePopen, kills: list[int] | None = None) -> SnippetRunner:
    def kill(pid: int) -> None:
        if kills is not None:
            kills.append(pid)

    return SnippetRunner(ASSEMBLIES, Path("C:/ps.exe"), popen, kill, poll_s=0.0)


# --- Preparing the source --------------------------------------------------


def test_a_body_is_wrapped_in_run_and_keeps_its_line_numbers() -> None:
    prepared = prepare_source("using System.IO;\r\n// note\r\nreturn app;")

    assert prepared.form == "body"
    lines = prepared.source.splitlines()
    assert lines[0] == "using System.IO;"
    assert "using SolidWorks.Interop.sldworks;" in lines
    start = lines.index('#line 1 "snippet.cs"')
    assert lines[start - 2 : start] == [
        "    public static object Run(SldWorks app)",
        "    {",
    ]
    # The using line stays as a blank line 1, so 'return app;' is still line 3.
    assert lines[start + 1 : start + 4] == ["", "// note", "return app;"]
    assert "        return null;" in lines


def test_a_run_method_is_wrapped_in_a_class() -> None:
    prepared = prepare_source("public static object Run(SldWorks app) { return 1; }")

    assert prepared.form == "method"
    assert "public static class Snippet" in prepared.source


def test_classes_are_kept_and_usings_not_repeated() -> None:
    code = (
        "using System;\nusing System;\nusing SolidWorks.Interop.sldworks;\n"
        "public static class Mine { public static object Run(ISldWorks app) "
        "{ return null; } }"
    )

    prepared = prepare_source(code)

    assert prepared.form == "class"
    assert prepared.source.count("using System;") == 1
    assert prepared.source.count("using SolidWorks.Interop.sldworks;") == 1
    assert "public static class Snippet" not in prepared.source


def test_the_switch_reads_one_and_its_synonyms(monkeypatch: pytest.MonkeyPatch) -> None:
    for value, expected in (("1", True), (" TRUE ", True), ("on", True), ("0", False)):
        assert (
            snippets_allowed({"SOLIDWORKS_MCP_ALLOW_API_SNIPPETS": value}) is expected
        )
    assert snippets_allowed({}) is False
    monkeypatch.setenv("SOLIDWORKS_MCP_ALLOW_API_SNIPPETS", "yes")
    assert snippets_allowed() is True


# --- Finding what a snippet needs -------------------------------------------


def _dlls(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SolidWorks.Interop.sldworks.dll").write_bytes(b"")
    (folder / "SolidWorks.Interop.swconst.dll").write_bytes(b"")


def test_the_interop_assemblies_from_the_environment(tmp_path: Path) -> None:
    _dlls(tmp_path)

    found = find_interop_assemblies(None, {"SOLIDWORKS_MCP_INTEROP_DIR": str(tmp_path)})

    assert found.paths() == [
        tmp_path / "SolidWorks.Interop.sldworks.dll",
        tmp_path / "SolidWorks.Interop.swconst.dll",
    ]


def test_the_interop_assemblies_in_the_install_or_its_redist_folder(
    tmp_path: Path,
) -> None:
    asked: list[int | None] = []

    def installed(year: int | None) -> tuple[int | None, Path | None]:
        asked.append(year)
        return 2026, tmp_path

    _dlls(tmp_path / "api" / "redist")
    assert find_interop_assemblies(2026, {}, installed).sldworks.parent == (
        tmp_path / "api" / "redist"
    )
    _dlls(tmp_path)
    assert find_interop_assemblies(2026, {}, installed).sldworks.parent == tmp_path
    assert asked == [2026, 2026]


def test_missing_interop_assemblies_say_where_they_were_looked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SnippetSetupError, match="no SolidWorks install was found"):
        find_interop_assemblies(None, {}, lambda year: (None, None))
    monkeypatch.setenv("SOLIDWORKS_MCP_INTEROP_DIR", str(tmp_path))
    with pytest.raises(SnippetSetupError, match="SOLIDWORKS_MCP_INTEROP_DIR"):
        find_interop_assemblies()


def test_windows_powershell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SnippetSetupError, match="need Windows"):
        find_powershell({}, "linux")
    with pytest.raises(SnippetSetupError, match="isn't at"):
        find_powershell({"SystemRoot": str(tmp_path)}, "win32")
    exe = tmp_path / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert find_powershell({"SystemRoot": str(tmp_path)}, "win32") == exe
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setattr(api_snippets.sys, "platform", "win32")
    assert find_powershell() == exe


def test_a_runner_for_the_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        api_snippets, "find_interop_assemblies", lambda year: ASSEMBLIES
    )
    monkeypatch.setattr(api_snippets, "find_powershell", lambda: Path("C:/ps.exe"))

    runner = SnippetRunner.for_install(2026)

    assert (runner.assemblies, runner.powershell) == (ASSEMBLIES, Path("C:/ps.exe"))


def test_the_runner_script_quotes_its_paths() -> None:
    script = api_snippets.runner_script(Path("C:/it's"), ASSEMBLIES, True)

    assert "it''s'" in script
    assert str(ASSEMBLIES.sldworks) + "|" + str(ASSEMBLIES.swconst) in script
    assert script.rstrip().endswith("exit $code")
    assert "$true)" in script
    assert "$false)" in api_snippets.runner_script(Path("C:/w"), ASSEMBLIES, False)


def test_killing_a_tree_uses_taskkill(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr(
        api_snippets.subprocess, "run", lambda command, **_k: seen.append(command)
    )

    api_snippets.kill_tree(77)

    assert seen == [["taskkill", "/PID", "77", "/T", "/F"]]


# --- Running ---------------------------------------------------------------


def test_a_run_returns_the_result_and_both_streams() -> None:
    popen = FakePopen(
        {
            "ok": True,
            "stage": "done",
            "compiled": True,
            "compile_errors": [],
            "compile_warnings": [{"line": 2, "code": "CS0219", "message": "unused"}],
            "entry_point": "Snippet.Run(SldWorks)",
            "result": [1, 2],
            "result_type": "System.Int32[]",
        },
        stdout="\ufeffhello\n".encode(),
        stderr=b"note",
        polls=2,
    )

    outcome = _runner(popen).run("return new[] { 1, 2 };", timeout_s=30)

    assert outcome["ok"] is True
    assert outcome["result"] == [1, 2]
    assert outcome["stdout"] == "hello\n"
    assert outcome["stderr"] == "note"
    assert outcome["source_form"] == "body"
    assert outcome["exit_code"] == 0
    assert "compile_errors" not in outcome
    assert outcome["compile_warnings"][0]["code"] == "CS0219"
    call = popen.calls[0]
    assert call["command"][:2] == [str(Path("C:/ps.exe")), "-NoLogo"]
    assert "-EncodedCommand" in call["command"]
    assert "[SolidWorksMcpSnippets.Host]::Execute" in call["script"]
    assert "return new[] { 1, 2 };" in call["source"]
    assert "namespace SolidWorksMcpSnippets" in call["host"]
    assert not call["work"].exists(), "the run's folder is removed"


def test_a_run_that_leaves_no_result_failed_in_the_host() -> None:
    outcome = _runner(FakePopen(None, stderr=b"Add-Type failed", returncode=3)).run(
        "return 1;"
    )

    assert (outcome["ok"], outcome["stage"], outcome["exit_code"]) == (False, "host", 3)
    assert failure_message(outcome) == (
        "API snippet failed (host): the PowerShell runner stopped before the "
        "snippet ran: Add-Type failed"
    )


def test_a_run_past_its_time_is_killed() -> None:
    kills: list[int] = []
    popen = FakePopen(None, hang=True)

    outcome = _runner(popen, kills).run("while (true) {}", timeout_s=0.0)

    assert (outcome["ok"], outcome["stage"], outcome["timeout_s"]) == (
        False,
        "timeout",
        0.0,
    )
    assert kills == [4242]
    assert popen.process is not None and popen.process.killed
    assert "ran past timeout_s (0 s)" in failure_message(outcome)


def test_a_kill_that_fails_still_stops_the_process() -> None:
    popen = FakePopen(None, hang=True, stuck_after_kill=True)

    def kill(pid: int) -> None:
        raise OSError("taskkill missing")

    runner = SnippetRunner(ASSEMBLIES, Path("C:/ps.exe"), popen, kill, poll_s=0.0)

    outcome = runner.run("x();", timeout_s=0.0)

    assert (outcome["stage"], outcome["exit_code"]) == ("timeout", None)
    assert popen.process is not None and popen.process.killed


def test_a_stop_request_kills_the_run_and_is_raised() -> None:
    kills: list[int] = []

    def stop() -> None:
        raise RuntimeError("client gone")

    with pytest.raises(RuntimeError, match="client gone"):
        _runner(FakePopen(None, hang=True), kills).run("x();", 60, False, stop)
    assert kills == [4242]


def test_a_stop_check_that_passes_lets_the_run_finish() -> None:
    checks: list[str] = []
    popen = FakePopen({"ok": True, "stage": "done", "result": None}, polls=1)

    outcome = _runner(popen).run("x();", 60, False, lambda: checks.append("ok"))

    assert outcome["ok"] is True
    assert checks == ["ok"]


def test_long_streams_and_results_are_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_snippets, "MAX_STREAM_CHARS", 4)
    monkeypatch.setattr(api_snippets, "MAX_RESULT_CHARS", 10)
    popen = FakePopen(
        {"ok": True, "stage": "done", "result": list(range(50))},
        stdout=b"0123456789",
        stderr=("\u00e9" * 40).encode(),
    )

    outcome = _runner(popen).run("x();")

    assert (outcome["stdout"], outcome["stdout_truncated"]) == ("0123", True)
    assert outcome["stderr"] == "éééé" and outcome["stderr_truncated"] is True
    assert outcome["result"] is None
    assert outcome["result_text"] == "[0, 1, 2, "
    assert outcome["result_truncated"] is True


def test_a_stack_trace_loses_the_run_folder() -> None:
    popen = FakePopen(
        {
            "ok": False,
            "stage": "run",
            "exception": {
                "type": "System.Runtime.InteropServices.COMException",
                "message": "Error HRESULT E_FAIL",
                "hresult": "0x80004005",
                "stack": "   at Snippet.Run(SldWorks app) in <WORK>\\snippet.cs:line 3",
            },
        }
    )

    outcome = _runner(popen).run("x();")

    assert (
        outcome["exception"]["stack"]
        == "   at Snippet.Run(SldWorks app) in snippet.cs:line 3"
    )
    assert failure_message(outcome) == (
        "API snippet failed (run): System.Runtime.InteropServices.COMException: "
        "Error HRESULT E_FAIL (HRESULT 0x80004005) at line 3"
    )


def test_reading_a_missing_stream_file(tmp_path: Path) -> None:
    assert api_snippets._read_capped(tmp_path / "none.txt", 10) == ("", False)


# --- What a failure says ---------------------------------------------------


def test_failure_messages_by_stage() -> None:
    compile_error = {
        "stage": "compile",
        "compile_errors": [
            {"line": 2, "code": "CS1056", "message": "Unexpected character '$'"}
        ],
    }
    assert failure_message(compile_error).startswith(
        "API snippet failed (compile): 1 compile error(s); first at line 2: CS1056 "
        "Unexpected character '$'. The compiler is C# 5"
    )
    assert failure_message({"stage": "compile"}) == (
        "API snippet failed (compile): it did not compile"
    )
    assert failure_message({"stage": "entry", "error": "No entry point"}) == (
        "API snippet failed (entry): No entry point"
    )
    attach = {
        "stage": "attach",
        "exception": {
            "type": "COMException",
            "message": "Access denied",
            "hresult": "0x80070005",
        },
    }
    assert failure_message(attach) == (
        "API snippet failed (attach): COMException: Access denied (HRESULT 0x80070005)"
    )
    assert failure_message({}) == (
        "API snippet failed (host): the PowerShell runner stopped before the "
        "snippet ran: no output"
    )


def test_no_running_solidworks_reads_as_unavailable() -> None:
    outcome = {
        "stage": "attach",
        "exception": {"type": "COMException", "message": "x", "hresult": "0x800401e3"},
    }

    message = failure_message(outcome)

    assert message.startswith("SolidWorks is not running")
    assert ErrorClassifier().classify(message).error_type == "solidworks_unavailable"


def test_adapter_results_for_success_failure_and_setup() -> None:
    ok = snippet_result({"ok": True, "result": 1}, 0.5)
    assert (ok.status, ok.data, ok.execution_time) == (
        AdapterResultStatus.SUCCESS,
        {"ok": True, "result": 1},
        0.5,
    )

    failed = snippet_result({"ok": False, "stage": "entry", "error": "no Run"}, 0.1)
    assert failed.status is AdapterResultStatus.ERROR
    assert failed.error == "API snippet failed (entry): no Run"
    assert failed.data == {"ok": False, "stage": "entry", "error": "no Run"}
    assert ErrorClassifier().classify(failed.error).error_type == "api_snippet_failed"

    setup = api_snippets.setup_failure(SnippetSetupError("no DLLs"))
    assert setup.error == "API snippet failed (setup): no DLLs"
    assert setup.data == {"ok": False, "stage": "setup"}
