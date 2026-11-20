"""``export_image`` sends the picture back in its reply, over MCP.

99 of 142 image exports in the 2026-11-20 sample were followed by a ``Read``
of the file, 78 in a turn of their own (6.3 min of model time). The picture
now comes back as MCP image content beside the reply, which keeps every field
it had; the file is still written. Pillow is not a dependency, so nothing
resizes it: it goes at the export's own width and height, or stays in its
file with a note above a size cap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock

import pytest
from fastmcp import Client, FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.adapters.mock_adapter import MockSolidWorksAdapter
from solidworks_mcp.tools import export
from solidworks_mcp.tools.export import register_export_tools, reply_picture


async def _server(config: Any, adapter: Any = None) -> FastMCP:
    if adapter is None:
        adapter = MockSolidWorksAdapter({"mock_model_delay": 0})
        await adapter.connect()
        await adapter.create_part()
    mcp = FastMCP("image")
    await register_export_tools(mcp, adapter, config)
    return mcp


async def _call(mcp: FastMCP, arguments: dict[str, Any]) -> Any:
    async with Client(mcp) as client:
        return await client.call_tool("export_image", {"input_data": arguments})


async def test_an_mcp_client_gets_an_image_block_and_the_reply(
    mock_config: Any, tmp_path: Path
) -> None:
    shot = tmp_path / "shot.png"

    result = await _call(await _server(mock_config), {"file_path": str(shot)})

    text, image = result.content
    assert image.type == "image"
    assert image.mimeType == "image/png"
    reply = result.structured_content
    assert reply == {
        "status": "success",
        "message": f"Exported image: {shot}",
        "data": ANY,
        "file_check": ANY,
        "execution_time": ANY,
        "server_time_s": ANY,
    }
    assert reply["file_check"]["format"] == "PNG"
    assert text.type == "text"
    assert shot.stat().st_size > 0  # the file is still written


async def test_a_jpg_goes_back_as_a_jpeg(mock_config: Any, tmp_path: Path) -> None:
    result = await _call(
        await _server(mock_config), {"file_path": str(tmp_path / "a.jpg")}
    )

    assert result.content[1].mimeType == "image/jpeg"


async def test_inline_false_leaves_the_picture_in_its_file(
    mock_config: Any, tmp_path: Path
) -> None:
    shot = tmp_path / "shot.png"

    result = await _call(
        await _server(mock_config), {"file_path": str(shot), "inline": False}
    )

    assert [block.type for block in result.content] == ["text"]
    assert shot.exists()


async def test_a_direct_call_gets_the_reply_alone(
    mock_config: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    mcp = await _server(mock_config)
    tool = next(t.fn for t in await mcp.list_tools() if t.name == "export_image")

    result = await tool({"file_path": str(tmp_path / "shot.png")})

    assert type(result) is dict
    assert result["status"] == "success"


async def test_the_adapter_is_not_sent_inline(mock_config: Any, tmp_path: Path) -> None:
    adapter = MockSolidWorksAdapter({})
    adapter.export_image = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(status=AdapterResultStatus.SUCCESS, data={})
    )
    shot = tmp_path / "shot.png"
    shot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(17) + b"IEND\xaeB`\x82"
    )

    await _call(await _server(mock_config, adapter), {"file_path": str(shot)})

    assert "inline" not in adapter.export_image.await_args.args[0]


async def test_a_picture_too_big_to_send_is_a_warning(
    mock_config: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export, "INLINE_IMAGE_MAX_BYTES", 10)
    adapter = MockSolidWorksAdapter({})
    adapter.export_image = AsyncMock(  # type: ignore[method-assign]
        return_value=AdapterResult(
            status=AdapterResultStatus.SUCCESS,
            data={"section": {"plane": "Front Plane", "removed": False}},
        )
    )
    shot = tmp_path / "shot.png"
    shot.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(17) + b"IEND\xaeB`\x82"
    )

    result = await _call(await _server(mock_config, adapter), {"file_path": str(shot)})

    assert [block.type for block in result.content] == ["text"]
    warning = result.structured_content["warning"]
    # The export's own warning first, then why the picture isn't here.
    assert warning.startswith("The section on Front Plane could not be taken off")
    assert warning.endswith(
        f"The picture is not in this reply: it is 0.0 MB, over the 0 MB a reply "
        f"carries. Read {shot}, or export a smaller width and height."
    )


async def test_the_export_file_fallback_sends_its_picture_too(
    mock_config: Any, tmp_path: Path
) -> None:
    class _FileOnly:
        """An adapter with export_file but no export_image."""

        async def export_file(self, file_path: str, _format: str) -> AdapterResult:
            Path(file_path).write_bytes(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(17) + b"IEND\xaeB`\x82"
            )
            return AdapterResult(status=AdapterResultStatus.SUCCESS)

    result = await _call(
        await _server(mock_config, _FileOnly()), {"file_path": str(tmp_path / "f.png")}
    )

    assert [block.type for block in result.content] == ["text", "image"]
    # The picture's type comes from the file read back, not the asked format.
    assert result.content[1].mimeType == "image/png"


# ---- reply_picture ---------------------------------------------------------------------


def _check(path: Path, fmt: str, size: int, width: int = 0, height: int = 0) -> dict:
    return {
        "file_path": str(path),
        "format": fmt,
        "size_bytes": size,
        "width": width,
        "height": height,
    }


def test_a_png_or_jpg_is_sent_as_written(tmp_path: Path) -> None:
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"picture bytes")

    picture, note = reply_picture(_check(shot, "PNG", 13, 1920, 1080))

    assert note is None
    assert picture is not None
    assert picture.data == b"picture bytes"
    assert picture.to_image_content().mimeType == "image/png"


def test_another_format_stays_in_its_file(tmp_path: Path) -> None:
    picture, note = reply_picture(_check(tmp_path / "shot.bmp", "BMP", 100))

    assert picture is None
    assert note == (
        "The picture is not in this reply: only PNG and JPG go in a reply, and "
        f"{tmp_path / 'shot.bmp'} is BMP. Read the file, or export a .png."
    )


def test_a_picture_over_the_size_cap_stays_in_its_file(tmp_path: Path) -> None:
    big = export.INLINE_IMAGE_MAX_BYTES + 1

    picture, note = reply_picture(_check(tmp_path / "big.png", "PNG", big))

    assert picture is None
    assert note is not None
    assert note.startswith(
        "The picture is not in this reply: it is 2.0 MB, over the 2 MB"
    )


def test_a_picture_too_wide_stays_in_its_file(tmp_path: Path) -> None:
    picture, note = reply_picture(_check(tmp_path / "wide.png", "PNG", 100, 9000, 100))

    assert picture is None
    assert note is not None
    assert "it is 9000 px on its longest side, over the 8000 px a reply carries" in note
