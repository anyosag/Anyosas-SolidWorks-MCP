"""export_assembly_parts_stl: one STL per part of the active assembly."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import FastMCP

from solidworks_mcp.adapters.base import AdapterResult, AdapterResultStatus
from solidworks_mcp.tools.export import (
    ExportAssemblyPartsStlInput,
    ExportStlInput,
    register_export_tools,
)

#: A closed tetrahedron, 10 mm legs (what the mock adapter writes).
_STL = """solid t
facet normal 0 0 -1
outer loop
vertex 0 0 0
vertex 0 10 0
vertex 10 0 0
endloop
endfacet
facet normal 0 -1 0
outer loop
vertex 0 0 0
vertex 10 0 0
vertex 0 0 10
endloop
endfacet
facet normal -1 0 0
outer loop
vertex 0 0 0
vertex 0 0 10
vertex 0 10 0
endloop
endfacet
facet normal 1 1 1
outer loop
vertex 10 0 0
vertex 0 10 0
vertex 0 0 10
endloop
endfacet
endsolid t
"""


def _ok(data: Any = None) -> AdapterResult[Any]:
    return AdapterResult(
        status=AdapterResultStatus.SUCCESS, data=data, execution_time=0.2
    )


def _error(message: str) -> AdapterResult[Any]:
    return AdapterResult(status=AdapterResultStatus.ERROR, error=message)


def _part(title: str, **extra: Any) -> dict[str, Any]:
    return {
        "path": f"C:/d/{title}",
        "title": title,
        "configuration": "Default",
        "active_configuration": "Default",
        "instances": 1,
        "components": [],
        "virtual": False,
        **extra,
    }


class _Assembly:
    """An adapter whose assembly holds ``parts``; exports write a tetrahedron."""

    def __init__(self, parts: list[dict[str, Any]]) -> None:
        self.parts = parts
        self.active = "Rack.SLDASM"
        self.calls: list[tuple[str, ...]] = []
        self.fail_export: set[str] = set()
        self.fail_activate: set[str] = set()
        self.restored = True

    async def list_assembly_parts(self) -> AdapterResult[Any]:
        return _ok(
            {
                "assembly": {"title": "Rack.SLDASM", "path": "C:/d/Rack.SLDASM"},
                "parts": self.parts,
            }
        )

    async def activate_document(self, name: str) -> AdapterResult[Any]:
        self.calls.append(("activate", name))
        if name in self.fail_activate:
            return _error(f"No open document matches {name!r}")
        self.active = name
        return _ok({"activated": name, "opened_window": name.endswith("Comb.SLDPRT")})

    async def _write(self, path: str) -> AdapterResult[Any]:
        if self.active in self.fail_export:
            return _error("STL export failed")
        Path(path).write_text(_STL)
        data: dict[str, Any] = {
            "file_path": path,
            "units": "mm",
            "document_type": "part",
        }
        if not self.restored:
            data["mesh"] = {"applied": ["resolution"], "restored": False}
        return _ok(data)

    async def export_file(self, path: str, kind: str) -> AdapterResult[Any]:
        self.calls.append(("export", path))
        return await self._write(path)

    async def export_stl_with_settings(
        self, path: str, settings: dict[str, Any]
    ) -> AdapterResult[Any]:
        self.calls.append(("export_with", path, str(sorted(settings))))
        return await self._write(path)

    async def get_model_geometry_summary(self) -> AdapterResult[Any]:
        return _ok({"volume_mm3": 166.667})

    async def close_model(self) -> AdapterResult[Any]:
        self.calls.append(("close", self.active))
        return _ok()


async def _export(adapter: Any) -> Any:
    mcp = FastMCP("test")
    await register_export_tools(mcp, adapter, {})
    return next(
        t.fn for t in await mcp.list_tools() if t.name == "export_assembly_parts_stl"
    )


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"folder": " "}, "folder is required"),
        ({"name_pattern": "{name}.step"}, "must end in .stl"),
        ({"name_pattern": "out/{name}.stl"}, "put folders in folder"),
        ({"name_pattern": "{name.stl"}, "is not a pattern"),
        ({"name_pattern": "{part}.stl"}, "not part"),
        ({"resolution": "fine", "deviation_mm": 0.01}, "need resolution 'custom'"),
    ],
)
def test_inputs_refuse_what_cannot_be_used(fields: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        ExportAssemblyPartsStlInput(**{"folder": "C:/out", **fields})


def test_the_stl_input_keeps_its_field_order() -> None:
    assert list(ExportStlInput.model_fields)[:3] == [
        "file_path",
        "format_type",
        "resolution",
    ]


async def test_each_part_once_with_its_mesh_check(tmp_path: Path) -> None:
    adapter = _Assembly(
        [
            _part("Comb.SLDPRT", instances=2),
            _part("Stand.SLDPRT"),
            _part("Virtual.SLDPRT", virtual=True),
            _part("Peg.SLDPRT", configuration="Short", active_configuration="Long"),
        ]
    )
    export = await _export(adapter)

    reply = await export(
        ExportAssemblyPartsStlInput(
            folder=str(tmp_path / "stl"), name_pattern="{assembly}-{index}-{name}.stl"
        )
    )

    assert reply["status"] == "success", reply
    comb, stand = reply["files"]
    assert comb["file_path"] == str(tmp_path / "stl" / "Rack-1-Comb.stl")
    assert comb["instances"] == 2
    assert comb["mesh_check"]["closed"] is True
    assert comb["mesh_check"]["volume_ratio"] == pytest.approx(1.0, abs=0.01)
    assert stand["file_path"] == str(tmp_path / "stl" / "Rack-2-Stand.stl")
    assert [s["part"] for s in reply["skipped"]] == ["Virtual.SLDPRT", "Peg.SLDPRT"]
    assert "2 part(s) were skipped" in reply["warning"]
    # Comb's window was opened for it, so it is closed again; the assembly is
    # active at the end.
    assert ("close", "C:/d/Comb.SLDPRT") in adapter.calls
    assert not any(c == ("close", "C:/d/Stand.SLDPRT") for c in adapter.calls)
    assert adapter.calls[-1] == ("activate", "C:/d/Rack.SLDASM")


async def test_mesh_settings_failures_and_a_lost_assembly(tmp_path: Path) -> None:
    adapter = _Assembly(
        [_part("Comb.SLDPRT"), _part("Stand.SLDPRT"), _part("Lid.SLDPRT")]
    )
    adapter.fail_export = {"C:/d/Stand.SLDPRT"}
    adapter.fail_activate = {"C:/d/Lid.SLDPRT", "C:/d/Rack.SLDASM"}
    adapter.restored = False
    export = await _export(adapter)

    reply = await export(
        ExportAssemblyPartsStlInput(folder=str(tmp_path), resolution="fine")
    )

    assert reply["status"] == "success"
    assert [f["part"] for f in reply["files"]] == ["Comb.SLDPRT"]
    assert [f["part"] for f in reply["failed"]] == ["Stand.SLDPRT", "Lid.SLDPRT"]
    warning = reply["warning"]
    assert "options could not all be put back" in warning
    assert "2 part(s) failed: Stand.SLDPRT (STL export failed)" in warning
    assert "could not be made active again" in warning
    assert (
        "export_with",
        str(tmp_path / "Comb.stl"),
        "['resolution']",
    ) in adapter.calls


async def test_nothing_exported_is_an_error(tmp_path: Path) -> None:
    adapter = _Assembly([_part("Stand.SLDPRT")])
    adapter.fail_export = {"C:/d/Stand.SLDPRT"}
    export = await _export(adapter)

    failed = await export(ExportAssemblyPartsStlInput(folder=str(tmp_path)))
    adapter.parts = []
    empty = await export(ExportAssemblyPartsStlInput(folder=str(tmp_path)))

    assert failed["status"] == "error"
    assert "No part was exported" in failed["message"]
    assert failed["data"]["failed"][0]["part"] == "Stand.SLDPRT"
    assert empty["status"] == "error"
    assert empty["error_type"] == "not_found"


async def test_two_parts_with_one_file_name_export_nothing(tmp_path: Path) -> None:
    adapter = _Assembly(
        [
            _part("Peg.SLDPRT", path="C:/a/Peg.SLDPRT"),
            _part("Peg.SLDPRT", path="C:/b/Peg.SLDPRT"),
        ]
    )
    export = await _export(adapter)

    reply = await export(ExportAssemblyPartsStlInput(folder=str(tmp_path)))

    assert reply["status"] == "error"
    assert reply["error_type"] == "invalid_input"
    assert "add {config} or {index}" in reply["message"]
    assert adapter.calls == []


async def test_needs_the_capability() -> None:
    export = await _export(SimpleNamespace())

    reply = await export({"folder": "C:/out"})

    assert reply["error_type"] == "not_supported"


async def test_the_mock_assembly(mock_adapter: Any, tmp_path: Path) -> None:
    await mock_adapter.create_assembly()
    await mock_adapter.insert_component(str(tmp_path / "Comb.SLDPRT"))
    mock_adapter.activate_document = AsyncMock(wraps=mock_adapter.activate_document)
    export = await _export(mock_adapter)

    reply = await export(ExportAssemblyPartsStlInput(folder=str(tmp_path / "out")))

    assert reply["status"] == "success", reply
    (comb,) = reply["files"]
    assert Path(comb["file_path"]).name == "Comb.stl"
    assert comb["mesh_check"]["triangles"] == 4
