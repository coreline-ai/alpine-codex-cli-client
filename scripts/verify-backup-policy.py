#!/usr/bin/env python3
"""Fail closed when cloud/D2D exclusion or no-backup state routing drifts."""

from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
ANDROID = "{http://schemas.android.com/apk/res/android}"
DOMAINS = {
    "root",
    "file",
    "database",
    "sharedpref",
    "external",
    "device_root",
    "device_file",
    "device_database",
    "device_sharedpref",
}


def exclusions(path: Path) -> set[tuple[str, str]]:
    tree = ET.parse(path)
    return {
        (item.attrib.get("domain", ""), item.attrib.get("path", ""))
        for item in tree.iter("exclude")
    }


manifest = ET.parse(ROOT / "app/src/main/AndroidManifest.xml").getroot()
application = manifest.find("application")
if application is None:
    raise SystemExit("backup policy application missing")
if application.attrib.get(ANDROID + "allowBackup") != "false":
    raise SystemExit("backup policy allowBackup drift")
if application.attrib.get(ANDROID + "dataExtractionRules") != "@xml/data_extraction_rules":
    raise SystemExit("backup policy dataExtractionRules drift")
if application.attrib.get(ANDROID + "fullBackupContent") != "@xml/backup_rules":
    raise SystemExit("backup policy fullBackupContent drift")

required = {(domain, ".") for domain in DOMAINS}
data_rules = ROOT / "app/src/main/res/xml/data_extraction_rules.xml"
data_tree = ET.parse(data_rules).getroot()
for section in ("cloud-backup", "device-transfer"):
    value = data_tree.find(section)
    if value is None:
        raise SystemExit(f"backup policy {section} missing")
    found = {
        (item.attrib.get("domain", ""), item.attrib.get("path", ""))
        for item in value.findall("exclude")
    }
    if found != required:
        raise SystemExit(f"backup policy {section} exclusions drift")

if exclusions(ROOT / "app/src/main/res/xml/backup_rules.xml") != required:
    raise SystemExit("backup policy legacy exclusions drift")

migration = (ROOT / "app/src/main/java/dev/alpine/codexclient/SensitiveStateMigration.kt").read_text()
runtime = (ROOT / "alpine-runtime-android/src/main/kotlin/dev/alpine/runtime/android/internal/AndroidAlpineRuntimeManager.kt").read_text()
for token in (
    "context.noBackupFilesDir",
    "alpine-codex-home-v1",
    "alpine-grok-home-v1",
    "alpine-gateway-handoff-v1",
    "alpine-gateway-wrapped-v1",
    "alpine-conversation-state-v1",
    "StandardCopyOption.ATOMIC_MOVE",
):
    if token not in migration:
        raise SystemExit("backup policy migration contract drift")
if "appContext.noBackupFilesDir" not in runtime or "privateDirectoryBinds" not in runtime:
    raise SystemExit("backup policy Runtime bind drift")

print("backup/D2D policy: PASS")
