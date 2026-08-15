from __future__ import annotations

from dataclasses import replace
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from codex_gateway.agents.contracts import AgentConversationBinding, AgentId
from codex_gateway.agents.grok import (
    MAX_STREAM_TEXT_BYTES,
    GrokAdapterError,
    GrokAgentAdapter,
    GrokRetryPolicy,
)
from codex_gateway.grok_acp.contract import (
    AUTH_METHOD_ID,
    CACHED_TOKEN_AUTH_METHOD_ID,
    GrokInitializeState,
    parse_model_catalog,
)
from codex_gateway.grok_acp.process import GrokSupervisorState
from codex_gateway.grok_acp.rpc import AcpNotification, AcpRemoteError, GrokProfileAudit


def model_state(rows=None, current="model-alpha"):
    if rows is None:
        rows = [
            {"modelId": "model-alpha", "name": "Alpha"},
            {"modelId": "model-beta", "name": "Beta"},
        ]
    return {"result": {"availableModels": rows, "currentModelId": current}}


def chat(conversation_id="conversation-one", model="model-alpha", resume=False):
    return {
        "agent_id": "grok",
        "conversation_id": conversation_id,
        "resume_existing": resume,
        "model": model,
        "stream": True,
        "messages": [{"role": "user", "content": "fixture prompt"}],
    }


def agent_chunk(text, session_id="session-1", prompt_id="prompt-1"):
    return {
        "sessionId": session_id,
        "_meta": {"promptId": prompt_id},
        "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
        },
    }


def retry_state(value, session_id="session-1"):
    return {"sessionId": session_id, "update": {"sessionUpdate": "retry_state", **value}}


class FakeGrokSupervisor:
    def __init__(self):
        self.state = GrokSupervisorState.READY
        self.generation = 7
        self.initialize_state = None
        self.authenticated_method_id = None
        self.auth_info_payload = {}
        self.auth_info_unready_calls = 0
        self.auth_info_calls = 0
        self.auth_url = "https://accounts.x.ai/device?challenge=fixture"
        self.auth_mode = "device"
        self.auth_url_unready_calls = 0
        self.auth_url_timeouts = []
        self.model_response = model_state()
        self.authenticate_calls = []
        self.cancel_auth_calls = []
        self.call_order = []
        self.authenticate_entered = threading.Event()
        self.authenticate_release = threading.Event()
        self.authenticate_release.set()
        self.authenticate_ignores_cancel = False
        self.listeners = []
        self.new_session_calls = []
        self.load_session_calls = []
        self.resume_session_calls = []
        self.set_model_calls = []
        self.prompt_calls = []
        self.cancel_session_calls = []
        self.close_session_calls = []
        self.logout_calls = 0
        self.notification_sequence = 0
        self.profile_audit_value = GrokProfileAudit()

    @property
    def authenticated(self):
        return self.authenticated_method_id in {AUTH_METHOD_ID, CACHED_TOKEN_AUTH_METHOD_ID}

    @authenticated.setter
    def authenticated(self, value):
        self.authenticated_method_id = AUTH_METHOD_ID if value else None

    @property
    def model_response(self):
        return self._model_response

    @model_response.setter
    def model_response(self, value):
        self._model_response = value
        try:
            result = value["result"]
            summaries, current = parse_model_catalog(result)
        except (AttributeError, KeyError, TypeError, ValueError):
            self.initialize_state = None
            return
        self.initialize_state = GrokInitializeState(
            protocol_version="1",
            auth_method_id=AUTH_METHOD_ID,
            models=summaries,
            current_model_id=current,
            can_load_session=True,
            can_resume_session=True,
            can_close_session=True,
        )

    def start(self):
        self.state = GrokSupervisorState.READY
        self.generation += 1

    def stop(self, timeout_seconds=5.0):
        self.state = GrokSupervisorState.STOPPED

    def add_notification_listener(self, listener):
        self.listeners.append(listener)

        def remove():
            if listener in self.listeners:
                self.listeners.remove(listener)

        return remove

    def authenticate(self, request_sequence):
        self.authenticate_calls.append(request_sequence)
        self.call_order.append("authenticate")
        self.authenticate_entered.set()
        self.authenticate_release.wait(2.0)
        if request_sequence in self.cancel_auth_calls and not self.authenticate_ignores_cancel:
            raise RuntimeError("cancelled")
        self.authenticated = True
        return {"account": "discard-me"}

    def get_auth_url(self, timeout_seconds=None):
        self.call_order.append("get_url")
        self.auth_url_timeouts.append(timeout_seconds)
        if not self.authenticate_entered.wait(1.0):
            raise RuntimeError("authenticate not started")
        if self.auth_url_unready_calls > 0:
            self.auth_url_unready_calls -= 1
            return {"auth_url": None, "mode": None}
        return {
            "auth_url": self.auth_url,
            "mode": self.auth_mode,
            "user_code": "must-not-be-read",
        }

    def cancel_auth(self, request_sequence):
        self.cancel_auth_calls.append(request_sequence)
        self.authenticate_release.set()
        return {"cancelled": True, "account": "discard-me"}

    def auth_info(self):
        self.auth_info_calls += 1
        result = dict(self.auth_info_payload)
        if self.authenticated and self.auth_info_unready_calls > 0:
            self.auth_info_unready_calls -= 1
            result["methodId"] = None
        else:
            result["methodId"] = self.authenticated_method_id if self.authenticated else None
        return result

    def logout(self):
        self.logout_calls += 1
        self.authenticated = False
        return {
            "ok": True,
            "email": "private@example.invalid",
            "profileImageUrl": "https://private.invalid/image",
            "token": "private-token",
        }

    def list_models(self):
        return self.model_response

    def new_session(self, working_directory):
        self.new_session_calls.append(working_directory)
        return {"sessionId": "session-" + str(len(self.new_session_calls))}

    def load_session(self, session_id, working_directory):
        self.load_session_calls.append((session_id, working_directory))
        return {}

    def resume_session(self, session_id, working_directory):
        self.resume_session_calls.append((session_id, working_directory))
        return {}

    def set_session_model(self, session_id, model_id):
        self.set_model_calls.append((session_id, model_id))
        return {}

    def prompt(self, session_id, text):
        self.prompt_calls.append((session_id, text))
        return {"stopReason": "end_turn"}

    @property
    def profile_audit(self):
        return self.profile_audit_value

    @property
    def prompt_dispatch_count(self):
        return len(self.prompt_calls)

    def cancel_session(self, session_id):
        self.cancel_session_calls.append(session_id)

    def close_session(self, session_id):
        self.close_session_calls.append(session_id)
        return {}

    def emit(self, method, params, sequence=None, generation=None):
        if sequence is None:
            self.notification_sequence += 1
            sequence = self.notification_sequence
        notification = AcpNotification(
            generation=self.generation if generation is None else generation,
            sequence=sequence,
            method=method,
            params=params,
        )
        for listener in tuple(self.listeners):
            listener(notification)


class GrokLoginTest(unittest.TestCase):
    def setUp(self):
        self.supervisor = FakeGrokSupervisor()
        self.adapter = GrokAgentAdapter("/workspace", self.supervisor)
        self.adapter.activate()

    def tearDown(self):
        self.supervisor.authenticate_release.set()
        deadline = time.monotonic() + 1.0
        while self.adapter.activity().active_login and time.monotonic() < deadline:
            time.sleep(0.005)

    def test_authenticate_starts_before_get_url_and_only_complete_device_url_is_returned(self):
        self.supervisor.authenticate_release.clear()
        login = self.adapter.start_device_login()
        self.assertEqual(["authenticate", "get_url"], self.supervisor.call_order)
        self.assertEqual("pending", login.state)
        self.assertEqual(self.supervisor.auth_url, login.verification_url)
        self.assertIsNone(login.user_code)
        self.assertNotIn(self.supervisor.auth_url, repr(self.adapter.login_status(login.request_id)))

        self.supervisor.authenticate_release.set()
        deadline = time.monotonic() + 1.0
        while self.adapter.login_status(login.request_id).state == "pending" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual("authenticated", self.adapter.login_status(login.request_id).state)
        self.assertEqual([1], self.supervisor.authenticate_calls)

    def test_auth_issuer_host_remains_an_exact_allowed_fallback(self):
        self.supervisor.auth_url = "https://auth.x.ai/device?challenge=fixture"
        self.supervisor.authenticate_release.clear()

        login = self.adapter.start_device_login()

        self.assertEqual(self.supervisor.auth_url, login.verification_url)

    def test_auth_url_not_ready_is_polled_without_duplicate_authenticate(self):
        self.supervisor.authenticate_release.clear()
        self.supervisor.auth_url_unready_calls = 2

        login = self.adapter.start_device_login()

        self.assertEqual("pending", login.state)
        self.assertEqual(self.supervisor.auth_url, login.verification_url)
        self.assertEqual([1], self.supervisor.authenticate_calls)
        self.assertEqual(["authenticate", "get_url", "get_url", "get_url"], self.supervisor.call_order)
        self.assertEqual(3, len(self.supervisor.auth_url_timeouts))
        self.assertTrue(
            all(
                timeout is not None and 0 < timeout <= 15.0
                for timeout in self.supervisor.auth_url_timeouts
            )
        )

    def test_browser_completion_trusts_standard_authenticate_without_private_probe(self):
        self.supervisor.authenticate_release.clear()

        login = self.adapter.start_device_login()
        self.supervisor.authenticate_release.set()

        deadline = time.monotonic() + 1.0
        while self.adapter.login_status(login.request_id).state == "pending" and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual("authenticated", self.adapter.login_status(login.request_id).state)
        self.assertEqual([1], self.supervisor.authenticate_calls)
        self.assertEqual(0, self.supervisor.auth_info_calls)

    def test_auth_url_not_ready_timeout_cancels_exact_auth_sequence(self):
        self.supervisor.authenticate_release.clear()
        self.supervisor.auth_url_unready_calls = 10
        with (
            mock.patch("codex_gateway.agents.grok.GROK_AUTH_URL_READY_TIMEOUT_SECONDS", 0.01),
            mock.patch("codex_gateway.agents.grok.GROK_AUTH_URL_POLL_SECONDS", 0.005),
        ):
            with self.assertRaises(GrokAdapterError) as error:
                self.adapter.start_device_login()

        self.assertEqual("grok_login_challenge_invalid", error.exception.code)
        self.assertEqual([1], self.supervisor.authenticate_calls)
        self.assertEqual([1], self.supervisor.cancel_auth_calls)

    def test_duplicate_click_single_flight_cancel_and_late_success_stays_cancelled(self):
        self.supervisor.authenticate_release.clear()
        self.supervisor.authenticate_ignores_cancel = True
        login = self.adapter.start_device_login()
        with self.assertRaises(GrokAdapterError) as error:
            self.adapter.start_device_login()
        self.assertEqual("login_already_active", error.exception.code)
        self.assertEqual([1], self.supervisor.authenticate_calls)

        cancelled = self.adapter.cancel_login(login.request_id)
        self.assertEqual("cancelled", cancelled.state)
        self.assertEqual([1], self.supervisor.cancel_auth_calls)
        with self.assertRaises(GrokAdapterError):
            self.adapter.cancel_login(login.request_id)
        self.supervisor.authenticate_release.set()
        time.sleep(0.05)
        self.assertEqual("cancelled", self.adapter.login_status(login.request_id).state)
        self.assertEqual([1], self.supervisor.cancel_auth_calls)

    def test_invalid_oversized_non_https_or_fragment_url_is_never_returned(self):
        invalid = (
            "http://auth.x.ai/device?challenge=x",
            "https://evil.invalid/device?challenge=x",
            "https://accounts.x.ai.evil.invalid/device?challenge=x",
            "https://login.accounts.x.ai/device?challenge=x",
            "https://user@auth.x.ai/device?challenge=x",
            "https://auth.x.ai:444/device?challenge=x",
            "https://auth.x.ai/device?challenge=x#fragment",
            "https://auth.x.ai/" + "x" * 2050,
        )
        for url in invalid:
            supervisor = FakeGrokSupervisor()
            supervisor.auth_url = url
            supervisor.authenticate_release.clear()
            adapter = GrokAgentAdapter("/workspace", supervisor)
            adapter.activate()
            with self.assertRaises(GrokAdapterError) as error:
                adapter.start_device_login()
            self.assertEqual("grok_login_challenge_invalid", error.exception.code)
            self.assertEqual([1], supervisor.cancel_auth_calls)

        supervisor = FakeGrokSupervisor()
        supervisor.auth_mode = "loopback"
        supervisor.authenticate_release.clear()
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        with self.assertRaises(GrokAdapterError):
            adapter.start_device_login()

    def test_sensitive_account_and_logout_fields_are_discarded(self):
        self.supervisor.authenticated = True
        self.supervisor.auth_info_payload = {
            "email": "private@example.invalid",
            "profileImageUrl": "https://private.invalid/image",
            "token": "private-token",
        }
        account = self.adapter.account()
        self.assertTrue(account.authenticated)
        self.assertEqual(0, self.supervisor.auth_info_calls)
        rendered = repr(account)
        for value in self.supervisor.auth_info_payload.values():
            self.assertNotIn(value, rendered)
        self.adapter.logout()
        self.assertEqual(1, self.supervisor.logout_calls)
        self.assertFalse(self.adapter.account().authenticated)

    def test_persisted_oauth_cached_token_is_authenticated(self):
        self.supervisor.authenticated = True
        self.supervisor.authenticated_method_id = CACHED_TOKEN_AUTH_METHOD_ID

        account = self.adapter.account()

        self.assertTrue(account.authenticated)
        self.assertFalse(account.requires_auth)


class GrokModelsAndSessionTest(unittest.TestCase):
    def setUp(self):
        self.supervisor = FakeGrokSupervisor()
        self.supervisor.authenticated = True
        self.adapter = GrokAgentAdapter("/workspace", self.supervisor)
        self.adapter.activate()

    def test_zero_one_many_duplicate_removal_and_malformed_catalog(self):
        self.supervisor.model_response = model_state([], None)
        self.assertEqual((), self.adapter.models())
        self.supervisor.model_response = model_state(
            [{"modelId": "one", "name": "One"}], "one"
        )
        self.assertEqual(["one"], [item.model_id for item in self.adapter.models()])
        self.supervisor.model_response = model_state(
            [
                {"modelId": "one", "name": "One"},
                {"modelId": "one", "name": "Duplicate"},
                {"modelId": "two", "name": "Two"},
            ],
            "two",
        )
        values = self.adapter.models()
        self.assertEqual(["one", "two"], [item.model_id for item in values])
        self.assertEqual([False, True], [item.is_default for item in values])
        self.supervisor.model_response = model_state(
            [{"modelId": "two", "name": "Two"}], "two"
        )
        self.assertEqual(["two"], [item.model_id for item in self.adapter.models()])

        for malformed in (
            {},
            {"result": "bad"},
            {"result": {"availableModels": "bad", "currentModelId": None}},
            model_state([{"modelId": "", "name": "Bad"}], ""),
        ):
            self.supervisor.model_response = malformed
            with self.assertRaises(GrokAdapterError):
                self.adapter.models()

    def test_new_resume_model_change_stream_and_close_lifecycle(self):
        handle = self.adapter.start_turn(chat())
        events = list(self.adapter.stream(handle))
        self.assertEqual(["start", "done"], [event.event_type for event in events])
        self.assertEqual(["/workspace"], self.supervisor.new_session_calls)
        self.assertEqual(1, len(self.supervisor.prompt_calls))

        handle = self.adapter.start_turn(chat(model="model-beta", resume=True))
        list(self.adapter.stream(handle))
        self.assertEqual([("session-1", "/workspace")], self.supervisor.resume_session_calls)
        self.assertEqual([("session-1", "model-beta")], self.supervisor.set_model_calls)
        bindings = self.adapter.conversation_bindings()
        self.assertEqual(AgentId.GROK, bindings[0].agent_id)
        self.assertEqual("model-beta", bindings[0].model_id)
        self.assertEqual(self.supervisor.generation, bindings[0].process_generation)

        self.adapter.logout()
        self.assertEqual(["session-1"], self.supervisor.close_session_calls)
        self.assertEqual((), self.adapter.conversation_bindings())

    def test_new_session_selects_non_default_model_after_documented_session_new(self):
        handle = self.adapter.start_turn(chat(model="model-beta"))
        list(self.adapter.stream(handle))

        self.assertEqual(["/workspace"], self.supervisor.new_session_calls)
        self.assertEqual([("session-1", "model-beta")], self.supervisor.set_model_calls)

    def test_session_setup_remote_errors_are_stage_specific_and_content_free(self):
        self.supervisor.new_session = lambda _: (_ for _ in ()).throw(AcpRemoteError(-32602))
        with self.assertRaises(GrokAdapterError) as caught:
            self.adapter.start_turn(chat())
        self.assertEqual("grok_session_new_remote_n32602", caught.exception.code)

        self.supervisor.new_session = lambda _: {"sessionId": "session-new"}
        self.supervisor.set_session_model = lambda *_: (_ for _ in ()).throw(AcpRemoteError(-32000))
        with self.assertRaises(GrokAdapterError) as caught:
            self.adapter.start_turn(chat(model="model-beta"))
        self.assertEqual("grok_set_model_remote_n32000", caught.exception.code)

    def test_agent_mismatch_rejects_and_generation_mismatch_loads_before_prompt(self):
        wrong_agent = AgentConversationBinding(
            agent_id=AgentId.CODEX,
            conversation_id="conversation-one",
            backend_session_id="session-old",
            model_id="model-alpha",
            process_generation=self.supervisor.generation,
        )
        with self.assertRaises(ValueError):
            GrokAgentAdapter("/workspace", self.supervisor, (wrong_agent,))

        stale = replace(wrong_agent, agent_id=AgentId.GROK, process_generation=6)
        adapter = GrokAgentAdapter("/workspace", self.supervisor, (stale,))
        adapter.activate()
        handle = adapter.start_turn(chat(resume=True))
        self.assertEqual("done", list(adapter.stream(handle))[-1].event_type)
        self.assertEqual([("session-old", "/workspace")], self.supervisor.load_session_calls)
        self.assertEqual([], self.supervisor.resume_session_calls)
        self.assertEqual([("session-old", "fixture prompt")], self.supervisor.prompt_calls)

    def test_binding_store_loads_official_session_after_gateway_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory, 0o700)
            store = os.path.join(directory, "conversation-bindings.v1.json")
            first_supervisor = FakeGrokSupervisor()
            first_supervisor.authenticated = True
            first = GrokAgentAdapter(
                "/workspace",
                first_supervisor,
                binding_store_path=store,
            )
            first.activate()
            first_handle = first.start_turn(chat())
            self.assertEqual("done", list(first.stream(first_handle))[-1].event_type)

            self.assertEqual(0o600, stat.S_IMODE(os.stat(store).st_mode))
            with open(store, encoding="utf-8") as source:
                payload = json.load(source)
            self.assertEqual(1, payload["schema_version"])
            self.assertEqual("grok", payload["bindings"][0]["agent_id"])
            self.assertNotIn("fixture prompt", repr(payload))

            second_supervisor = FakeGrokSupervisor()
            second_supervisor.authenticated = True
            second = GrokAgentAdapter(
                "/workspace",
                second_supervisor,
                binding_store_path=store,
            )
            second.activate()
            second_handle = second.start_turn(chat(resume=True))
            self.assertEqual("done", list(second.stream(second_handle))[-1].event_type)

            self.assertEqual([], second_supervisor.new_session_calls)
            self.assertEqual([("session-1", "/workspace")], second_supervisor.load_session_calls)
            self.assertEqual([("session-1", "fixture prompt")], second_supervisor.prompt_calls)

    def test_restored_binding_load_error_is_stage_specific_and_content_free(self):
        stale = AgentConversationBinding(
            agent_id=AgentId.GROK,
            conversation_id="conversation-one",
            backend_session_id="session-old",
            model_id="model-alpha",
            process_generation=6,
        )
        adapter = GrokAgentAdapter("/workspace", self.supervisor, (stale,))
        adapter.activate()
        self.supervisor.load_session = lambda *_: (_ for _ in ()).throw(
            AcpRemoteError(-32603, remote_category="FS_NOT_FOUND"),
        )

        with self.assertRaises(GrokAdapterError) as caught:
            adapter.start_turn(chat(resume=True))

        self.assertEqual(
            "grok_session_load_remote_n32603_fs_not_found",
            caught.exception.code,
        )
        self.assertNotIn("session-old", str(caught.exception))
        self.assertEqual([], self.supervisor.prompt_calls)

    def test_stop_is_idempotent_for_one_active_session(self):
        release = threading.Event()
        prompt_started = threading.Event()

        def blocking_prompt(session_id, text):
            self.supervisor.prompt_calls.append((session_id, text))
            prompt_started.set()
            release.wait(1.0)
            return {"stopReason": "cancelled"}

        self.supervisor.prompt = blocking_prompt
        handle = self.adapter.start_turn(chat())
        self.assertTrue(prompt_started.wait(1.0))
        self.adapter.interrupt(handle.request_id)
        self.adapter.interrupt(handle.request_id)
        self.assertEqual(["session-1"], self.supervisor.cancel_session_calls)
        # The acknowledged session/cancel owns the terminal event. Do not wait for the blocked
        # session/prompt response, which the real Grok CLI may leave open past the SSE timeout.
        events = list(self.adapter.stream(handle))
        self.assertEqual("turn_interrupted", events[-1].code)
        self.assertEqual(1, events[-1].diagnostics.prompt_dispatch_count)
        self.assertEqual(1, events[-1].diagnostics.cancel_dispatch_count)
        self.assertEqual(1, events[-1].diagnostics.terminal_count)
        release.set()


class GrokStreamPolicyTest(unittest.TestCase):
    def setUp(self):
        self.supervisor = FakeGrokSupervisor()
        self.supervisor.authenticated = True
        self.adapter = GrokAgentAdapter("/workspace", self.supervisor)
        self.adapter.activate()

    def blocking_turn(self, adapter=None, supervisor=None, stop_reason="end_turn"):
        adapter = adapter or self.adapter
        supervisor = supervisor or self.supervisor
        entered = threading.Event()
        release = threading.Event()

        def prompt(session_id, text):
            supervisor.prompt_calls.append((session_id, text))
            entered.set()
            release.wait(2.0)
            return {"stopReason": stop_reason}

        supervisor.prompt = prompt
        handle = adapter.start_turn(chat())
        self.assertTrue(entered.wait(1.0))
        return handle, release

    def test_normal_empty_late_delta_and_duplicate_terminal_are_normalized_once(self):
        handle, release = self.blocking_turn()
        self.supervisor.emit("session/update", agent_chunk("first"))
        self.supervisor.emit("session/update", agent_chunk(""))
        self.supervisor.emit("_x.ai/session/update", agent_chunk("second"))
        terminal = {
            "sessionId": "session-1",
            "promptId": "prompt-1",
            "stopReason": "end_turn",
        }
        self.supervisor.emit("_x.ai/session/prompt_complete", terminal)
        self.supervisor.emit("session/update", agent_chunk("late"))
        self.supervisor.emit("_x.ai/session/prompt_complete", terminal)
        release.set()

        events = list(self.adapter.stream(handle))
        self.assertEqual(
            ["start", "delta", "delta", "delta", "done"],
            [item.event_type for item in events],
        )
        self.assertEqual(
            ["first", "second", "late"],
            [item.text for item in events if item.event_type == "delta"],
        )
        self.assertEqual([("session-1", "fixture prompt")], self.supervisor.prompt_calls)
        metrics = self.adapter.turn_metrics()
        self.assertEqual(1, metrics.prompt_dispatch_count)
        self.assertEqual(3, metrics.visible_delta_count)
        self.assertEqual(1, metrics.terminal_count)
        self.assertEqual(0, metrics.cancel_dispatch_count)

    def test_content_arriving_after_prompt_response_is_drained_before_done(self):
        handle = self.adapter.start_turn(chat())
        time.sleep(0.05)
        self.supervisor.emit("session/update", agent_chunk("post-response"))

        events = list(self.adapter.stream(handle))

        self.assertEqual(["start", "delta", "done"], [item.event_type for item in events])
        self.assertEqual("post-response", events[1].text)
        self.assertEqual(1, events[-1].diagnostics.visible_delta_count)

    def test_large_and_malformed_content_fail_closed_without_second_prompt(self):
        for value, expected in (
            (agent_chunk("x" * (MAX_STREAM_TEXT_BYTES + 1)), "grok_stream_overflow"),
            (
                {
                    "sessionId": "session-1",
                    "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "image", "text": "discard"},
                    },
                },
                "grok_notification_invalid",
            ),
        ):
            with self.subTest(expected=expected):
                supervisor = FakeGrokSupervisor()
                supervisor.authenticated = True
                adapter = GrokAgentAdapter("/workspace", supervisor)
                adapter.activate()
                handle, release = self.blocking_turn(adapter, supervisor)
                supervisor.emit("session/update", value)
                release.set()
                events = list(adapter.stream(handle))
                self.assertEqual(["start", "error"], [item.event_type for item in events])
                self.assertEqual(expected, events[-1].code)
                self.assertEqual(1, len(supervisor.prompt_calls))
                self.assertEqual(["session-1"], supervisor.cancel_session_calls)

    def test_stop_before_start_during_delta_after_done_and_disconnect_is_idempotent(self):
        handle, release = self.blocking_turn(stop_reason="cancelled")
        self.adapter.interrupt(handle.request_id)
        self.adapter.interrupt(handle.request_id)
        self.assertEqual(["session-1"], self.supervisor.cancel_session_calls)
        release.set()
        self.assertEqual("turn_interrupted", list(self.adapter.stream(handle))[-1].code)

        supervisor = FakeGrokSupervisor()
        supervisor.authenticated = True
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        handle, release = self.blocking_turn(adapter, supervisor, stop_reason="cancelled")
        supervisor.emit("session/update", agent_chunk("partial"))
        adapter.interrupt(handle.request_id)
        adapter.interrupt(handle.request_id)
        release.set()
        events = list(adapter.stream(handle))
        self.assertEqual(["start", "delta", "error"], [item.event_type for item in events])
        self.assertEqual(["session-1"], supervisor.cancel_session_calls)

        supervisor = FakeGrokSupervisor()
        supervisor.authenticated = True
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        handle = adapter.start_turn(chat())
        self.assertEqual("done", list(adapter.stream(handle))[-1].event_type)
        adapter.interrupt(handle.request_id)
        adapter.interrupt(handle.request_id)
        self.assertEqual([], supervisor.cancel_session_calls)

        supervisor = FakeGrokSupervisor()
        supervisor.authenticated = True
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        handle, release = self.blocking_turn(adapter, supervisor)
        stream = adapter.stream(handle)
        self.assertEqual("start", next(stream).event_type)
        stream.close()
        self.assertEqual(["session-1"], supervisor.cancel_session_calls)
        self.assertFalse(adapter.activity().active_turn)
        release.set()

    def test_pre_output_retry_is_observed_without_gateway_replay(self):
        handle, release = self.blocking_turn()
        private_reason = "private upstream detail"
        self.supervisor.emit(
            "_x.ai/session_notification",
            retry_state(
                {
                    "type": "retrying",
                    "attempt": 1,
                    "max_retries": 3,
                    "reason": private_reason,
                }
            ),
        )
        self.supervisor.emit("session/update", agent_chunk("visible"))
        release.set()
        events = list(self.adapter.stream(handle))
        self.assertEqual(["start", "delta", "done"], [item.event_type for item in events])
        self.assertEqual(1, len(self.supervisor.prompt_calls))
        metrics = self.adapter.turn_metrics()
        self.assertEqual("pre_output", metrics.retry_classification)
        self.assertEqual(1, metrics.retry_attempts)
        self.assertEqual(3, metrics.retry_max)
        self.assertNotIn(private_reason, repr(metrics))

    def test_terminal_event_exposes_only_redacted_turn_and_profile_counts(self):
        handle, release = self.blocking_turn()
        self.supervisor.emit("session/update", agent_chunk("secret-output-fragment"))
        release.set()
        events = list(self.adapter.stream(handle))
        diagnostics = events[-1].diagnostics
        self.assertIsNotNone(diagnostics)
        self.assertEqual(1, diagnostics.prompt_dispatch_count)
        self.assertEqual(1, diagnostics.visible_delta_count)
        self.assertEqual(1, diagnostics.terminal_count)
        self.assertEqual(0, diagnostics.cancel_dispatch_count)
        self.assertEqual("none", diagnostics.retry_classification)
        self.assertEqual(
            (0, 0, 0, 0, 0),
            (
                diagnostics.tool_event_count,
                diagnostics.subagent_event_count,
                diagnostics.mcp_event_count,
                diagnostics.filesystem_event_count,
                diagnostics.terminal_event_count,
            ),
        )
        rendered = repr(diagnostics)
        for forbidden in (
            "session-1",
            "conversation-one",
            "fixture prompt",
            "secret-output-fragment",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_dirty_profile_stops_before_prompt_dispatch(self):
        self.supervisor.profile_audit_value = GrokProfileAudit(tool_event_count=1)
        with self.assertRaises(GrokAdapterError) as error:
            self.adapter.start_turn(chat())
        self.assertEqual("grok_chat_profile_violation", error.exception.code)
        self.assertEqual([], self.supervisor.prompt_calls)

    def test_post_output_retry_auth_failure_exhaustion_and_strict_policy_fail_stably(self):
        cases = (
            (
                GrokRetryPolicy.ALLOW_PRE_OUTPUT,
                True,
                {"type": "retrying", "attempt": 1, "max_retries": 3, "reason": "private"},
                "grok_retry_after_output",
                "post_output",
            ),
            (
                GrokRetryPolicy.ALLOW_PRE_OUTPUT,
                False,
                {"type": "failed", "error_type": "auth", "message": "private 401 detail"},
                "grok_auth_recovery_failed",
                "auth_failed",
            ),
            (
                GrokRetryPolicy.ALLOW_PRE_OUTPUT,
                False,
                {"type": "exhausted", "attempts": 4, "reason": "private", "is_rate_limited": False},
                "grok_retry_exhausted",
                "exhausted",
            ),
            (
                GrokRetryPolicy.STRICT,
                False,
                {"type": "retrying", "attempt": 1, "max_retries": 3, "reason": "private"},
                "grok_cli_retry_forbidden",
                "strict_blocked",
            ),
        )
        for policy, partial, update, expected_code, classification in cases:
            with self.subTest(expected_code=expected_code):
                supervisor = FakeGrokSupervisor()
                supervisor.authenticated = True
                adapter = GrokAgentAdapter("/workspace", supervisor, retry_policy=policy)
                adapter.activate()
                handle, release = self.blocking_turn(adapter, supervisor)
                if partial:
                    supervisor.emit("session/update", agent_chunk("partial"))
                supervisor.emit("_x.ai/session_notification", retry_state(update))
                release.set()
                events = list(adapter.stream(handle))
                self.assertEqual(expected_code, events[-1].code)
                self.assertEqual(1, sum(item.event_type == "error" for item in events))
                self.assertEqual(1, len(supervisor.prompt_calls))
                self.assertEqual(["session-1"], supervisor.cancel_session_calls)
                self.assertEqual(classification, adapter.turn_metrics().retry_classification)

    def test_malformed_retry_process_loss_and_prompt_timeout_never_replay(self):
        handle, release = self.blocking_turn()
        self.supervisor.emit(
            "_x.ai/session_notification",
            retry_state({"type": "retrying", "attempt": 1, "maxRetries": 3, "reason": "wrong key"}),
        )
        release.set()
        events = list(self.adapter.stream(handle))
        self.assertEqual("grok_notification_invalid", events[-1].code)
        self.assertEqual(1, len(self.supervisor.prompt_calls))

        supervisor = FakeGrokSupervisor()
        supervisor.authenticated = True
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()
        handle, release = self.blocking_turn(adapter, supervisor)
        supervisor.state = GrokSupervisorState.FAILED
        events = list(adapter.stream(handle))
        self.assertEqual("grok_process_lost", events[-1].code)
        release.set()
        self.assertEqual(1, len(supervisor.prompt_calls))

        supervisor = FakeGrokSupervisor()
        supervisor.authenticated = True
        adapter = GrokAgentAdapter("/workspace", supervisor)
        adapter.activate()

        def timeout_prompt(session_id, text):
            supervisor.prompt_calls.append((session_id, text))
            raise TimeoutError

        supervisor.prompt = timeout_prompt
        handle = adapter.start_turn(chat())
        events = list(adapter.stream(handle))
        self.assertEqual("grok_turn_failed", events[-1].code)
        time.sleep(0.02)
        self.assertEqual(1, len(supervisor.prompt_calls))


if __name__ == "__main__":
    unittest.main()
