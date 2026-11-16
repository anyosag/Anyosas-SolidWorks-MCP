"""Check a file an export just wrote: it is there, has bytes, and is its format.

SolidWorks can report an export done and leave nothing, an empty file, or a
file cut short. Pure Python and no COM, so the export tools can read back
what they wrote. Only the start and the end of the file are read, so a large
export costs two small reads.

The format comes from the file's extension, which is what SolidWorks picks
the format from; a tool's own format is the fallback for an extension this
module doesn't know. Each format is checked the way its own specification
starts and ends a file:

* STEP (ISO 10303-21): ``ISO-10303-21;`` first, ``END-ISO-10303-21;`` last;
* IGES: 80-column lines whose column 73 names the section, ``S`` (start)
  first and ``T`` (terminate) last;
* PDF: ``%PDF-`` in the first 1024 bytes and ``%%EOF`` in the last 1024;
* DWG: a version string ``AC10xx`` as its first six bytes;
* DXF: the binary sentinel, or an ASCII group code with ``SECTION``, ending
  in ``EOF``;
* PNG, JPG, BMP, TIFF, GLB and glTF by their magic bytes, with the picture's
  size where it can be read.
"""

from __future__ import annotations

import os
import re
import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: The format each extension stands for, as SolidWorks writes it.
FORMAT_BY_EXTENSION: dict[str, str] = {
    ".step": "STEP",
    ".stp": "STEP",
    ".igs": "IGES",
    ".iges": "IGES",
    ".pdf": "PDF",
    ".dwg": "DWG",
    ".dxf": "DXF",
    ".png": "PNG",
    ".jpg": "JPG",
    ".jpeg": "JPG",
    ".bmp": "BMP",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".glb": "GLB",
    ".gltf": "GLTF",
}

#: How many bytes of each end of the file are read.
CHUNK = 4096

#: DWG version strings and the AutoCAD release that wrote them.
DWG_RELEASES = {
    "AC1012": "R13",
    "AC1014": "R14",
    "AC1015": "R2000",
    "AC1018": "R2004",
    "AC1021": "R2007",
    "AC1024": "R2010",
    "AC1027": "R2013",
    "AC1032": "R2018",
}

_BINARY_DXF = b"AutoCAD Binary DXF\r\n\x1a\x00"
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}

#: A checker takes the file's first and last bytes and its size, and gives
#: the problem (None when the file is good) and what it read.
Checker = Callable[[bytes, bytes, int], tuple[str | None, dict[str, Any]]]


def format_for(path: str | os.PathLike[str]) -> str | None:
    """The format a file's extension stands for, or None for one not known."""
    return FORMAT_BY_EXTENSION.get(Path(path).suffix.lower())


def _text(data: bytes) -> str:
    """Bytes as text, a byte per character; a leading BOM dropped."""
    return data.decode("latin-1").lstrip("﻿\xef\xbb\xbf")


def _lines(data: bytes) -> list[str]:
    """The non-blank lines of ``data``, line ends dropped."""
    return [line for line in _text(data).splitlines() if line.strip()]


def _step(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    text = _text(head).lstrip()
    if not text.startswith("ISO-10303-21;"):
        return "it does not start with ISO-10303-21;", {}
    details: dict[str, Any] = {}
    schema = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text)
    if schema:
        details["schema"] = schema.group(1)
    if b"END-ISO-10303-21;" not in tail:
        return "it has no END-ISO-10303-21; line, so it was cut short", details
    return None, details


def _section(line: str) -> str:
    """The section letter of an IGES line (column 73), or empty."""
    return line[72] if len(line) > 72 else ""


def _iges(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    first = _lines(head)
    if not first or _section(first[0]) != "S":
        return "its first line has no S (start section) in column 73", {}
    last = _lines(tail)
    if not last or _section(last[-1]) != "T":
        return (
            "its last line has no T (terminate section) in column 73, so it was "
            "cut short",
            {},
        )
    return None, {}


def _pdf(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    start = head[:1024].find(b"%PDF-")
    if start < 0:
        return "it has no %PDF- header", {}
    version = re.match(rb"%PDF-(\d+\.\d+)", head[start:])
    details = {"version": version.group(1).decode("ascii")} if version else {}
    if b"%%EOF" not in tail[-1024:]:
        return "it has no %%EOF marker at its end, so it was cut short", details
    return None, details


def _dwg(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    version = re.match(rb"AC1\d{3}", head[:6])
    if not version:
        return "it does not start with a DWG version string (AC10xx)", {}
    code = version.group(0).decode("ascii")
    details: dict[str, Any] = {"version": code}
    if code in DWG_RELEASES:
        details["release"] = DWG_RELEASES[code]
    return None, details


def _dxf(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if head.startswith(_BINARY_DXF):
        return None, {"binary": True}
    lines = [line.strip() for line in _lines(head)]
    if not lines or not lines[0].isdigit() or "SECTION" not in lines[:8]:
        return "it does not start with a DXF section (group code 0, SECTION)", {}
    end = [line.strip() for line in _lines(tail)]
    if not end or end[-1] != "EOF":
        return "its last line is not EOF, so it was cut short", {"binary": False}
    return None, {"binary": False}


def _png(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if not head.startswith(_PNG):
        return "it does not start with the PNG signature", {}
    details: dict[str, Any] = {}
    if head[12:16] == b"IHDR" and len(head) >= 24:
        details["width"], details["height"] = struct.unpack(">II", head[16:24])
    if b"IEND" not in tail[-12:]:
        return "it has no IEND chunk at its end, so it was cut short", details
    return None, details


def _jpeg_size(head: bytes) -> dict[str, Any]:
    """A JPEG's width and height from its first frame header, when in ``head``."""
    index = 2
    while index + 9 <= len(head) and head[index] == 0xFF:
        marker = head[index + 1]
        if marker in _JPEG_SOF:
            height, width = struct.unpack(">HH", head[index + 5 : index + 9])
            return {"width": width, "height": height}
        (length,) = struct.unpack(">H", head[index + 2 : index + 4])
        index += 2 + length
    return {}


def _jpg(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if not head.startswith(b"\xff\xd8\xff"):
        return "it does not start with the JPEG start-of-image marker", {}
    details = _jpeg_size(head)
    if b"\xff\xd9" not in tail[-32:]:
        return "it has no end-of-image marker, so it was cut short", details
    return None, details


def _bmp(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if not head.startswith(b"BM") or len(head) < 26:
        return "it does not start with a BMP header (BM)", {}
    (declared,) = struct.unpack("<I", head[2:6])
    width, height = struct.unpack("<ii", head[18:26])
    details = {"width": width, "height": abs(height)}
    if declared and declared != size:
        return (
            f"its header says {declared} bytes but the file has {size}, so it "
            "was cut short",
            details,
        )
    return None, details


def _tiff(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if head[:4] not in (b"II*\x00", b"MM\x00*"):
        return "it does not start with a TIFF byte-order header", {}
    return None, {}


def _glb(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if not head.startswith(b"glTF") or len(head) < 12:
        return "it does not start with the binary glTF magic (glTF)", {}
    version, declared = struct.unpack("<II", head[4:12])
    details = {"version": version}
    if declared != size:
        return (
            f"its header says {declared} bytes but the file has {size}, so it "
            "was cut short",
            details,
        )
    return None, details


def _gltf(head: bytes, tail: bytes, size: int) -> tuple[str | None, dict[str, Any]]:
    if not _text(head).lstrip().startswith("{") or b'"asset"' not in head:
        return 'it is not glTF JSON (an object with "asset")', {}
    return None, {}


CHECKERS: dict[str, Checker] = {
    "STEP": _step,
    "IGES": _iges,
    "PDF": _pdf,
    "DWG": _dwg,
    "DXF": _dxf,
    "PNG": _png,
    "JPG": _jpg,
    "BMP": _bmp,
    "TIFF": _tiff,
    "GLB": _glb,
    "GLTF": _gltf,
}


def _ends(file: Path, size: int) -> tuple[bytes, bytes]:
    """The first and last :data:`CHUNK` bytes of ``file``."""
    with file.open("rb") as handle:
        head = handle.read(CHUNK)
        handle.seek(max(0, size - CHUNK))
        tail = handle.read(CHUNK)
    return head, tail


def check_export(
    path: str | os.PathLike[str], fallback_format: str | None = None
) -> dict[str, Any]:
    """Read back an exported file and say whether it is a good file of its format.

    Args:
        path: The file written; a relative path is taken from the working
            directory, as the adapter takes it.
        fallback_format: The format to check when the extension names none
            this module knows (the exporting tool's own, e.g. ``"STEP"``).

    Returns:
        dict[str, Any]: ``file_path`` (absolute), ``exists``, ``size_bytes``
        (None when missing), ``format`` (None when not known), ``format_ok``
        (None when the format isn't one this module checks) and, when the
        file is not good, ``problem``; plus what the format's header gave:
        ``schema`` (STEP), ``version`` (PDF, DWG, GLB), ``release`` (DWG),
        ``binary`` (DXF), ``width`` and ``height`` (pictures).
    """
    file = Path(os.path.abspath(path))
    expected = format_for(file) or (fallback_format or "").upper() or None
    check: dict[str, Any] = {
        "file_path": str(file),
        "exists": file.is_file(),
        "size_bytes": None,
        "format": expected,
        "format_ok": None,
    }
    if not check["exists"]:
        check["format_ok"] = False
        check["problem"] = "no file was written there"
        return check
    try:
        size = file.stat().st_size
        check["size_bytes"] = size
        if size == 0:
            check["format_ok"] = False
            check["problem"] = "it is empty (0 bytes)"
            return check
        head, tail = _ends(file, size)
    except OSError as exc:
        check["format_ok"] = False
        check["problem"] = f"it could not be read ({exc.strerror or exc})"
        return check
    checker = CHECKERS.get(expected or "")
    if checker is None:
        return check
    problem, details = checker(head, tail, size)
    check.update(details)
    check["format_ok"] = problem is None
    if problem:
        check["problem"] = problem
    return check


def passed(check: dict[str, Any]) -> bool:
    """Whether a :func:`check_export` result is a good file."""
    return bool(check.get("exists")) and check.get("format_ok") is not False


def failure_message(check: dict[str, Any]) -> str:
    """The error for a :func:`check_export` result that didn't pass.

    ``tools/error_classifier`` gives the ``export_invalid`` rule its words,
    "is not a valid <format> file".
    """
    kind = check.get("format") or "exported"
    return (
        f"The export reported success, but {check.get('file_path')} is not a "
        f"valid {kind} file: {check.get('problem', 'it could not be checked')}."
    )
