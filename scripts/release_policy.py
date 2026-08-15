#!/usr/bin/env python3
"""Static fail-closed policy checks for the public Android release route."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


class ReleasePolicyError(ValueError):
    """Raised when the public release boundary drifts."""


EXPECTED_SIGNING_ENV = (
    "ALPINE_RELEASE_STORE_FILE",
    "ALPINE_RELEASE_STORE_PASSWORD",
    "ALPINE_RELEASE_KEY_ALIAS",
    "ALPINE_RELEASE_KEY_PASSWORD",
)
PACK_BUILDS = (
    "codex-cli-pack/build.gradle.kts",
    "grok-cli-pack/build.gradle.kts",
    "codex-gateway-pack-bundled/build.gradle.kts",
    "alpine-python-pack-bundled/build.gradle.kts",
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ReleasePolicyError(f"cannot read release policy input: {path.name}") from error


def _require(text: str, token: str, label: str) -> None:
    if token not in text:
        raise ReleasePolicyError(label)


def verify_project(project_root: Path) -> dict[str, int | bool]:
    project_root = project_root.resolve()
    app_build = _read(project_root / "app/build.gradle.kts")

    for variable in EXPECTED_SIGNING_ENV:
        if app_build.count(f'environmentVariable("{variable}")') != 1:
            raise ReleasePolicyError("external release signing input contract drift")
    _require(app_build, 'getByName("release")', "release build type is not explicit")
    _require(app_build, 'isDebuggable = false', "release must be non-debuggable")
    _require(
        app_build,
        'buildConfigField("boolean", "ALLOW_REAL_OAUTH", "true")',
        "release OAuth route is not enabled",
    )
    _require(app_build, 'create("externalRelease")', "external release signing config is missing")
    _require(
        app_build,
        "Release signing requires all four ALPINE_RELEASE_* environment variables",
        "partial signing input does not fail closed",
    )
    _require(app_build, "verifyReleaseSigningInputs", "release signing task gate is missing")
    _require(
        app_build,
        "verifyReleasePythonPackagePack",
        "release Python package pack task gate is missing",
    )
    _require(
        app_build,
        ":alpine-python-pack-bundled:verifyProductionPythonPackagePack",
        "release Python package pack verification is not connected",
    )
    for task_name in (
        "assembleRelease",
        "bundleRelease",
        "packageRelease",
        "packageReleaseBundle",
        "packageReleaseUniversalApk",
        "makeApkFromBundleForRelease",
        "extractApksFromBundleForRelease",
        "signReleaseBundle",
    ):
        _require(app_build, f'"{task_name}"', f"release task is not signing-gated: {task_name}")
    if re.search(r'(?m)^\s*(?:storePassword|keyPassword)\s*=\s*"', app_build):
        raise ReleasePolicyError("release signing password is hard-coded")
    if re.search(
        r'getByName\("release"\)[\s\S]{0,800}?signingConfigs\.getByName\("debug"\)',
        app_build,
    ):
        raise ReleasePolicyError("release uses the debug certificate")
    _require(
        app_build,
        'getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/distribution/assets/audit"))',
        "release component inventory source is missing",
    )
    _require(
        app_build,
        'scripts/generate-component-inventory.py',
        "distribution component inventory generator is missing",
    )

    for relative in PACK_BUILDS:
        pack_build = _read(project_root / relative)
        _require(
            pack_build,
            'getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/distribution/assets"))',
            f"release asset source is missing: {relative}",
        )
        if 'getByName("debug").assets.srcDir' in pack_build:
            raise ReleasePolicyError(f"pack remains debug-only: {relative}")

    bootstrapper = _read(
        project_root / "app/src/main/java/dev/alpine/codexclient/GatewayPythonBootstrapper.kt"
    )
    for token, label in (
        ('executable = "/sbin/apk"', "fixed apk executable is missing"),
        ('add("--no-network")', "Python install does not disable apk networking"),
        ("guestPackagePaths", "Python install is not sourced from staged APK assets"),
        ("PythonPackagePackException", "Python package pack failures are not fail-closed"),
    ):
        _require(bootstrapper, token, label)
    if re.search(
        r'arguments\s*=\s*listOf\([\s\S]{0,400}?"add"[\s\S]{0,400}?(?:PACKAGE|"python3")',
        bootstrapper,
    ):
        raise ReleasePolicyError("online package-name bootstrap is present")
    python_pack_build = _read(project_root / "alpine-python-pack-bundled/build.gradle.kts")
    _require(
        python_pack_build,
        'environmentVariable("ALPINE_PYTHON_PACKAGE_DIR")',
        "local Python package input contract is missing",
    )
    _require(
        python_pack_build,
        "--require-production",
        "production Python package marker is not enforced",
    )
    if any(token in python_pack_build.lower() for token in ("curl ", "wget ", "http://", "https://")):
        raise ReleasePolicyError("Python package pack build contains a network download route")

    profile_root = project_root / "grok-cli-pack/src/main/assets/grok-profile"
    if not (profile_root / "chat-only.md").is_file() or not (
        profile_root / "chat-only.lock.json"
    ).is_file():
        raise ReleasePolicyError("release Grok profile is missing")
    debug_profile_root = project_root / "grok-cli-pack/src/debug/assets/grok-profile"
    if (debug_profile_root / "chat-only.md").exists() or (
        debug_profile_root / "chat-only.lock.json"
    ).exists():
        raise ReleasePolicyError("Grok profile still has a debug-only source")

    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=project_root).split(b"\0")
    signing_suffixes = (".jks", ".keystore", ".ks", ".p12", ".pfx", ".pem", ".key")
    for encoded in tracked:
        if encoded and encoded.decode("utf-8").lower().endswith(signing_suffixes):
            raise ReleasePolicyError("private signing material is tracked")

    return {
        "release_variant": True,
        "external_signing_inputs": len(EXPECTED_SIGNING_ENV),
        "distribution_asset_packs": len(PACK_BUILDS),
        "private_signing_material_tracked": False,
    }
