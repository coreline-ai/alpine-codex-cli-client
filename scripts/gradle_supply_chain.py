#!/usr/bin/env python3
"""Dependency-lock and cache-isolation checks for the Android build."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path


MODULE_PATTERN = re.compile(r'include\("(?P<module>:[A-Za-z0-9_.-]+)"\)')
COORDINATE_PATTERN = re.compile(r"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:(?P<version>[^=\s]+)")
LITERAL_DEPENDENCY_PATTERN = re.compile(
    r"(?:api|implementation|testImplementation|androidTestImplementation|"
    r"debugImplementation|runtimeOnly|compileOnly)\(\s*(?:platform\()?\s*"
    r'"[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:(?P<version>[^"]+)"'
)
ANDROID_RELEASE_LOCK_MODULES = (
    "alpine-runtime-android",
    "alpine-runtime-background-android",
    "alpine-runtime-pack-bundled",
    "alpine-python-pack-bundled",
    "alpine-runtime-ui-compose",
    "alpine-workspace-android",
    "app",
    "codex-cli-pack",
    "codex-gateway-pack-bundled",
    "grok-cli-pack",
)


class GradleSupplyChainError(ValueError):
    """Raised when pinned Gradle build policy drifts."""


def is_dynamic_version(version: str) -> bool:
    normalized = version.strip().lower()
    return (
        not normalized
        or "+" in normalized
        or "snapshot" in normalized
        or normalized.startswith("latest.")
        or normalized.startswith("[")
        or normalized.startswith("(")
        or normalized.endswith("]")
        or normalized.endswith(")")
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise GradleSupplyChainError(f"cannot read {path.name}") from error


def _properties(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in _read(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise GradleSupplyChainError(f"malformed property in {path.name}")
        key, value = line.split("=", 1)
        if key in result:
            raise GradleSupplyChainError(f"duplicate property in {path.name}")
        result[key] = value
    return result


def _module_lock_paths(project_root: Path, settings: str) -> tuple[Path, ...]:
    modules = sorted(match.group("module")[1:] for match in MODULE_PATTERN.finditer(settings))
    if not modules or len(modules) != len(set(modules)):
        raise GradleSupplyChainError("Gradle module declarations are missing or duplicated")
    return tuple(project_root / module / "gradle.lockfile" for module in modules)


def _verify_lock(path: Path) -> set[str]:
    if not path.is_file():
        raise GradleSupplyChainError(f"dependency lock is missing: {path.name}")
    entries: list[str] = []
    components: set[str] = set()
    for raw_line in _read(path).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise GradleSupplyChainError("dependency lock contains a malformed entry")
        coordinate, configurations = line.split("=", 1)
        if not configurations or any(character.isspace() for character in configurations):
            raise GradleSupplyChainError("dependency lock configuration list is invalid")
        if coordinate != "empty":
            match = COORDINATE_PATTERN.fullmatch(coordinate)
            if match is None or is_dynamic_version(match.group("version")):
                raise GradleSupplyChainError("dependency lock coordinate is not pinned")
            components.add(coordinate)
        entries.append(line)
    dependency_entries = [entry for entry in entries if not entry.startswith("empty=")]
    empty_entries = [entry for entry in entries if entry.startswith("empty=")]
    if (
        not entries
        or dependency_entries != sorted(dependency_entries)
        or len(empty_entries) > 1
        or entries != dependency_entries + empty_entries
    ):
        raise GradleSupplyChainError("dependency lock entries are empty or non-deterministic")
    return components


def verify_project(project_root: Path) -> dict[str, int | bool]:
    project_root = project_root.resolve()
    settings = _read(project_root / "settings.gradle.kts")
    if "RepositoriesMode.FAIL_ON_PROJECT_REPOS" not in settings:
        raise GradleSupplyChainError("project repositories are not centrally restricted")
    for required in ("google()", "mavenCentral()", "gradlePluginPortal()"):
        if required not in settings:
            raise GradleSupplyChainError("approved Gradle repository set is incomplete")
    if re.search(r"\bmaven\s*\{", settings):
        raise GradleSupplyChainError("custom Maven repository is not allowed")

    root_build = _read(project_root / "build.gradle.kts")
    if "lockAllConfigurations()" not in root_build:
        raise GradleSupplyChainError("dependency locking is not enabled for all projects")

    properties = _properties(project_root / "gradle.properties")
    for key in ("org.gradle.caching", "kotlin.caching.enabled", "kotlin.incremental"):
        if properties.get(key) != "false":
            raise GradleSupplyChainError(f"unsafe build cache setting: {key}")

    wrapper = _properties(project_root / "gradle/wrapper/gradle-wrapper.properties")
    if wrapper.get("distributionUrl") != (
        "https\\://services.gradle.org/distributions/gradle-8.11.1-bin.zip"
    ):
        raise GradleSupplyChainError("Gradle wrapper distribution URL drift")
    if wrapper.get("validateDistributionUrl") != "true":
        raise GradleSupplyChainError("Gradle wrapper URL validation is disabled")

    try:
        catalog = tomllib.loads(_read(project_root / "gradle/libs.versions.toml"))
    except tomllib.TOMLDecodeError as error:
        raise GradleSupplyChainError("Gradle version catalog is malformed") from error
    versions = catalog.get("versions")
    if not isinstance(versions, dict) or not versions:
        raise GradleSupplyChainError("Gradle version catalog is missing")
    for version in versions.values():
        if not isinstance(version, str) or is_dynamic_version(version):
            raise GradleSupplyChainError("Gradle version catalog contains a dynamic version")

    for build_file in sorted(project_root.glob("**/build.gradle.kts")):
        if any(part in {"build", ".gradle"} for part in build_file.parts):
            continue
        for match in LITERAL_DEPENDENCY_PATTERN.finditer(_read(build_file)):
            if is_dynamic_version(match.group("version")):
                raise GradleSupplyChainError("Gradle build contains a dynamic dependency")

    lock_paths = _module_lock_paths(project_root, settings)
    settings_lock = project_root / "settings-gradle.lockfile"
    components: set[str] = set()
    for lock_path in (*lock_paths, settings_lock):
        components.update(_verify_lock(lock_path))
    for module in ANDROID_RELEASE_LOCK_MODULES:
        lock_text = _read(project_root / module / "gradle.lockfile")
        for configuration in ("releaseCompileClasspath", "releaseRuntimeClasspath"):
            if configuration not in lock_text:
                raise GradleSupplyChainError(
                    f"public release dependency lock is missing: {module}/{configuration}"
                )

    return {
        "module_lock_count": len(lock_paths),
        "locked_component_count": len(components),
        "build_cache_disabled": True,
        "release_configurations_locked": True,
        "wrapper_checksum_pinned": "distributionSha256Sum" in wrapper,
        "dependency_verification_present": (
            project_root / "gradle/verification-metadata.xml"
        ).is_file(),
    }
