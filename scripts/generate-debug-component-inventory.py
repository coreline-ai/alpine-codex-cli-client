#!/usr/bin/env python3
"""Generates a deterministic debug-only component/license/SBOM inventory asset."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_catalog(path: Path) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    section = ""
    versions: dict[str, str] = {}
    libraries: dict[str, dict[str, str]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "versions":
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"', line)
            if match:
                versions[match.group(1)] = match.group(2)
        elif section == "libraries":
            match = re.fullmatch(
                r'([A-Za-z0-9_.-]+)\s*=\s*\{\s*module\s*=\s*"([^"]+)"(?:,\s*version(?:\.ref)?\s*=\s*"([^"]+)")?\s*\}',
                line,
            )
            if match:
                alias, module, version = match.groups()
                libraries[alias] = {"module": module, "version": version or ""}
    if not versions or not libraries:
        raise SystemExit("version catalog contains no supported versions or libraries")
    return versions, libraries


def resolved_version(value: str, versions: dict[str, str]) -> str:
    if value in versions:
        return versions[value]
    if value:
        return value
    return "managed-by-compose-bom"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def generate(project_root: Path, output: Path) -> None:
    catalog_path = project_root / "gradle/libs.versions.toml"
    lock_path = project_root / "codex-cli-pack/codex-cli.lock.json"
    grok_lock_path = project_root / "grok-cli-pack/grok-cli.lock.json"
    runtime_sbom_path = project_root / "alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json"
    versions, libraries = read_catalog(catalog_path)
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    grok_lock = json.loads(grok_lock_path.read_text(encoding="utf-8"))

    components: list[dict[str, str]] = [
        {
            "kind": "official-cli",
            "name": "Codex CLI",
            "version": lock["version"],
            "license": "NOASSERTION; see docs/codex-cli-notice.md",
            "source": lock["source_url"],
            "scope": "generated-debug-asset",
        },
        {
            "kind": "bundled-runtime",
            "name": "Alpine Runtime",
            "version": "3.21.3",
            "license": "SPDX-2.3; see bundled runtime SBOM",
            "source": "alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json",
            "scope": "generated-debug-asset",
        },
        {
            "kind": "official-cli",
            "name": "Grok CLI",
            "version": grok_lock["version"],
            "license": "Apache-2.0; see docs/grok-cli-notice.md",
            "source": grok_lock["source_url"],
            "scope": "generated-debug-asset",
        },
        {
            "kind": "workspace-source",
            "name": "Codex app-server Gateway",
            "version": "workspace",
            "license": "NOASSERTION",
            "source": "codex_gateway",
            "scope": "generated-debug-asset",
        },
    ]
    for alias, dependency in sorted(libraries.items()):
        module = dependency["module"]
        components.append(
            {
                "kind": "maven-direct",
                "name": module,
                "version": resolved_version(dependency["version"], versions),
                "license": "Apache-2.0",
                "source": f"gradle/libs.versions.toml#{alias}",
                "scope": "debug-apk-or-instrumentation",
            }
        )
    payload = {
        "format": "alpine-codex-debug-component-inventory/v1",
        "scope": "debug-only",
        "components": sorted(components, key=lambda item: (item["kind"], item["name"])),
        "sboms": [
            {
                "format": "SPDX-2.3",
                "path": "META-INF/alpine-runtime/sbom.spdx.json",
                "sha256": sha256(runtime_sbom_path),
            }
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    args = parse_args()
    generate(args.project_root.resolve(), args.output.resolve())
