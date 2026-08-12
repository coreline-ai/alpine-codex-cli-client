#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
reference_repo=${ALPINE_REFERENCE_REPO:-/Volumes/ExternalSSD/projects_8/alpine-llm-gateway}
lab_apk="$project_root/app/build/outputs/apk/debug/app-debug.apk"
secure_apk="$project_root/app/build/outputs/apk/secureDebug/app-secureDebug.apk"

cd "$project_root"
export PYTHONDONTWRITEBYTECODE=1

python3 -m unittest discover -s tests -p 'test_*.py'
./gradlew \
  :alpine-runtime-api:test \
  :alpine-runtime-host:test \
  :alpine-workspace-api:test \
  :codex-runtime-bridge:test \
  :alpine-runtime-android:testDebugUnitTest \
  :alpine-runtime-background-android:testDebugUnitTest \
  :alpine-runtime-ui-compose:testDebugUnitTest \
  :alpine-runtime-pack-bundled:testDebugUnitTest \
  :alpine-workspace-android:testDebugUnitTest \
  :app:testDebugUnitTest \
  :app:lintDebug \
  :app:lintSecureDebug \
  :app:assembleDebug \
  :app:assembleDebugAndroidTest \
  :app:assembleSecureDebug \
  --offline --no-daemon --console=plain
python3 scripts/verify-codex-protocol-fixture.py --project-root "$project_root"
python3 scripts/verify-debug-clean-room.py --project-root "$project_root" --apk "$lab_apk"
python3 scripts/verify-debug-clean-room.py --project-root "$project_root" --apk "$secure_apk"
python3 scripts/verify-grok-cli-artifact.py --project-root "$project_root" --apk "$lab_apk"
python3 scripts/verify-grok-cli-artifact.py --project-root "$project_root" --apk "$secure_apk"
python3 scripts/verify-grok-profile.py --project-root "$project_root" --apk "$lab_apk"
python3 scripts/verify-grok-profile.py --project-root "$project_root" --apk "$secure_apk"
python3 scripts/verify-grok-acp-contract.py --project-root "$project_root"
sh scripts/verify-secure-debug-apk.sh "$lab_apk" "$secure_apk"
python3 scripts/verify-sensitive-evidence.py \
  docs/samsung-debug-e2e-evidence.md \
  docs/grok-phase8-security-evidence.md \
  docs/samsung-grok-secure-debug-e2e.md
sh scripts/verify-reference-source-map.sh "$reference_repo"
sh scripts/verify-runtime-reference-manifest.sh "$reference_repo"
git diff --check
printf '%s\n' "secure debug milestone: PASS"
