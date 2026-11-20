"""``export_for_blender``: a model handed to Blender with its units, axes and import code.

No build session had moved a model to Blender yet; the Blender MCP session
found the hand-off's traps from the outside: Blender reads an STL's numbers
as metres while SolidWorks writes mm, it is +Z up, and it can't open
SolidWorks files. These tests run the tool on the mock adapter, whose GLB
is a real one (a 10 mm tetrahedron in metres) and whose STL is in mm.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools import export as export_module
from solidworks_mcp.tools.export import ExportForBlenderInput, register_export_tools


async def _tool(adapter: Any, config: Any) -> Any:
    mcp = FastMCP("blender")
    await register_export_tools(mcp, adapter, config)
    return next(t.fn for t in await mcp.list_tools() if t.name == "export_for_blender")


async def _part(size_mm: list[float] | None = None) -> MockSolidWorksAdapter:
    adapter = MockSolidWorksAdapter({})
    await adapter.connect()
    await adapter.create_part()
    if size_mm is not None:
        adapter.get_model_geometry_summary = AsyncMock(  # type: ignore[method-assign]
            return_value=AdapterResult(
                status=AdapterResultStatus.SUCCESS,
                data={"bounding_box_mm": {"size": size_mm}},
            )
        )
    return adapter


def _manifest(result: dict[str, Any]) -> dict[str, Any]:
    with open(result["data"]["manifest_path"], encoding="utf-8") as handle:
        return json.load(handle)


async def test_a_part_goes_as_a_glb_with_its_size_checked(
    tmp_path: Path, mock_config: Any
) -> None:
    tool = await _tool(await _part([10.0, 10.0, 10.0]), mock_config)

    result = await tool({"folder": str(tmp_path)})

    assert result["status"] == "success", result
    data = result["data"]
    assert data["file_path"] == str(tmp_path / "Part1.glb")
    assert data["units"] == "m" and data["format"] == "glb"
    assert data["parts"] == ["mock"]
    assert data["materials"][0]["base_color"] == [0.8, 0.8, 0.8, 1.0]
    assert data["expected_size_mm"] == [10.0, 10.0, 10.0]
    assert data["expected_size_from"] == "model"
    assert data["collection"] == "SW Part1"
    compile(data["blender_code"], "<blender>", "exec")
    manifest = _manifest(result)
    assert manifest["units_check"]["checked"] is True
    assert manifest["import"] == {"operator": "import_scene.gltf"}
    assert manifest["blender_matrix"][0] == [1.0, 0.0, 0.0, 0.0]
    assert result["warning"].startswith("Experimental:")  # no hand-off warning


async def test_a_part_goes_as_an_stl_in_mm_and_model_coordinates(
    tmp_path: Path, mock_config: Any
) -> None:
    adapter = await _part()
    sent: list[dict[str, Any]] = []
    export = adapter.export_stl_with_settings

    async def record(file_path: str, settings: dict[str, Any]) -> Any:
        sent.append(settings)
        return await export(file_path, settings)

    adapter.export_stl_with_settings = record  # type: ignore[method-assign]
    tool = await _tool(adapter, mock_config)

    result = await tool(
        {
            "folder": str(tmp_path),
            "file_format": "stl",
            "up_axis": "z",
            "resolution": "fine",
            "name": "lid v2",
        }
    )

    assert result["status"] == "success", result
    assert sent == [
        {"units": "mm", "translate_to_positive": False, "resolution": "fine"}
    ]
    data = result["data"]
    assert data["file_path"] == str(tmp_path / "lid v2.stl")
    assert data["units"] == "mm" and "parts" not in data
    # The mock has no model size, so the file's own size is what Blender should see.
    assert data["expected_size_mm"] == [10.0, 10.0, 10.0]
    assert data["expected_size_from"] == "file"
    manifest = _manifest(result)
    assert manifest["import"] == {"operator": "wm.stl_import", "global_scale": 0.001}
    assert manifest["collection"] == "SW lid v2"


async def test_the_default_folder_is_under_the_temp_folder(
    tmp_path: Path, mock_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_module.tempfile, "gettempdir", lambda: str(tmp_path))
    tool = await _tool(await _part(), mock_config)

    result = await tool({})

    expected = tmp_path / "solidworks_mcp" / "blender" / "Part1" / "Part1.glb"
    assert result["data"]["file_path"] == str(expected)
    assert expected.exists()


@pytest.mark.parametrize(
    ("summary", "model_size", "fragment"),
    [
        (None, None, "gave no model size"),
        ({"size": [0.001, 0.001, 0.001]}, [40.0, 20.0, 10.0], "matches the model's"),
        (
            {"size": [0.04, 0.01, 0.02]},
            [40.0, 20.0, 10.0],
            "SolidWorks turned the model",
        ),
    ],
)
async def test_a_glb_the_size_check_doubts_is_a_warning(
    tmp_path: Path,
    mock_config: Any,
    monkeypatch: pytest.MonkeyPatch,
    summary: dict[str, Any] | None,
    model_size: list[float] | None,
    fragment: str,
) -> None:
    if summary is not None:
        monkeypatch.setattr(
            export_module.gltf_summary,
            "check_glb",
            lambda path: {
                "nodes": [{"name": "rack"}],
                "materials": [],
                "bounds": summary,
            },
        )
    tool = await _tool(await _part(model_size), mock_config)

    result = await tool({"folder": str(tmp_path)})

    assert result["status"] == "success"
    assert fragment in result["warning"]


async def test_a_glb_with_no_mesh_says_blender_gets_nothing(
    tmp_path: Path, mock_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        export_module.gltf_summary,
        "check_glb",
        lambda path: {"nodes": [], "materials": [], "bounds": None},
    )
    tool = await _tool(await _part(), mock_config)

    result = await tool({"folder": str(tmp_path)})

    assert "no mesh node" in result["warning"]
    assert result["data"]["expected_size_mm"] is None

    # With the model's size known too, that is the one thing to say.
    tool = await _tool(await _part([40.0, 20.0, 10.0]), mock_config)

    result = await tool({"folder": str(tmp_path)})

    assert result["status"] == "success"
    assert "no mesh node" in result["warning"]
    assert "in no unit" not in result["warning"]


async def test_a_glb_with_no_scene_is_an_error(
    tmp_path: Path, mock_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        export_module.gltf_summary, "check_glb", lambda path: {"error": "no JSON chunk"}
    )
    tool = await _tool(await _part(), mock_config)

    result = await tool({"folder": str(tmp_path)})

    assert result["status"] == "error"
    assert "no scene" in result["message"]
    assert not (tmp_path / "Part1.blender.json").exists()


async def test_an_stl_that_cant_be_read_back_is_an_error(
    tmp_path: Path, mock_config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        export_module.stl_mesh, "check_stl", lambda path, units: {"error": "empty"}
    )
    tool = await _tool(await _part(), mock_config)

    result = await tool({"folder": str(tmp_path), "file_format": "stl"})

    assert result["status"] == "error"
    assert "can't be read back" in result["message"]


async def test_an_stl_needs_an_adapter_that_sets_stl_options(
    tmp_path: Path, mock_config: Any
) -> None:
    class WithoutStlOptions:
        """The mock, less ``export_stl_with_settings``."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            if name == "export_stl_with_settings":
                raise AttributeError(name)
            return getattr(self._inner, name)

    tool = await _tool(WithoutStlOptions(await _part()), mock_config)

    result = await tool({"folder": str(tmp_path), "file_format": "stl"})

    assert result["status"] == "error"
    assert result["error_type"] == "not_supported"
    assert not (tmp_path / "Part1.stl").exists()


@pytest.mark.parametrize(
    "info",
    [
        AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"title": "Sheet", "type": "Drawing"},
        ),
        AdapterResult(status=AdapterResultStatus.SUCCESS, data=None),
    ],
)
async def test_only_a_part_or_assembly_goes_to_blender(
    tmp_path: Path, mock_config: Any, info: AdapterResult[Any]
) -> None:
    adapter = await _part()
    adapter.get_model_info = AsyncMock(return_value=info)  # type: ignore[method-assign]
    tool = await _tool(adapter, mock_config)

    result = await tool({"folder": str(tmp_path)})

    assert result["status"] == "error"
    assert "needs an active part or assembly" in result["message"]


async def test_a_model_size_that_cant_be_read_is_left_out(mock_config: Any) -> None:
    failing = AsyncMock(
        return_value=AdapterResult(status=AdapterResultStatus.ERROR, error="no model")
    )
    adapter = type("Adapter", (), {"get_model_geometry_summary": failing})()

    assert await export_module._model_size_mm(adapter) is None
    assert await export_module._model_size_mm(object()) is None


@pytest.mark.parametrize(
    ("arguments", "fragment"),
    [
        ({"resolution": "fine"}, "file_format 'stl' only"),
        ({"name": "  "}, "name is blank"),
        ({"folder": ""}, "folder is blank"),
    ],
)
def test_inputs_that_cant_apply_are_refused(
    arguments: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(ValueError, match=fragment):
        ExportForBlenderInput(**arguments)
