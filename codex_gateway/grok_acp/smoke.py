"""Credential-free, redacted Grok version and production-supervisor smoke probe."""

from __future__ import annotations

import os
import subprocess
import sys

from codex_gateway.agents.grok import GrokAgentAdapter

from .policy import CHILD_UMASK, GUEST_WORK, GrokLaunchPolicy, LOCKED_VERSION_OUTPUT
from .process import GrokAcpSupervisor, GrokSupervisorError, GrokSupervisorState


STOP_TIMEOUT_SECONDS = 2.0
READY_MARKER = "GROK_SMOKE_READY"
FAILED_MARKER = "GROK_SMOKE_FAILED"
PROCESS_ERROR_CODES = frozenset(
    {
        "grok_process_start_failed",
        "grok_process_stdio_unavailable",
    }
)


def run() -> int:
    supervisor: GrokAcpSupervisor | None = None
    adapter: GrokAgentAdapter | None = None
    failure_stage = "POLICY"
    previous_umask = os.umask(CHILD_UMASK)
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
        )
        if version.returncode != 0 or version.stdout.decode("utf-8", "strict").strip() != LOCKED_VERSION_OUTPUT:
            raise RuntimeError

        failure_stage = "PROCESS"
        supervisor = GrokAcpSupervisor()
        adapter = GrokAgentAdapter(GUEST_WORK.as_posix(), supervisor=supervisor)
        try:
            adapter.activate()
        except GrokSupervisorError as error:
            if error.code not in PROCESS_ERROR_CODES:
                failure_stage = "INITIALIZE"
            raise

        failure_stage = "INITIALIZE"
        failure_stage = "LIFECYCLE"
        if not adapter.is_ready() or supervisor.state is not GrokSupervisorState.READY:
            raise RuntimeError

        failure_stage = "ACCOUNT"
        account = adapter.account()
        if not isinstance(account.authenticated, bool) or not isinstance(account.requires_auth, bool):
            raise RuntimeError
        if account.authenticated == account.requires_auth:
            raise RuntimeError

        print(READY_MARKER)
        return 0
    except Exception:
        print(f"{FAILED_MARKER}_{failure_stage}")
        return 1
    finally:
        if adapter is not None and adapter.is_ready():
            try:
                adapter.deactivate()
            except Exception:
                pass
        elif supervisor is not None:
            try:
                supervisor.stop(STOP_TIMEOUT_SECONDS)
            except Exception:
                pass
        os.umask(previous_umask)


if __name__ == "__main__":
    sys.exit(run())
