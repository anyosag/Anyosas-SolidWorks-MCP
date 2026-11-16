"""Reading back an exported file: there, not empty, and its format."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from solidworks_mcp.utils import export_check
from solidworks_mcp.utils.export_check import check_export, failure_message, passed


def _iges(*lines: tuple[str, str]) -> bytes:
    return "".join(f"{text:<72}{section}{1:>7}\r\n" for text, section in lines).encode()


_STEP = (
    b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 }'));\n"
    b"ENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
)
_IGES = _iges(("start", "S"), ("1H,,1H;;", "G"), ("S1G1D0P0", "T"))
_PDF = b"%PDF-1.7\n%\xe2\xe3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\r\n"
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    + struct.pack(">II", 640, 480)
    + bytes(9)
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)
_JPG = (
    b"\xff\xd8\xff\xe0\x00\x04\x00\x00"
    + b"\xff\xc0\x00\x0b\x08"
    + struct.pack(">HH", 300, 200)
    + b"\x01\x01\x11\x00\xff\xd9"
)
_BMP_HEADER = b"BM" + struct.pack("<I", 30) + bytes(12) + struct.pack("<ii", 3, -2)
_BMP = _BMP_HEADER + bytes(30 - len(_BMP_HEADER))


def _write(tmp_path: Path, name: str, data: bytes) -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.parametrize(
    ("name", "data", "details"),
    [
        ("a.step", _STEP, {"schema": "AUTOMOTIVE_DESIGN { 1 0 10303 214 }"}),
        ("a.stp", b"\xef\xbb\xbf" + _STEP, {}),
        ("a.igs", _IGES, {}),
        ("a.pdf", _PDF, {"version": "1.7"}),
        ("a.dwg", b"AC1027" + bytes(40), {"version": "AC1027", "release": "R2013"}),
        ("a.dwg", b"AC1099" + bytes(40), {"version": "AC1099"}),
        ("a.dxf", b"999\ncomment\n  0\nSECTION\n  0\nEOF\n", {"binary": False}),
        ("a.dxf", b"AutoCAD Binary DXF\r\n\x1a\x00rest", {"binary": True}),
        ("a.png", _PNG, {"width": 640, "height": 480}),
        ("a.jpeg", _JPG, {"width": 200, "height": 300}),
        ("a.bmp", _BMP, {"width": 3, "height": 2}),
        ("a.tiff", b"MM\x00*" + bytes(10), {}),
        ("a.glb", b"glTF" + struct.pack("<II", 2, 16) + bytes(4), {"version": 2}),
        ("a.gltf", b'  {"asset": {"version": "2.0"}}', {}),
    ],
)
def test_a_good_file_of_each_format_passes(
    tmp_path: Path, name: str, data: bytes, details: dict
) -> None:
    check = check_export(_write(tmp_path, name, data))

    assert check["exists"] is True
    assert check["size_bytes"] == len(data)
    assert check["format_ok"] is True, check
    assert "problem" not in check
    assert details.items() <= check.items()
    assert passed(check)


@pytest.mark.parametrize(
    ("name", "data", "problem"),
    [
        ("a.step", b"<html>not step</html>", "does not start with ISO-10303-21"),
        ("a.step", _STEP[:-20], "cut short"),
        ("a.igs", b"short line\n", "no S (start section)"),
        ("a.igs", _IGES.rsplit(b"\r\n", 2)[0], "no T (terminate section)"),
        ("a.pdf", b"hello", "no %PDF- header"),
        ("a.pdf", b"%PDF-1.4\n1 0 obj", "no %%EOF"),
        ("a.dwg", b"%PDF-1.4", "DWG version string"),
        ("a.dxf", b"hello\nworld\n", "DXF section"),
        ("a.dxf", b"  0\nSECTION\n  2\nHEADER\n", "last line is not EOF"),
        ("a.png", b"GIF89a....", "PNG signature"),
        ("a.png", _PNG[:-12], "no IEND"),
        ("a.jpg", _PNG, "start-of-image"),
        ("a.jpg", _JPG[:-2], "end-of-image"),
        ("a.bmp", b"BM", "BMP header"),
        ("a.bmp", _BMP[:-4], "header says 30 bytes but the file has 26"),
        ("a.tif", b"TIFF", "byte-order header"),
        ("a.glb", b"glTX" + bytes(8), "binary glTF magic"),
        ("a.glb", b"glTF" + struct.pack("<II", 2, 99), "header says 99 bytes"),
        ("a.gltf", b"[]", "glTF JSON"),
    ],
)
def test_a_bad_file_says_what_is_wrong(
    tmp_path: Path, name: str, data: bytes, problem: str
) -> None:
    check = check_export(_write(tmp_path, name, data))

    assert check["exists"] is True
    assert check["format_ok"] is False
    assert problem in check["problem"]
    assert not passed(check)


def test_a_missing_file_is_not_valid(tmp_path: Path) -> None:
    check = check_export(tmp_path / "none.step")

    assert check == {
        "file_path": str(tmp_path / "none.step"),
        "exists": False,
        "size_bytes": None,
        "format": "STEP",
        "format_ok": False,
        "problem": "no file was written there",
    }
    assert failure_message(check) == (
        f"The export reported success, but {tmp_path / 'none.step'} is not a "
        "valid STEP file: no file was written there."
    )


def test_an_empty_file_is_not_valid(tmp_path: Path) -> None:
    check = check_export(_write(tmp_path, "a.pdf", b""))

    assert check["size_bytes"] == 0
    assert check["problem"] == "it is empty (0 bytes)"
    assert not passed(check)


def test_the_extension_decides_and_the_tools_format_is_the_fallback(
    tmp_path: Path,
) -> None:
    as_dxf = check_export(_write(tmp_path, "a.dxf", b"AC1032"), "DWG")
    unknown = check_export(_write(tmp_path, "a.out", _STEP), "step")
    nothing_known = check_export(_write(tmp_path, "a.xyz", b"data"))

    assert as_dxf["format"] == "DXF" and as_dxf["format_ok"] is False
    assert unknown["format"] == "STEP" and unknown["format_ok"] is True
    assert nothing_known["format"] is None and nothing_known["format_ok"] is None
    assert passed(nothing_known)


def test_a_large_file_reads_only_its_ends(tmp_path: Path) -> None:
    body = b"#1=CARTESIAN_POINT('',(0.,0.,0.));\n" * 2000
    data = _STEP.replace(b"DATA;\n", b"DATA;\n" + body)

    check = check_export(_write(tmp_path, "big.step", data))

    assert len(data) > 2 * export_check.CHUNK
    assert check["format_ok"] is True


def test_an_unreadable_file_is_not_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "a.step", _STEP)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", refuse)
    check = check_export(path)

    assert check["format_ok"] is False
    assert check["problem"] == "it could not be read (Permission denied)"


def test_a_jpeg_without_a_frame_header_in_its_start_has_no_size(
    tmp_path: Path,
) -> None:
    data = b"\xff\xd8\xff\xe0\x00\x04\x00\x00\x00\x00\x00\x00\x00\x00\xff\xd9"

    check = check_export(_write(tmp_path, "a.jpg", data))

    assert check["format_ok"] is True
    assert "width" not in check


def test_the_message_without_a_format_or_problem() -> None:
    assert failure_message({"file_path": "x.bin"}) == (
        "The export reported success, but x.bin is not a valid exported file: "
        "it could not be checked."
    )


def test_format_for_reads_the_extension() -> None:
    assert export_check.format_for("C:/out/PART.STP") == "STEP"
    assert export_check.format_for("drawing.slddrw") is None
