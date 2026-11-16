"""Macro Recording and Playback tools for SolidWorks MCP Server.

Provides tools for recording, managing, and executing SolidWorks macros for automation
and workflow optimization.
"""

import asyncio
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from pydantic import Field

from ..adapters.base import SolidWorksAdapter
from ..adapters.solidworks.api_snippets import (
    ALLOW_ENV,
    DEFAULT_TIMEOUT_S,
    MAX_CODE_CHARS,
    MAX_TIMEOUT_S,
    snippets_allowed,
)
from .error_classifier import NOT_SUPPORTED, Classification
from .input_compat import CompatInput
from .tool_boundary import (
    ToolOperationError,
    UnsupportedOperationError,
    normalize_input,
    require_capability,
    require_success,
    with_tool_boundary,
)

# Input schemas for macro operations

# ISldWorks::RunMacro2 runs only binary macros.
_RUNNABLE_MACRO_SUFFIXES = frozenset({".swp", ".swb"})


class MacroRecordingInput(CompatInput):
    """Input schema for macro recording operations.

    Attributes:
        auto_cleanup (bool): The auto cleanup value.
        auto_stop (bool): The auto stop value.
        capture_keyboard (bool): The capture keyboard value.
        capture_mouse (bool): The capture mouse value.
        description (str): The description value.
        macro_name (str | None): The macro name value.
        output_file (str): The output file value.
        recording_mode (str): The recording mode value.
        recording_name (str | None): The recording name value.
        recording_quality (str): The recording quality value.
        timeout_seconds (int): The timeout seconds value.
    """

    macro_name: str | None = Field(
        default=None, description="Name for the recorded macro"
    )
    recording_name: str | None = Field(
        default=None, description="Alternative recording name"
    )
    description: str = Field(
        default="", description="Description of macro functionality"
    )
    output_file: str = Field(description="Output file for the recorded macro")
    recording_mode: str = Field(default="User actions", description="Recording mode")
    capture_mouse: bool = Field(default=True, description="Capture mouse actions")
    capture_keyboard: bool = Field(default=True, description="Capture keyboard actions")
    recording_quality: str = Field(
        default="High", description="Recording quality level"
    )
    auto_cleanup: bool = Field(default=False, description="Cleanup temporary files")
    auto_stop: bool = Field(
        default=False, description="Auto-stop recording after timeout"
    )
    timeout_seconds: int = Field(
        default=300, description="Timeout for auto-stop in seconds"
    )

    def model_post_init(self, __context: Any) -> None:
        """Provide model post init support for the macro recording input.

        Args:
            __context (Any): The context value.

        Returns:
            None: None.
        """
        if self.macro_name is None:
            self.macro_name = self.recording_name or "Recorded Macro"


class MacroPlaybackInput(CompatInput):
    """Input schema for macro playback.

    Attributes:
        macro_file (str | None): The macro file value.
        macro_path (str | None): The macro path value.
        proc_name (str): The procedure to call.
        pause_between_runs (float): The pause between runs value.
        repeat_count (int): The repeat count value.
    """

    macro_path: str | None = Field(
        default=None,
        description=(
            "Path to a binary SolidWorks macro (.swp, or legacy .swb). "
            "VBA source (.vb, .bas, text) cannot be run directly."
        ),
    )
    macro_file: str | None = Field(
        default=None, description="Alternative to macro_path (.swp or .swb)"
    )
    proc_name: str = Field(
        default="main", description="The macro procedure to call; main by default"
    )
    repeat_count: int = Field(
        default=1,
        ge=1,
        le=100,
        description="How many times to run the macro (1-100); it stops at the first failure",
    )
    pause_between_runs: float = Field(
        default=0.0, ge=0.0, description="Seconds to wait between runs"
    )

    def model_post_init(self, __context: Any) -> None:
        """Reject macro files ``ISldWorks::RunMacro2`` cannot run.

        ``RunMacro2`` only runs binary ``.swp`` (or legacy ``.swb``) macros;
        anything else fails deep inside COM with an opaque error.

        Args:
            __context (Any): The context value.

        Raises:
            ValueError: A macro path is not a ``.swp`` or ``.swb`` file.
        """
        for path in (self.macro_path, self.macro_file):
            if path and Path(path).suffix.lower() not in _RUNNABLE_MACRO_SUFFIXES:
                raise ValueError(
                    f"execute_macro runs only binary SolidWorks macros "
                    f"(.swp or .swb), got {path!r}. VBA source (.vb, .bas or "
                    "text) must be saved as a .swp in the SolidWorks VBA "
                    "editor first."
                )


class ApiSnippetInput(CompatInput):
    """Input schema for ``run_api_snippet``.

    Attributes:
        code (str): The C# snippet.
        timeout_s (float): Seconds before its process is killed.
        compile_only (bool): Only compile it.
    """

    code: str = Field(
        min_length=1,
        max_length=MAX_CODE_CHARS,
        description=(
            "C# 5 code: classes declaring `public static object Run(SldWorks "
            "app)`, that method alone, or only its body (statements using "
            "`app`, ending in `return ...;`). System, System.Linq and the "
            "SolidWorks sldworks and swconst namespaces are imported"
        ),
    )
    timeout_s: float = Field(
        default=DEFAULT_TIMEOUT_S,
        ge=1.0,
        le=MAX_TIMEOUT_S,
        description="Seconds before the snippet's process is killed (1-600)",
    )
    compile_only: bool = Field(
        default=False,
        description="Only compile it and find Run; SolidWorks isn't touched",
    )


_SNIPPETS_OFF_HINT = (
    "Nothing ran. API snippets run arbitrary code on the user's SolidWorks, so "
    f"they are off unless the server's environment sets {ALLOW_ENV}=1. Ask the "
    "user whether to turn them on (it takes a server restart); meanwhile use the "
    "tools, or give the user the code to run themselves."
)


class MacroAnalysisInput(CompatInput):
    """Input schema for macro analysis.

    Attributes:
        analysis_depth (str): The analysis depth value.
        analysis_type (str): The analysis type value.
        macro_file (str | None): The macro file value.
        macro_path (str | None): The macro path value.
        suggest_optimizations (bool): The suggest optimizations value.
    """

    macro_path: str | None = Field(
        default=None, description="Path to macro file to analyze"
    )
    macro_file: str | None = Field(
        default=None, description="Alternative macro file path"
    )
    analysis_type: str = Field(
        default="full", description="Analysis type (full, dependencies, performance)"
    )
    analysis_depth: str = Field(default="Basic", description="Analysis depth alias")
    suggest_optimizations: bool = Field(
        default=False, description="Suggest optimizations"
    )


class MacroBatchInput(CompatInput):
    """Input schema for batch macro operations.

    Attributes:
        execution_order (str): The execution order value.
        file_pattern (str | None): The file pattern value.
        macro_list (list[str]): The macro list value.
        source_directory (str | None): The source directory value.
        stop_on_error (bool): The stop on error value.
        target_directory (str | None): The target directory value.
    """

    macro_list: list[str] = Field(description="List of macro file paths")
    target_directory: str | None = Field(
        default=None, description="Target directory alias"
    )
    source_directory: str | None = Field(
        default=None, description="Source directory alias"
    )
    file_pattern: str | None = Field(default=None, description="File pattern alias")
    execution_order: str = Field(
        default="sequential", description="Execution order (sequential, parallel)"
    )
    stop_on_error: bool = Field(default=True, description="Stop batch if error occurs")


async def register_macro_recording_tools(
    mcp: FastMCP, adapter: SolidWorksAdapter, config: Any
) -> int:
    """Register macro recording and playback tools with FastMCP.

    Args:
        mcp (FastMCP): The mcp value.
        adapter (SolidWorksAdapter): Adapter instance used for the operation.
        config (Any): Configuration values for the operation.

    Returns:
        int: The computed numeric result.

    Example:
                        >>> tool_count = await register_macro_recording_tools(mcp, adapter, config)
    """
    mcp = with_tool_boundary(mcp, adapter)
    tool_count = 0

    @mcp.tool()
    async def start_macro_recording(input_data: MacroRecordingInput) -> dict[str, Any]:
        """Start SolidWorks' macro recorder, to capture what the user does as a macro.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. Recording is
        done in SolidWorks today (Tools > Macro > Record). Changes: once available,
        starts a recording session. Returns: once available, the session details under
        `data`.

        Args:
            input_data: The recording. `output_file` (where the macro is saved) is
                required; `macro_name`, `recording_mode`, the capture switches and
                `timeout_seconds` have defaults.

        Returns:
            dict[str, Any]: The session, or a `not_supported` error.
        """
        require_capability(
            adapter,
            "start_macro_recording",
            "Active adapter does not support start_macro_recording; "
            f"no recording of {input_data.macro_name} was started.",
        )
        result = await adapter.start_macro_recording(input_data.model_dump())
        require_success(result, "Failed to start recording")
        return {
            "status": "success",
            "message": f"Macro recording started: {input_data.macro_name}",
            "data": result.data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def stop_macro_recording(input_data: dict[str, Any]) -> dict[str, Any]:
        """Stop the macro recorder and save the recorded macro.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. Recording is
        done in SolidWorks today (Tools > Macro > Stop). Changes: once available, ends
        the session and writes the macro file. Returns: once available, the saved
        macro's details under `data`.

        Args:
            input_data: A mapping; nothing in it is used yet.

        Returns:
            dict[str, Any]: The saved macro, or a `not_supported` error.
        """
        require_capability(
            adapter,
            "stop_macro_recording",
            "No adapter capability exists for stopping a macro "
            "recording; this tool never started a real recording "
            "session, so there is no macro code to save.",
        )
        result = await adapter.stop_macro_recording(input_data)
        require_success(result, "Failed to stop recording")
        return {
            "status": "success",
            "message": "Macro recording completed and saved",
            "data": result.data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def execute_macro(input_data: MacroPlaybackInput) -> dict[str, Any]:
        """Run a binary SolidWorks macro (.swp, or legacy .swb) in the running SolidWorks, calling its `main` procedure once.

        Use it to run an existing macro file. VBA source (.bas, .vb, plain text, or what
        the `generate_vba_*` tools return) is refused as invalid input before SolidWorks
        sees it; to run API code given as text, write it in C# for `run_api_snippet`.
        The macro must not wait for the user (no MsgBox or forms), or the call
        hangs. It runs `repeat_count` times, `pause_between_runs` seconds apart, and
        stops at the first run that fails. Needs: SolidWorks running and the macro file
        on disk. Changes: whatever the macro does; it can't be undone from here.
        Returns: `runs_completed` and `data` from the last run, with `macro_path`,
        `module_name`, `proc_name` and `errors`.

        Args:
            input_data: The macro to run. Give `macro_path` (or `macro_file`);
                `proc_name` (main), `repeat_count` (1) and `pause_between_runs` (0 s)
                have defaults.

        Returns:
            dict[str, Any]: The run's result.
        """
        input_data = normalize_input(input_data, MacroPlaybackInput)
        require_capability(
            adapter,
            "execute_macro",
            "Active adapter does not support execute_macro; "
            f"{input_data.macro_path} was not run, and no features "
            "were created.",
        )
        payload = input_data.model_dump()
        total = input_data.repeat_count
        execution_time = 0.0
        result = None
        for run in range(total):
            if run and input_data.pause_between_runs:
                await asyncio.sleep(input_data.pause_between_runs)
            result = await adapter.execute_macro(payload)
            if not result.is_success:
                raise ToolOperationError(
                    f"Run {run + 1} of {total} failed after {run} completed: "
                    f"{result.error or 'the macro reported an error'}",
                    execution_time=execution_time + (result.execution_time or 0.0),
                    data=result.data,
                )
            execution_time += result.execution_time or 0.0
        runs = "1 run" if total == 1 else f"{total} runs"
        return {
            "status": "success",
            "message": f"Macro completed {runs}",
            "runs_completed": total,
            "data": result.data if result is not None else None,
            "execution_time": execution_time,
        }

    @mcp.tool()
    async def run_api_snippet(input_data: ApiSnippetInput) -> dict[str, Any]:
        """Compile and run a C# snippet on the user's live SolidWorks, for a job no tool does.

        Off unless the server runs with SOLIDWORKS_MCP_ALLOW_API_SNIPPETS=1;
        otherwise every call returns `not_supported`. The snippet runs in its own
        Windows PowerShell process: compiled (C# 5) against SolidWorks' interop
        assemblies and attached to the running SolidWorks (it never starts one),
        its `Run(SldWorks app)` gets the application. Look calls up first
        (`get_api_remarks`, `lookup_api_enum`, `lookup_api_method`), and check it
        with `compile_only`. If it opens or activates a document, call
        `activate_document` afterwards. Needs: Windows, SolidWorks running.
        Changes: whatever the code does to the user's documents; it can't be
        undone from here. Returns: `data` with `result` (what Run returned, as
        JSON), `result_type`, `stdout`, `stderr` and `stage`. A failure is
        `api_snippet_failed`, with the compile errors or the exception in `data`.

        Example input: ``{"code": "return ((ModelDoc2)app.ActiveDoc).GetTitle();"}``

        Args:
            input_data: The snippet. `code` is required; `timeout_s` (60 s) and
                `compile_only` (false) have defaults.

        Returns:
            dict[str, Any]: The run's outcome.
        """
        input_data = normalize_input(input_data, ApiSnippetInput)
        if not snippets_allowed():
            raise ToolOperationError(
                "run_api_snippet is off: it runs arbitrary code on the user's "
                f"SolidWorks. Set {ALLOW_ENV}=1 in the MCP server's environment and "
                "restart the server to turn it on.",
                classification=Classification(NOT_SUPPORTED, _SNIPPETS_OFF_HINT),
            )
        result = await adapter.run_api_snippet(
            input_data.code, input_data.timeout_s, input_data.compile_only
        )
        require_success(result, with_data=True)
        data = result.data or {}
        if input_data.compile_only:
            message = f"Compiled; entry point {data.get('entry_point', 'Run')}"
        else:
            message = f"Snippet ran; it returned {data.get('result_type') or 'null'}"
        return {
            "status": "success",
            "message": message,
            "data": data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def analyze_macro(input_data: MacroAnalysisInput) -> dict[str, Any]:
        """Analyse a macro's structure, dependencies and complexity, and suggest optimisations.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. Read-only.
        Returns: once available, the findings under `data`.

        Args:
            input_data: The macro. Give `macro_path` (or `macro_file`);
                `analysis_type`, `analysis_depth` and `suggest_optimizations` have
                defaults.

        Returns:
            dict[str, Any]: The findings, or a `not_supported` error.
        """
        require_capability(
            adapter,
            "analyze_macro",
            "Active adapter does not support analyze_macro; "
            f"{input_data.macro_path} was not analyzed.",
        )
        result = await adapter.analyze_macro(input_data.model_dump())
        require_success(result, "Failed to analyze macro")
        return {
            "status": "success",
            "message": f"Macro analysis completed for {input_data.macro_path}",
            "data": result.data,
            "execution_time": result.execution_time,
        }

    @mcp.tool()
    async def batch_execute_macros(input_data: MacroBatchInput) -> dict[str, Any]:
        """Run several macros in sequence against the files in a folder.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. To run one
        macro, use `execute_macro`. Changes: once available, whatever the macros do.
        Returns: once available, each run's result under `data`.

        Args:
            input_data: The batch. `macro_list` (macro file paths) is required;
                `target_directory`, `file_pattern`, `execution_order` and
                `stop_on_error` have defaults.

        Returns:
            dict[str, Any]: The results, or a `not_supported` error.
        """
        payload = input_data.model_dump()

        if hasattr(adapter, "batch_execute_macros"):
            result = await adapter.batch_execute_macros(payload)
            require_success(result, "Failed batch execution")
            return {
                "status": "success",
                "message": "Batch macro execution completed successfully",
                "data": result.data,
                "execution_time": result.execution_time,
            }

        macro_list = payload.get("macro_list", [])

        raise UnsupportedOperationError(
            "Active adapter does not support batch_execute_macros; "
            f"none of the {len(macro_list)} macro(s) were run."
        )

    @mcp.tool()
    async def optimize_macro(input_data: dict[str, Any]) -> dict[str, Any]:
        """Suggest improvements to a macro's code for speed and reliability.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. Read-only. It
        would suggest changes, not make them. Returns: once available, the suggestions
        under `data`.

        Args:
            input_data: A mapping with the macro's `macro_path` (or `macro_file`).

        Returns:
            dict[str, Any]: The suggestions, or a `not_supported` error.
        """
        payload = dict(input_data)

        if hasattr(adapter, "optimize_macro"):
            result = await adapter.optimize_macro(payload)
            require_success(result, "Failed to optimize macro")
            return {
                "status": "success",
                "message": "Macro optimization completed successfully",
                "data": result.data,
                "execution_time": result.execution_time,
            }

        macro_path = payload.get("macro_path") or payload.get("macro_file", "")

        raise UnsupportedOperationError(
            "Active adapter does not support optimize_macro; "
            f"{macro_path} was not analyzed or changed."
        )

    @mcp.tool()
    async def create_macro_library(input_data: dict[str, Any]) -> dict[str, Any]:
        """Set up a shared folder of categorised, documented macros.

        Not available yet: no adapter implements it, so every call returns an error with
        `error_type: "not_supported"`. It is planned for a future release. Changes: once
        available, creates the library folder and its index. Returns: once available,
        the library's contents under `data`.

        Args:
            input_data: A mapping with the `library_path` to create.

        Returns:
            dict[str, Any]: The library, or a `not_supported` error.
        """
        payload = dict(input_data)

        if hasattr(adapter, "create_macro_library"):
            result = await adapter.create_macro_library(payload)
            require_success(result, "Failed to create library")
            return {
                "status": "success",
                "message": "Macro library created successfully",
                "data": result.data,
                "execution_time": result.execution_time,
            }

        library_path = payload.get("library_path", "")

        raise UnsupportedOperationError(
            "Active adapter does not support create_macro_library; "
            f"no library was created at {library_path or '(unspecified path)'}."
        )

    tool_count = 9  # Macro recording and management tools, and run_api_snippet
    return tool_count
