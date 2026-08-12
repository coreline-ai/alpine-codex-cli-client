"""Negative and positive tests for the pinned Grok CLI artifact verifier."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_grok_cli_artifact",
    ROOT / "scripts/verify-grok-cli-artifact.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def synthetic_elf(machine=183, program_type=1):
    value = bytearray(128)
    value[:4] = b"\x7fELF"
    value[4] = 2
    value[5] = 1
    struct.pack_into("<H", value, 18, machine)
    struct.pack_into("<Q", value, 32, 64)
    struct.pack_into("<H", value, 54, 56)
    struct.pack_into("<H", value, 56, 1)
    struct.pack_into("<I", value, 64, program_type)
    return bytes(value)


def lock_for(payload):
    return {
        "version": "1.0.0",
        "target": "linux-aarch64-static",
        "source_url": "https://x.ai/cli/grok-1.0.0-linux-aarch64",
        "artifact_name": "grok-1.0.0-linux-aarch64",
        "binary_name": "grok",
        "binary_size": len(payload),
        "binary_sha256": hashlib.sha256(payload).hexdigest(),
        "version_output": "grok 1.0.0 (0123456789)",
        "source_repository": "https://github.com/xai-org/grok-build",
        "source_repository_commit": "1" * 40,
        "source_revision": "2" * 40,
        "license": "Apache-2.0",
    }


class GrokArtifactVerifierTest(unittest.TestCase):
    def verify_payload(self, payload, lock):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grok"
            path.write_bytes(payload)
            MODULE.verify_binary(path, lock)

    def test_project_lock_is_structurally_valid(self):
        lock = MODULE.read_lock(ROOT / "grok-cli-pack/grok-cli.lock.json")
        self.assertEqual("1.0.0", lock["version"])

    def test_static_aarch64_elf_passes(self):
        payload = synthetic_elf()
        self.verify_payload(payload, lock_for(payload))

    def test_version_size_and_checksum_fail_closed(self):
        payload = synthetic_elf()
        lock = lock_for(payload)
        invalid_version = copy.deepcopy(lock)
        invalid_version["version"] = "2.0.0"
        with self.assertRaises(MODULE.VerificationError):
            MODULE.verify_lock(invalid_version)
        for field, value in (("binary_size", len(payload) + 1), ("binary_sha256", "0" * 64)):
            changed = copy.deepcopy(lock)
            changed[field] = value
            with self.assertRaises(MODULE.VerificationError):
                self.verify_payload(payload, changed)

    def test_elf_class_abi_and_interpreter_fail_closed(self):
        for payload in (
            b"not-elf" + b"\0" * 121,
            synthetic_elf(machine=62),
            synthetic_elf(program_type=3),
        ):
            with self.assertRaises(MODULE.VerificationError):
                self.verify_payload(payload, lock_for(payload))


if __name__ == "__main__":
    unittest.main()
