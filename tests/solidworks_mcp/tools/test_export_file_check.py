"""Every export tool reads back the file it wrote (``utils/export_check``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools.export import register_export_tools


async def _tools(config: Any, adapter: Any = None) -> dict[str, Any]:
    if adapter is None:
        adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
        await adapter.connect()
        await adapter.create_part()
    mcp = FastMCP("exports")
    await register_export_tools(mcp, adapter, config)
    return {t.name: t.fn for t in await mcp.list_tools()}


@pytest.mark.parametrize(
    ("tool", "name", "fmt"),
    [
        ("export_step", "part.step", "STEP"),
        ("export_step", "part.stp", "STEP"),
        ("export_iges", "part.igs", "IGES"),
        ("export_pdf", "sheet.pdf", "PDF"),
        ("export_dwg", "sheet.dwg", "DWG"),
        ("export_dwg", "sheet.dxf", "DXF"),
        ("export_image", "shot.png", "PNG"),
        ("export_image", "shot.jpg", "JPG"),
        ("export_image", "shot.bmp", "BMP"),
    ],
)
async def test_a_good_export_carries_its_file_check(
    mock_config: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    name: str,
    fmt: str,
) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    tools = await _tools(mock_config)
    out = tmp_path / name

    result = await tools[tool]({"file_path": str(out)})

    assert result["status"] == "success", result
    check = result["file_check"]
    assert check["file_path"] == str(out)
    assert check["exists"] is True
    assert check["size_bytes"] == out.stat().st_size > 0
    assert check["format"] == fmt
    assert check["format_ok"] is True


@pytest.mark.parametrize(
    ("tool", "name", "fmt"),
    [
        ("export_step", "part.step", "STEP"),
        ("export_iges", "part.iges", "IGES"),
        ("export_pdf", "sheet.pdf", "PDF"),
        ("export_dwg", "sheet.dwg", "DWG"),
    ],
)
async def test_an_export_that_left_no_file_is_an_error(
    mock_config: Any, tmp_path: Path, tool: str, name: str, fmt: str
) -> None:
    adapter = MockSolidWorksAdapter({})
    ok = AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)
    adapter.export_file = AsyncMock(return_value=ok)  # type: ignore[method-assign]
    tools = await _tools(mock_config, adapter)
    out = tmp_path / name

    result = await tools[tool]({"file_path": str(out)})

    assert result["status"] == "error"
    assert result["error_type"] == "export_invalid"
    assert result["message"] == (
        f"The export reported success, but {out} is not a valid {fmt} file: no "
        "file was written there."
    )
    assert result["data"]["file_check"]["exists"] is False
    assert "file_check" in result["hint"]


async def test_a_file_of_another_format_is_an_error(
    mock_config: Any, tmp_path: Path
) -> None:
    adapter = MockSolidWorksAdapter({})
    out = tmp_path / "part.step"

    async def writes_html(file_path: str, _format: str) -> AdapterResult[Any]:
        Path(file_path).write_text("<html>error page</html>", encoding="utf-8")
        return AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)

    adapter.export_file = writes_html  # type: ignore[method-assign]
    tools = await _tools(mock_config, adapter)

    result = await tools["export_step"]({"file_path": str(out)})

    assert result["error_type"] == "export_invalid"
    assert result["message"].endswith(
        "is not a valid STEP file: it does not start with ISO-10303-21;."
    )
    assert result["data"]["file_check"]["size_bytes"] == 23


async def test_an_image_is_checked_where_the_adapter_wrote_it(
    mock_config: Any, tmp_path: Path
) -> None:
    adapter = MockSolidWorksAdapter({})
    written = tmp_path / "resolved.png"
    written.write_bytes(b"not a png")
    adapter.export_image = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS, data={"file_path": str(written)}
        )
    )
    tools = await _tools(mock_config, adapter)

    result = await tools["export_image"]({"file_path": str(tmp_path / "asked.png")})

    assert result["error_type"] == "export_invalid"
    assert result["data"]["file_check"]["file_path"] == str(written)
    assert "PNG signature" in result["message"]


async def test_the_image_fallback_through_export_file_is_checked(
    mock_config: Any, tmp_path: Path
) -> None:
    class _FileOnly:
        async def export_file(self, file_path: str, _format: str) -> AdapterResult:
            return AdapterResult(status=AdapterResultStatus.SUCCESS, data=None)

    tools = await _tools(mock_config, _FileOnly())

    result = await tools["export_image"]({"file_path": str(tmp_path / "a.jpg")})

    assert result["error_type"] == "export_invalid"
    assert "is not a valid JPG file" in result["message"]
