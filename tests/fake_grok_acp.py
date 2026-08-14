"""Credential-free official-Grok-compatible JSONL fixture for supervisor tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time


MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
LOCK = threading.Lock()
INITIALIZED_AT = 0.0
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "grok-acp-v1.0.0.json").read_text())


def encode(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def emit(value, *, split=False):
    payload = encode(value)
    with LOCK:
        if split:
            one = max(1, len(payload) // 3)
            two = max(one + 1, 2 * len(payload) // 3)
            for part in (payload[:one], payload[one:two], payload[two:]):
                sys.stdout.buffer.write(part)
                sys.stdout.buffer.flush()
        else:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()


def response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def notification(method, params):
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_result():
    return FIXTURE["initializeResult"]


def emit_initialize(request_id):
    global INITIALIZED_AT
    if MODE == "initialize_timeout":
        time.sleep(2.0)
        return
    if MODE == "invalid_utf8":
        sys.stdout.buffer.write(b'{"jsonrpc":"2.0","id":1,"result":{"bad":"\xff"}}\n')
        sys.stdout.buffer.flush()
        return
    if MODE == "malformed_json":
        sys.stdout.buffer.write(b'{"jsonrpc":"2.0","id":1,"result":}\n')
        sys.stdout.buffer.flush()
        return
    if MODE == "oversized":
        sys.stdout.buffer.write(b"{" + b"x" * (1024 * 1024 + 1) + b"}\n")
        sys.stdout.buffer.flush()
        return
    if MODE == "unknown_id":
        emit(response(999, {}))
        return
    if MODE == "duplicate_id":
        emit(response(request_id, initialize_result()))
        emit(response(request_id, initialize_result()))
        return
    if MODE == "reverse_request":
        emit({"jsonrpc": "2.0", "id": 77, "method": "fs/read_text_file", "params": {}})
        return
    if MODE == "combined":
        first = notification("session/update", {"sessionId": "fixture", "update": {}})
        with LOCK:
            sys.stdout.buffer.write(encode(first) + encode(response(request_id, initialize_result())))
            sys.stdout.buffer.flush()
        return
    if MODE == "stderr_privacy":
        for chunk in (
            b"https://sso.example.invalid/device?co",
            b"de=private-value\nuser=person@exam",
            b"ple.invalid\ntoken=synthetic-secret\nprompt=do-not-retain\n",
        ):
            os.write(sys.stderr.fileno(), chunk)
    emit(response(request_id, initialize_result()), split=MODE == "split")
    INITIALIZED_AT = time.monotonic()


def delayed_emit(seconds, value):
    timer = threading.Timer(seconds, lambda: emit(value))
    timer.daemon = True
    timer.start()


def handle(message):
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})
    if method == "initialize":
        emit_initialize(request_id)
        return MODE not in {
            "invalid_utf8",
            "malformed_json",
            "oversized",
            "unknown_id",
            "duplicate_id",
            "reverse_request",
        }
    if method == "_x.ai/models/list":
        if MODE in {"late_response", "hold"}:
            if MODE == "late_response":
                delayed_emit(0.5, response(request_id, {"models": []}))
            else:
                time.sleep(5.0)
            return True
        if MODE == "crash":
            return False
        emit(response(request_id, {"models": []}))
        return True
    if method == "session/prompt":
        terminal = notification(
            "_x.ai/session/prompt_complete",
            {
                "sessionId": params.get("sessionId", "session-1"),
                "promptId": "prompt-1",
                "stopReason": "end_turn",
                "agentResult": None,
            },
        )
        emit(notification("session/update", {"sessionId": "session-1", "update": {}}))
        emit(terminal)
        if MODE == "duplicate_terminal":
            emit(terminal)
        emit(response(request_id, {"stopReason": "end_turn"}))
        return True
    if method == "session/cancel" and request_id is None:
        return True
    if method == "authenticate":
        meta = params.get("_meta", {})
        if not isinstance(meta.get("request_seq"), int) or "use_oauth" in meta:
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602}})
            return True
        emit(response(request_id, {}))
        return True
    if method == "_x.ai/auth/get_url":
        emit(response(request_id, {"auth_url": None, "mode": None}))
        return True
    if method == "_x.ai/auth/info":
        if MODE == "reject_early_auth" and time.monotonic() - INITIALIZED_AT < 0.2:
            return False
        emit(response(request_id, {"methodId": None}))
        return True
    if method == "_x.ai/auth/cancel":
        emit(response(request_id, {"cancelled": True}))
        return True
    if method == "_x.ai/auth/logout":
        emit(response(request_id, {"ok": True}))
        return True
    if method == "session/new":
        emit(response(request_id, {"sessionId": "session-1"}))
        return True
    if method in {"session/load", "session/resume"}:
        emit(response(request_id, {"models": {}}))
        return True
    if method == "session/set_model":
        emit(response(request_id, {}))
        return True
    if method == "session/close":
        emit(response(request_id, {}))
        return True
    emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601}})
    return True


def main():
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw.decode("utf-8"))
        except Exception:
            return 2
        if not handle(message):
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
