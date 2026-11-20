"""Tests for the hot-reload relay (solidworks_mcp.relay)."""

from __future__ import annotations

import io
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from solidworks_mcp import relay as relay_module
from solidworks_mcp.relay import RELOAD_TOOL, Relay, snapshot

FAKE = str(Path(__file__).with_name("fake_mcp_child.py"))
INIT = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {"protocolVersion": "x"},
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class Collector:
    """A binary stdout that records each JSON line the relay writes."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._buffer = b""
        self._cond = threading.Condition()

    def write(self, data: bytes) -> int:
        with self._cond:
            self._buffer += data
            while b"\n" in self._buffer:
                line, self._buffer = self._buffer.split(b"\n", 1)
                self.messages.append(json.loads(line))
            self._cond.notify_all()
        return len(data)

    def flush(self) -> None:
        return None

    def wait_for(self, match, timeout: float = 20.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for message in self.messages:
                    if match(message):
                        return message
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssertionError(f"no matching message in {self.messages}")
                self._cond.wait(left)

    def reply(self, request_id: Any, timeout: float = 20.0) -> dict[str, Any]:
        return self.wait_for(
            lambda m: m.get("id") == request_id and "method" not in m, timeout
        )


def send(relay: Relay, message: dict[str, Any]) -> None:
    relay.from_client(json.dumps(message).encode() + b"\n")


def call(relay: Relay, request_id: Any, name: str, **arguments: Any) -> None:
    send(
        relay,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )


def wait_until(condition, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition never held")
        time.sleep(0.02)


@pytest.fixture
def started(monkeypatch):
    """A relay over the fake server, after the client's handshake."""
    monkeypatch.setenv("FAKE_TOOL", "tool_a")
    out = Collector()
    relay = Relay([sys.executable, FAKE], out, stop_timeout=5.0)
    relay.start()
    send(relay, INIT)
    out.reply(0)
    send(relay, INITIALIZED)
    yield relay, out
    relay.close()


def tool_names(out: Collector, request_id: Any) -> list[str]:
    return [t["name"] for t in out.reply(request_id)["result"]["tools"]]


def test_passes_calls_through_and_adds_the_reload_tool(started):
    relay, out = started
    send(relay, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tool_names(out, 1) == ["tool_a", RELOAD_TOOL]
    call(relay, 2, "echo", x=1)
    text = json.loads(out.reply(2)["result"]["content"][0]["text"])
    assert text == {"tool": "tool_a", "name": "echo", "arguments": {"x": 1}}


def test_reload_tool_swaps_the_server_and_says_the_list_changed(started, monkeypatch):
    relay, out = started
    monkeypatch.setenv("FAKE_TOOL", "tool_b")
    call(relay, 5, RELOAD_TOOL)
    reply = out.reply(5)
    assert "Reloaded" in reply["result"]["content"][0]["text"]
    out.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed")
    send(relay, {"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
    assert tool_names(out, 6) == ["tool_b", RELOAD_TOOL]
    # The new server's handshake answer and log notice never reach the client.
    assert [m.get("id") for m in out.messages].count(0) == 1
    notices = [m for m in out.messages if m.get("method") == "notifications/message"]
    assert len(notices) == 1  # the first server's, from the client's own handshake


def test_reload_waits_for_a_call_in_flight(started):
    relay, out = started
    call(relay, 7, "slow", seconds=1.0)
    relay.request_reload("test")
    reply = out.reply(7)
    assert "result" in reply
    wait_until(lambda: relay.reloads == 1)
    index = out.messages.index(reply)
    changed = out.wait_for(
        lambda m: m.get("method") == "notifications/tools/list_changed"
    )
    assert out.messages.index(changed) > index


def test_a_server_that_fails_to_start_keeps_the_old_one(started, monkeypatch):
    relay, out = started
    relay.handshake_timeout = 3.0
    monkeypatch.setenv("FAKE_FAIL", "1")
    call(relay, 8, RELOAD_TOOL)
    reply = out.reply(8)
    assert reply["result"]["isError"] is True
    assert "exited while starting" in reply["result"]["content"][0]["text"]
    monkeypatch.delenv("FAKE_FAIL")
    call(relay, 9, "echo")
    assert "result" in out.reply(9)
    assert relay.reloads == 0


def test_a_server_that_never_answers_is_given_up_on(started, monkeypatch):
    relay, out = started
    relay.handshake_timeout = 0.5
    monkeypatch.setenv("FAKE_MUTE", "1")
    call(relay, 19, RELOAD_TOOL)
    text = out.reply(19)["result"]["content"][0]["text"]
    assert "didn't answer initialize" in text
    monkeypatch.delenv("FAKE_MUTE")
    call(relay, 20, "echo")
    assert "result" in out.reply(20)


def test_a_crashed_server_answers_its_call_and_restarts(started):
    relay, out = started
    call(relay, 10, "crash")
    reply = out.reply(10)
    assert "stopped during this call" in reply["error"]["message"]
    wait_until(lambda: relay.reloads == 1)
    call(relay, 11, "echo")
    assert "result" in out.reply(11)


def test_a_server_that_keeps_failing_is_retried(started, monkeypatch):
    relay, out = started
    relay.handshake_timeout = 3.0
    relay.retry_seconds = 0.2
    monkeypatch.setenv("FAKE_FAIL", "1")
    call(relay, 12, "crash")
    out.reply(12)
    wait_until(lambda: relay.last_error is not None)
    call(relay, 13, "echo")
    assert "restarting" in out.reply(13)["error"]["message"]
    monkeypatch.delenv("FAKE_FAIL")
    wait_until(lambda: relay.reloads == 1)
    call(relay, 14, "echo")
    assert "result" in out.reply(14)


def test_enable_tool_groups_is_replayed_to_the_new_server(
    started, monkeypatch, tmp_path
):
    relay, out = started
    log = tmp_path / "log.txt"
    call(relay, 15, "enable_tool_groups", groups=["drawings"])
    out.reply(15)
    monkeypatch.setenv("FAKE_LOG", str(log))
    call(relay, 16, RELOAD_TOOL)
    out.reply(16)
    wait_until(lambda: log.exists() and "tools/call" in log.read_text())
    assert log.read_text().split()[1::2][:3] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    # The replay's answer stays inside the relay.
    assert not any(str(m.get("id")).startswith("relay-") for m in out.messages)


def test_server_requests_are_passed_both_ways(started):
    relay, out = started
    call(relay, 17, "ask")
    out.wait_for(lambda m: m.get("method") == "roots/list")
    send(relay, {"jsonrpc": "2.0", "id": "srv-1", "result": {"roots": []}})
    assert "result" in out.reply(17)


def test_lines_that_are_not_json_objects_pass_through(started):
    relay, out = started
    relay.from_client(b"[]\n")
    relay.from_client(b"not json\n")
    call(relay, 18, "echo")
    assert "result" in out.reply(18)


def test_file_changes_reload_after_they_settle(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_TOOL", "tool_a")
    (tmp_path / "a.py").write_text("x = 1")
    out = Collector()
    relay = Relay(
        [sys.executable, FAKE],
        out,
        watch=tmp_path,
        poll_seconds=0.1,
        settle_seconds=0.3,
    )
    relay.start()
    try:
        send(relay, INIT)
        out.reply(0)
        time.sleep(0.3)
        (tmp_path / "a.py").write_text("x = 22")
        wait_until(lambda: relay.reloads == 1)
        out.wait_for(lambda m: m.get("method") == "notifications/prompts/list_changed")
    finally:
        relay.close()


def test_reload_before_the_client_handshake_sends_no_notice(monkeypatch):
    monkeypatch.setenv("FAKE_TOOL", "tool_a")
    out = Collector()
    relay = Relay([sys.executable, FAKE], out)
    relay.start()
    try:
        relay.request_reload("early")
        wait_until(lambda: relay.reloads == 1)
        assert out.messages == []
    finally:
        relay.close()


def test_run_relays_stdin_until_it_closes(monkeypatch):
    monkeypatch.setenv("FAKE_TOOL", "tool_a")
    out = Collector()
    relay = Relay([sys.executable, FAKE], out)
    lines = [
        json.dumps(INIT),
        "",
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ]
    stdin = io.BytesIO(("\n".join(lines) + "\n").encode())

    def slow_readline(_reader=stdin.readline):
        line = _reader()
        if not line:
            time.sleep(1.0)  # let the answers arrive before EOF stops the server
        return line

    monkeypatch.setattr(stdin, "readline", slow_readline)
    relay.run(stdin)
    assert tool_names(out, 1) == ["tool_a", RELOAD_TOOL]


def test_snapshot_skips_caches(tmp_path):
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "a.cpython.pyc").write_bytes(b"x")
    (tmp_path / "b.pyc").write_bytes(b"x")
    (tmp_path / "c.py").write_text("y")
    (tmp_path / "sub").mkdir()
    assert list(snapshot(tmp_path)) == [str(tmp_path / "c.py")]


def test_snapshot_skips_files_that_vanish(tmp_path, monkeypatch):
    (tmp_path / "gone.py").write_text("y")
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self.name == "gone.py":
            raise FileNotFoundError(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    assert snapshot(tmp_path) == {}


def test_child_send_after_stop_and_kill_of_a_stuck_server():
    child = relay_module._Child(
        [sys.executable, "-c", "import time; time.sleep(60)"], 1
    )
    child.stop(timeout=0.3)  # it ignores EOF, so it is killed
    assert child.process.returncode is not None
    assert child.send(b"{}") is False


def test_a_reload_after_close_discards_the_new_server(monkeypatch):
    monkeypatch.setenv("FAKE_TOOL", "tool_a")
    relay = Relay([sys.executable, FAKE], Collector(), stop_timeout=5.0)
    relay.close()
    assert relay._reload("late") is False


def test_main_reads_real_stdin(monkeypatch):
    out = Collector()
    monkeypatch.setenv("SOLIDWORKS_MCP_HOT_RELOAD_WATCH", "0")
    monkeypatch.setattr(sys, "stdin", type("In", (), {"buffer": io.BytesIO(b"")})())
    monkeypatch.setattr(sys, "stdout", type("Out", (), {"buffer": out})())
    monkeypatch.setattr(
        relay_module, "server_command", lambda args: [sys.executable, FAKE]
    )
    relay_module.main([])
    assert out.messages == []


def test_add_reload_tool_leaves_other_replies():
    line = b'{"result": {"tools": [], "nextCursor": "2"}}\n'
    assert relay_module._add_reload_tool(json.loads(line), line) == line
    line = b'{"error": {}}\n'
    assert relay_module._add_reload_tool(json.loads(line), line) == line
    tools = {"result": {"tools": [{"name": RELOAD_TOOL}]}}
    line = json.dumps(tools).encode()
    assert relay_module._add_reload_tool(tools, line) == line


def test_main_builds_the_server_command(monkeypatch):
    seen: list[Relay] = []
    monkeypatch.setenv("SOLIDWORKS_MCP_HOT_RELOAD_WATCH", "0")
    relay_module.main(["--year", "2026"], run=seen.append)
    assert seen[0].command[1:] == ["-m", "solidworks_mcp.server", "--year", "2026"]
    assert seen[0].watch is None
    monkeypatch.delenv("SOLIDWORKS_MCP_HOT_RELOAD_WATCH")
    relay_module.main([], run=seen.append)
    assert seen[1].watch == Path(relay_module.__file__).resolve().parent
