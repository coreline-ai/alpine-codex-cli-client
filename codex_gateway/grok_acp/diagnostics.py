"""Content-free, bounded diagnostics for the Grok ACP stderr stream."""

from __future__ import annotations

import threading


class DiscardingStderrDiagnostics:
    """Discard every stderr byte and expose only bounded counters.

    OAuth URLs, account fields, prompts, and credentials can cross arbitrary pipe chunk
    boundaries. Retaining no text is stronger and simpler than trying to enumerate every future
    sensitive representation emitted by the official CLI.
    """

    def __init__(self, max_observed_bytes: int = 64 * 1024) -> None:
        if max_observed_bytes <= 0:
            raise ValueError("max_observed_bytes must be positive")
        self._limit = max_observed_bytes
        self._observed = 0
        self._truncated = False
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        if not isinstance(chunk, bytes):
            raise TypeError("stderr chunk must be bytes")
        with self._lock:
            remaining = self._limit - self._observed
            accepted = min(remaining, len(chunk))
            self._observed += accepted
            self._truncated = self._truncated or accepted != len(chunk)

    def snapshot(self) -> str:
        with self._lock:
            if self._observed == 0:
                return ""
            suffix = ":truncated" if self._truncated else ""
            return f"grok_stderr_redacted:{self._observed}{suffix}"

    @property
    def retained_content_bytes(self) -> int:
        return 0
