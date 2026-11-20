"""A stand-in MCP server for the relay tests (run as a script).

Environment:
    FAKE_TOOL: the one tool name tools/list returns (default "tool_a").
    FAKE_FAIL: "1" exits at once, so the relay sees a server that can't start.
    FAKE_MUTE: "1" never answers initialize.
    FAKE_LOG: a file each received method is appended to.

Calls: "echo" answers its arguments, "slow" sleeps for arguments.seconds,
"crash" exits without answering, "ask" sends the client a request first.
"""

import json
import os
import sys
import time

if os.environ.get("FAKE_FAIL") == "1":
    sys.exit(3)

TOOL = os.environ.get("FAKE_TOOL", "tool_a")
LOG = os.environ.get("FAKE_LOG")


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    try:
        message = json.loads(raw)
    except ValueError:
        continue
    if not isinstance(message, dict):
        continue
    method = message.get("method")
    if LOG:
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(f"{os.getpid()} {method or 'response'}\n")
    if method is None or "id" not in message:
        continue
    request_id = message["id"]
    if method == "initialize" and os.environ.get("FAKE_MUTE") == "1":
        continue
    if method == "initialize":
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {}})
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"serverInfo": {"name": TOOL}},
            }
        )
    elif method == "tools/list":
        send(
            {"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{"name": TOOL}]}}
        )
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "crash":
            sys.exit(5)
        if name == "slow":
            time.sleep(arguments.get("seconds", 1))
        if name == "ask":
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "roots/list"})
        text = json.dumps({"tool": TOOL, "name": name, "arguments": arguments})
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        )
