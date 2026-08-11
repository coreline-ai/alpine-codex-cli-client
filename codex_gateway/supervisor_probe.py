"""Bounded credential-free real CLI smoke used only during Phase 3 verification."""

import argparse
import sys

from codex_gateway.app_server.process import AppServerSupervisor
from codex_gateway.app_server.protocol import CodexAppServerProtocol


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    supervisor = AppServerSupervisor(
        command=[args.codex, "app-server"],
        working_directory=args.workdir,
        environment={"HOME": args.home},
    )
    try:
        protocol = CodexAppServerProtocol(supervisor)
        protocol.initialize("alpine-codex-client", "0.1.0-debug")
        protocol.account_read()
        # Account detail is deliberately discarded. The Android side accepts this closed marker.
        sys.stdout.write("APP_SERVER_SMOKE_OK\n")
        sys.stdout.flush()
        return 0
    except Exception:
        # Never expose process stderr, account state, or any auth challenge through guest output.
        return 2
    finally:
        supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(main())
