"""Credential-free JSONL fixture for app-server and loopback gateway tests."""

import json
import sys
import threading
import time


MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"
OUTPUT_LOCK = threading.Lock()
STATE = {
    "authenticated": MODE in {
        "gateway_auth",
        "gateway_chat",
        "gateway_zero_delta",
        "gateway_one_delta",
        "gateway_silent",
        "gateway_malformed",
        "gateway_models_malformed",
        "gateway_models_empty",
        "gateway_resume_failure",
        "gateway_crash",
    },
    "thread_start": 0,
    "thread_resume": 0,
    "turn_interrupt": 0,
    "thread_id": "thread-gateway-1",
    "turn_id": "turn-gateway-1",
}


def emit(value, split=False):
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"
    with OUTPUT_LOCK:
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


def notification(method, params):
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize_result():
    return {
        "codexHome": "/tmp/fake-codex-home",
        "platformFamily": "unix",
        "platformOs": "linux",
        "userAgent": "fake-codex",
    }


def later(seconds, value):
    timer = threading.Timer(seconds, lambda: emit(value))
    timer.daemon = True
    timer.start()


def complete_login_later():
    def complete():
        STATE["authenticated"] = True
        emit(notification("account/login/completed", {"loginId": "login-gateway-1", "success": True}))

    timer = threading.Timer(0.05, complete)
    timer.daemon = True
    timer.start()


def gateway_account():
    authenticated = STATE["authenticated"]
    return {
        "requiresOpenaiAuth": not authenticated,
        "account": {"type": "chatgpt"} if authenticated else None,
    }


def gateway_models(cursor):
    if MODE == "gateway_models_malformed":
        return {"data": "invalid"}
    if MODE == "gateway_models_empty":
        return {"data": [], "nextCursor": None}
    if cursor is None:
        return {
            "data": [
                {"id": "catalog-alpha", "model": "model-alpha", "displayName": "Alpha", "isDefault": True, "hidden": False},
                {"id": "catalog-hidden", "model": "model-hidden", "displayName": "Hidden", "isDefault": False, "hidden": True},
            ],
            "nextCursor": "page-2",
        }
    if cursor == "page-2":
        return {
            "data": [
                {"id": "catalog-alpha-again", "model": "model-alpha", "displayName": "Alpha duplicate", "isDefault": False, "hidden": False},
                {"id": "catalog-beta", "model": "model-beta", "displayName": "Beta", "isDefault": False, "hidden": False},
            ],
            "nextCursor": None,
        }
    return {"data": [], "nextCursor": None}


def turn_completed(status="completed"):
    return notification(
        "turn/completed",
        {
            "threadId": STATE["thread_id"],
            "turn": {"id": STATE["turn_id"], "status": status, "items": []},
        },
    )


def schedule_gateway_turn():
    if MODE == "gateway_zero_delta":
        later(0.03, turn_completed())
    elif MODE == "gateway_one_delta":
        later(
            0.03,
            notification(
                "item/agentMessage/delta",
                {
                    "threadId": STATE["thread_id"],
                    "turnId": STATE["turn_id"],
                    "itemId": "item-1",
                    "delta": "single",
                },
            ),
        )
        later(0.05, turn_completed())
    elif MODE == "gateway_malformed":
        later(
            0.03,
            notification(
                "item/agentMessage/delta",
                {"threadId": STATE["thread_id"], "turnId": STATE["turn_id"], "itemId": "item-1"},
            ),
        )
    elif MODE == "gateway_chat":
        later(
            0.03,
            notification(
                "item/agentMessage/delta",
                {
                    "threadId": STATE["thread_id"],
                    "turnId": STATE["turn_id"],
                    "itemId": "item-1",
                    "delta": "first ",
                },
            ),
        )
        later(
            0.05,
            notification(
                "item/agentMessage/delta",
                {
                    "threadId": STATE["thread_id"],
                    "turnId": STATE["turn_id"],
                    "itemId": "item-1",
                    "delta": "second",
                },
            ),
        )
        later(0.07, turn_completed())


def handle(message):
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params", {})
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
    if method == "initialized" and request_id is None:
        return True
    if method == "account/read":
        emit(response(request_id, gateway_account()))
        return True
    if method == "account/login/start":
        emit(response(request_id, {"loginId": "login-gateway-1", "verificationUrl": "https://example.invalid/device", "userCode": "CODE-ONLY-TEST"}))
        if MODE == "gateway_login_complete":
            complete_login_later()
        return True
    if method == "account/login/cancel":
        emit(response(request_id, {}))
        return True
    if method == "account/logout":
        STATE["authenticated"] = False
        emit(response(request_id, {}))
        return True
    if method == "model/list":
        emit(response(request_id, gateway_models(params.get("cursor"))))
        return True
    if method == "thread/start":
        STATE["thread_start"] += 1
        emit(response(request_id, {"thread": {"id": STATE["thread_id"]}}))
        return True
    if method == "thread/resume":
        STATE["thread_resume"] += 1
        if MODE == "gateway_resume_failure":
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": "resume_rejected"}})
            return True
        emit(response(request_id, {"thread": {"id": params.get("threadId")}}))
        return True
    if method == "turn/start":
        if not MODE.startswith("gateway_"):
            return False
        emit(response(request_id, {"turn": {"id": STATE["turn_id"], "status": "inProgress", "items": []}}))
        if MODE == "gateway_crash":
            time.sleep(0.05)
            return False
        schedule_gateway_turn()
        return True
    if method == "turn/interrupt":
        STATE["turn_interrupt"] += 1
        emit(response(request_id, {}))
        later(0.01, turn_completed("interrupted"))
        return True
    if method == "test/query":
        emit(response(request_id, dict(STATE)))
        return True
    if method == "test/delay":
        time.sleep(1.0)
        emit(response(request_id, {}))
        return True
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
