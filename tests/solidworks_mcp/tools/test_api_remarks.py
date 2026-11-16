"""Reading the API help: topics, examples, the index on disk, and the build command.

The topics in ``api_help_fixture/`` are made up, in the layout of the
SolidWorks help that ``hh.exe -decompile`` writes; nothing here needs
SolidWorks or hh.exe.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp.tools import api_remarks
from solidworks_mcp.tools.api_remarks import (
    RemarksIndexError,
    build_index,
    decode_topic,
    find_api_chm,
    html_to_text,
    parse_example,
    parse_help_folder,
    parse_topic,
)

FIXTURE = Path(__file__).parent / "api_help_fixture"
METHOD_TOPIC = (
    "SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IWidgetManager"
    "~InsertWidget.html"
)


def _fixture_page(name: str) -> str:
    return decode_topic((FIXTURE / name).read_bytes())


def _fake_decompile(chm: Path, out: Path) -> None:
    assert chm.read_bytes() == b"chm"
    for path in FIXTURE.iterdir():
        shutil.copy(path, out / path.name)


def _no_short_path(path: Path) -> Path:
    return path


@pytest.fixture
def chm(tmp_path: Path) -> Path:
    path = tmp_path / "help" / "sldworksapi.chm"
    path.parent.mkdir()
    path.write_bytes(b"chm")
    return path


# --- Topics ----------------------------------------------------------------


def test_a_method_topic() -> None:
    topic = parse_topic(_fixture_page(METHOD_TOPIC), "InsertWidget")

    assert topic.kind == "method"
    assert topic.summary == "Inserts a widget from the selected bodies."
    assert [(p.name, p.description) for p in topic.parameters] == [
        ("Mode", "Mode as defined in swWidgetMode_e"),
        (
            "Target",
            "If Mode is ... | Then set Target to ...\n"
            "swWidgetMode_e.swWidgetAdd | Nothing or null\n"
            "swWidgetMode_e.swWidgetCut | Target body",
        ),
    ]
    assert topic.returns == "IFeature"
    assert topic.remarks == (
        "Before calling this method, select:\n"
        "1. The target body, with mark 1\n"
        "2. The tool bodies, with mark 2\n"
        "- Then call it.\n"
        "Watch the spaces."
    )
    assert [(e.topic, e.language) for e in topic.examples] == [
        ("Insert_Widget_Example_CSharp.htm", "csharp"),
        ("Insert_Widget_Example_VB.htm", "vba"),
        ("Elsewhere_Example.htm", None),
    ]
    assert topic.see_also == ["IWidgetManager Interface", "IWidget Interface"]
    assert topic.availability == "SOLIDWORKS 2020 FCS"


def test_a_property_topic_without_a_footer() -> None:
    page = _fixture_page(
        "SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IWidgetManager"
        "~Count.html"
    )

    topic = parse_topic(page, "Count")

    assert topic.kind == "property"
    assert topic.returns == "Number of widgets"
    assert topic.remarks is None
    assert [e.language for e in topic.examples] == ["vbnet"]


def test_an_interface_topic_drops_the_members_link_note() -> None:
    page = _fixture_page(
        "SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IWidgetManager.html"
    )

    topic = parse_topic(page, "IWidgetManager")

    assert topic.kind == "interface"
    assert topic.summary == "Allows you to make widgets."
    assert topic.accessors == "IModelDoc2::WidgetManager"


def test_a_page_with_no_title_or_sections() -> None:
    topic = parse_topic("<p>Just text.</p>", "Odd")

    assert (topic.kind, topic.summary, topic.parameters) == (
        "interface",
        "Just text.",
        [],
    )


def test_an_example_keeps_its_code_layout() -> None:
    example = parse_example(
        _fixture_page("Insert_Widget_Example_CSharp.htm"), "Insert_Widget.htm"
    )

    assert example.title == "Insert Widget Example (C#)"
    assert example.description == "This example inserts a widget."
    assert example.code == (
        "using SolidWorks.Interop.sldworks;\n"
        "public void Main()\n"
        "{\n"
        "    swModel.InsertWidget(1, null);\n"
        "}"
    )


def test_an_example_without_code_or_title() -> None:
    example = parse_example("<body><p>Only words.</p></body>", "x.htm")

    assert (example.title, example.description, example.code) == (
        "x.htm",
        "Only words.",
        "",
    )


def test_text_from_html_closes_lists_and_skips_scripts() -> None:
    text = html_to_text(
        "<ul><li>a</li></ul></ol><style>p{}</style><p>b&nbsp;&nbsp;c</p>"
    )

    assert text == "- a\nb c"


def test_topics_decode_by_their_charset() -> None:
    assert decode_topic("\ufeffplain".encode()) == "plain"
    assert decode_topic(b'<meta charset="windows-1252">caf\xe9').endswith("café")
    assert decode_topic(b"<meta charset=nonsense>ok") == "<meta charset=nonsense>ok"


def test_a_help_folder_indexes_interfaces_members_and_linked_examples() -> None:
    interfaces, examples = parse_help_folder(FIXTURE)

    assert list(interfaces) == ["IWidgetManager"]
    record = interfaces["IWidgetManager"]
    assert record["topic"]["accessors"] == "IModelDoc2::WidgetManager"
    assert sorted(record["members"]) == ["Count", "InsertWidget"]
    assert list(examples) == ["Insert_Widget_Example_CSharp.htm"]


# --- Where things are ------------------------------------------------------


def test_the_index_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert api_remarks.remarks_root({"SOLIDWORKS_MCP_API_REMARKS_DIR": "D:/x"}) == Path(
        "D:/x"
    )
    assert api_remarks.remarks_root({"LOCALAPPDATA": "C:/L"}) == Path(
        "C:/L/solidworks_mcp/api_remarks"
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert api_remarks.remarks_root({}) == tmp_path / "solidworks_mcp" / "api_remarks"
    monkeypatch.setenv("SOLIDWORKS_MCP_API_REMARKS_DIR", str(tmp_path / "env"))
    assert api_remarks.remarks_root() == tmp_path / "env"
    assert api_remarks.index_dir(2026) == tmp_path / "env" / "2026"
    assert api_remarks.index_dir(None, tmp_path) == tmp_path / "default"


def test_the_build_command_names_the_year() -> None:
    assert api_remarks.build_command() == "python -m solidworks_mcp.tools.api_remarks"
    assert api_remarks.build_command(2025).endswith(" --year 2025")


def test_the_help_file_from_the_environment(chm: Path) -> None:
    env = {"SOLIDWORKS_MCP_API_CHM": str(chm)}
    assert find_api_chm(2026, env) == (2026, chm)

    with pytest.raises(RemarksIndexError, match="which doesn't exist"):
        find_api_chm(None, {"SOLIDWORKS_MCP_API_CHM": str(chm.with_name("no.chm"))})


def test_the_help_file_in_the_install_folder(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()

    def installed(year: int | None) -> tuple[int | None, Path | None]:
        return 2026, tmp_path

    with pytest.raises(RemarksIndexError, match="wasn't installed with SolidWorks"):
        find_api_chm(None, {}, installed)

    (tmp_path / "api" / "sldworksapi.chm").write_bytes(b"chm")
    assert find_api_chm(None, {}, installed) == (
        2026,
        tmp_path / "api" / "sldworksapi.chm",
    )


def test_no_solidworks_no_help_file() -> None:
    with pytest.raises(RemarksIndexError, match="SolidWorks isn't installed here"):
        find_api_chm(None, {}, lambda year: (None, None))


def test_the_real_environment_is_read_by_default(
    monkeypatch: pytest.MonkeyPatch, chm: Path
) -> None:
    monkeypatch.setenv("SOLIDWORKS_MCP_API_CHM", str(chm))

    assert find_api_chm() == (None, chm)


# --- Decompiling -----------------------------------------------------------


class FakeKernel32:
    """``GetShortPathNameW`` returning a fixed answer."""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def GetShortPathNameW(self, path: str, buffer: Any, size: int) -> int:
        buffer.value = self.answer
        return len(self.answer)


def test_a_path_with_spaces_gets_its_short_name() -> None:
    assert api_remarks._short_path(Path("C:/no_spaces")) == Path("C:/no_spaces")
    assert api_remarks._short_path(
        Path("C:/with space"), FakeKernel32("C:/WITHSP~1")
    ) == Path("C:/WITHSP~1")
    with pytest.raises(RemarksIndexError, match="no short name"):
        api_remarks._short_path(Path("C:/with space"), FakeKernel32(""))
    with pytest.raises(RemarksIndexError, match="TEMP"):
        api_remarks._short_path(Path("C:/with space"), FakeKernel32("C:/still space"))


@pytest.fixture
def system_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "windows"
    root.mkdir()
    monkeypatch.setenv("SystemRoot", str(root))
    return root


def test_decompiling_without_hh_exe(system_root: Path, tmp_path: Path) -> None:
    with pytest.raises(RemarksIndexError, match="hh.exe isn't there"):
        api_remarks.decompile_chm(tmp_path / "a.chm", tmp_path)


def test_decompiling_runs_hh_exe(
    monkeypatch: pytest.MonkeyPatch, system_root: Path, tmp_path: Path
) -> None:
    (system_root / "hh.exe").write_bytes(b"")
    out = tmp_path / "out"
    out.mkdir()
    seen: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> None:
        seen.append(command)
        (out / "topic.html").write_text("x")

    monkeypatch.setattr(api_remarks.subprocess, "run", run)

    api_remarks.decompile_chm(tmp_path / "a.chm", out)

    assert seen == [
        [str(system_root / "hh.exe"), "-decompile", str(out), str(tmp_path / "a.chm")]
    ]


def test_decompiling_that_writes_nothing_or_hangs(
    monkeypatch: pytest.MonkeyPatch, system_root: Path, tmp_path: Path
) -> None:
    (system_root / "hh.exe").write_bytes(b"")
    monkeypatch.setattr(api_remarks.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(RemarksIndexError, match="wrote no topics"):
        api_remarks.decompile_chm(tmp_path / "a.chm", tmp_path)

    def hang(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired("hh.exe", 900)

    monkeypatch.setattr(api_remarks.subprocess, "run", hang)
    with pytest.raises(RemarksIndexError, match="ran over 900 s"):
        api_remarks.decompile_chm(tmp_path / "a.chm", tmp_path)


# --- The index on disk -----------------------------------------------------


def test_building_writes_the_index_and_replaces_an_old_one(
    chm: Path, tmp_path: Path
) -> None:
    target = tmp_path / "index" / "2026"
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("old")

    manifest = build_index(chm, target, 2026, _fake_decompile, _no_short_path)

    assert manifest["format"] == api_remarks.INDEX_FORMAT
    assert manifest["year"] == 2026
    assert manifest["interfaces"] == ["IWidgetManager"]
    assert (manifest["member_count"], manifest["example_count"]) == (2, 1)
    assert manifest["source"]["size"] == 3
    assert not (target / "stale.txt").exists()
    assert api_remarks.read_manifest(target) == manifest
    record = api_remarks.read_interface(target, "IWidgetManager")
    assert record["members"]["InsertWidget"]["returns"] == "IFeature"
    example = api_remarks.read_example(target, "Insert_Widget_Example_CSharp.htm")
    assert example is not None and "InsertWidget" in example.code
    assert api_remarks.read_example(target, "Nope.htm") is None


def test_building_uses_the_default_decompiler(
    monkeypatch: pytest.MonkeyPatch, chm: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(api_remarks, "decompile_chm", _fake_decompile)
    monkeypatch.setattr(api_remarks, "_short_path", _no_short_path)

    manifest = build_index(chm, tmp_path / "idx")

    assert manifest["year"] is None


def test_a_manifest_that_is_missing_or_another_format(tmp_path: Path) -> None:
    assert api_remarks.read_manifest(tmp_path) is None
    (tmp_path / "manifest.json").write_text(json.dumps({"format": "0"}))
    assert api_remarks.read_manifest(tmp_path) is None
    (tmp_path / "manifest.json").write_text("not json")
    assert api_remarks.read_manifest(tmp_path) is None


def test_an_index_is_current_while_its_help_file_is_unchanged(
    chm: Path, tmp_path: Path
) -> None:
    manifest = build_index(chm, tmp_path / "i", None, _fake_decompile, _no_short_path)

    assert api_remarks.is_current(manifest, chm)
    chm.write_bytes(b"a newer help file")
    assert not api_remarks.is_current(manifest, chm)
    # A help file that has gone doesn't make a built index useless.
    assert api_remarks.is_current(manifest, chm.with_name("gone.chm"))


def test_interfaces_resolve_by_case_and_without_the_i() -> None:
    manifest = {"interfaces": ["IWidgetManager", "IBody2"]}

    assert api_remarks.resolve_interface(manifest, "iwidgetmanager") == "IWidgetManager"
    assert (
        api_remarks.resolve_interface(manifest, " WidgetManager ") == "IWidgetManager"
    )
    assert api_remarks.resolve_interface(manifest, "IFace2") is None


def test_a_damaged_interface_file(tmp_path: Path) -> None:
    with pytest.raises(RemarksIndexError, match="damaged"):
        api_remarks.read_interface(tmp_path, "IWidgetManager")


# --- The build command -----------------------------------------------------


@pytest.fixture
def quick_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(api_remarks, "decompile_chm", _fake_decompile)
    monkeypatch.setattr(api_remarks, "_short_path", _no_short_path)
    monkeypatch.setenv("SOLIDWORKS_MCP_API_REMARKS_DIR", str(tmp_path / "idx"))
    return tmp_path / "idx"


def test_the_command_builds_from_a_named_help_file(
    quick_build: Path, chm: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert api_remarks.main(["--chm", str(chm), "--year", "2026"]) == 0

    assert (quick_build / "2026" / "manifest.json").is_file()
    assert "1 interfaces, 2 members, 1 examples" in capsys.readouterr().out


def test_the_command_finds_the_installed_help(
    quick_build: Path, chm: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_remarks, "find_api_chm", lambda year: (2025, chm))

    assert api_remarks.main([]) == 0
    assert (quick_build / "2025" / "manifest.json").is_file()


def test_the_command_reports_what_is_missing(
    quick_build: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert api_remarks.main(["--chm", str(tmp_path / "none.chm")]) == 1
    assert "doesn't exist" in capsys.readouterr().err
