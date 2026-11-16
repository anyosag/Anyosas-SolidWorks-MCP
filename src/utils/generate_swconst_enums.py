"""Generate the swconst enum index that ``lookup_api_enum`` answers from.

Reads every enum in ``SolidWorks.Interop.swconst.dll`` by .NET reflection
(Windows PowerShell loads the assembly; nothing starts or calls SolidWorks)
and writes ``src/solidworks_mcp/data/swconst_enums.json``: each enum's
members and values in declaration order, plus the SolidWorks release the
assembly came from. The JSON is committed, so the tool works on machines
without SolidWorks; run this again after a SolidWorks upgrade.

Usage::

    python src/utils/generate_swconst_enums.py [--dll PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

from solidworks_mcp.utils.solidworks_install import install_folder  # noqa: E402

DEFAULT_OUT = _ROOT / "src" / "solidworks_mcp" / "data" / "swconst_enums.json"
_DLL_NAME = "SolidWorks.Interop.swconst.dll"
_YEAR_OFFSET = 1992

# One line per member: enum <TAB> member <TAB> value <TAB> flags (0/1).
# The first line is the assembly version, the second its file version.
_REFLECT = r"""
$ErrorActionPreference = 'Stop'
$path = $env:SWCONST_DLL
$asm = [Reflection.Assembly]::LoadFrom($path)
[Console]::Out.WriteLine($asm.GetName().Version.ToString())
[Console]::Out.WriteLine([Diagnostics.FileVersionInfo]::GetVersionInfo($path).FileVersion)
$flags = [Reflection.BindingFlags]'Public,Static'
foreach ($type in ($asm.GetTypes() | Where-Object { $_.IsEnum } | Sort-Object Name)) {
    $isFlags = [int]($type.GetCustomAttributes([FlagsAttribute], $false).Count -gt 0)
    foreach ($field in $type.GetFields($flags)) {
        [Console]::Out.WriteLine(("{0}`t{1}`t{2}`t{3}" -f
            $type.Name, $field.Name, $field.GetRawConstantValue(), $isFlags))
    }
}
"""


def release_name(file_version: str) -> str:
    """Name the SolidWorks release a file version belongs to.

    Args:
        file_version (str): E.g. ``34.4.1.1011``.

    Returns:
        str: E.g. ``SOLIDWORKS 2026 SP4.1``.
    """
    parts = [int(p) for p in file_version.split(".")[:3] if p.strip().isdigit()]
    if len(parts) < 2:
        return f"SOLIDWORKS (file version {file_version})"
    year = parts[0] + _YEAR_OFFSET
    service_pack = f"SP{parts[1]}"
    if len(parts) > 2 and parts[2]:
        service_pack += f".{parts[2]}"
    return f"SOLIDWORKS {year} {service_pack}"


def reflect(dll: Path) -> tuple[str, str, dict[str, dict[str, int]], list[str]]:
    """Read the enums out of the assembly.

    Args:
        dll (Path): ``SolidWorks.Interop.swconst.dll``.

    Returns:
        tuple: The assembly version, file version, enums (name -> member ->
        value) and the names of the ``[Flags]`` enums.
    """
    env = {**os.environ, "SWCONST_DLL": str(dll)}
    encoded = base64.b64encode(_REFLECT.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=True,
    )
    lines = completed.stdout.splitlines()
    assembly_version, file_version = lines[0].strip(), lines[1].strip()
    enums: dict[str, dict[str, int]] = {}
    flags: list[str] = []
    for line in lines[2:]:
        if not line.strip():
            continue
        enum, member, value, is_flags = line.split("\t")
        enums.setdefault(enum, {})[member] = int(value)
        if is_flags.strip() == "1" and enum not in flags:
            flags.append(enum)
    return assembly_version, file_version, enums, flags


def main(argv: list[str] | None = None) -> int:
    """Generate the index.

    Args:
        argv (list[str] | None): Command-line arguments.

    Returns:
        int: The exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dll", type=Path, help=f"Path to {_DLL_NAME}")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    dll = args.dll
    if dll is None:
        _, folder = install_folder()
        if folder is None:
            print("SolidWorks isn't installed here; pass --dll.", file=sys.stderr)
            return 2
        dll = folder / _DLL_NAME
    if not dll.is_file():
        print(f"{dll} doesn't exist.", file=sys.stderr)
        return 2

    assembly_version, file_version, enums, flags = reflect(dll)
    index = {
        "format": "1",
        "source": {
            "assembly": "SolidWorks.Interop.swconst",
            "assembly_version": assembly_version,
            "file_version": file_version,
            "solidworks_version": release_name(file_version),
            "generated": dt.date.today().isoformat(),
            "enum_count": len(enums),
            "member_count": sum(len(m) for m in enums.values()),
        },
        # SolidWorks marks none of its bitmask enums [Flags] (2026 SP4.1),
        # so the list is kept only for a release that does.
        **({"flags_enums": flags} if flags else {}),
        "enums": enums,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(index, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"{len(enums)} enums, {index['source']['member_count']} members from "
        f"{index['source']['solidworks_version']} -> {args.out} "
        f"({args.out.stat().st_size:,} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
