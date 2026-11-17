"""The files this MCP server wrote, remembered across restarts (the save guard's ledger).

``save_assembly`` asks before it overwrites a part with unsaved changes that
isn't the session's own (runbook #15, ``saving.py``). The session's own files
were known only in memory, so a server restarted mid-conversation (a context
compaction in The Dipper build, SOLIDWORKS 2026 SP4.1) forgot them, and the
guard asked about a part the agent had made itself, about six times. Each
time the workaround was to close the part without saving.

Two ways to remember were weighed:

* **A custom property** written into each document the server creates. It
  travels with the file, but it changes the user's file, shows in its
  properties and BOMs, needs a COM write that dirties the document, and is
  copied with the file: a part the user made from a copy of an agent's part
  would carry it, and the guard would then overwrite the user's edits.
* **A ledger** under ``%LOCALAPPDATA%\\solidworks_mcp\\`` (this module). It
  never touches a document, and each entry pins one exact file: its id on
  the volume, size and modified time just after the server's save. A save by
  anyone else, a copy, or a move to another drive no longer matches, and the
  guard asks again. Losing the ledger only means asking again.

The ledger was chosen. ``saving.ledger_owns`` decides from an entry; this
module only stores them. Several servers may write it: each write reads it
again and replaces it whole, so a race can drop an entry (the guard then
asks), never corrupt one. ``SOLIDWORKS_MCP_FILE_LEDGER`` names another file.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

#: The most entries kept; the oldest saves go first.
MAX_ENTRIES = 500


def ledger_path(env: dict[str, str] | None = None) -> Path:
    """Where the ledger lives (``SOLIDWORKS_MCP_FILE_LEDGER`` overrides)."""
    env = dict(os.environ) if env is None else env
    explicit = env.get("SOLIDWORKS_MCP_FILE_LEDGER", "").strip()
    if explicit:
        return Path(explicit)
    base = env.get("LOCALAPPDATA", "").strip() or str(Path.home())
    return Path(base) / "solidworks_mcp" / "written_files.json"


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def disk_identity(path: str) -> dict[str, Any] | None:
    """A file's ``file_id`` (``[st_dev, st_ino]``, None without ids), size and mtime."""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return {
        "file_id": [status.st_dev, status.st_ino] if status.st_ino else None,
        "size": status.st_size,
        "mtime_ns": status.st_mtime_ns,
    }


def _same_file(entry: dict[str, Any], found: dict[str, Any]) -> bool:
    """Whether the file on disk is the one the entry recorded, unchanged."""
    if entry.get("size") != found["size"] or entry.get("mtime_ns") != found["mtime_ns"]:
        return False
    recorded = entry.get("file_id")
    return recorded is None or found["file_id"] is None or recorded == found["file_id"]


class FileLedger:
    """The ledger file: one entry per file the server wrote, by normalized path.

    Args:
        path: The JSON file.
        clock: ``time.time`` (tests pass a fake).
    """

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = path
        self._clock = clock

    def entries(self) -> dict[str, dict[str, Any]]:
        """Every entry; an unreadable or malformed ledger is empty."""
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(loaded, dict):
            return {}
        return {k: v for k, v in loaded.items() if isinstance(v, dict)}

    def _store(self, entries: dict[str, dict[str, Any]]) -> None:
        newest = sorted(
            entries.items(), key=lambda item: -float(item[1].get("saved_at") or 0)
        )[:MAX_ENTRIES]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".tmp")
            partial.write_text(json.dumps(dict(newest)), encoding="utf-8")
            partial.replace(self.path)
        except OSError as exc:
            logger.debug("Could not write the file ledger {}: {}", self.path, exc)

    def record(
        self,
        path: str,
        *,
        created: bool,
        solidworks: tuple[int, int] | None,
        update_stamp: int | None,
    ) -> None:
        """Record that the server just wrote ``path``.

        Args:
            path: The file written.
            created: The server wrote it first (it was not the user's file).
                Once recorded, it stays so through later saves.
            solidworks: The SolidWorks process (id, start time) that saved it.
            update_stamp: The document's ``GetUpdateStamp`` just after.
        """
        found = disk_identity(path)
        if found is None:
            return
        entries = self.entries()
        key = _key(path)
        old = entries.get(key) or {}
        entries[key] = {
            "path": os.path.abspath(path),
            "created": bool(created or old.get("created")),
            **found,
            "solidworks_pid": solidworks[0] if solidworks else None,
            "solidworks_started": solidworks[1] if solidworks else None,
            "update_stamp": update_stamp,
            "saved_at": self._clock(),
        }
        self._store(entries)

    def unchanged_entry(self, path: str) -> dict[str, Any] | None:
        """The entry for the file now at ``path``, if nobody has saved it since.

        Found by path, else by file id (a rename or a move on the drive). The
        file's size and modified time (and id, when both have one) must be
        those recorded just after the server's save.
        """
        found = disk_identity(path)
        if found is None:
            return None
        entries = self.entries()
        entry = entries.get(_key(path))
        if entry is None and found["file_id"] is not None:
            entry = next(
                (e for e in entries.values() if e.get("file_id") == found["file_id"]),
                None,
            )
        return entry if entry is not None and _same_file(entry, found) else None

    def follow_move(self, source: str, destination: str) -> None:
        """Carry ``source``'s entry to ``destination`` when the file moved unchanged."""
        entries = self.entries()
        entry = entries.pop(_key(source), None)
        if entry is None:
            return
        found = disk_identity(destination)
        if (
            found is not None
            and entry.get("size") == found["size"]
            and entry.get("mtime_ns") == found["mtime_ns"]
        ):
            entries[_key(destination)] = {
                **entry,
                "path": os.path.abspath(destination),
                "file_id": found["file_id"],
            }
        self._store(entries)
