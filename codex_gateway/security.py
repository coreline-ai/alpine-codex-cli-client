"""Session-capability loading and canonical-v1 request authentication."""

from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import hmac
import math
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable, Iterable, Mapping, Tuple


AUTH_VERSION = "1"
SECRET_BYTES = 32
NONCE_BYTES = 16
TIMESTAMP_WINDOW_SECONDS = 30
NONCE_TTL_SECONDS = 120
# The Android client is single-flight. Four authenticated requests/second already covers health,
# model/login polling, turn start, and Stop with ample headroom. A 2x burst allowance is retained
# in five-second buckets. Partitioning prevents one valid burst from denying every later request
# for the full replay TTL while never evicting a still-live nonce.
MAX_AUTHENTICATED_REQUESTS_PER_SECOND = 4
NONCE_BUCKET_SECONDS = 5
NONCE_BURST_MULTIPLIER = 2
MAX_NONCES_PER_BUCKET = (
    MAX_AUTHENTICATED_REQUESTS_PER_SECOND
    * NONCE_BUCKET_SECONDS
    * NONCE_BURST_MULTIPLIER
)
# Compatibility/exported total upper bound. One extra bucket covers a boundary overlap caused by
# conservatively retaining a complete bucket for the entire TTL.
MAX_NONCE_BUCKETS = math.ceil(NONCE_TTL_SECONDS / NONCE_BUCKET_SECONDS) + 2
MAX_NONCES = MAX_NONCES_PER_BUCKET * MAX_NONCE_BUCKETS
MAX_SECURITY_COUNTER = (1 << 31) - 1
AUTH_HEADERS = {
    "x-alpine-auth-version",
    "x-alpine-timestamp",
    "x-alpine-nonce",
    "x-alpine-content-sha256",
    "x-alpine-signature",
}


class BoundedSecurityCounters:
    """Thread-safe, content-free counters with a fixed schema and saturating values."""

    def __init__(self, names: Iterable[str]) -> None:
        fixed = tuple(names)
        if not fixed or len(set(fixed)) != len(fixed) or any(not name for name in fixed):
            raise ValueError("invalid security counter schema")
        self._values = {name: 0 for name in fixed}
        self._lock = threading.Lock()

    def increment(self, name: str) -> None:
        with self._lock:
            if name not in self._values:
                raise ValueError("unknown security counter")
            self._values[name] = min(MAX_SECURITY_COUNTER, self._values[name] + 1)

    def snapshot(self) -> Mapping[str, int]:
        with self._lock:
            return dict(self._values)


class GatewayAuthenticationError(PermissionError):
    """Stable failure with no authentication detail."""

    def __init__(self) -> None:
        super().__init__("gateway_auth_failed")


def _decode_base64url(value: str, expected_length: int) -> bytes:
    if not value or "=" in value:
        raise GatewayAuthenticationError()
    try:
        decoded = base64.b64decode(
            value + "=" * ((4 - len(value) % 4) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, base64.binascii.Error) as error:
        raise GatewayAuthenticationError() from error
    if len(decoded) != expected_length:
        raise GatewayAuthenticationError()
    return decoded


def _private_directory(path: Path) -> None:
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o700:
        raise GatewayAuthenticationError()
    if value.st_uid != os.getuid() or path.is_symlink():
        raise GatewayAuthenticationError()


def load_one_time_capability(path_value: str) -> bytes:
    """Read one exact owner-only regular file, then unlink it before serving."""

    try:
        path = Path(path_value)
        if not path.is_absolute() or path.name != "gateway-capability.v1":
            raise GatewayAuthenticationError()
        _private_directory(path.parent)
        value = path.lstat()
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_IMODE(value.st_mode) != 0o600
            or value.st_uid != os.getuid()
            or value.st_size != SECRET_BYTES
            or path.is_symlink()
        ):
            raise GatewayAuthenticationError()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if opened.st_ino != value.st_ino or opened.st_dev != value.st_dev:
                raise GatewayAuthenticationError()
            secret = b""
            while len(secret) <= SECRET_BYTES:
                chunk = os.read(descriptor, SECRET_BYTES + 1 - len(secret))
                if not chunk:
                    break
                secret += chunk
        finally:
            os.close(descriptor)
        if len(secret) != SECRET_BYTES:
            raise GatewayAuthenticationError()
        path.unlink()
        return secret
    except GatewayAuthenticationError:
        raise
    except OSError as error:
        raise GatewayAuthenticationError() from error


class SessionCapabilityVerifier:
    def __init__(
        self,
        secret: bytes,
        *,
        now: Callable[[], float] = time.time,
        nonce_now: Callable[[], float] = time.monotonic,
        timestamp_window_seconds: int = TIMESTAMP_WINDOW_SECONDS,
        nonce_ttl_seconds: int = NONCE_TTL_SECONDS,
        nonce_bucket_seconds: int = NONCE_BUCKET_SECONDS,
        max_nonces_per_bucket: int = MAX_NONCES_PER_BUCKET,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) != SECRET_BYTES:
            raise ValueError("invalid session capability")
        if (
            timestamp_window_seconds <= 0
            or nonce_ttl_seconds <= 0
            or nonce_bucket_seconds <= 0
            or max_nonces_per_bucket <= 0
        ):
            raise ValueError("invalid verifier bounds")
        self._secret = secret
        self._now = now
        self._nonce_now = nonce_now
        self._window = timestamp_window_seconds
        self._ttl = nonce_ttl_seconds
        self._bucket_seconds = nonce_bucket_seconds
        self._maximum_per_bucket = max_nonces_per_bucket
        self._maximum_buckets = math.ceil(nonce_ttl_seconds / nonce_bucket_seconds) + 2
        self._buckets: "OrderedDict[int, set[bytes]]" = OrderedDict()
        self._nonces: set[bytes] = set()
        self._lock = threading.Lock()
        self._telemetry = BoundedSecurityCounters(
            ("accepted", "auth_rejected", "replay_rejected", "capacity_rejected")
        )

    def authorize(
        self,
        method: str,
        exact_path: str,
        headers: Mapping[str, Tuple[str, ...]],
        body: bytes,
    ) -> None:
        try:
            if method not in {"GET", "POST"}:
                raise GatewayAuthenticationError()
            if not exact_path.startswith("/") or "?" in exact_path or "#" in exact_path:
                raise GatewayAuthenticationError()
            values = {name: self._one(headers, name) for name in AUTH_HEADERS}
            if values["x-alpine-auth-version"] != AUTH_VERSION:
                raise GatewayAuthenticationError()
            timestamp_text = values["x-alpine-timestamp"]
            if not timestamp_text.isdigit() or len(timestamp_text) > 12:
                raise GatewayAuthenticationError()
            timestamp = int(timestamp_text)
            now = self._now()
            if abs(now - timestamp) > self._window:
                raise GatewayAuthenticationError()
            nonce = _decode_base64url(values["x-alpine-nonce"], NONCE_BYTES)
            body_hash = hashlib.sha256(body).hexdigest()
            supplied_hash = values["x-alpine-content-sha256"]
            if len(supplied_hash) != 64 or not hmac.compare_digest(body_hash, supplied_hash):
                raise GatewayAuthenticationError()
            signature = _decode_base64url(values["x-alpine-signature"], 32)
            canonical = (
                f"v1\n{method}\n{exact_path}\n{timestamp_text}\n"
                f"{values['x-alpine-nonce']}\n{body_hash}"
            ).encode("utf-8")
            expected = hmac.new(self._secret, canonical, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, signature):
                raise GatewayAuthenticationError()
            self._remember_once(nonce, self._nonce_now())
            self._telemetry.increment("accepted")
        except GatewayAuthenticationError:
            self._telemetry.increment("auth_rejected")
            raise
        except Exception as error:
            self._telemetry.increment("auth_rejected")
            raise GatewayAuthenticationError() from error

    @staticmethod
    def _one(headers: Mapping[str, Tuple[str, ...]], name: str) -> str:
        values = headers.get(name)
        if not isinstance(values, tuple) or len(values) != 1 or not isinstance(values[0], str):
            raise GatewayAuthenticationError()
        return values[0]

    def _remember_once(self, nonce: bytes, now: float) -> None:
        with self._lock:
            self._expire_buckets_locked(now)
            if nonce in self._nonces:
                self._telemetry.increment("replay_rejected")
                raise GatewayAuthenticationError()
            bucket_id = int(now // self._bucket_seconds)
            bucket = self._buckets.get(bucket_id)
            if bucket is None:
                # A monotonic clock and normal pruning keep this below the derived bound. Refuse
                # rather than evict a live bucket if an injected/broken clock violates it.
                if len(self._buckets) >= self._maximum_buckets:
                    self._telemetry.increment("capacity_rejected")
                    raise GatewayAuthenticationError()
                bucket = set()
                self._buckets[bucket_id] = bucket
            if len(bucket) >= self._maximum_per_bucket:
                self._telemetry.increment("capacity_rejected")
                raise GatewayAuthenticationError()
            bucket.add(nonce)
            self._nonces.add(nonce)

    def _expire_buckets_locked(self, now: float) -> None:
        while self._buckets:
            bucket_id, values = next(iter(self._buckets.items()))
            bucket_end = (bucket_id + 1) * self._bucket_seconds
            if bucket_end + self._ttl >= now:
                break
            self._buckets.popitem(last=False)
            self._nonces.difference_update(values)

    def nonce_count(self) -> int:
        with self._lock:
            return len(self._nonces)

    def nonce_capacity(self) -> int:
        return self._maximum_per_bucket * self._maximum_buckets

    def telemetry_snapshot(self) -> Mapping[str, int]:
        return self._telemetry.snapshot()
