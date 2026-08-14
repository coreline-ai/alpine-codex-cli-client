"""Tests for the Agent-neutral selection and operation invariants."""

import unittest

from codex_gateway.agents.contracts import AgentActivity, AgentCapabilities, AgentId
from codex_gateway.agents.router import AgentRouter, AgentRoutingError


class FakeAdapter:
    capabilities = AgentCapabilities(True, True, True, True, True)

    def __init__(self, agent_id, *, ready=True, activity=None, fail_activate=False, fail_deactivate=False):
        self.agent_id = agent_id
        self.ready = ready
        self.current_activity = activity or AgentActivity()
        self.fail_activate = fail_activate
        self.fail_deactivate = fail_deactivate
        self.activate_calls = 0
        self.deactivate_calls = 0

    def is_ready(self):
        return self.ready

    def activity(self):
        return self.current_activity

    def activate(self):
        self.activate_calls += 1
        if self.fail_activate:
            raise RuntimeError("start failed")
        self.ready = True

    def deactivate(self):
        self.deactivate_calls += 1
        if self.fail_deactivate:
            raise RuntimeError("stop failed")
        self.ready = False


class AgentRouterTest(unittest.TestCase):
    def test_agent_ids_are_exact_and_unknown_values_fail_closed(self):
        self.assertEqual(AgentId.CODEX, AgentId.parse_exact("codex"))
        self.assertEqual(AgentId.GROK, AgentId.parse_exact("grok"))
        for value in ("GROK", "other", "", None):
            with self.assertRaises(ValueError):
                AgentId.parse_exact(value)

        router = AgentRouter([FakeAdapter(AgentId.CODEX)])
        for value in ("GROK", "other"):
            with self.assertRaises(AgentRoutingError) as error:
                router.select(value)
            self.assertEqual("invalid_agent", error.exception.code)
        with self.assertRaises(AgentRoutingError) as error:
            router.select("grok")
        self.assertEqual("agent_unavailable", error.exception.code)

    def test_gateway_health_is_separate_from_selected_backend_readiness(self):
        router = AgentRouter([FakeAdapter(AgentId.CODEX, ready=False)])
        state = router.state()
        self.assertTrue(state.gateway_ready)
        self.assertFalse(state.backend_ready)
        self.assertEqual(AgentId.CODEX, state.selected_agent)

    def test_active_login_and_turn_each_block_switch_and_duplicate_operation(self):
        codex = FakeAdapter(AgentId.CODEX)
        grok = FakeAdapter(AgentId.GROK)
        router = AgentRouter([codex, grok])

        router.begin_login("codex", "login_1")
        with self.assertRaises(AgentRoutingError) as error:
            router.select("grok")
        self.assertEqual("agent_login_active", error.exception.code)
        with self.assertRaises(AgentRoutingError) as error:
            router.begin_login("codex", "login_2")
        self.assertEqual("agent_login_active", error.exception.code)
        router.finish_login("codex", "login_1")

        router.begin_turn("codex", "turn_1")
        with self.assertRaises(AgentRoutingError) as error:
            router.select("grok")
        self.assertEqual("agent_turn_active", error.exception.code)
        with self.assertRaises(AgentRoutingError) as error:
            router.finish_turn("grok", "turn_1")
        self.assertEqual("agent_operation_mismatch", error.exception.code)
        router.finish_turn("codex", "turn_1")

        state = router.select("grok")
        self.assertEqual(AgentId.GROK, state.selected_agent)
        self.assertEqual(1, codex.deactivate_calls)
        self.assertEqual(1, grok.activate_calls)

    def test_adapter_reported_activity_blocks_switch_before_process_calls(self):
        codex = FakeAdapter(AgentId.CODEX, activity=AgentActivity(active_turn=True))
        grok = FakeAdapter(AgentId.GROK)
        router = AgentRouter([codex, grok])
        with self.assertRaises(AgentRoutingError) as error:
            router.select("grok")
        self.assertEqual("agent_turn_active", error.exception.code)
        self.assertEqual(0, codex.deactivate_calls)
        self.assertEqual(0, grok.activate_calls)

    def test_target_start_failure_does_not_reactivate_previous_agent(self):
        codex = FakeAdapter(AgentId.CODEX)
        grok = FakeAdapter(AgentId.GROK, ready=False, fail_activate=True)
        router = AgentRouter([codex, grok])
        with self.assertRaises(AgentRoutingError) as error:
            router.select("grok")
        self.assertEqual("agent_start_failed", error.exception.code)
        state = router.state()
        self.assertEqual(AgentId.GROK, state.selected_agent)
        self.assertEqual("agent_start_failed", state.stable_error)
        self.assertFalse(state.switching)
        self.assertEqual(1, codex.deactivate_calls)
        self.assertEqual(0, codex.activate_calls)
        self.assertEqual(1, grok.activate_calls)

    def test_same_selected_failed_backend_is_explicitly_restarted(self):
        grok = FakeAdapter(AgentId.GROK, ready=False)
        router = AgentRouter([grok], selected_agent=AgentId.GROK)

        state = router.select("grok")

        self.assertTrue(state.backend_ready)
        self.assertEqual(1, grok.deactivate_calls)
        self.assertEqual(1, grok.activate_calls)

    def test_same_selected_ready_backend_remains_a_noop(self):
        grok = FakeAdapter(AgentId.GROK, ready=True)
        router = AgentRouter([grok], selected_agent=AgentId.GROK)

        state = router.select("grok")

        self.assertTrue(state.backend_ready)
        self.assertEqual(0, grok.deactivate_calls)
        self.assertEqual(0, grok.activate_calls)

    def test_same_selected_recovery_still_rejects_adapter_activity(self):
        grok = FakeAdapter(
            AgentId.GROK,
            ready=False,
            activity=AgentActivity(active_login=True),
        )
        router = AgentRouter([grok], selected_agent=AgentId.GROK)

        with self.assertRaises(AgentRoutingError) as error:
            router.select("grok")

        self.assertEqual("agent_login_active", error.exception.code)
        self.assertEqual(0, grok.deactivate_calls)
        self.assertEqual(0, grok.activate_calls)

    def test_invalid_request_ids_and_non_selected_operations_fail_before_state_change(self):
        router = AgentRouter([FakeAdapter(AgentId.CODEX), FakeAdapter(AgentId.GROK)])
        with self.assertRaises(AgentRoutingError) as error:
            router.begin_turn("grok", "turn_1")
        self.assertEqual("agent_not_selected", error.exception.code)
        with self.assertRaises(AgentRoutingError) as error:
            router.begin_turn("codex", "bad/id")
        self.assertEqual("invalid_request", error.exception.code)
        self.assertIsNone(router.state().active_turn)


if __name__ == "__main__":
    unittest.main()
