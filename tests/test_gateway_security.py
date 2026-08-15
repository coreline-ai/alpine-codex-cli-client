from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path
import tempfile
import threading
import unittest

from codex_gateway.security import (
    BoundedSecurityCounters,
    GatewayAuthenticationError,
    MAX_SECURITY_COUNTER,
    SessionCapabilityVerifier,
    load_one_time_capability,
)


SECRET = bytes(range(32))
NONCE = bytes(range(16, 32))
NOW = 1_700_000_000


def headers(method="POST", path="/internal/agents/select", body=b"{}", timestamp=NOW, nonce=NONCE):
    encoded_nonce = base64.urlsafe_b64encode(nonce).rstrip(b"=").decode("ascii")
    body_hash = hashlib.sha256(body).hexdigest()
    canonical = f"v1\n{method}\n{path}\n{timestamp}\n{encoded_nonce}\n{body_hash}".encode()
    signature = base64.urlsafe_b64encode(
        hmac.new(SECRET, canonical, hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    return {
        "x-alpine-auth-version": ("1",),
        "x-alpine-timestamp": (str(timestamp),),
        "x-alpine-nonce": (encoded_nonce,),
        "x-alpine-content-sha256": (body_hash,),
        "x-alpine-signature": (signature,),
    }


class SessionCapabilityVerifierTest(unittest.TestCase):
    def test_golden_request_and_replay_boundary(self):
        verifier = SessionCapabilityVerifier(SECRET, now=lambda: NOW)
        value = headers()
        verifier.authorize("POST", "/internal/agents/select", value, b"{}")
        self.assertEqual(1, verifier.nonce_count())
        with self.assertRaises(GatewayAuthenticationError):
            verifier.authorize("POST", "/internal/agents/select", value, b"{}")

    def test_method_path_body_time_and_duplicate_header_tampering_fail(self):
        mutations = (
            ("GET", "/internal/agents/select", headers(), b"{}"),
            ("POST", "/healthz", headers(), b"{}"),
            ("POST", "/internal/agents/select", headers(), b"{\"x\":1}"),
            ("POST", "/internal/agents/select", headers(timestamp=NOW - 31), b"{}"),
            ("POST", "/internal/agents/select", headers(timestamp=NOW + 31), b"{}"),
        )
        for method, path, value, body in mutations:
            with self.subTest(method=method, path=path, body=body):
                with self.assertRaises(GatewayAuthenticationError):
                    SessionCapabilityVerifier(SECRET, now=lambda: NOW).authorize(
                        method, path, value, body
                    )
        duplicated = headers(nonce=bytes(range(32, 48)))
        duplicated["x-alpine-signature"] *= 2
        with self.assertRaises(GatewayAuthenticationError):
            SessionCapabilityVerifier(SECRET, now=lambda: NOW).authorize(
                "POST", "/internal/agents/select", duplicated, b"{}"
            )

    def test_nonce_cache_is_thread_safe_and_bounded(self):
        verifier = SessionCapabilityVerifier(
            SECRET,
            now=lambda: NOW,
            nonce_now=lambda: NOW,
            max_nonces_per_bucket=16,
        )
        results = []

        def submit(index):
            nonce = index.to_bytes(16, "big")
            try:
                verifier.authorize(
                    "GET", "/healthz", headers("GET", "/healthz", b"", nonce=nonce), b""
                )
                results.append(True)
            except GatewayAuthenticationError:
                results.append(False)

        workers = [threading.Thread(target=submit, args=(index,)) for index in range(24)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(16, sum(results))
        self.assertEqual(16, verifier.nonce_count())

    def test_nonce_buckets_recover_without_evicting_live_replay_entries(self):
        clock = [NOW]
        verifier = SessionCapabilityVerifier(
            SECRET,
            now=lambda: NOW,
            nonce_now=lambda: clock[0],
            nonce_ttl_seconds=20,
            nonce_bucket_seconds=5,
            max_nonces_per_bucket=2,
        )
        first = bytes(range(16))
        second = bytes(range(16, 32))
        rejected = bytes(range(32, 48))
        verifier.authorize("GET", "/healthz", headers("GET", "/healthz", b"", nonce=first), b"")
        verifier.authorize("GET", "/healthz", headers("GET", "/healthz", b"", nonce=second), b"")
        with self.assertRaises(GatewayAuthenticationError):
            verifier.authorize(
                "GET", "/healthz", headers("GET", "/healthz", b"", nonce=rejected), b""
            )

        clock[0] += 5
        next_bucket = bytes(range(48, 64))
        verifier.authorize(
            "GET", "/healthz", headers("GET", "/healthz", b"", nonce=next_bucket), b""
        )
        with self.assertRaises(GatewayAuthenticationError):
            verifier.authorize(
                "GET", "/healthz", headers("GET", "/healthz", b"", nonce=first), b""
            )
        self.assertEqual(3, verifier.nonce_count())
        telemetry = verifier.telemetry_snapshot()
        self.assertEqual(1, telemetry["capacity_rejected"])
        self.assertEqual(1, telemetry["replay_rejected"])
        self.assertEqual(2, telemetry["auth_rejected"])

    def test_invalid_signatures_cannot_consume_nonce_capacity(self):
        verifier = SessionCapabilityVerifier(
            SECRET,
            now=lambda: NOW,
            nonce_now=lambda: NOW,
            max_nonces_per_bucket=2,
        )
        for index in range(32):
            value = headers("GET", "/healthz", b"", nonce=index.to_bytes(16, "big"))
            value["x-alpine-signature"] = ("A" * 43,)
            with self.assertRaises(GatewayAuthenticationError):
                verifier.authorize("GET", "/healthz", value, b"")
        self.assertEqual(0, verifier.nonce_count())
        self.assertEqual(0, verifier.telemetry_snapshot()["capacity_rejected"])

    def test_security_counters_have_fixed_schema_and_saturate(self):
        counters = BoundedSecurityCounters(("rejected",))
        counters._values["rejected"] = MAX_SECURITY_COUNTER
        counters.increment("rejected")
        self.assertEqual(MAX_SECURITY_COUNTER, counters.snapshot()["rejected"])
        with self.assertRaises(ValueError):
            counters.increment("request-content")

    def test_one_time_file_requires_owner_modes_and_is_unlinked(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "private"
            parent.mkdir(mode=0o700)
            path = parent / "gateway-capability.v1"
            path.write_bytes(SECRET)
            path.chmod(0o600)
            self.assertEqual(SECRET, load_one_time_capability(str(path)))
            self.assertFalse(path.exists())

            path.write_bytes(SECRET)
            path.chmod(0o644)
            with self.assertRaises(GatewayAuthenticationError):
                load_one_time_capability(str(path))

    def test_one_time_file_rejects_symlink_and_wrong_length(self):
        with tempfile.TemporaryDirectory() as raw:
            parent = Path(raw) / "private"
            parent.mkdir(mode=0o700)
            target = parent / "target"
            target.write_bytes(SECRET)
            target.chmod(0o600)
            link = parent / "gateway-capability.v1"
            link.symlink_to(target)
            with self.assertRaises(GatewayAuthenticationError):
                load_one_time_capability(str(link))
            link.unlink()
            link.write_bytes(SECRET[:-1])
            link.chmod(0o600)
            with self.assertRaises(GatewayAuthenticationError):
                load_one_time_capability(str(link))


if __name__ == "__main__":
    unittest.main()
