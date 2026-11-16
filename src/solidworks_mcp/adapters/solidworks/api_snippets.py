"""Run a C# snippet against the user's running SolidWorks, in its own process.

When no tool does a job, an agent can write the API calls itself. VBA text
can't be run (``execute_macro`` runs only binary ``.swp`` files), and
PowerShell's late-bound COM fails on ``SldWorks`` (``TYPE_E_ELEMENTNOTFOUND``
on every member). What works is C# compiled against SolidWorks' .NET interop
assemblies and attached to the running SolidWorks with
``Marshal.GetActiveObject("SldWorks.Application")``.

A Windows PowerShell subprocess (``powershell.exe``, .NET Framework: it has
``GetActiveObject`` and the C# compiler) compiles a small host with
``Add-Type``. The host compiles the snippet with ``CSharpCodeProvider``
(so compile errors come back with line numbers), finds its
``static Run(SldWorks app)``, attaches to the running SolidWorks (never
starting one), calls it and writes the result as JSON. The snippet runs in
that process, not the server's, so the server's COM apartment rule (every
COM call on the connect thread) is untouched.

It runs arbitrary code on the user's live session, so the tool is off unless
``SOLIDWORKS_MCP_ALLOW_API_SNIPPETS=1``. Each run has a time limit (the
process tree is killed when it passes), and its output is capped.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ...utils.solidworks_install import install_folder
from ..base import AdapterResult, AdapterResultStatus

ALLOW_ENV = "SOLIDWORKS_MCP_ALLOW_API_SNIPPETS"
INTEROP_DIR_ENV = "SOLIDWORKS_MCP_INTEROP_DIR"

MAX_CODE_CHARS = 100_000
#: Characters of stdout and of stderr returned; the rest is dropped.
MAX_STREAM_CHARS = 20_000
#: Characters of the result's JSON returned; a longer result comes back as text.
MAX_RESULT_CHARS = 100_000
#: Seconds the snippet gets by default, and at most.
DEFAULT_TIMEOUT_S = 60.0
MAX_TIMEOUT_S = 600.0

_SLDWORKS_DLL = "SolidWorks.Interop.sldworks.dll"
_SWCONST_DLL = "SolidWorks.Interop.swconst.dll"
#: ``GetActiveObject``'s "nothing registered under that name": SolidWorks isn't running.
_MK_E_UNAVAILABLE = "0x800401E3"
_CREATE_NO_WINDOW = 0x08000000

SourceForm = Literal["class", "method", "body"]

#: Namespaces every snippet may use without a ``using`` line.
DEFAULT_USINGS = (
    "System",
    "System.Collections.Generic",
    "System.Linq",
    "System.Runtime.InteropServices",
    "SolidWorks.Interop.sldworks",
    "SolidWorks.Interop.swconst",
)

_USING = re.compile(r"^\s*using\s+(?:static\s+)?[\w.]+(?:\s*=\s*[\w.<>, ]+)?\s*;\s*$")
_TYPE_DECLARATION = re.compile(r"\b(?:class|struct)\s+[A-Za-z_]\w*")
_RUN_DECLARATION = re.compile(r"\bstatic\b[^;{}()=]*\bRun\s*\(")

#: The C# host: compiles the snippet, finds ``Run``, attaches, runs it and
#: writes the result. C# 5, the language Windows PowerShell's compiler takes.
HOST_SOURCE = r"""
using System;
using System.CodeDom.Compiler;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.CSharp;

namespace SolidWorksMcpSnippets
{
    public sealed class JsonOut
    {
        private readonly StringBuilder text = new StringBuilder("{");
        private bool first = true;

        private void Key(string name)
        {
            if (!first) text.Append(',');
            first = false;
            Quote(text, name);
            text.Append(':');
        }

        public void Raw(string name, string json) { Key(name); text.Append(json); }
        public void Text(string name, string value)
        {
            Key(name);
            if (value == null) text.Append("null"); else Quote(text, value);
        }
        public void Bool(string name, bool value) { Key(name); text.Append(value ? "true" : "false"); }
        public void Number(string name, long value)
        {
            Key(name);
            text.Append(value.ToString(CultureInfo.InvariantCulture));
        }
        public override string ToString() { return text.ToString() + "}"; }

        public static void Quote(StringBuilder text, string value)
        {
            text.Append('"');
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"': text.Append("\\\""); break;
                    case '\\': text.Append("\\\\"); break;
                    case '\n': text.Append("\\n"); break;
                    case '\r': text.Append("\\r"); break;
                    case '\t': text.Append("\\t"); break;
                    default:
                        if (c < ' ') text.Append("\\u").Append(((int)c).ToString("x4"));
                        else text.Append(c);
                        break;
                }
            }
            text.Append('"');
        }
    }

    public sealed class ValueWriter
    {
        private const int MaxChars = 400000;
        private const int MaxItems = 10000;
        private const int MaxDepth = 8;
        private readonly StringBuilder text = new StringBuilder();
        private readonly Assembly snippet;
        public bool Truncated;

        public ValueWriter(Assembly snippet) { this.snippet = snippet; }

        public string Write(object value) { Value(value, 0); return text.ToString(); }

        public static string ComName(object value)
        {
            try { return Microsoft.VisualBasic.Information.TypeName(value); }
            catch (Exception) { return "object"; }
        }

        public static string TypeName(Type type)
        {
            if (!type.IsGenericType) return type.FullName ?? type.Name;
            string name = type.Name;
            int tick = name.IndexOf('`');
            if (tick > 0) name = name.Substring(0, tick);
            List<string> arguments = new List<string>();
            foreach (Type argument in type.GetGenericArguments()) arguments.Add(TypeName(argument));
            return (type.Namespace == null ? "" : type.Namespace + ".") + name
                + "<" + string.Join(", ", arguments.ToArray()) + ">";
        }

        private void Value(object value, int depth)
        {
            if (text.Length > MaxChars) { Truncated = true; text.Append("null"); return; }
            if (value == null || value is DBNull) { text.Append("null"); return; }
            if (value is string || value is char) { JsonOut.Quote(text, value.ToString()); return; }
            if (value is bool) { text.Append((bool)value ? "true" : "false"); return; }
            if (value is Enum) { JsonOut.Quote(text, value.ToString()); return; }
            if (value is double || value is float)
            {
                double number = Convert.ToDouble(value, CultureInfo.InvariantCulture);
                if (double.IsNaN(number) || double.IsInfinity(number))
                    JsonOut.Quote(text, number.ToString(CultureInfo.InvariantCulture));
                else
                    text.Append(number.ToString("R", CultureInfo.InvariantCulture));
                return;
            }
            if (value is IConvertible && value.GetType().IsPrimitive || value is decimal)
            {
                text.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
                return;
            }
            if (value is DateTime)
            {
                JsonOut.Quote(text, ((DateTime)value).ToString("o", CultureInfo.InvariantCulture));
                return;
            }
            if (Marshal.IsComObject(value)) { JsonOut.Quote(text, "<COM " + ComName(value) + ">"); return; }
            if (depth >= MaxDepth) { Truncated = true; JsonOut.Quote(text, "<nested too deep>"); return; }
            IDictionary map = value as IDictionary;
            if (map != null)
            {
                text.Append('{');
                int count = 0;
                foreach (DictionaryEntry entry in map)
                {
                    if (count >= MaxItems) { Truncated = true; break; }
                    if (count++ > 0) text.Append(',');
                    JsonOut.Quote(text, Convert.ToString(entry.Key, CultureInfo.InvariantCulture));
                    text.Append(':');
                    Value(entry.Value, depth + 1);
                }
                text.Append('}');
                return;
            }
            IEnumerable items = value as IEnumerable;
            if (items != null)
            {
                text.Append('[');
                int count = 0;
                foreach (object item in items)
                {
                    if (count >= MaxItems) { Truncated = true; break; }
                    if (count++ > 0) text.Append(',');
                    Value(item, depth + 1);
                }
                text.Append(']');
                return;
            }
            Type type = value.GetType();
            bool anonymous = type.IsDefined(typeof(CompilerGeneratedAttribute), false);
            if (anonymous || type.Assembly == snippet)
            {
                text.Append('{');
                int count = 0;
                foreach (PropertyInfo property in type.GetProperties(BindingFlags.Public | BindingFlags.Instance))
                {
                    if (property.GetIndexParameters().Length > 0) continue;
                    if (count++ > 0) text.Append(',');
                    JsonOut.Quote(text, property.Name);
                    text.Append(':');
                    object inner;
                    try { inner = property.GetValue(value, null); }
                    catch (Exception error) { inner = "<" + error.GetType().Name + ">"; }
                    Value(inner, depth + 1);
                }
                foreach (FieldInfo field in type.GetFields(BindingFlags.Public | BindingFlags.Instance))
                {
                    if (count++ > 0) text.Append(',');
                    JsonOut.Quote(text, field.Name);
                    text.Append(':');
                    Value(field.GetValue(value), depth + 1);
                }
                text.Append('}');
                return;
            }
            JsonOut.Quote(text, Convert.ToString(value, CultureInfo.InvariantCulture));
        }
    }

    public static class Host
    {
        private static string[] references = new string[0];

        public static int Execute(string sourcePath, string resultPath, string referenceList, bool compileOnly)
        {
            JsonOut reply = new JsonOut();
            references = referenceList.Split(new char[] { '|' }, StringSplitOptions.RemoveEmptyEntries);
            AppDomain.CurrentDomain.AssemblyResolve += Resolve;
            CompilerResults compiled = Compile(sourcePath, references, Path.GetDirectoryName(resultPath));
            reply.Raw("compile_errors", Errors(compiled.Errors, true));
            reply.Raw("compile_warnings", Errors(compiled.Errors, false));
            if (compiled.Errors.HasErrors)
                return Finish(reply, resultPath, false, "compile");
            reply.Bool("compiled", true);
            Assembly snippet = compiled.CompiledAssembly;
            MethodInfo entry;
            string problem = FindEntry(snippet, out entry);
            if (entry == null)
            {
                reply.Text("error", problem);
                return Finish(reply, resultPath, false, "entry");
            }
            reply.Text("entry_point", entry.DeclaringType.FullName + "." + entry.Name
                + "(" + entry.GetParameters()[0].ParameterType.Name + ")");
            if (compileOnly)
                return Finish(reply, resultPath, true, "compiled");
            object app;
            try { app = Marshal.GetActiveObject("SldWorks.Application"); }
            catch (Exception error)
            {
                Describe(reply, error);
                return Finish(reply, resultPath, false, "attach");
            }
            return Run(entry, app, snippet, reply, resultPath);
        }

        public static int Run(MethodInfo entry, object app, Assembly snippet, JsonOut reply, string resultPath)
        {
            // Debug.Print and Trace output, as the help's examples use, goes to
            // stdout. The default listener would show a failed Debug.Assert in
            // a message box nobody can see.
            Debug.Listeners.Clear();
            Debug.Listeners.Add(new TextWriterTraceListener(Console.Out));
            Stopwatch clock = Stopwatch.StartNew();
            object result;
            try { result = entry.Invoke(null, new object[] { app }); }
            catch (TargetInvocationException error)
            {
                Describe(reply, error.InnerException ?? error);
                return Finish(reply, resultPath, false, "run");
            }
            catch (ArgumentException error)
            {
                reply.Text("error", "Run's parameter can't take the SolidWorks application ("
                    + error.Message + "); declare it SldWorks, ISldWorks or object.");
                return Finish(reply, resultPath, false, "entry");
            }
            finally { Console.Out.Flush(); }
            reply.Number("run_ms", clock.ElapsedMilliseconds);
            if (result != null)
                reply.Text("result_type", Marshal.IsComObject(result)
                    ? ValueWriter.ComName(result) : ValueWriter.TypeName(result.GetType()));
            ValueWriter writer = new ValueWriter(snippet);
            string json;
            try { json = writer.Write(result); }
            catch (Exception error)
            {
                Describe(reply, error);
                return Finish(reply, resultPath, false, "output");
            }
            reply.Raw("result", json);
            reply.Bool("result_truncated", writer.Truncated);
            return Finish(reply, resultPath, true, "done");
        }

        private static int Finish(JsonOut reply, string resultPath, bool ok, string stage)
        {
            reply.Bool("ok", ok);
            reply.Text("stage", stage);
            File.WriteAllText(resultPath, reply.ToString(), new UTF8Encoding(false));
            return ok ? 0 : 2;
        }

        private static void Describe(JsonOut reply, Exception error)
        {
            StringBuilder text = new StringBuilder("{");
            text.Append("\"type\":"); JsonOut.Quote(text, error.GetType().FullName);
            text.Append(",\"message\":"); JsonOut.Quote(text, error.Message);
            text.Append(",\"hresult\":"); JsonOut.Quote(text, "0x" + error.HResult.ToString("X8"));
            string stack = error.StackTrace ?? "";
            if (stack.Length > 4000) stack = stack.Substring(0, 4000);
            text.Append(",\"stack\":"); JsonOut.Quote(text, stack);
            text.Append('}');
            reply.Raw("exception", text.ToString());
        }

        private static CompilerResults Compile(string sourcePath, string[] extra, string workDir)
        {
            CSharpCodeProvider provider = new CSharpCodeProvider();
            CompilerParameters options = new CompilerParameters();
            // Written to disk with its .pdb beside it, so stack traces name
            // the snippet's lines (an in-memory assembly has no symbols).
            options.GenerateInMemory = false;
            options.OutputAssembly = Path.Combine(workDir, "snippet.dll");
            options.GenerateExecutable = false;
            options.IncludeDebugInformation = true;
            options.TreatWarningsAsErrors = false;
            options.WarningLevel = 3;
            options.CompilerOptions = "/define:DEBUG;TRACE";
            options.TempFiles = new TempFileCollection(workDir, false);
            string[] standard = { "System.dll", "System.Core.dll", "System.Xml.dll",
                "System.Xml.Linq.dll", "Microsoft.CSharp.dll", "Microsoft.VisualBasic.dll" };
            foreach (string name in standard) options.ReferencedAssemblies.Add(name);
            foreach (string path in extra) options.ReferencedAssemblies.Add(path);
            return provider.CompileAssemblyFromFile(options, sourcePath);
        }

        private static string Errors(CompilerErrorCollection errors, bool wanted)
        {
            StringBuilder text = new StringBuilder("[");
            int count = 0;
            foreach (CompilerError error in errors)
            {
                if (error.IsWarning == wanted || count >= 50) continue;
                if (count++ > 0) text.Append(',');
                text.Append("{\"line\":").Append(error.Line);
                text.Append(",\"column\":").Append(error.Column);
                text.Append(",\"code\":"); JsonOut.Quote(text, error.ErrorNumber);
                text.Append(",\"message\":"); JsonOut.Quote(text, error.ErrorText);
                text.Append('}');
            }
            return text.Append(']').ToString();
        }

        private static string FindEntry(Assembly snippet, out MethodInfo entry)
        {
            entry = null;
            List<MethodInfo> found = new List<MethodInfo>();
            foreach (Type type in snippet.GetTypes())
                foreach (MethodInfo method in type.GetMethods(BindingFlags.Public
                    | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.DeclaredOnly))
                    if (method.Name == "Run" && method.GetParameters().Length == 1)
                        found.Add(method);
            if (found.Count == 1) { entry = found[0]; return null; }
            if (found.Count == 0)
                return "No entry point: declare public static object Run(SldWorks app) "
                    + "in a class, or send only the method, or only its body.";
            List<string> names = new List<string>();
            foreach (MethodInfo method in found) names.Add(method.DeclaringType.FullName + ".Run");
            return "More than one static Run method with one parameter ("
                + string.Join(", ", names.ToArray()) + "); keep one.";
        }

        private static Assembly Resolve(object sender, ResolveEventArgs args)
        {
            string wanted = new AssemblyName(args.Name).Name;
            foreach (string path in references)
                if (string.Equals(Path.GetFileNameWithoutExtension(path), wanted,
                        StringComparison.OrdinalIgnoreCase) && File.Exists(path))
                    return Assembly.LoadFrom(path);
            return null;
        }
    }
}
"""

#: Compiles the host, then hands over to it. ``{work}``, ``{references}`` and
#: ``{compile_only}`` are filled in; PowerShell runs it from -EncodedCommand,
#: which no execution policy blocks.
RUNNER_SCRIPT = """
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {{ [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) }} catch {{ }}
$work = '{work}'
try {{
    Add-Type -Path (Join-Path $work 'host.cs') -ReferencedAssemblies 'Microsoft.VisualBasic' -IgnoreWarnings
}} catch {{
    [Console]::Error.WriteLine('The snippet host did not compile: ' + $_.Exception.Message)
    exit 3
}}
$code = [SolidWorksMcpSnippets.Host]::Execute((Join-Path $work 'snippet.cs'), (Join-Path $work 'result.json'), '{references}', ${compile_only})
exit $code
"""


class SnippetSetupError(Exception):
    """What a snippet needs to run is missing (Windows PowerShell, the interop DLLs)."""


@dataclass(frozen=True)
class PreparedSource:
    """A snippet as compiled: wrapped in a class when it was only a method or a body.

    Attributes:
        source (str): The C# compiled; ``#line`` directives keep the
            snippet's own line numbers in compile errors and stack traces.
        form (SourceForm): ``class`` (sent whole), ``method`` (a ``Run``
            method, wrapped in a class) or ``body`` (statements, wrapped in
            ``Run``).
    """

    source: str
    form: SourceForm


def prepare_source(code: str) -> PreparedSource:
    """Wrap a snippet for compiling, adding the usual ``using`` lines.

    Three forms are accepted: whole classes declaring
    ``static object Run(SldWorks app)``; that method alone; or just its
    body (statements using ``app``; it returns null unless it returns
    something). Leading ``using`` lines move to the top of the file, and the
    namespaces in :data:`DEFAULT_USINGS` are added when missing.

    Args:
        code (str): The snippet.

    Returns:
        PreparedSource: The source to compile and the form it was read as.
    """
    lines = code.replace("\r\n", "\n").split("\n")
    usings: list[str] = []
    for position, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        if not _USING.match(line):
            break
        usings.append(stripped)
        lines[position] = ""  # keep the line, so line numbers hold
    body = "\n".join(lines)
    header = list(dict.fromkeys(usings))
    header += [f"using {ns};" for ns in DEFAULT_USINGS if f"using {ns};" not in header]
    mark = '#line 1 "snippet.cs"'
    form: SourceForm
    if _TYPE_DECLARATION.search(body):
        form, wrapped = "class", [mark, body]
    elif _RUN_DECLARATION.search(body):
        form, wrapped = "method", ["public static class Snippet", "{", mark, body, "}"]
    else:
        form = "body"
        wrapped = [
            "public static class Snippet",
            "{",
            "    public static object Run(SldWorks app)",
            "    {",
            mark,
            body,
            "#line hidden",
            # Unreachable after a body that returns; that is expected.
            "#pragma warning disable 162",
            "        return null;",
            "    }",
            "}",
        ]
    return PreparedSource("\n".join([*header, *wrapped]) + "\n", form)


def snippets_allowed(env: dict[str, str] | None = None) -> bool:
    """Whether ``SOLIDWORKS_MCP_ALLOW_API_SNIPPETS`` turns snippets on.

    Args:
        env (dict[str, str] | None): The environment; ``os.environ`` by default.

    Returns:
        bool: True for ``1``, ``true``, ``yes`` or ``on``.
    """
    value = (os.environ if env is None else env).get(ALLOW_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class InteropAssemblies:
    """The interop assemblies a snippet is compiled against.

    Attributes:
        sldworks (Path): ``SolidWorks.Interop.sldworks.dll``.
        swconst (Path): ``SolidWorks.Interop.swconst.dll``.
    """

    sldworks: Path
    swconst: Path

    def paths(self) -> list[Path]:
        """Both assemblies, ``sldworks`` first."""
        return [self.sldworks, self.swconst]


def find_interop_assemblies(
    year: int | None = None,
    env: dict[str, str] | None = None,
    locate: Callable[[int | None], tuple[int | None, Path | None]] = install_folder,
) -> InteropAssemblies:
    """Find SolidWorks' interop assemblies (``SOLIDWORKS_MCP_INTEROP_DIR`` overrides).

    Args:
        year (int | None): The SolidWorks release; None for the newest.
        env (dict[str, str] | None): The environment; ``os.environ`` by default.
        locate (Callable): Finds a release's install folder (tests pass a fake).

    Returns:
        InteropAssemblies: The two assemblies.

    Raises:
        SnippetSetupError: They weren't found.
    """
    env = dict(os.environ) if env is None else env
    explicit = env.get(INTEROP_DIR_ENV, "").strip()
    if explicit:
        folders = [Path(explicit)]
    else:
        _, folder = locate(year)
        folders = [folder, folder / "api" / "redist"] if folder else []
    for folder in folders:
        sldworks, swconst = folder / _SLDWORKS_DLL, folder / _SWCONST_DLL
        if sldworks.is_file() and swconst.is_file():
            return InteropAssemblies(sldworks, swconst)
    where = ", ".join(str(f) for f in folders) or "no SolidWorks install was found"
    raise SnippetSetupError(
        f"{_SLDWORKS_DLL} and {_SWCONST_DLL} weren't found ({where}). Set "
        f"{INTEROP_DIR_ENV} to the folder that holds them."
    )


def find_powershell(
    env: dict[str, str] | None = None, platform: str | None = None
) -> Path:
    """Windows PowerShell 5.1, which has the .NET Framework snippets need.

    PowerShell 7 runs on .NET, which has neither
    ``Marshal.GetActiveObject`` nor ``CSharpCodeProvider``.

    Args:
        env (dict[str, str] | None): The environment; ``os.environ`` by default.
        platform (str | None): ``sys.platform``; tests pass another value.

    Returns:
        Path: ``powershell.exe``.

    Raises:
        SnippetSetupError: Not on Windows, or it isn't there.
    """
    if (platform or sys.platform) != "win32":
        raise SnippetSetupError("API snippets need Windows and Windows PowerShell.")
    # os.environ itself, not a copy: it reads names regardless of case.
    root = Path((os.environ if env is None else env).get("SystemRoot", r"C:\Windows"))
    path = root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not path.is_file():
        raise SnippetSetupError(f"Windows PowerShell isn't at {path}.")
    return path


def _ps_literal(text: str) -> str:
    """``text`` inside a PowerShell single-quoted string."""
    return text.replace("'", "''")


def runner_script(work: Path, assemblies: InteropAssemblies, compile_only: bool) -> str:
    """The PowerShell that compiles the host and runs the snippet in ``work``.

    Args:
        work (Path): The run's folder (``host.cs``, ``snippet.cs``).
        assemblies (InteropAssemblies): What the snippet references.
        compile_only (bool): Stop after compiling.

    Returns:
        str: The script.
    """
    references = "|".join(str(p) for p in assemblies.paths())
    return RUNNER_SCRIPT.format(
        work=_ps_literal(str(work)),
        references=_ps_literal(references),
        compile_only="true" if compile_only else "false",
    )


def kill_tree(pid: int) -> None:
    """Kill a process and its children (the C# compiler PowerShell starts).

    Args:
        pid (int): The process.
    """
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        timeout=15,
        check=False,
        creationflags=_CREATE_NO_WINDOW,
    )


def _read_capped(path: Path, limit: int) -> tuple[str, bool]:
    """The first ``limit`` characters of a file, and whether there was more."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit * 4 + 4)
    except OSError:
        return "", False
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    if len(text) > limit:
        return text[:limit], True
    return text, len(raw) > limit * 4


@dataclass
class SnippetRunner:
    """Runs snippets in Windows PowerShell subprocesses.

    Attributes:
        assemblies (InteropAssemblies): What snippets compile against.
        powershell (Path): ``powershell.exe``.
        popen (Callable): ``subprocess.Popen`` (tests pass a fake).
        kill (Callable): Kills a process tree (tests pass a fake).
        poll_s (float): Seconds between checks while a snippet runs.
    """

    assemblies: InteropAssemblies
    powershell: Path
    popen: Callable[..., Any] = subprocess.Popen
    kill: Callable[[int], None] = kill_tree
    poll_s: float = 0.25

    @classmethod
    def for_install(cls, year: int | None = None) -> SnippetRunner:
        """A runner for the installed SolidWorks.

        Args:
            year (int | None): The release whose interop assemblies to use.

        Returns:
            SnippetRunner: The runner.

        Raises:
            SnippetSetupError: Windows PowerShell or the assemblies are missing.
        """
        return cls(find_interop_assemblies(year), find_powershell())

    def run(
        self,
        code: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        compile_only: bool = False,
        should_stop: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        """Compile and run one snippet; never raises for the snippet's own faults.

        Args:
            code (str): The C# snippet.
            timeout_s (float): Seconds before the process tree is killed.
            compile_only (bool): Compile and find ``Run``; don't attach or run.
            should_stop (Callable | None): Called while waiting; raises to
                stop the run (``limits.check``), after the process is killed.

        Returns:
            dict[str, Any]: The outcome: ``ok``, ``stage`` (``done``,
            ``compiled``, or where it failed: ``compile``, ``entry``,
            ``attach``, ``run``, ``output``, ``timeout``, ``host``), the
            result, stdout and stderr, and what failed.
        """
        prepared = prepare_source(code)
        work = Path(tempfile.mkdtemp(prefix="sw_mcp_snippet_"))
        started = time.monotonic()
        try:
            (work / "snippet.cs").write_text(prepared.source, encoding="utf-8")
            (work / "host.cs").write_text(HOST_SOURCE, encoding="utf-8")
            script = runner_script(work, self.assemblies, compile_only)
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            command = [
                str(self.powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-OutputFormat",
                "Text",
                "-EncodedCommand",
                encoded,
            ]
            with (
                (work / "stdout.txt").open("wb") as out,
                (work / "stderr.txt").open("wb") as err,
            ):
                process = self.popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    cwd=str(work),
                    creationflags=_CREATE_NO_WINDOW,
                )
                timed_out = self._wait(process, timeout_s, should_stop)
            outcome = self._outcome(work, process, timed_out, timeout_s)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        outcome["source_form"] = prepared.form
        outcome["duration_s"] = round(time.monotonic() - started, 3)
        return outcome

    def _wait(
        self,
        process: Any,
        timeout_s: float,
        should_stop: Callable[[], None] | None,
    ) -> bool:
        """Wait for the process; kill its tree on timeout or when told to stop.

        Returns:
            bool: True when it ran out of time.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                process.wait(timeout=self.poll_s)
                return False
            except subprocess.TimeoutExpired:
                pass
            if should_stop is not None:
                try:
                    should_stop()
                except BaseException:
                    self._stop(process)
                    raise
            if time.monotonic() >= deadline:
                self._stop(process)
                return True

    def _stop(self, process: Any) -> None:
        """Kill the process tree, then the process itself if it is still there."""
        try:
            self.kill(process.pid)
        except (OSError, subprocess.SubprocessError):
            pass
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def _outcome(
        self, work: Path, process: Any, timed_out: bool, timeout_s: float
    ) -> dict[str, Any]:
        """Read what the run left: the result file, stdout and stderr."""
        stdout, stdout_cut = _read_capped(work / "stdout.txt", MAX_STREAM_CHARS)
        stderr, stderr_cut = _read_capped(work / "stderr.txt", MAX_STREAM_CHARS)
        outcome: dict[str, Any] = {"exit_code": process.returncode}
        try:
            reply = json.loads((work / "result.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            reply = None
        if isinstance(reply, dict):
            outcome.update(
                (key, value)
                for key, value in reply.items()
                if not (key in ("compile_errors", "compile_warnings") and not value)
            )
        exception = outcome.get("exception")
        if isinstance(exception, dict) and isinstance(exception.get("stack"), str):
            # "in <run folder>\snippet.cs:line 3": the folder is gone by now.
            folder = re.compile(re.escape(str(work)) + r"[\\/]", re.IGNORECASE)
            exception["stack"] = folder.sub("", exception["stack"])
        if timed_out:
            outcome.update(ok=False, stage="timeout", timeout_s=timeout_s)
        elif not isinstance(reply, dict):
            outcome.update(ok=False, stage="host")
        outcome["stdout"] = stdout
        outcome["stderr"] = stderr
        if stdout_cut:
            outcome["stdout_truncated"] = True
        if stderr_cut:
            outcome["stderr_truncated"] = True
        _cap_result(outcome)
        return outcome


def _cap_result(outcome: dict[str, Any]) -> None:
    """Replace a result too long to return with the start of its JSON text."""
    if "result" not in outcome:
        return
    text = json.dumps(outcome["result"], ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return
    outcome["result"] = None
    outcome["result_text"] = text[:MAX_RESULT_CHARS]
    outcome["result_truncated"] = True


def _first_compile_error(outcome: dict[str, Any]) -> str:
    errors = outcome.get("compile_errors") or []
    if not errors:
        return "it did not compile"
    first = errors[0]
    return (
        f"{len(errors)} compile error(s); first at line {first.get('line')}: "
        f"{first.get('code')} {first.get('message')}. The compiler is C# 5 "
        '(Windows PowerShell): no $"..." strings, ?. or nameof'
    )


def failure_message(outcome: dict[str, Any]) -> str:
    """The error message for a snippet that didn't finish.

    Args:
        outcome (dict[str, Any]): What :meth:`SnippetRunner.run` returned.

    Returns:
        str: ``API snippet failed (<stage>): ...``, or the not-running
        message the classifier reads as ``solidworks_unavailable``.
    """
    stage = outcome.get("stage", "host")
    exception = outcome.get("exception") or {}
    hresult = str(exception.get("hresult", "")).lower()
    if stage == "attach" and hresult == _MK_E_UNAVAILABLE.lower():
        return (
            "SolidWorks is not running: the snippet found no running SolidWorks "
            "to attach to, and it never starts one. Nothing ran."
        )
    detail: str
    if stage == "compile":
        detail = _first_compile_error(outcome)
    elif stage in ("run", "attach", "output"):
        line = re.search(r"snippet\.cs:line (\d+)", str(exception.get("stack", "")))
        detail = (
            f"{exception.get('type', 'error')}: {exception.get('message', '')}"
            f" (HRESULT {exception.get('hresult', '?')})"
            + (f" at line {line.group(1)}" if line else "")
        )
    elif stage == "timeout":
        detail = (
            f"it ran past timeout_s ({outcome.get('timeout_s', 0):g} s) and was "
            "stopped; a SolidWorks call it had started may still finish"
        )
    elif stage == "entry":
        detail = str(outcome.get("error", "no Run method"))
    else:
        tail = (outcome.get("stderr") or "").strip()[-600:]
        detail = f"the PowerShell runner stopped before the snippet ran: {tail or 'no output'}"
    return f"API snippet failed ({stage}): {detail}"


def snippet_result(
    outcome: dict[str, Any], execution_time: float | None
) -> AdapterResult[dict[str, Any]]:
    """An adapter result for a run's outcome.

    Args:
        outcome (dict[str, Any]): What :meth:`SnippetRunner.run` returned.
        execution_time (float | None): The run's time.

    Returns:
        AdapterResult[dict[str, Any]]: Success with the outcome, or an error
        whose ``data`` is the outcome (stdout, stderr, compile errors).
    """
    if outcome.get("ok"):
        return AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data=outcome,
            execution_time=execution_time,
        )
    return AdapterResult(
        status=AdapterResultStatus.ERROR,
        error=failure_message(outcome),
        data=outcome,
        execution_time=execution_time,
    )


def setup_failure(error: SnippetSetupError) -> AdapterResult[dict[str, Any]]:
    """The adapter result for a snippet that can't be run on this machine."""
    return AdapterResult(
        status=AdapterResultStatus.ERROR,
        error=f"API snippet failed (setup): {error}",
        data={"ok": False, "stage": "setup"},
    )
