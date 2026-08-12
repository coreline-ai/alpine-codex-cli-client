#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
reference_repo=${ALPINE_REFERENCE_REPO:-/Volumes/ExternalSSD/projects_8/alpine-llm-gateway}
apk_path="$project_root/app/build/outputs/apk/debug/app-debug.apk"

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
  :app:assembleDebug \
  :app:assembleDebugAndroidTest \
  --offline --no-daemon --console=plain
python3 scripts/verify-codex-protocol-fixture.py --project-root "$project_root"
python3 scripts/verify-debug-clean-room.py --project-root "$project_root" --apk "$apk_path"
python3 scripts/verify-grok-cli-artifact.py --project-root "$project_root" --apk "$apk_path"
python3 scripts/verify-grok-profile.py --project-root "$project_root" --apk "$apk_path"
sh scripts/verify-reference-source-map.sh "$reference_repo"
sh scripts/verify-runtime-reference-manifest.sh "$reference_repo"
git diff --check
printf '%s\n' "debug milestone: PASS"
