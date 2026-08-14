from __future__ import annotations

import contextlib
import io
import types
import unittest
from unittest import mock

from codex_gateway.grok_acp import smoke
from codex_gateway.grok_acp.process import GrokSupervisorError, GrokSupervisorState


class _Policy:
    executable = types.SimpleNamespace(as_posix=lambda: "/fixed/grok")
    work = "/fixed/work"

    def validate(self) -> None:
        return None

    def permission_probe(self) -> None:
        return None

    def environment(self) -> dict[str, str]:
        return {}


class _Supervisor:
    def __init__(self, state: GrokSupervisorState = GrokSupervisorState.READY) -> None:
        self.state = state
        self.stopped = False

    def start(self) -> None:
        return None

    def stop(self, timeout_seconds: float) -> None:
        self.stopped = timeout_seconds == smoke.STOP_TIMEOUT_SECONDS


class _Adapter:
    def __init__(self, _work: str, *, supervisor: _Supervisor) -> None:
        self.supervisor = supervisor
        self.deactivated = False

    def activate(self) -> None:
        self.supervisor.start()

    def is_ready(self) -> bool:
        return self.supervisor.state is GrokSupervisorState.READY

    def account(self):
        return types.SimpleNamespace(authenticated=False, requires_auth=True)

    def deactivate(self) -> None:
        self.deactivated = True
        self.supervisor.stop(smoke.STOP_TIMEOUT_SECONDS)


class GrokSmokeTest(unittest.TestCase):
    def _run(self, supervisor: _Supervisor) -> tuple[int, str]:
        completed = types.SimpleNamespace(returncode=0, stdout=(smoke.LOCKED_VERSION_OUTPUT + "\n").encode())
        output = io.StringIO()
        with (
            mock.patch.object(smoke.GrokLaunchPolicy, "production", return_value=_Policy()),
            mock.patch.object(smoke.subprocess, "run", return_value=completed),
            mock.patch.object(smoke, "GrokAcpSupervisor", return_value=supervisor),
            mock.patch.object(smoke, "GrokAgentAdapter", _Adapter),
            contextlib.redirect_stdout(output),
        ):
            result = smoke.run()
        self.assertTrue(supervisor.stopped)
        return result, output.getvalue().strip()

    def test_ready_requires_production_supervisor_to_remain_ready(self) -> None:
        result, marker = self._run(_Supervisor())

        self.assertEqual(0, result)
        self.assertEqual(smoke.READY_MARKER, marker)

    def test_post_initialize_process_loss_is_lifecycle_failure(self) -> None:
        result, marker = self._run(_Supervisor(GrokSupervisorState.FAILED))

        self.assertEqual(1, result)
        self.assertEqual("GROK_SMOKE_FAILED_LIFECYCLE", marker)

    def test_process_start_error_remains_content_free(self) -> None:
        supervisor = _Supervisor()
        supervisor.start = mock.Mock(side_effect=GrokSupervisorError("grok_process_start_failed"))

        result, marker = self._run(supervisor)

        self.assertEqual(1, result)
        self.assertEqual("GROK_SMOKE_FAILED_PROCESS", marker)

    def test_initialize_error_remains_content_free(self) -> None:
        supervisor = _Supervisor()
        supervisor.start = mock.Mock(side_effect=GrokSupervisorError("grok_initialize_failed"))

        result, marker = self._run(supervisor)

        self.assertEqual(1, result)
        self.assertEqual("GROK_SMOKE_FAILED_INITIALIZE", marker)

    def test_account_error_remains_content_free(self) -> None:
        supervisor = _Supervisor()

        class AccountFailingAdapter(_Adapter):
            def account(self):
                raise RuntimeError

        completed = types.SimpleNamespace(returncode=0, stdout=(smoke.LOCKED_VERSION_OUTPUT + "\n").encode())
        output = io.StringIO()
        with (
            mock.patch.object(smoke.GrokLaunchPolicy, "production", return_value=_Policy()),
            mock.patch.object(smoke.subprocess, "run", return_value=completed),
            mock.patch.object(smoke, "GrokAcpSupervisor", return_value=supervisor),
            mock.patch.object(smoke, "GrokAgentAdapter", AccountFailingAdapter),
            contextlib.redirect_stdout(output),
        ):
            result = smoke.run()

        self.assertEqual(1, result)
        self.assertEqual("GROK_SMOKE_FAILED_ACCOUNT", output.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
