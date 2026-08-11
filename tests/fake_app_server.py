"""Credential-free JSONL fixture that models only supervisor edge cases."""

import json
import os
import sys
import time


MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


def emit(value, split=False):
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
    if split:
        midpoint = max(1, len(payload) // 2)
        sys.stdout.buffer.write(payload[:midpoint])
        sys.stdout.buffer.flush()
        sys.stdout.buffer.write(payload[midpoint:])
    else:
        sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def initialize_result():
    return {
        "codexHome": "/tmp/fake-codex-home",
        "platformFamily": "unix",
        "platformOs": "linux",
        "userAgent": "fake-codex",
    }


def handle(message):
    request_id = message.get("id")
    method = message.get("method")
    if MODE == "invalid_utf8":
        sys.stdout.buffer.write(b'{"jsonrpc":"2.0","id":1,"result":"\xff"}\n')
        sys.stdout.buffer.flush()
        return False
    if MODE == "unknown_id":
        emit(response(999, {}))
        return False
    if MODE == "duplicate_id":
        emit(response(request_id, initialize_result()))
        emit(response(request_id, initialize_result()))
        return False
    if method == "initialize":
        if MODE == "initialize_error":
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": "init_rejected"}})
        elif MODE == "initialize_timeout":
            time.sleep(2.0)
        else:
            emit(response(request_id, initialize_result()), split=MODE == "split")
        return True
    if method == "account/read":
        emit(response(request_id, {"requiresOpenaiAuth": True, "account": None}))
        return True
    if method == "test/delay":
        time.sleep(1.0)
        emit(response(request_id, {}))
        return True
    if method == "turn/start":
        return False
    if method == "test/stderr_flood":
        header = "Auth" + "orization: " + "Bearer"
        sys.stderr.write((header + " synthetic-secret-value\n") * 4096)
        sys.stderr.flush()
        emit(response(request_id, {}))
        return True
    emit(response(request_id, {"echo": method}))
    return True


def main():
    buffered = []
    for raw in sys.stdin.buffer:
        message = json.loads(raw.decode("utf-8"))
        if MODE == "out_of_order" and message.get("method") == "test/out_of_order":
            buffered.append(message)
            if len(buffered) < 2:
                continue
            emit(response(buffered[1]["id"], {"position": 2}))
            emit(response(buffered[0]["id"], {"position": 1}))
            buffered = []
            continue
        if not handle(message):
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
