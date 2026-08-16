#!/usr/bin/env python3
"""Privacy-safe, app-level Android QA harness.

This tool drives only launcher/system lifecycle operations. It never reads app-private files,
credentials, account/model values, prompts, responses, screenshots, or browser content. UI XML is
reduced in memory to an allowlisted set of fixed control booleans and deleted immediately.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Iterable
import xml.etree.ElementTree as ET


SCHEMA = "alpine-app-real-use-qa/v1"
ALLOWED_PACKAGES = {
    "dev.alpine.codexclient.labdebug",
    "dev.alpine.codexclient.debug",
    "dev.alpine.codexclient",
}
EXPECTED_DEBUGGABLE = {
    "dev.alpine.codexclient.labdebug": True,
    "dev.alpine.codexclient.debug": False,
    "dev.alpine.codexclient": False,
}
MAIN_ACTIVITY = "dev.alpine.codexclient.MainActivity"
SAFE_SERIAL = re.compile(r"[A-Za-z0-9._:-]{4,128}")
SAFE_MODEL = re.compile(r"[A-Za-z0-9._-]{2,128}")
PACKAGE_RE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,8}")
APK_PATH_RE = re.compile(r"/[A-Za-z0-9._+=@~/-]{1,1024}/base\.apk")

# Exact fixed product labels only. Unknown UI strings are neither returned nor hashed.
UI_LABELS: dict[str, frozenset[str]] = {
    "agent_selector": frozenset({"AGENT"}),
    "model_selector": frozenset({"MODEL"}),
    "agent_codex": frozenset({"Codex"}),
    "agent_grok": frozenset({"Grok"}),
    "agent_sheet": frozenset({"Agent 선택"}),
    "model_sheet": frozenset({"Codex 모델", "Grok 모델"}),
    "runtime_status": frozenset({"상태 · Runtime", "Runtime", "Runtime 상태"}),
    "login_action": frozenset({"로그인", "Codex 로그인", "Grok 로그인"}),
    "login_cancel": frozenset({"현재 로그인 취소", "로그인 취소"}),
    "send_action": frozenset({"전송", "보내기", "Send"}),
    "stop_action": frozenset({"Stop", "중지"}),
    "new_conversation": frozenset({"새 대화", "New conversation"}),
}


class QaError(RuntimeError):
    """Stable app-QA failure without captured command output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Adb:
    def __init__(self, executable: str, serial: str) -> None:
        self.executable = executable
        self.serial = serial

    def run(
        self,
        *args: str,
        timeout: float = 30.0,
        binary: bool = False,
        check: bool = True,
    ) -> str | bytes:
        try:
            result = subprocess.run(
                [self.executable, "-s", self.serial, *args],
                check=False,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise QaError("ADB_COMMAND_FAILED") from error
        if check and result.returncode != 0:
            raise QaError("ADB_COMMAND_FAILED")
        if binary:
            return result.stdout
        return result.stdout.decode("utf-8", errors="replace")

    def shell(self, *args: str, timeout: float = 30.0, check: bool = True) -> str:
        return str(self.run("shell", *args, timeout=timeout, check=check))


def validate_args(args: argparse.Namespace) -> None:
    if not SAFE_SERIAL.fullmatch(args.serial):
        raise QaError("INVALID_SERIAL")
    if args.package not in ALLOWED_PACKAGES or not PACKAGE_RE.fullmatch(args.package):
        raise QaError("INVALID_PACKAGE")
    if not SAFE_MODEL.fullmatch(args.expected_model):
        raise QaError("INVALID_MODEL")
    if args.scenario != "baseline" and not args.confirm_app_control:
        raise QaError("APP_CONTROL_CONFIRMATION_REQUIRED")
    if not 1 <= args.wait_seconds <= 120:
        raise QaError("INVALID_WAIT")


def connected_serials(adb_path: str) -> dict[str, str]:
    try:
        result = subprocess.run(
            [adb_path, "devices", "-l"],
            check=False,
            capture_output=True,
            timeout=15,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise QaError("ADB_ENUMERATION_FAILED") from error
    if result.returncode != 0:
        raise QaError("ADB_ENUMERATION_FAILED")
    devices: dict[str, str] = {}
    for line in result.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2 and SAFE_SERIAL.fullmatch(fields[0]):
            devices[fields[0]] = fields[1]
    return devices


def parse_package_dump(text: str) -> dict[str, Any]:
    version_code = re.search(r"\bversionCode=(\d+)", text)
    version_name = re.search(r"\bversionName=([^\s]+)", text)
    flags = " ".join(re.findall(r"\b(?:pkgFlags|flags)=\[([^\]]*)\]", text))
    if not version_code or not version_name:
        raise QaError("PACKAGE_METADATA_MISSING")
    return {
        "version_code": int(version_code.group(1)),
        "version_name": version_name.group(1)[:128],
        "debuggable": "DEBUGGABLE" in flags.split(),
    }


def parse_process_counts(text: str, package: str) -> dict[str, int]:
    counts = {"app": 0, "proot": 0, "python_gateway": 0, "codex": 0, "grok": 0}
    rows: list[tuple[int, int, str]] = []
    for raw in text.splitlines():
        fields = raw.split()
        if len(fields) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        rows.append((int(fields[0]), int(fields[1]), fields[2]))
    app_pids = {pid for pid, _, name in rows if name == package or name.startswith(f"{package}:")}
    descendants = set(app_pids)
    while True:
        discovered = {pid for pid, ppid, _ in rows if ppid in descendants}
        if discovered.issubset(descendants):
            break
        descendants.update(discovered)
    for pid, _, name in rows:
        if pid not in descendants:
            continue
        lowered = name.lower()
        if pid in app_pids:
            counts["app"] += 1
        elif lowered in {"libproot.so", "proot"}:
            counts["proot"] += 1
        elif lowered in {"python", "python3"}:
            counts["python_gateway"] += 1
        elif lowered in {"codex", "libcodex_app_server.so"}:
            counts["codex"] += 1
        elif lowered == "grok":
            counts["grok"] += 1
    return counts


def count_tcp_8787(texts: Iterable[str]) -> int:
    count = 0
    for text in texts:
        for line in text.splitlines()[1:]:
            fields = line.split()
            if len(fields) >= 4 and fields[1].upper().endswith(":2253") and fields[3] == "0A":
                count += 1
    return count


def redact_ui_xml(payload: bytes) -> dict[str, bool]:
    found = {name: False for name in UI_LABELS}
    if len(payload) > 4 * 1024 * 1024:
        raise QaError("UI_HIERARCHY_OVERSIZED")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise QaError("UI_HIERARCHY_INVALID") from error
    for node in root.iter():
        values = {
            node.attrib.get("text", ""),
            node.attrib.get("content-desc", ""),
        }
        for name, allowlist in UI_LABELS.items():
            if not found[name] and values.intersection(allowlist):
                found[name] = True
    return found


def process_counts(adb: Adb, package: str) -> dict[str, int]:
    return parse_process_counts(adb.shell("ps", "-A", "-o", "PID,PPID,NAME"), package)


def audit_count(adb: Adb) -> int:
    output = str(
        adb.run(
            "logcat",
            "-d",
            "-s",
            "AgentTurnAudit:I",
            "*:S",
            timeout=20,
            check=False,
        )
    )
    return sum(1 for line in output.splitlines() if "AgentTurnAudit" in line)


def tcp_listener_count(adb: Adb) -> int:
    tcp = adb.shell("cat", "/proc/net/tcp", check=False)
    tcp6 = adb.shell("cat", "/proc/net/tcp6", check=False)
    return count_tcp_8787((tcp, tcp6))


def ui_payload(adb: Adb, package: str) -> bytes:
    token = hashlib.sha256(f"{adb.serial}:{package}".encode()).hexdigest()[:12]
    remote = f"/data/local/tmp/alpine-app-qa-{token}.xml"
    try:
        adb.shell("uiautomator", "dump", "--compressed", remote, timeout=20)
        payload = adb.run("exec-out", "cat", remote, timeout=20, binary=True)
        return bytes(payload)
    finally:
        adb.shell("rm", "-f", remote, timeout=10, check=False)


def ui_probes(adb: Adb, package: str) -> dict[str, bool]:
    return redact_ui_xml(ui_payload(adb, package))


def exact_label_center(payload: bytes, labels: frozenset[str]) -> tuple[int, int] | None:
    if len(payload) > 4 * 1024 * 1024:
        raise QaError("UI_HIERARCHY_OVERSIZED")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise QaError("UI_HIERARCHY_INVALID") from error
    for node in root.iter():
        if node.attrib.get("text", "") not in labels and node.attrib.get("content-desc", "") not in labels:
            continue
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
        if match is None:
            continue
        left, top, right, bottom = (int(value) for value in match.groups())
        if right > left and bottom > top:
            return ((left + right) // 2, (top + bottom) // 2)
    return None


def wait_for_label_center(
    adb: Adb,
    package: str,
    labels: frozenset[str],
    seconds: int = 10,
) -> tuple[int, int] | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            center = exact_label_center(ui_payload(adb, package), labels)
        except QaError:
            center = None
        if center is not None:
            return center
        time.sleep(0.5)
    return None


def dismiss_sheet(adb: Adb, package: str) -> bool:
    for _ in range(2):
        adb.shell("input", "keyevent", "KEYCODE_BACK")
        if wait_for_shell(adb, package, 5)["ready"]:
            return True
    return False


def resumed(adb: Adb, package: str) -> bool:
    output = adb.shell("dumpsys", "activity", "activities", timeout=20)
    return bool(
        re.search(
            rf"(?:mResumedActivity|topResumedActivity).*{re.escape(package)}/{re.escape(MAIN_ACTIVITY)}",
            output,
        )
    )


def launch(adb: Adb, package: str) -> dict[str, Any]:
    started = time.monotonic()
    output = adb.shell(
        "am",
        "start",
        "-W",
        "-n",
        f"{package}/{MAIN_ACTIVITY}",
        timeout=45,
    )
    status = re.search(r"^Status:\s*(\S+)", output, re.MULTILINE)
    total = re.search(r"^TotalTime:\s*(\d+)", output, re.MULTILINE)
    return {
        "status_ok": bool(status and status.group(1) == "ok"),
        "total_time_ms": int(total.group(1)) if total else round((time.monotonic() - started) * 1000),
    }


def wait_for_shell(adb: Adb, package: str, seconds: int) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    last: dict[str, bool] = {name: False for name in UI_LABELS}
    while time.monotonic() < deadline:
        if resumed(adb, package):
            try:
                last = ui_probes(adb, package)
            except QaError:
                last = {name: False for name in UI_LABELS}
            if last["agent_selector"] and last["model_selector"] and last["send_action"]:
                return {"ready": True, "ui": last}
        time.sleep(1)
    return {"ready": False, "ui": last}


def selected_agent_role(counts: dict[str, int]) -> str | None:
    if counts["grok"] == 1 and counts["codex"] == 0:
        return "grok"
    if counts["codex"] == 1 and counts["grok"] == 0:
        return "codex"
    return None


def wait_for_backend(
    adb: Adb,
    package: str,
    expected_agent: str | None,
    seconds: int,
) -> tuple[bool, dict[str, int]]:
    deadline = time.monotonic() + seconds
    last = process_counts(adb, package)
    while time.monotonic() < deadline:
        last = process_counts(adb, package)
        core_ready = last["app"] == 1 and last["proot"] == 1 and last["python_gateway"] == 1
        agent_ready = expected_agent is None or last[expected_agent] == 1
        if core_ready and agent_ready:
            return True, last
        time.sleep(1)
    return False, last


def installed_apk_sha256(adb: Adb, package: str) -> str | None:
    output = adb.shell("pm", "path", package)
    paths = [line.removeprefix("package:").strip() for line in output.splitlines()]
    base = next((path for path in paths if APK_PATH_RE.fullmatch(path)), None)
    if base is None:
        return None
    result = adb.shell("sha256sum", base, timeout=120, check=False)
    match = re.match(r"([0-9a-f]{64})\s", result)
    return match.group(1) if match else None


def baseline(adb: Adb, args: argparse.Namespace) -> dict[str, Any]:
    model = adb.shell("getprop", "ro.product.model").strip()
    abi = adb.shell("getprop", "ro.product.cpu.abi").strip()
    sdk_raw = adb.shell("getprop", "ro.build.version.sdk").strip()
    if model != args.expected_model:
        raise QaError("MODEL_MISMATCH")
    if abi != args.expected_abi:
        raise QaError("ABI_MISMATCH")
    if not sdk_raw.isdigit():
        raise QaError("SDK_INVALID")
    package_dump = adb.shell("dumpsys", "package", args.package, timeout=45)
    meta = parse_package_dump(package_dump)
    return {
        "device": {"model": model, "abi": abi, "sdk": int(sdk_raw)},
        "package": {"id": args.package, **meta, "apk_sha256": installed_apk_sha256(adb, args.package)},
        "resumed": resumed(adb, args.package),
        "processes": process_counts(adb, args.package),
        "tcp_8787_listeners": tcp_listener_count(adb),
        "audit_count": audit_count(adb),
    }


def run_launch(adb: Adb, args: argparse.Namespace) -> dict[str, Any]:
    before_processes = process_counts(adb, args.package)
    expected_agent = selected_agent_role(before_processes)
    before_audits = audit_count(adb)
    started = time.monotonic()
    launch_result = launch(adb, args.package)
    shell = wait_for_shell(adb, args.package, args.wait_seconds)
    shell_ready_ms = round((time.monotonic() - started) * 1000)
    backend_ready, final_processes = wait_for_backend(
        adb,
        args.package,
        expected_agent,
        args.wait_seconds,
    )
    return {
        "launch": launch_result,
        "expected_selected_agent": expected_agent or "none",
        "shell_ready_time_ms": shell_ready_ms,
        "backend_ready_time_ms": round((time.monotonic() - started) * 1000),
        "backend_recovered": backend_ready,
        "shell": shell,
        "resumed": resumed(adb, args.package),
        "processes": final_processes,
        "tcp_8787_listeners": tcp_listener_count(adb),
        "automatic_turn_audit_delta": max(0, audit_count(adb) - before_audits),
    }


def run_background_resume(adb: Adb, args: argparse.Namespace) -> dict[str, Any]:
    initial = run_launch(adb, args)
    before_audits = audit_count(adb)
    adb.shell("input", "keyevent", "KEYCODE_HOME")
    time.sleep(2)
    background_resumed = resumed(adb, args.package)
    relaunched = launch(adb, args.package)
    shell = wait_for_shell(adb, args.package, args.wait_seconds)
    return {
        "initial_ready": bool(initial["shell"]["ready"]),
        "background_removed_focus": not background_resumed,
        "resume_launch": relaunched,
        "shell": shell,
        "processes": process_counts(adb, args.package),
        "automatic_turn_audit_delta": max(0, audit_count(adb) - before_audits),
    }


def run_force_stop_relaunch(adb: Adb, args: argparse.Namespace) -> dict[str, Any]:
    initial = run_launch(adb, args)
    expected_agent = selected_agent_role(initial["processes"])
    before_audits = audit_count(adb)
    adb.shell("am", "force-stop", args.package)
    time.sleep(2)
    stopped = process_counts(adb, args.package)
    started = time.monotonic()
    relaunched = launch(adb, args.package)
    shell = wait_for_shell(adb, args.package, args.wait_seconds)
    shell_recovery_ms = round((time.monotonic() - started) * 1000)
    backend_ready, final_processes = wait_for_backend(
        adb,
        args.package,
        expected_agent,
        args.wait_seconds,
    )
    return {
        "expected_selected_agent": expected_agent or "none",
        "stopped_processes": stopped,
        "resume_launch": relaunched,
        "shell_recovery_time_ms": shell_recovery_ms,
        "backend_recovery_time_ms": round((time.monotonic() - started) * 1000),
        "backend_recovered": backend_ready,
        "shell": shell,
        "processes": final_processes,
        "tcp_8787_listeners": tcp_listener_count(adb),
        "automatic_turn_audit_delta": max(0, audit_count(adb) - before_audits),
    }


def run_selector_probe(adb: Adb, args: argparse.Namespace) -> dict[str, Any]:
    initial = run_launch(adb, args)
    before_audits = audit_count(adb)
    initial_processes = initial["processes"]

    agent_center = wait_for_label_center(adb, args.package, UI_LABELS["agent_selector"])
    if agent_center is None:
        raise QaError("AGENT_SELECTOR_NOT_FOUND")
    adb.shell("input", "tap", str(agent_center[0]), str(agent_center[1]))
    time.sleep(1)
    agent_sheet = ui_probes(adb, args.package)
    if not dismiss_sheet(adb, args.package):
        raise QaError("AGENT_SHEET_DISMISS_FAILED")

    model_center = wait_for_label_center(adb, args.package, UI_LABELS["model_selector"])
    if model_center is None:
        raise QaError("MODEL_SELECTOR_NOT_FOUND")
    adb.shell("input", "tap", str(model_center[0]), str(model_center[1]))
    time.sleep(1)
    model_sheet = ui_probes(adb, args.package)
    if not dismiss_sheet(adb, args.package):
        raise QaError("MODEL_SHEET_DISMISS_FAILED")
    shell = wait_for_shell(adb, args.package, args.wait_seconds)
    final_processes = process_counts(adb, args.package)
    return {
        "agent_sheet_opened": bool(
            agent_sheet["agent_sheet"] and agent_sheet["agent_codex"] and agent_sheet["agent_grok"]
        ),
        "model_sheet_opened": model_sheet["model_sheet"],
        "shell": shell,
        "processes_unchanged": final_processes == initial_processes,
        "processes": final_processes,
        "tcp_8787_listeners": tcp_listener_count(adb),
        "automatic_turn_audit_delta": max(0, audit_count(adb) - before_audits),
    }


def safe_result(args: argparse.Namespace, adb: Adb) -> dict[str, Any]:
    devices = connected_serials(adb.executable)
    if devices.get(args.serial) != "device":
        raise QaError("TARGET_NOT_ONLINE")
    baseline_result = baseline(adb, args)
    if baseline_result["package"]["debuggable"] != EXPECTED_DEBUGGABLE[args.package]:
        raise QaError("VARIANT_DEBUGGABLE_MISMATCH")
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "scenario": args.scenario,
        "target": {"serial_alias": "SAMSUNG_TARGET", "connected_device_count": len(devices)},
        "baseline": baseline_result,
    }
    if args.scenario == "launch":
        result["result"] = run_launch(adb, args)
    elif args.scenario == "background-resume":
        result["result"] = run_background_resume(adb, args)
    elif args.scenario == "force-stop-relaunch":
        result["result"] = run_force_stop_relaunch(adb, args)
    elif args.scenario == "selector-probe":
        result["result"] = run_selector_probe(adb, args)
    elif args.scenario == "smoke":
        result["result"] = {
            "launch": run_launch(adb, args),
            "background_resume": run_background_resume(adb, args),
            "force_stop_relaunch": run_force_stop_relaunch(adb, args),
        }
    result["passed"] = evaluate(result)
    return result


def evaluate(result: dict[str, Any]) -> bool:
    base = result["baseline"]
    package_id = base["package"].get("id")
    if (
        package_id not in EXPECTED_DEBUGGABLE
        or base["package"]["debuggable"] != EXPECTED_DEBUGGABLE[package_id]
        or base["tcp_8787_listeners"] != 0
    ):
        return False
    scenario = result["scenario"]
    if scenario == "baseline":
        return True
    checks = result["result"]
    if scenario == "smoke":
        return all(
            (
                checks["launch"]["launch"]["status_ok"],
                checks["launch"]["shell"]["ready"],
                checks["launch"]["backend_recovered"],
                checks["launch"]["automatic_turn_audit_delta"] == 0,
                checks["background_resume"]["background_removed_focus"],
                checks["background_resume"]["shell"]["ready"],
                checks["background_resume"]["automatic_turn_audit_delta"] == 0,
                checks["force_stop_relaunch"]["stopped_processes"]["app"] == 0,
                checks["force_stop_relaunch"]["shell"]["ready"],
                checks["force_stop_relaunch"]["backend_recovered"],
                checks["force_stop_relaunch"]["tcp_8787_listeners"] == 0,
                checks["force_stop_relaunch"]["automatic_turn_audit_delta"] == 0,
            )
        )
    if scenario == "selector-probe":
        return bool(
            checks["agent_sheet_opened"]
            and checks["model_sheet_opened"]
            and checks["shell"]["ready"]
            and checks["processes_unchanged"]
            and checks["tcp_8787_listeners"] == 0
            and checks["automatic_turn_audit_delta"] == 0
        )
    return bool(
        checks.get("shell", {}).get("ready")
        and checks.get("backend_recovered", True)
        and checks.get("automatic_turn_audit_delta") == 0
        and checks.get("tcp_8787_listeners", 0) == 0
    )


def parser() -> argparse.ArgumentParser:
    default_adb = str(
        Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / "adb"
        if os.environ.get("ANDROID_HOME")
        else "adb"
    )
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--adb", default=default_adb)
    value.add_argument("--serial", required=True)
    value.add_argument("--package", default="dev.alpine.codexclient.debug")
    value.add_argument("--expected-model", default="SM-S931N")
    value.add_argument("--expected-abi", default="arm64-v8a")
    value.add_argument(
        "--scenario",
        choices=(
            "baseline",
            "launch",
            "background-resume",
            "force-stop-relaunch",
            "selector-probe",
            "smoke",
        ),
        default="baseline",
    )
    value.add_argument("--wait-seconds", type=int, default=45)
    value.add_argument("--confirm-app-control", action="store_true")
    value.add_argument("--output", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        validate_args(args)
        result = safe_result(args, Adb(args.adb, args.serial))
    except QaError as error:
        result = {"schema": SCHEMA, "passed": False, "error": error.code}
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    sys.stdout.write(payload)
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
