"""Strict, bounded JSON object framing for the Codex app-server stdio transport."""

import json
from typing import Any, Dict, List


class JsonlProtocolError(Exception):
    """The peer produced framing or JSON that cannot be safely multiplexed."""


class JsonlDecoder:
    """Incrementally decodes UTF-8 JSON objects separated by LF or CRLF.

    Bytes are retained only until one complete line is available. This prevents an unbounded peer
    from growing memory before a JSON parser ever gets a chance to reject the input.
    """

    def __init__(self, max_line_bytes: int = 1024 * 1024) -> None:
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be positive")
        self._max_line_bytes = max_line_bytes
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> List[Dict[str, Any]]:
        if not isinstance(chunk, bytes):
            raise TypeError("JSONL input must be bytes")
        if not chunk:
            return []
        self._buffer.extend(chunk)
        if len(self._buffer) > self._max_line_bytes and b"\n" not in self._buffer:
            raise JsonlProtocolError("jsonl_line_too_large")

        messages: List[Dict[str, Any]] = []
        while True:
            try:
                newline = self._buffer.index(0x0A)
            except ValueError:
                break
            if newline > self._max_line_bytes:
                raise JsonlProtocolError("jsonl_line_too_large")
            line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            if not line:
                raise JsonlProtocolError("jsonl_empty_line")
            try:
                value = json.loads(line.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise JsonlProtocolError("jsonl_invalid_json") from error
            if not isinstance(value, dict):
                raise JsonlProtocolError("jsonl_object_required")
            messages.append(value)

        if len(self._buffer) > self._max_line_bytes:
            raise JsonlProtocolError("jsonl_line_too_large")
        return messages

    def finish(self) -> None:
        """Reject an EOF that splits a JSONL record instead of silently accepting it."""
        if self._buffer:
            raise JsonlProtocolError("jsonl_unterminated_eof")
