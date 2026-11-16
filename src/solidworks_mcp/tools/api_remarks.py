"""The Remarks, parameters and examples of the local SolidWorks API help.

The type library (``type_library``) gives names and signatures, but the
rules that make a call work are in the help's Remarks: which selection mark
each argument takes, when to pass null, what to call first. They are in the
installed ``api\\sldworksapi.chm``. This module decompiles a copy of it with
``hh.exe -decompile`` (which fails on paths with spaces, so the copy goes to
one without), reads each member topic's summary, parameters, return value,
Remarks, example links and availability, and each linked example's code, and
saves them as an index under ``%LOCALAPPDATA%\\solidworks_mcp\\api_remarks\\``
(``SOLIDWORKS_MCP_API_REMARKS_DIR`` overrides).

The help is copyrighted and large, so the index is built on the user's
machine from their own install and never shipped. Build it with
``get_api_remarks`` (it builds on first use) or from a shell::

    python -m solidworks_mcp.tools.api_remarks [--year 2026] [--chm PATH]

Nothing here starts, attaches to or calls SolidWorks.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..utils.solidworks_install import install_folder
from .tool_boundary import ToolOperationError

#: Raise this when the index format changes; older indexes are rebuilt.
INDEX_FORMAT = "1"
CHM_NAME = "sldworksapi.chm"
_CHM_ENV = "SOLIDWORKS_MCP_API_CHM"
_DIR_ENV = "SOLIDWORKS_MCP_API_REMARKS_DIR"
#: How long hh.exe may take to decompile (about 30 s for SolidWorks 2026).
_DECOMPILE_TIMEOUT_S = 900

Language = Literal["csharp", "vbnet", "vba", "cpp"]
_LANGUAGES: dict[str, Language] = {
    "c#": "csharp",
    "vb.net": "vbnet",
    "vba": "vba",
    "c++": "cpp",
    "c++/cli": "cpp",
}

#: Topic files named ``<assembly>~<namespace>.<Interface>[~<Member>].html``.
_TOPIC = re.compile(
    r"^SolidWorks\.Interop\.sldworks~SolidWorks\.Interop\.sldworks\."
    r"(?P<interface>[A-Za-z_]\w*?)(?:~(?P<member>[A-Za-z_]\w*))?\.html?$",
    re.IGNORECASE,
)
#: Interface pages that only list members, not one topic.
_LIST_PAGE = re.compile(r"_(members|methods|properties|events|namespace)$", re.I)
_HEADING = re.compile(r'<h1 class="heading">(.*?)</h1>', re.S | re.I)
_SUMMARY_START = re.compile(r'<div class="saveHistory"[^>]*>\s*</div>', re.I)
_PAGE_END = re.compile(r'<div id="pagefooter"', re.I)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_KIND = re.compile(r"\s(Method|Property|Event|Interface)\b", re.I)
_H4 = re.compile(r"<h4 class=\"?dxh4\"?>(.*?)</h4>", re.S | re.I)
_PARAMETER = re.compile(
    r"<DT>(.*?)</DT>\s*<DD>(.*?)</DD>(?=\s*(?:<DT>|</DL>))", re.S | re.I
)
_LINK = re.compile(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_PRE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_CHARSET = re.compile(rb"charset=[\"']?([\w-]+)", re.I)
_LANGUAGE_IN_TITLE = re.compile(r"\(([^()]+)\)\s*$")
_BOILERPLATE = re.compile(r"^NOTE: Click the Members link.*$", re.M)


class RemarksIndexError(ToolOperationError):
    """The help index can't be built or read; the message says what to do."""


class ApiExample(BaseModel):
    """A help example linked from a topic.

    Attributes:
        title (str): The link text, e.g. ``Combine Bodies (C#)``.
        topic (str): The example's topic file, for ``example``.
        language (Language | None): ``csharp``, ``vbnet``, ``vba`` or ``cpp``.
    """

    title: str = Field(description="Example title, e.g. 'Combine Bodies (C#)'")
    topic: str = Field(description="Its topic file; pass it as `example` for its code")
    language: Language | None = Field(
        default=None, description="csharp, vbnet, vba or cpp"
    )


class ApiParameterHelp(BaseModel):
    """One parameter's help text.

    Attributes:
        name (str): The parameter.
        description (str): What the help says about it.
    """

    name: str = Field(description="Parameter name")
    description: str = Field(description="What the help says about it")


class ApiTopic(BaseModel):
    """One help topic: an interface or one of its members.

    Attributes:
        name (str): The member or interface name.
        kind (str): ``method``, ``property``, ``event`` or ``interface``.
        summary (str): The topic's first paragraph.
        parameters (list[ApiParameterHelp]): The parameters, in order.
        returns (str | None): The return or property value.
        remarks (str | None): The Remarks section.
        accessors (str | None): How to get the interface (interfaces only).
        examples (list[ApiExample]): The linked examples.
        see_also (list[str]): Related topics' titles.
        availability (str | None): The release that added it.
    """

    name: str
    kind: str
    summary: str = ""
    parameters: list[ApiParameterHelp] = Field(default_factory=list)
    returns: str | None = None
    remarks: str | None = None
    accessors: str | None = None
    examples: list[ApiExample] = Field(default_factory=list)
    see_also: list[str] = Field(default_factory=list)
    availability: str | None = None


class ExampleCode(BaseModel):
    """An example topic's text and code.

    Attributes:
        topic (str): Its topic file.
        title (str): Its title.
        description (str): The text before the code.
        code (str): The code, as shown in the help.
    """

    topic: str
    title: str
    description: str = ""
    code: str = ""


class _Text(HTMLParser):
    """HTML to plain text: paragraphs, list items and table rows on their own lines."""

    _BLOCKS = frozenset(
        {"p", "div", "br", "table", "thead", "tbody", "h1", "h2", "h3", "h4"}
        | {"dl", "dt", "dd", "pre", "ol", "ul"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[list[Any]] = []
        self.skipping = 0
        self.cells = 0
        # A list item's "- " or "1. ", written with its first text so a
        # paragraph inside the item doesn't leave the marker on a line alone.
        self.marker = ""

    def _break(self) -> None:
        self.parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self.skipping += 1
        elif tag in ("ol", "ul"):
            self.lists.append([tag, 0])
            self._break()
        elif tag == "li":
            self._break()
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self.marker = f"{self.lists[-1][1]}. "
            else:
                self.marker = "- "
        elif tag == "tr":
            self._break()
            self.cells = 0
        elif tag in ("td", "th"):
            if self.cells:
                self.parts.append(" | ")
            self.cells += 1
        elif tag in self._BLOCKS:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self.skipping = max(0, self.skipping - 1)
        elif tag in ("ol", "ul"):
            if self.lists:
                self.lists.pop()
            self._break()
        elif tag in self._BLOCKS or tag in ("li", "tr"):
            self._break()

    def handle_data(self, data: str) -> None:
        if self.skipping:
            return
        text = re.sub(r"\s+", " ", data.replace("\xa0", " "))
        if self.marker and text.strip():
            self.parts.append(self.marker)
            self.marker = ""
        self.parts.append(text)

    def text(self) -> str:
        lines = [
            re.sub(r" {2,}", " ", line).strip()
            for line in "".join(self.parts).split("\n")
        ]
        joined = "\n".join(line for line in lines if line)
        return joined.strip()


def html_to_text(fragment: str) -> str:
    """Plain text of an HTML fragment.

    Args:
        fragment (str): The HTML.

    Returns:
        str: Its text, one paragraph, list item or table row per line
        (cells joined by `` | ``).
    """
    parser = _Text()
    parser.feed(fragment)
    parser.close()
    return parser.text()


def _code_text(fragment: str) -> str:
    """The code in a ``<pre>`` block, with its line breaks and indentation."""
    text = html.unescape(_TAG.sub("", fragment)).replace("\xa0", " ")
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def decode_topic(raw: bytes) -> str:
    """Decode a help topic by the charset it declares (UTF-8 if none).

    Args:
        raw (bytes): The file's bytes.

    Returns:
        str: Its text.
    """
    match = _CHARSET.search(raw[:2048])
    encoding = match.group(1).decode("ascii") if match else "utf-8"
    try:
        text = raw.decode(encoding, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return text.lstrip("\ufeff")


def _language(title: str) -> Language | None:
    match = _LANGUAGE_IN_TITLE.search(title)
    return _LANGUAGES.get(match.group(1).strip().lower()) if match else None


def _sections(page: str) -> tuple[str, dict[str, str]]:
    """Split a topic into its summary and its sections by heading.

    Returns:
        tuple[str, dict[str, str]]: The summary HTML, and lowercased heading
        -> section HTML.
    """
    headings = list(_HEADING.finditer(page))
    start = _SUMMARY_START.search(page)
    summary_from = start.end() if start else 0
    summary_to = headings[0].start() if headings else len(page)
    end = _PAGE_END.search(page)
    page_end = end.start() if end else len(page)
    sections: dict[str, str] = {}
    for position, heading in enumerate(headings):
        stop = (
            headings[position + 1].start() if position + 1 < len(headings) else page_end
        )
        sections[html_to_text(heading.group(1)).lower()] = page[heading.end() : stop]
    return page[summary_from:summary_to], sections


def _h4_blocks(section: str) -> dict[str, str]:
    """Lowercased ``<h4>`` title -> the HTML after it, up to the next one."""
    blocks: dict[str, str] = {}
    marks = list(_H4.finditer(section))
    for position, mark in enumerate(marks):
        stop = marks[position + 1].start() if position + 1 < len(marks) else None
        blocks[html_to_text(mark.group(1)).lower()] = section[mark.end() : stop]
    return blocks


def _or_none(text: str) -> str | None:
    return text or None


def parse_topic(page: str, name: str) -> ApiTopic:
    """Read one interface or member topic.

    Args:
        page (str): The topic's HTML.
        name (str): The interface or member it documents.

    Returns:
        ApiTopic: What the topic says.
    """
    title_match = _TITLE.search(page)
    title = html_to_text(title_match.group(1)) if title_match else name
    kind_match = _KIND.search(f" {title}")
    summary_html, sections = _sections(page)
    blocks = _h4_blocks(sections.get(".net syntax", ""))
    parameters = [
        ApiParameterHelp(name=html_to_text(dt_), description=html_to_text(dd))
        for dt_, dd in _PARAMETER.findall(blocks.get("parameters", ""))
    ]
    returns = blocks.get("return value", blocks.get("property value", ""))
    examples = [
        ApiExample(
            title=html_to_text(text),
            topic=href.rsplit("/", 1)[-1],
            language=_language(html_to_text(text)),
        )
        for href, text in _LINK.findall(sections.get("example", ""))
        + _LINK.findall(sections.get("examples", ""))
    ]
    see_also = [
        text
        for _, raw in _LINK.findall(sections.get("see also", ""))
        if (text := html_to_text(raw))
    ]
    return ApiTopic(
        name=name,
        kind=kind_match.group(1).lower() if kind_match else "interface",
        summary=_BOILERPLATE.sub("", html_to_text(summary_html)).strip(),
        parameters=parameters,
        returns=_or_none(html_to_text(returns)),
        remarks=_or_none(html_to_text(sections.get("remarks", ""))),
        accessors=_or_none(html_to_text(sections.get("accessors", ""))),
        examples=examples,
        see_also=see_also,
        availability=_or_none(html_to_text(sections.get("availability", ""))),
    )


def parse_example(page: str, topic: str) -> ExampleCode:
    """Read one example topic: its title, the text before the code, the code.

    Args:
        page (str): The example's HTML.
        topic (str): Its file name.

    Returns:
        ExampleCode: The example.
    """
    title_match = _TITLE.search(page)
    blocks = list(_PRE.finditer(page))
    before = page[: blocks[0].start()] if blocks else page
    body = re.split(r"<body[^>]*>", before, maxsplit=1, flags=re.I)[-1]
    description = "\n".join(
        html_to_text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", body, re.S | re.I)
    )
    return ExampleCode(
        topic=topic,
        title=html_to_text(title_match.group(1)) if title_match else topic,
        description=description.strip(),
        code="\n\n".join(_code_text(block.group(1)) for block in blocks),
    )


def parse_help_folder(
    folder: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, ExampleCode]]:
    """Read every interface and member topic in a decompiled help folder.

    Args:
        folder (Path): Where ``hh.exe -decompile`` put the topics.

    Returns:
        tuple: Interface -> its record (``topic`` and ``members``), and
        example topic -> its code, for the examples the topics link to.
    """
    interfaces: dict[str, dict[str, Any]] = {}
    linked: set[str] = set()
    for path in sorted(folder.iterdir()):
        match = _TOPIC.match(path.name)
        if match is None or _LIST_PAGE.search(match.group("interface")):
            continue
        interface, member = match.group("interface"), match.group("member")
        topic = parse_topic(decode_topic(path.read_bytes()), member or interface)
        linked.update(example.topic for example in topic.examples)
        record = interfaces.setdefault(interface, {"topic": None, "members": {}})
        if member:
            record["members"][member] = topic.model_dump(exclude_defaults=True)
        else:
            record["topic"] = topic.model_dump(exclude_defaults=True)
    examples: dict[str, ExampleCode] = {}
    for topic_name in sorted(linked):
        path = folder / topic_name
        if path.is_file():
            examples[topic_name] = parse_example(
                decode_topic(path.read_bytes()), topic_name
            )
    return interfaces, examples


# --- Where things are ------------------------------------------------------


def remarks_root(env: dict[str, str] | None = None) -> Path:
    """The folder that holds the indexes (``SOLIDWORKS_MCP_API_REMARKS_DIR`` overrides).

    Args:
        env (dict[str, str] | None): The environment; ``os.environ`` by default.

    Returns:
        Path: ``%LOCALAPPDATA%\\solidworks_mcp\\api_remarks``.
    """
    env = dict(os.environ) if env is None else env
    explicit = env.get(_DIR_ENV, "").strip()
    if explicit:
        return Path(explicit)
    base = env.get("LOCALAPPDATA", "").strip() or str(Path.home())
    return Path(base) / "solidworks_mcp" / "api_remarks"


def find_api_chm(
    year: int | None = None,
    env: dict[str, str] | None = None,
    locate: Callable[[int | None], tuple[int | None, Path | None]] = install_folder,
) -> tuple[int | None, Path]:
    """Find the API help to index (``SOLIDWORKS_MCP_API_CHM`` overrides).

    Args:
        year (int | None): The SolidWorks release; None for the newest.
        env (dict[str, str] | None): The environment; ``os.environ`` by default.
        locate (Callable): Finds a release's install folder (tests pass a fake).

    Returns:
        tuple[int | None, Path]: The release's year (None when unknown) and
        the ``sldworksapi.chm`` path.

    Raises:
        RemarksIndexError: No help file was found.
    """
    env = dict(os.environ) if env is None else env
    explicit = env.get(_CHM_ENV, "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise RemarksIndexError(f"{_CHM_ENV} names {path}, which doesn't exist.")
        return year, path
    found_year, folder = locate(year)
    if folder is None:
        raise RemarksIndexError(
            "SolidWorks isn't installed here, so there is no API help to read "
            f"Remarks from. Set {_CHM_ENV} to a copy of {CHM_NAME}."
        )
    path = folder / "api" / CHM_NAME
    if not path.is_file():
        raise RemarksIndexError(
            f"{path} doesn't exist; the API help wasn't installed with SolidWorks. "
            f"Set {_CHM_ENV} to a copy of {CHM_NAME}."
        )
    return found_year, path


def index_dir(year: int | None, root: Path | None = None) -> Path:
    """The folder of one release's index.

    Args:
        year (int | None): The release; None when unknown.
        root (Path | None): The indexes' folder; :func:`remarks_root` by default.

    Returns:
        Path: ``<root>/<year>`` (``<root>/default`` without a year).
    """
    return (root or remarks_root()) / (str(year) if year else "default")


def build_command(year: int | None = None) -> str:
    """The shell command that builds the index, for messages."""
    suffix = f" --year {year}" if year else ""
    return f"python -m solidworks_mcp.tools.api_remarks{suffix}"


# --- Building --------------------------------------------------------------


def _short_path(path: Path, kernel32: Any = None) -> Path:
    """``path`` without spaces (its 8.3 form), for hh.exe.

    Args:
        path (Path): An existing folder.
        kernel32 (Any): ``kernel32``-like (tests pass a fake).

    Returns:
        Path: The same folder, named without spaces.

    Raises:
        RemarksIndexError: It has spaces and no short form.
    """
    if " " not in str(path):
        return path
    kernel32 = kernel32 or ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    short = buffer.value if length else ""
    if not short or " " in short:
        raise RemarksIndexError(
            f"hh.exe can't decompile into {path} (it has spaces, and no short "
            "name); set TEMP to a folder without spaces and try again."
        )
    return Path(short)


def decompile_chm(chm: Path, out: Path) -> None:
    """Decompile a CHM with ``hh.exe -decompile`` (paths must have no spaces).

    Args:
        chm (Path): The help file.
        out (Path): An empty folder for the topics.

    Raises:
        RemarksIndexError: hh.exe is missing, ran too long, or wrote nothing.
    """
    hh = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "hh.exe"
    if not hh.is_file():
        raise RemarksIndexError(f"{hh} isn't there, so the API help can't be read.")
    try:
        subprocess.run(
            [str(hh), "-decompile", str(out), str(chm)],
            timeout=_DECOMPILE_TIMEOUT_S,
            check=False,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemarksIndexError(
            f"hh.exe ran over {_DECOMPILE_TIMEOUT_S} s decompiling {chm}."
        ) from exc
    if not any(out.glob("*.htm*")):
        raise RemarksIndexError(f"hh.exe wrote no topics from {chm} into {out}.")


def _chm_stamp(chm: Path) -> dict[str, Any]:
    stat = chm.stat()
    return {"path": str(chm), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _file_name(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )


def build_index(
    chm: Path,
    target: Path,
    year: int | None = None,
    decompile: Callable[[Path, Path], None] | None = None,
    short_path: Callable[[Path], Path] | None = None,
) -> dict[str, Any]:
    """Build the index of one help file into ``target``, replacing what is there.

    Args:
        chm (Path): ``sldworksapi.chm``.
        target (Path): The index folder (see :func:`index_dir`).
        year (int | None): The SolidWorks release it belongs to.
        decompile (Callable | None): Decompiles a CHM into a folder;
            :func:`decompile_chm` by default.
        short_path (Callable | None): A path without spaces; the 8.3 name by
            default.

    Returns:
        dict[str, Any]: The manifest written: source file, counts, build time.

    Raises:
        RemarksIndexError: The help couldn't be decompiled.
    """
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sw_mcp_chm_") as scratch:
        work = (short_path or _short_path)(Path(scratch))
        copy = work / "api.chm"
        shutil.copyfile(chm, copy)
        topics = work / "topics"
        topics.mkdir()
        (decompile or decompile_chm)(copy, topics)
        interfaces, examples = parse_help_folder(topics)

    staging = target.with_name(f"{target.name}.building-{os.getpid()}")
    shutil.rmtree(staging, ignore_errors=True)
    (staging / "interfaces").mkdir(parents=True)
    (staging / "examples").mkdir()
    for name, record in interfaces.items():
        _write_json(staging / "interfaces" / f"{_file_name(name)}.json", record)
    for name, example in examples.items():
        _write_json(
            staging / "examples" / f"{_file_name(name)}.json", example.model_dump()
        )
    manifest = {
        "format": INDEX_FORMAT,
        "source": _chm_stamp(chm),
        "year": year,
        "built": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "build_seconds": round(time.monotonic() - started, 1),
        "interfaces": sorted(interfaces),
        "member_count": sum(len(r["members"]) for r in interfaces.values()),
        "example_count": len(examples),
    }
    _write_json(staging / "manifest.json", manifest)
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(target)
    return manifest


# --- Reading ---------------------------------------------------------------


def read_manifest(folder: Path) -> dict[str, Any] | None:
    """The index's manifest, or None when there is no usable index there.

    Args:
        folder (Path): The index folder.

    Returns:
        dict[str, Any] | None: The manifest, when it is this format.
    """
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("format") != INDEX_FORMAT:
        return None
    return manifest


def is_current(manifest: dict[str, Any], chm: Path) -> bool:
    """Whether an index was built from this help file as it is now.

    A service pack replaces the help in place, so its size and time are
    compared, not only its path.
    """
    try:
        stamp = _chm_stamp(chm)
    except OSError:
        return True
    source = manifest.get("source") or {}
    return all(source.get(key) == stamp[key] for key in ("size", "mtime_ns"))


def resolve_interface(manifest: dict[str, Any], name: str) -> str | None:
    """The indexed interface for a name, ignoring case and a missing ``I``.

    Args:
        manifest (dict[str, Any]): The index manifest.
        name (str): ``IFeatureManager``, ``ifeaturemanager`` or ``FeatureManager``.

    Returns:
        str | None: The name as indexed, or None.
    """
    by_lower = {n.lower(): n for n in manifest.get("interfaces", [])}
    lowered = name.strip().lower()
    return by_lower.get(lowered) or by_lower.get(f"i{lowered}")


def read_interface(folder: Path, interface: str) -> dict[str, Any]:
    """One interface's record: its own topic and its members' topics.

    Raises:
        RemarksIndexError: The file is missing or unreadable.
    """
    path = folder / "interfaces" / f"{_file_name(interface)}.json"
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise RemarksIndexError(
            f"The API help index at {folder} is damaged ({exc}); rebuild it with "
            f"{build_command()}."
        ) from exc


def read_example(folder: Path, topic: str) -> ExampleCode | None:
    """An example's code, or None when the index has no such example."""
    path = folder / "examples" / f"{_file_name(topic)}.json"
    try:
        return ExampleCode.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    """Build the index from a shell.

    Args:
        argv (list[str] | None): Command-line arguments.

    Returns:
        int: The exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m solidworks_mcp.tools.api_remarks",
        description="Index the SolidWorks API help's Remarks for get_api_remarks.",
    )
    parser.add_argument("--year", type=int, help="SolidWorks release; newest if unset")
    parser.add_argument("--chm", type=Path, help=f"A {CHM_NAME} to index instead")
    args = parser.parse_args(argv)
    try:
        if args.chm is not None:
            year, chm = args.year, args.chm
            if not chm.is_file():
                raise RemarksIndexError(f"{chm} doesn't exist.")
        else:
            year, chm = find_api_chm(args.year)
        target = index_dir(year)
        print(f"Indexing {chm} into {target} (about a minute)...")
        manifest = build_index(chm, target, year)
    except RemarksIndexError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"{len(manifest['interfaces'])} interfaces, {manifest['member_count']} "
        f"members, {manifest['example_count']} examples in "
        f"{manifest['build_seconds']} s."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
