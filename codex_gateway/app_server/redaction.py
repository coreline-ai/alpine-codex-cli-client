"""Bounded diagnostics that never retain credential-shaped stderr fragments."""

import re


_AUTHORIZATION = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)\S+")
_SECRET_FIELD = re.compile(
    r"(?i)((?:access|refresh|id)[_-]?token|password|secret)\s*[:=]\s*['\"]?[^\s,'\"]+"
)
_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b")


def redact_text(value: str) -> str:
    """Returns stable diagnostics without preserving common credential representations."""
    value = _AUTHORIZATION.sub(r"\1[REDACTED]", value)
    value = _SECRET_FIELD.sub(lambda match: match.group(1) + "=[REDACTED]", value)
    return _LONG_TOKEN.sub("[REDACTED]", value)


class RedactingRingBuffer:
    """Stores at most ``max_bytes`` of already-redacted UTF-8 diagnostic text."""

    def __init__(self, max_bytes: int = 64 * 1024) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._max_bytes = max_bytes
        self._buffer = bytearray()
        self._line_tail = ""

    def append(self, chunk: bytes) -> None:
        # A credential can straddle a pipe read boundary. Keep only a short incomplete line until
        # its delimiter arrives, then redact it as one unit before it reaches the ring buffer.
        self._line_tail += chunk.decode("utf-8", errors="replace")
        lines = self._line_tail.splitlines(keepends=True)
        self._line_tail = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                self._append_redacted(line)
            else:
                self._line_tail = line
        if len(self._line_tail.encode("utf-8")) > 8192:
            self._append_redacted(self._line_tail)
            self._line_tail = ""

    def _append_redacted(self, value: str) -> None:
        self._buffer.extend(redact_text(value).encode("utf-8"))
        if len(self._buffer) > self._max_bytes:
            del self._buffer[: len(self._buffer) - self._max_bytes]

    def snapshot(self) -> str:
        return self._buffer.decode("utf-8", errors="replace") + redact_text(self._line_tail)

    @property
    def size(self) -> int:
        return len(self._buffer)
