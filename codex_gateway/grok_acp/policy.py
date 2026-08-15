"""Fail-closed launch and filesystem policy for the pinned official Grok CLI."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Mapping, Tuple


class GrokPolicyError(RuntimeError):
    """Stable error that never includes a path, environment value, or credential detail."""

    code = "grok_launch_policy_invalid"

    def __init__(self) -> None:
        super().__init__(self.code)


GUEST_WORKSPACE = Path("/workspace")
GUEST_ROOT = GUEST_WORKSPACE / ".alpine-grok"
GUEST_HOME = GUEST_ROOT / "home"
GUEST_STAGING = GUEST_ROOT / "staging"
GUEST_PROFILE_DIRECTORY = GUEST_ROOT / "profile"
GUEST_PROFILE = GUEST_PROFILE_DIRECTORY / "chat-only.md"
GUEST_WORK = GUEST_ROOT / "work"
GUEST_EXECUTABLE = GUEST_STAGING / "grok-cli" / "1.0.0" / "grok"

LOCKED_BINARY_SIZE = 133_745_832
LOCKED_BINARY_SHA256 = "bb7c51116564a2219f6a49850815060f416918ac407f1f2ba82c53c0b0d4383f"
LOCKED_VERSION_OUTPUT = "grok 1.0.0 (3cd0d0cbce)"
LOCKED_PROFILE_SIZE = 570
LOCKED_PROFILE_SHA256 = "5c7cf4ac7fb9035f2675af836b72578f7c6811357ce0852e70b4c0c2dc146c72"

# This is the complete child environment. In particular, it is not merged with os.environ.
FIXED_ENVIRONMENT = {
    "HOME": GUEST_HOME.as_posix(),
    "GROK_HOME": GUEST_HOME.as_posix(),
    "GROK_LOGIN_DEVICE_FLOW": "true",
    "GROK_DISABLE_API_KEY_AUTH": "true",
    "GROK_DISABLE_AUTOUPDATER": "1",
    "GROK_SUBAGENTS": "0",
    "GROK_TELEMETRY_ENABLED": "false",
    "GROK_TELEMETRY_TRACE_UPLOAD": "false",
    "GROK_EXTERNAL_OTEL": "0",
}

FIXED_COMMAND = (
    GUEST_EXECUTABLE.as_posix(),
    "--cwd",
    GUEST_WORK.as_posix(),
    "--no-auto-update",
    "agent",
    "--no-leader",
    "--agent-profile",
    GUEST_PROFILE.as_posix(),
    "stdio",
)
CHILD_UMASK = 0o077


@dataclass(frozen=True)
class GrokLaunchPolicy:
    """Validated fixed launch values, with a test-only host-root mapping seam."""

    allowed_root: Path
    root: Path
    home: Path
    staging: Path
    profile_directory: Path
    profile: Path
    work: Path
    executable: Path

    @classmethod
    def production(cls) -> "GrokLaunchPolicy":
        return cls.for_root(GUEST_WORKSPACE, GUEST_ROOT)

    @classmethod
    def for_root(cls, allowed_root: Path, root: Path) -> "GrokLaunchPolicy":
        """Map the fixed relative layout under an owned root for deterministic tests."""

        allowed = Path(allowed_root).absolute()
        base = Path(root).absolute()
        return cls(
            allowed_root=allowed,
            root=base,
            home=base / "home",
            staging=base / "staging",
            profile_directory=base / "profile",
            profile=base / "profile" / "chat-only.md",
            work=base / "work",
            executable=base / "staging" / "grok-cli" / "1.0.0" / "grok",
        )

    def command(self) -> Tuple[str, ...]:
        return FIXED_COMMAND

    def environment(self) -> Mapping[str, str]:
        return dict(FIXED_ENVIRONMENT)

    def validate(self, *, verify_binary_hash: bool = True) -> None:
        try:
            self._validate_layout()
            self._validate_private_tree(self.home)
            self._validate_private_tree(self.profile_directory)
            self._validate_private_tree(self.staging)
            self._validate_private_tree(self.work)
            self._validate_regular_file(self.profile, 0o600)
            if self.profile.stat().st_size != LOCKED_PROFILE_SIZE:
                raise ValueError
            if _sha256(self.profile) != LOCKED_PROFILE_SHA256:
                raise ValueError
            self._validate_regular_file(self.executable, 0o700)
            if self.executable.stat().st_size != LOCKED_BINARY_SIZE:
                raise ValueError
            if verify_binary_hash and _sha256(self.executable) != LOCKED_BINARY_SHA256:
                raise ValueError
            if tuple(self.environment()) != tuple(FIXED_ENVIRONMENT):
                raise ValueError
            if tuple(self.command()) != FIXED_COMMAND:
                raise ValueError
        except GrokPolicyError:
            raise
        except (OSError, ValueError):
            raise GrokPolicyError() from None

    def permission_probe(self) -> None:
        """Prove that a CLI-style new config/credential file is created owner-only."""

        probe = self.home / ".permission-probe"
        descriptor: int | None = None
        previous_umask = os.umask(0o077)
        try:
            self._validate_directory(self.home)
            if probe.exists() or probe.is_symlink():
                raise ValueError
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(probe, flags, 0o600)
            os.write(descriptor, b"permission-probe\n")
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            self._validate_regular_file(probe, 0o600)
        except (OSError, ValueError):
            raise GrokPolicyError() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                probe.unlink(missing_ok=True)
            finally:
                os.umask(previous_umask)

    def _validate_layout(self) -> None:
        expected = type(self).for_root(self.allowed_root, self.root)
        if self != expected:
            raise ValueError
        if self.root == self.allowed_root or self.allowed_root not in self.root.parents:
            raise ValueError
        for path in (
            self.allowed_root,
            self.root,
            self.home,
            self.staging,
            self.profile_directory,
            self.work,
            self.executable.parent,
        ):
            self._reject_symlink_components(path)
        resolved_root = self.root.resolve(strict=True)
        resolved_allowed = self.allowed_root.resolve(strict=True)
        if resolved_allowed not in resolved_root.parents:
            raise ValueError

    def _reject_symlink_components(self, path: Path) -> None:
        current = path
        while True:
            if current.is_symlink():
                raise ValueError
            if current == self.allowed_root:
                return
            if current == current.parent:
                raise ValueError
            current = current.parent

    def _validate_private_tree(self, directory: Path) -> None:
        self._validate_directory(directory)
        count = 0
        for path in directory.rglob("*"):
            count += 1
            if count > 4096:
                raise ValueError
            self._reject_symlink_components(path)
            if path.is_dir():
                self._validate_directory(path)
            elif path.is_file():
                expected_mode = 0o700 if path == self.executable else 0o600
                self._validate_regular_file(path, expected_mode)
            else:
                raise ValueError

    def _validate_directory(self, directory: Path) -> None:
        self._reject_symlink_components(directory)
        value = directory.stat()
        if not stat.S_ISDIR(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o700:
            raise ValueError

    def _validate_regular_file(self, path: Path, expected_mode: int) -> None:
        self._reject_symlink_components(path)
        value = path.stat()
        if not stat.S_ISREG(value.st_mode) or stat.S_IMODE(value.st_mode) != expected_mode:
            raise ValueError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(64 * 1024):
            digest.update(block)
    return digest.hexdigest()
