from __future__ import annotations

import argparse
import unittest

from scripts.app_real_use_qa import (
    QaError,
    count_tcp_8787,
    evaluate,
    exact_label_center,
    parse_package_dump,
    parse_process_counts,
    redact_ui_xml,
    selected_agent_role,
    validate_args,
)


class AppRealUseQaHarnessTest(unittest.TestCase):
    def args(self, **changes):
        values = {
            "serial": "R3CY40PXCAP",
            "package": "dev.alpine.codexclient.debug",
            "expected_model": "SM-S931N",
            "expected_abi": "arm64-v8a",
            "scenario": "baseline",
            "confirm_app_control": False,
            "wait_seconds": 45,
        }
        values.update(changes)
        return argparse.Namespace(**values)

    def test_mutating_scenario_requires_explicit_control_confirmation(self):
        with self.assertRaisesRegex(QaError, "APP_CONTROL_CONFIRMATION_REQUIRED"):
            validate_args(self.args(scenario="smoke"))
        validate_args(self.args(scenario="smoke", confirm_app_control=True))

    def test_target_and_package_allowlists_fail_closed(self):
        for args in (
            self.args(serial="bad serial"),
            self.args(package="com.example.other"),
            self.args(expected_model="bad model"),
        ):
            with self.assertRaises(QaError):
                validate_args(args)

    def test_package_metadata_is_reduced_to_fixed_fields(self):
        parsed = parse_package_dump(
            "versionCode=2 minSdk=26 targetSdk=36\n"
            "versionName=0.2.0-secure-debug\n"
            "pkgFlags=[ HAS_CODE ALLOW_CLEAR_USER_DATA ]\n"
            "account=must-not-survive"
        )
        self.assertEqual(2, parsed["version_code"])
        self.assertEqual("0.2.0-secure-debug", parsed["version_name"])
        self.assertFalse(parsed["debuggable"])
        self.assertNotIn("account", parsed)

    def test_ui_reducer_outputs_only_allowlisted_booleans(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
        <hierarchy>
          <node text="Codex" content-desc="" />
          <node text="AGENT" content-desc="" />
          <node text="MODEL" content-desc="" />
          <node text="private prompt" content-desc="private response" />
          <node text="Grok" content-desc="" />
          <node text="Stop" content-desc="" />
        </hierarchy>'''
        result = redact_ui_xml(xml)
        self.assertTrue(result["agent_codex"])
        self.assertTrue(result["agent_grok"])
        self.assertTrue(result["agent_selector"])
        self.assertTrue(result["model_selector"])
        self.assertTrue(result["stop_action"])
        self.assertNotIn("private prompt", repr(result))
        self.assertNotIn("private response", repr(result))

    def test_oversized_or_invalid_ui_dump_is_rejected(self):
        with self.assertRaisesRegex(QaError, "UI_HIERARCHY_INVALID"):
            redact_ui_xml(b"not xml")
        with self.assertRaisesRegex(QaError, "UI_HIERARCHY_OVERSIZED"):
            redact_ui_xml(b"x" * (4 * 1024 * 1024 + 1))

    def test_process_parser_returns_counts_without_process_text(self):
        parsed = parse_process_counts(
            "PID PPID NAME\n"
            "9 0 libcodex_app_server.so\n"
            "10 0 dev.alpine.integrated.debug\n"
            "11 10 libcodex_app_server.so\n"
            "1 0 dev.alpine.codexclient.debug\n"
            "2 1 libproot.so\n"
            "3 2 python3\n"
            "4 3 grok\n",
            "dev.alpine.codexclient.debug",
        )
        self.assertEqual(
            {"app": 1, "proot": 1, "python_gateway": 1, "codex": 0, "grok": 1},
            parsed,
        )

    def test_only_listening_tcp_8787_is_counted(self):
        header = "sl local_address rem_address st\n"
        tcp = header + "0: 0100007F:2253 00000000:0000 0A\n"
        connected = header + "1: 0100007F:2253 0100007F:1234 01\n"
        self.assertEqual(1, count_tcp_8787((tcp, connected)))

    def test_selected_agent_role_requires_exactly_one_backend(self):
        base = {"app": 1, "proot": 1, "python_gateway": 1, "codex": 0, "grok": 1}
        self.assertEqual("grok", selected_agent_role(base))
        self.assertEqual("codex", selected_agent_role({**base, "codex": 1, "grok": 0}))
        self.assertIsNone(selected_agent_role({**base, "codex": 1, "grok": 1}))
        self.assertIsNone(selected_agent_role({**base, "codex": 0, "grok": 0}))

    def test_ready_shell_cannot_hide_backend_recovery_failure(self):
        result = {
            "scenario": "force-stop-relaunch",
            "baseline": {
                "package": {
                    "id": "dev.alpine.codexclient.debug",
                    "debuggable": False,
                },
                "tcp_8787_listeners": 0,
            },
            "result": {
                "shell": {"ready": True},
                "backend_recovered": False,
                "automatic_turn_audit_delta": 0,
                "tcp_8787_listeners": 0,
            },
        }
        self.assertFalse(evaluate(result))

    def test_debuggability_is_matched_to_variant_instead_of_blanket_rejected(self):
        lab = {
            "scenario": "baseline",
            "baseline": {
                "package": {
                    "id": "dev.alpine.codexclient.labdebug",
                    "debuggable": True,
                },
                "tcp_8787_listeners": 0,
            },
        }
        self.assertTrue(evaluate(lab))
        lab["baseline"]["package"]["debuggable"] = False
        self.assertFalse(evaluate(lab))

    def test_exact_label_center_uses_only_allowlisted_label_and_valid_bounds(self):
        xml = b'''<hierarchy>
          <node text="private model" bounds="[0,0][100,100]" />
          <node text="MODEL" bounds="[100,200][300,400]" />
        </hierarchy>'''
        self.assertEqual((200, 300), exact_label_center(xml, frozenset({"MODEL"})))
        self.assertIsNone(exact_label_center(xml, frozenset({"AGENT"})))

    def test_selector_probe_requires_both_sheets_without_turn_or_process_change(self):
        result = {
            "scenario": "selector-probe",
            "baseline": {
                "package": {"id": "dev.alpine.codexclient.debug", "debuggable": False},
                "tcp_8787_listeners": 0,
            },
            "result": {
                "agent_sheet_opened": True,
                "model_sheet_opened": True,
                "shell": {"ready": True},
                "processes_unchanged": True,
                "tcp_8787_listeners": 0,
                "automatic_turn_audit_delta": 0,
            },
        }
        self.assertTrue(evaluate(result))
        result["result"]["model_sheet_opened"] = False
        self.assertFalse(evaluate(result))


if __name__ == "__main__":
    unittest.main()
