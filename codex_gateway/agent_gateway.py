"""Authenticated production entrypoint for one selected official CLI backend."""

from __future__ import annotations

import argparse
import os
import re
from typing import Optional

from codex_gateway.agents.grok import GrokAgentAdapter
from codex_gateway.agents.http import make_agent_handler
from codex_gateway.agents.production import ManagedCodexAgentAdapter
from codex_gateway.agents.router import AgentRouter
from codex_gateway.agents.service import AgentGatewayService
from codex_gateway.gateway import PrivateUnixGatewayServer
from codex_gateway.grok_acp.policy import CHILD_UMASK, GUEST_EXECUTABLE, GUEST_HOME, GUEST_WORK
from codex_gateway.security import SessionCapabilityVerifier, load_one_time_capability


CODEX_HOME = "/workspace/.alpine-codex/home"
CAPABILITY_FILE = "/workspace/.alpine-codex/security/gateway-capability.v1"
WORKSPACE = "/workspace"
# Host-path fixture used by entrypoint tests. Production accepts only the same canonical
# app-private shape for the release/debug/lab package ids (see _valid_socket_path).
SOCKET_PATH = (
    "/data/user/0/dev.alpine.codexclient.debug/files/"
    "alpine-codex-runtime/workspace/.gateway/gateway.sock"
)
SOCKET_PATH_PATTERN = re.compile(
    r"/data/(?:user/[0-9]+|data)/[A-Za-z0-9._-]{1,128}/files/"
    r"alpine-codex-runtime/workspace/\.gateway/gateway\.sock"
)


def serve(codex_path: str, capability_file: str, socket_path: str, expected_peer_uid: int) -> None:
    try:
        secret = load_one_time_capability(capability_file)
    except Exception:
        _startup_failed("CAPABILITY")
        raise
    verifier = SessionCapabilityVerifier(secret)
    codex = ManagedCodexAgentAdapter(codex_path, CODEX_HOME, WORKSPACE)
    grok = GrokAgentAdapter(
        GUEST_WORK.as_posix(),
        binding_store_path=(GUEST_HOME / "conversation-bindings.v1.json").as_posix(),
    )
    server: Optional[PrivateUnixGatewayServer] = None
    try:
        codex.activate()
    except Exception:
        _startup_failed("CODEX")
        raise
    try:
        router = AgentRouter([codex, grok])
        service = AgentGatewayService(router)
        try:
            server = PrivateUnixGatewayServer(
                socket_path,
                make_agent_handler(service, verifier.authorize),
                expected_peer_uid=expected_peer_uid,
            )
        except Exception:
            _startup_failed("BIND")
            raise
        print("AGENT_GATEWAY_READY", flush=True)
        server.serve_forever(poll_interval=0.25)
    finally:
        if server is not None:
            server.server_close()
        for adapter in (codex, grok):
            if adapter.is_ready():
                try:
                    adapter.deactivate()
                except Exception:
                    pass


def _startup_failed(stage: str) -> None:
    """Emit one closed diagnostic marker without exception, path, or process output."""

    print(f"AGENT_GATEWAY_FAILED_{stage}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--grok", required=True)
    parser.add_argument("--codex-home", required=True)
    parser.add_argument("--grok-home", required=True)
    parser.add_argument("--grok-work", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--capability-file", required=True)
    parser.add_argument("--socket-path", required=True)
    parser.add_argument("--peer-uid", required=True, type=int)
    args = parser.parse_args()
    expected = {
        args.codex_home: CODEX_HOME,
        args.grok: GUEST_EXECUTABLE.as_posix(),
        args.grok_home: GUEST_HOME.as_posix(),
        args.grok_work: GUEST_WORK.as_posix(),
        args.workdir: WORKSPACE,
        args.capability_file: CAPABILITY_FILE,
    }
    if (
        any(actual != fixed for actual, fixed in expected.items())
        or not _valid_socket_path(args.socket_path)
        or args.peer_uid < 0
    ):
        return 2
    if not args.codex.startswith("/workspace/.alpine-codex/staging/codex-cli/"):
        return 2
    # This production process never restores a broader mask. Grok worker-thread launches can
    # therefore use posix_spawn without a pre-exec callback or per-child umask operation.
    os.umask(CHILD_UMASK)
    try:
        serve(args.codex, args.capability_file, args.socket_path, args.peer_uid)
    except Exception:
        return 2
    return 0


def _valid_socket_path(path: str) -> bool:
    return bool(
        isinstance(path, str)
        and len(os.fsencode(path)) <= 107
        and SOCKET_PATH_PATTERN.fullmatch(path)
    )


if __name__ == "__main__":
    raise SystemExit(main())
