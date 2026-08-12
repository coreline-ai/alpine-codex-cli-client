"""Credential-free, redacted Grok version and ACP initialize smoke probe."""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from typing import Any

from .policy import GrokLaunchPolicy, LOCKED_VERSION_OUTPUT, child_umask


MAX_LINE_BYTES = 1024 * 1024
INITIALIZE_TIMEOUT_SECONDS = 30.0
STOP_TIMEOUT_SECONDS = 2.0
READY_MARKER = "GROK_SMOKE_READY"
FAILED_MARKER = "GROK_SMOKE_FAILED"


def _discard(stream: Any) -> None:
    try:
        while stream.read(64 * 1024):
            pass
    except Exception:
        pass


def _read_lines(stream: Any, output: "queue.Queue[bytes | BaseException]") -> None:
    try:
        while True:
            line = stream.readline(MAX_LINE_BYTES + 1)
            if not line:
                output.put(EOFError())
                return
            output.put(line)
    except BaseException as error:
        output.put(error)


def _write_request(
    process: subprocess.Popen[bytes],
    request_id: int,
    method: str,
    params: dict[str, Any],
) -> None:
    assert process.stdin is not None
    message = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }
    encoded = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    process.stdin.write(encoded)
    process.stdin.flush()


def _write_initialize(process: subprocess.Popen[bytes]) -> None:
    _write_request(
        process,
        1,
        "initialize",
        {
            "protocolVersion": "1",
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "_meta": {
                "clientType": "alpine-android",
                "clientVersion": "0.1.0-debug",
                "startupHints": {
                    "nonInteractive": True,
                    "skipGitStatus": True,
                    "skipProjectLayout": True,
                },
            },
        },
    )


def _await_result(
    process: subprocess.Popen[bytes],
    lines: "queue.Queue[bytes | BaseException]",
    request_id: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + INITIALIZE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None and lines.empty():
            raise RuntimeError
        try:
            item = lines.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if isinstance(item, BaseException):
            raise RuntimeError from None
        if len(item) > MAX_LINE_BYTES or not item.endswith(b"\n"):
            raise RuntimeError
        try:
            value = json.loads(item.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError from None
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0":
            raise RuntimeError
        if value.get("id") != request_id:
            continue
        if "error" in value or not isinstance(value.get("result"), dict):
            raise RuntimeError
        return value["result"]
    raise RuntimeError


def _validate_initialize(result: dict[str, Any]) -> None:
    if result.get("protocolVersion") not in ("1", 1):
        raise RuntimeError
    methods = result.get("authMethods")
    if not isinstance(methods, list):
        raise RuntimeError
    method_ids = {
        item.get("id")
        for item in methods
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if method_ids != {"grok.com"}:
        raise RuntimeError


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=STOP_TIMEOUT_SECONDS)


def run() -> int:
    process: subprocess.Popen[bytes] | None = None
    failure_stage = "POLICY"
    try:
        policy = GrokLaunchPolicy.production()
        policy.validate()
        policy.permission_probe()
        failure_stage = "VERSION"
        version = subprocess.run(
            [policy.executable.as_posix(), "--version"],
            cwd=policy.work,
            env=policy.environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            preexec_fn=child_umask,
        )
        if version.returncode != 0 or version.stdout.decode("utf-8", "strict").strip() != LOCKED_VERSION_OUTPUT:
            raise RuntimeError
        failure_stage = "PROCESS"
        process = subprocess.Popen(
            policy.command(),
            cwd=policy.work,
            env=policy.environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=child_umask,
        )
        assert process.stderr is not None
        threading.Thread(target=_discard, args=(process.stderr,), daemon=True).start()
        assert process.stdout is not None
        lines: "queue.Queue[bytes | BaseException]" = queue.Queue()
        threading.Thread(target=_read_lines, args=(process.stdout, lines), daemon=True).start()
        failure_stage = "INITIALIZE"
        _write_initialize(process)
        _validate_initialize(_await_result(process, lines, 1))
        print(READY_MARKER)
        return 0
    except Exception:
        print(f"{FAILED_MARKER}_{failure_stage}")
        return 1
    finally:
        if process is not None:
            try:
                _stop(process)
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(run())
