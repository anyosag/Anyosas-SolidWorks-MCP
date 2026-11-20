"""Hot-reload relay: keeps the MCP client connected while the server restarts.

``solidworks-mcp --hot-reload [server args]`` starts this relay in place of
the server. The client (Claude) talks to the relay over stdio; the relay runs
the real server as a child process and passes every JSON-RPC line through.

When the package's source files change (a merge or checkout in the install's
folder) or the ``reload_mcp_server`` tool is called, the relay:

1. waits until no request is in flight;
2. starts a new server and replays the client's ``initialize`` handshake to
   it (and any ``enable_tool_groups`` calls), so it is ready before anything
   else changes;
3. stops the old server by closing its stdin;
4. tells the client ``notifications/tools/list_changed`` (and the resource
   and prompt variants), so it fetches the new tool list.

A new server that fails to start is discarded and the old one keeps
serving. A server that dies on its own is restarted, and its in-flight
requests are answered with an error.

A tool call with no reply or progress for ``SOLIDWORKS_MCP_HANG_SECONDS``
(900 by default; ``0`` turns this off) is stuck: a COM call that never
returns blocks the server's event loop, so the server can neither answer nor
stop it. The relay answers that call, and every other one in flight, with an
``operation_stuck`` error, kills the server and starts a new one as above.

The relay uses only the standard library and never imports the server, so a
broken server file can't break it. Its own code is the one part that still
needs a client restart to update.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

RELOAD_TOOL = "reload_mcp_server"
RELOAD_TOOL_SPEC: dict[str, Any] = {
    "name": RELOAD_TOOL,
    "description": (
        "Restart the SolidWorks MCP server process so code changes take effect, "
        "without restarting the client. Open SolidWorks documents stay open; "
        "the server's session memory (active document, tool groups other than "
        "those enabled with enable_tool_groups) starts fresh. The relay also "
        "does this by itself when the server's files change."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}
_LIST_CHANGED = (
    "notifications/tools/list_changed",
    "notifications/resources/list_changed",
    "notifications/prompts/list_changed",
)
_REPLAYED_TOOLS = ("enable_tool_groups",)

HANG_SECONDS = 900.0
"""How long a tool call may go without a reply or progress (the default)."""
STUCK_ERROR = "operation_stuck"
_STUCK_HINT = (
    "The call may have finished, finished in part, or still be running in "
    "SolidWorks. Before repeating it, pick the document again "
    "(list_open_documents, activate_document) and check what the call did "
    "(list_features). If calls keep getting stuck, SolidWorks itself is "
    "stuck, for example on a dialog: tell the user."
)
_STARTS_FRESH = (
    " Documents stay open in SolidWorks; the server's session memory "
    "(current document and sketch) starts fresh."
)


def _log(message: str) -> None:
    """Write a relay message to stderr (stdout is the MCP channel)."""
    sys.stderr.write(f"[solidworks-mcp relay] {message}\n")
    sys.stderr.flush()


def snapshot(folder: Path) -> dict[str, tuple[float, int]]:
    """Return modified time and size of every source file under ``folder``.

    Args:
        folder: The package folder to watch.

    Returns:
        dict[str, tuple[float, int]]: Path to (mtime, size), skipping caches.
    """
    files: dict[str, tuple[float, int]] = {}
    for path in folder.rglob("*"):
        if "__pycache__" in path.parts or path.suffix in (".pyc", ".pyo"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file():
            files[str(path)] = (stat.st_mtime, stat.st_size)
    return files


class _Child:
    """One server process and the thread that reads its stdout."""

    def __init__(self, command: list[str], generation: int) -> None:
        self.generation = generation
        self.retiring = False
        self.exited = False
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
            # The server itself must not start another relay.
            env={
                k: v for k, v in os.environ.items() if k != "SOLIDWORKS_MCP_HOT_RELOAD"
            },
        )

    def send(self, line: bytes) -> bool:
        """Write one JSON-RPC line to the server; False if its pipe is gone."""
        stdin = self.process.stdin
        assert stdin is not None
        try:
            stdin.write(line if line.endswith(b"\n") else line + b"\n")
            stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def stop(self, timeout: float) -> None:
        """Close stdin so the server exits by itself, then kill it if it doesn't.

        stdin is closed on a thread of its own: closing waits for a write in
        progress, and a write to a server that has stopped reading (one stuck
        in a COM call) ends only when the server is killed.
        """
        self.retiring = True
        assert self.process.stdin is not None
        threading.Thread(target=self.process.stdin.close, daemon=True).start()
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()


@dataclass(eq=False)
class _Call:
    """A client request sent to a server that hasn't answered it yet.

    Attributes:
        method: The request's method.
        child: The server it went to.
        name: The tool, for ``tools/call``.
        token: Its ``params._meta.progressToken``, if any.
        started: When it went in (``time.monotonic``).
        heard: When it went in or last had progress; the watchdog's clock.
    """

    method: str
    child: _Child
    name: Any
    token: Any
    started: float
    heard: float


def _new_call(message: dict[str, Any], child: _Child) -> _Call:
    params = message.get("params") or {}
    token = (params.get("_meta") or {}).get("progressToken")
    now = time.monotonic()
    return _Call(message["method"], child, params.get("name"), token, now, now)


class Relay:
    """Pass MCP traffic between a client and a restartable server process.

    Args:
        command: The server command line.
        out: The client's stdout (binary).
        watch: The folder whose changes trigger a reload, or None.
        poll_seconds: How often to look at the watched files.
        settle_seconds: How long the files must stay unchanged before a reload
            (a merge writes many files).
        handshake_timeout: How long a new server gets to answer ``initialize``.
        stop_timeout: How long an old server gets to exit after EOF.
        hang_seconds: How long a tool call may go without a reply or progress
            before it counts as stuck and the server is restarted; 0 turns
            the watchdog off.
        kill_grace: How long a stuck server gets to exit after EOF before it
            is killed (inside a COM call it can't read EOF).
    """

    def __init__(
        self,
        command: list[str],
        out: BinaryIO,
        watch: Path | None = None,
        poll_seconds: float = 2.0,
        settle_seconds: float = 3.0,
        handshake_timeout: float = 60.0,
        stop_timeout: float = 10.0,
        hang_seconds: float = HANG_SECONDS,
        kill_grace: float = 2.0,
    ) -> None:
        self.command = command
        self.out = out
        self.watch = watch
        self.poll_seconds = poll_seconds
        self.settle_seconds = settle_seconds
        self.handshake_timeout = handshake_timeout
        self.stop_timeout = stop_timeout
        self.hang_seconds = hang_seconds
        self.kill_grace = kill_grace

        self._lock = threading.Condition()
        self._out_lock = threading.Lock()
        self._child: _Child | None = None
        self._generation = 0
        self._initialize: dict[str, Any] | None = None
        self._initialized: bytes | None = None
        self._replays: dict[str, bytes] = {}
        self._in_flight: dict[Any, _Call] = {}
        self._child_requests: set[Any] = set()
        self._handshakes: dict[int, threading.Event] = {}
        self._reload_wanted: list[str] = []
        self._reload_done = threading.Condition(self._lock)
        self._killing: _Child | None = None
        self._reloads = 0
        self._attempts = 0
        self.last_error: str | None = None
        self.retry_seconds = 10.0
        self._closed = False

    # -- lifecycle -----------------------------------------------------

    def start(self) -> None:
        """Start the first server and the reload, watch and watchdog threads."""
        with self._lock:
            self._child = self._spawn()
        threading.Thread(target=self._reload_loop, daemon=True).start()
        if self.watch is not None:
            threading.Thread(target=self._watch_loop, daemon=True).start()
        if self.hang_seconds > 0:
            threading.Thread(target=self._watchdog_loop, daemon=True).start()

    def close(self) -> None:
        """Stop the server; the client has gone."""
        with self._lock:
            self._closed = True
            child = self._child
            self._lock.notify_all()
        if child is not None:
            child.stop(self.stop_timeout)

    def run(self, stdin: BinaryIO) -> None:
        """Relay client lines until the client closes stdin."""
        self.start()
        try:
            for line in iter(stdin.readline, b""):
                if line.strip():
                    self.from_client(line)
        finally:
            self.close()

    @property
    def reloads(self) -> int:
        """How many reloads have completed."""
        return self._reloads

    def _spawn(self) -> _Child:
        self._generation += 1
        child = _Child(self.command, self._generation)
        threading.Thread(target=self._read_child, args=(child,), daemon=True).start()
        return child

    # -- client to server ----------------------------------------------

    def from_client(self, line: bytes) -> None:
        """Handle one line from the client."""
        message = _parse(line)
        if message is None:
            self._forward_to_child(line)
            return
        method = message.get("method")
        has_id = "id" in message
        request = method is not None and has_id
        tool = (
            (message.get("params") or {}).get("name")
            if method == "tools/call"
            else None
        )
        if request and tool == RELOAD_TOOL:
            threading.Thread(
                target=self._reload_tool_call, args=(message["id"],), daemon=True
            ).start()
            return
        call: _Call | None = None
        with self._lock:
            if method == "initialize":
                self._initialize = message
            elif method == "notifications/initialized":
                self._initialized = line
            if method is None and has_id:
                self._child_requests.discard(message["id"])
                now = time.monotonic()
                for waiting in self._in_flight.values():
                    waiting.heard = now  # it waited on the client, not on SolidWorks
                self._lock.notify_all()
            child = self._child
            if child is not None and request:
                call = _new_call(message, child)
                self._in_flight[message["id"]] = call
        # Written outside the lock: a write to a server that has stopped
        # reading blocks until it is killed, and the watchdog needs the lock.
        sent = child is not None and child.send(line)
        if not request:
            return
        with self._lock:
            if sent:
                if tool in _REPLAYED_TOOLS:
                    self._replays[tool] = line
                return
            if call is not None and self._in_flight.get(message["id"]) is not call:
                return  # answered already, when its server was stopped
            self._in_flight.pop(message["id"], None)
        self._answer_error(
            message["id"], "The SolidWorks MCP server is restarting; try again."
        )

    def _forward_to_child(self, line: bytes) -> None:
        with self._lock:
            child = self._child
        if child is not None:
            child.send(line)

    # -- server to client ----------------------------------------------

    def _read_child(self, child: _Child) -> None:
        stdout = child.process.stdout
        assert stdout is not None
        for line in iter(stdout.readline, b""):
            if line.strip():
                self._from_child(child, line)
        self._child_exited(child)

    def _from_child(self, child: _Child, line: bytes) -> None:
        message = _parse(line) or {}
        method = message.get("method")
        with self._lock:
            if message.get("id") == _init_id(child):
                # A new server's answer to the replayed handshake.
                event = self._handshakes.get(child.generation)
                if event is not None:
                    event.set()
                return
            if method is None and "id" in message:
                # An answer reaches the client while its call is open, also
                # from a server replaced during the call.
                call = self._in_flight.get(message["id"])
                if call is None or call.child is not child:
                    return  # a replay's answer, or a call answered already
                del self._in_flight[message["id"]]
                self._lock.notify_all()
                if call.method == "tools/list":
                    line = _add_reload_tool(message, line)
            elif child is not self._child:
                return  # a new server's log notices; an old one's requests
            elif method == "notifications/progress":
                # Progress on a call restarts its watchdog clock.
                token = (message.get("params") or {}).get("progressToken")
                for running in self._in_flight.values():
                    if running.token is not None and running.token == token:
                        running.heard = time.monotonic()
            elif "id" in message:
                self._child_requests.add(message["id"])
        self._write(line)

    def _child_exited(self, child: _Child) -> None:
        with self._lock:
            child.exited = True
            handshake = self._handshakes.get(child.generation)
            if handshake is not None:
                handshake.set()  # a new server that died: don't wait it out
            if self._closed:
                return
            # Its open calls end with it, also when it had been replaced.
            lost = [rid for rid, call in self._in_flight.items() if call.child is child]
            for request_id in lost:
                del self._in_flight[request_id]
            if child is self._child and not child.retiring:
                _log(f"server exited (code {child.process.poll()}); restarting it")
                self._child_requests.clear()
                self._child = None
                self._reload_wanted.append("the server exited")
            self._lock.notify_all()
        for request_id in lost:
            self._answer_error(
                request_id,
                "The SolidWorks MCP server stopped during this call and is restarting.",
            )

    # -- stuck calls -------------------------------------------------------

    def _watchdog_loop(self) -> None:
        while True:
            with self._lock:
                stuck, wait = self._stuck_call(time.monotonic())
                while stuck is None and not self._closed:
                    self._lock.wait(timeout=min(wait, threading.TIMEOUT_MAX))
                    stuck, wait = self._stuck_call(time.monotonic())
                if self._closed or stuck is None:
                    return
                child = stuck.child
                lost = {
                    rid: call
                    for rid, call in self._in_flight.items()
                    if call.child is child
                }
                for request_id in lost:
                    del self._in_flight[request_id]
                self._child_requests.clear()
                self._child = None
                # The new server starts once this one is dead.
                self._killing = child
                self._reload_wanted.append(f"{stuck.name} was stuck")
            _log(
                f"{stuck.name} had no reply or progress for "
                f"{self.hang_seconds:g} s; restarting the server"
            )
            self._answer_stuck(stuck, lost)
            child.stop(self.kill_grace)
            with self._lock:
                self._killing = None
                self._lock.notify_all()

    def _stuck_call(self, now: float) -> tuple[_Call | None, float]:
        """The current server's stuck call, or how long until one can be."""
        calls = [
            call
            for call in self._in_flight.values()
            if call.method == "tools/call" and call.child is self._child
        ]
        if not calls or self._child_requests:
            # Nothing running, or the server is waiting on the client.
            return None, self.hang_seconds
        oldest = min(calls, key=lambda call: call.heard)
        left = oldest.heard + self.hang_seconds - now
        return (oldest if left <= 0 else None), left

    def _answer_stuck(self, stuck: _Call, lost: dict[Any, _Call]) -> None:
        now = time.monotonic()
        silent = f"{stuck.name} had no reply or progress for {self.hang_seconds:g} s"
        for request_id, call in lost.items():
            if call.method != "tools/call":
                self._answer_error(
                    request_id,
                    f"The SolidWorks MCP server was restarted because {silent}; "
                    "try again.",
                )
                continue
            if call is stuck:
                message = f"{silent}, so the SolidWorks MCP server was restarted."
            else:
                message = (
                    "The SolidWorks MCP server was restarted before this call "
                    f"finished, because {silent}."
                )
            payload = {
                "status": "error",
                "message": message + _STARTS_FRESH,
                "error_type": STUCK_ERROR,
                "hint": _STUCK_HINT,
                "execution_time": round(now - call.started, 3),
            }
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": json.dumps(payload)}],
                    },
                }
            )

    # -- reloading -----------------------------------------------------

    def request_reload(self, reason: str) -> None:
        """Ask for a reload; it happens once no request is in flight."""
        with self._lock:
            self._reload_wanted.append(reason)
            self._lock.notify_all()

    def _reload_tool_call(self, request_id: Any) -> None:
        with self._lock:
            target = self._attempts + 1
            reloads = self._reloads
            self._reload_wanted.append("reload_mcp_server was called")
            self._lock.notify_all()
            while self._attempts < target and not self._closed:
                self._reload_done.wait(timeout=1.0)
            error = self.last_error or "the client closed"
            ok = self._reloads > reloads
        if not ok:
            text = (
                f"Reload failed: {error}. The old server is still running, if it was."
            )
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
            return
        self._write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Reloaded the SolidWorks MCP server; the tool list is refreshed.",
                        }
                    ]
                },
            }
        )

    def _reload_loop(self) -> None:
        while True:
            with self._lock:
                while not self._closed and not (
                    self._reload_wanted
                    and not self._in_flight
                    and not self._child_requests
                    and self._killing is None
                ):
                    self._lock.wait(timeout=1.0)
                if self._closed:
                    return
                reasons = ", ".join(dict.fromkeys(self._reload_wanted))
                self._reload_wanted.clear()
            self._reload(reasons)

    def _reload(self, reason: str) -> bool:
        _log(f"reloading: {reason}")
        event = threading.Event()
        with self._lock:
            new = self._spawn()
            self._handshakes[new.generation] = event
            initialize = self._initialize
            initialized = self._initialized
            replays = list(self._replays.values())
        if initialize is None:
            with self._lock:
                self._handshakes.pop(new.generation, None)
        else:
            replay = dict(initialize, id=_init_id(new))
            new.send(json.dumps(replay).encode())
            ok = event.wait(self.handshake_timeout)
            with self._lock:
                self._handshakes.pop(new.generation, None)
            if not ok:
                return self._reload_failed(
                    new, "the new server didn't answer initialize"
                )
            if new.exited:
                return self._reload_failed(new, "the new server exited while starting")
            if initialized is not None:
                new.send(initialized)
            for index, line in enumerate(replays):
                call = _parse(line) or {}
                new.send(json.dumps(dict(call, id=f"relay-replay-{index}")).encode())
        with self._lock:
            closed = self._closed
            old = self._child
            if not closed:
                self._child = new
        if closed:
            new.stop(self.stop_timeout)
            return False
        if old is not None:
            old.stop(self.stop_timeout)
        if initialize is not None:
            for method in _LIST_CHANGED:
                self._write_json({"jsonrpc": "2.0", "method": method})
        with self._lock:
            self._reloads += 1
            self._attempts += 1
            self.last_error = None
            self._reload_done.notify_all()
        _log("reloaded")
        return True

    def _reload_failed(self, new: _Child, error: str) -> bool:
        new.stop(self.stop_timeout)
        _log(f"reload failed, {error}; keeping the old server")
        with self._lock:
            self.last_error = error
            self._attempts += 1
            no_server = self._child is None and not self._closed
            self._reload_done.notify_all()
        if no_server:
            # Nothing to fall back to (the old server died): keep trying.
            timer = threading.Timer(
                self.retry_seconds, self.request_reload, args=("retrying the start",)
            )
            timer.daemon = True
            timer.start()
        return False

    def _watch_loop(self) -> None:
        assert self.watch is not None
        seen = snapshot(self.watch)
        changed_at: float | None = None
        while not self._closed:
            time.sleep(self.poll_seconds)
            now = snapshot(self.watch)
            if now != seen:
                seen = now
                changed_at = time.monotonic()
            elif (
                changed_at is not None
                and time.monotonic() - changed_at >= self.settle_seconds
            ):
                changed_at = None
                self.request_reload("the server's files changed")

    # -- output ----------------------------------------------------------

    def _answer_error(self, request_id: Any, text: str) -> None:
        self._write_json(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": text},
            }
        )

    def _write_json(self, message: dict[str, Any]) -> None:
        self._write(json.dumps(message).encode() + b"\n")

    def _write(self, line: bytes) -> None:
        with self._out_lock:
            self.out.write(line if line.endswith(b"\n") else line + b"\n")
            self.out.flush()


def _parse(line: bytes) -> dict[str, Any] | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


def _init_id(child: _Child) -> str:
    return f"relay-init-{child.generation}"


def _add_reload_tool(message: dict[str, Any], line: bytes) -> bytes:
    result = message.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return line
    if result.get("nextCursor") or any(
        isinstance(t, dict) and t.get("name") == RELOAD_TOOL for t in result["tools"]
    ):
        return line
    result["tools"].append(RELOAD_TOOL_SPEC)
    return json.dumps(message).encode() + b"\n"


def server_command(args: list[str]) -> list[str]:
    """The command that runs the real server with the given arguments."""
    return [sys.executable, "-m", "solidworks_mcp.server", *args]


def _hang_seconds(value: str | None) -> float:
    """``SOLIDWORKS_MCP_HANG_SECONDS`` as seconds; 0 turns the watchdog off."""
    if value is None:
        return HANG_SECONDS
    try:
        seconds = float(value)
    except ValueError:
        _log(
            f"SOLIDWORKS_MCP_HANG_SECONDS={value!r} is not a number; "
            f"using {HANG_SECONDS:g}"
        )
        return HANG_SECONDS
    return seconds if 0 < seconds < float("inf") else 0.0


def main(args: list[str], run: Callable[[Relay], None] | None = None) -> None:
    """Run the relay for ``solidworks-mcp --hot-reload [server args]``.

    Args:
        args: The server's own arguments, without ``--hot-reload``.
        run: Test hook that runs the relay instead of reading real stdin.
    """
    watch = None
    if os.environ.get("SOLIDWORKS_MCP_HOT_RELOAD_WATCH", "1") != "0":
        watch = Path(__file__).resolve().parent
    hang = _hang_seconds(os.environ.get("SOLIDWORKS_MCP_HANG_SECONDS"))
    relay = Relay(
        server_command(args), sys.stdout.buffer, watch=watch, hang_seconds=hang
    )
    _log(
        (f"started; watching {watch}" if watch else "started; file watching off")
        + (f"; a call silent for {hang:g} s is stuck" if hang else "; no watchdog")
    )
    if run is not None:
        run(relay)
        return
    relay.run(sys.stdin.buffer)
