#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
lab_apk=${1:-"$project_root/app/build/outputs/apk/debug/app-debug.apk"}
secure_apk=${2:-"$project_root/app/build/outputs/apk/secureDebug/app-secureDebug.apk"}
android_sdk=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Library/Android/sdk}}
build_tools="$android_sdk/build-tools/36.0.0"
aapt="$build_tools/aapt"
apksigner="$build_tools/apksigner"

test -f "$lab_apk"
test -f "$secure_apk"
test -x "$aapt"
test -x "$apksigner"

lab_badging=$($aapt dump badging "$lab_apk")
secure_badging=$($aapt dump badging "$secure_apk")
case "$lab_badging" in
  *"package: name='dev.alpine.codexclient.labdebug'"*"versionCode='2'"*) ;;
  *) echo "lab APK identity invalid" >&2; exit 1 ;;
esac
case "$secure_badging" in
  *"package: name='dev.alpine.codexclient.debug'"*"versionCode='2'"*) ;;
  *) echo "secure APK identity invalid" >&2; exit 1 ;;
esac

lab_manifest=$($aapt dump xmltree "$lab_apk" AndroidManifest.xml)
secure_manifest=$($aapt dump xmltree "$secure_apk" AndroidManifest.xml)
printf '%s' "$lab_manifest" | grep -q 'android:debuggable.*0xffffffff'
if printf '%s' "$secure_manifest" | grep -Eq 'android:debuggable|android:testOnly|Phase6TestActivity|androidx.activity.ComponentActivity'; then
  echo "secure APK contains debuggable or test-only manifest state" >&2
  exit 1
fi
printf '%s' "$secure_manifest" | grep -q 'android:allowBackup.*0x0'
printf '%s' "$secure_manifest" | grep -q 'android:dataExtractionRules'
printf '%s' "$secure_manifest" | grep -q 'android:fullBackupContent'
launcher_count=$(printf '%s' "$secure_manifest" | grep -c 'android.intent.action.MAIN')
test "$launcher_count" -eq 1

lab_cert=$($apksigner verify --print-certs "$lab_apk" | sed -n 's/^Signer #1 certificate SHA-256 digest: //p')
secure_cert=$($apksigner verify --print-certs "$secure_apk" | sed -n 's/^Signer #1 certificate SHA-256 digest: //p')
test -n "$lab_cert"
test "$lab_cert" = "$secure_cert"
$apksigner verify "$lab_apk"
$apksigner verify "$secure_apk"

if unzip -l "$secure_apk" | grep -Eq 'Phase6TestActivity|ui-test-manifest|test-runner'; then
  echo "secure APK contains a test artifact" >&2
  exit 1
fi
if unzip -p "$secure_apk" 'classes*.dex' | strings | grep -Fq 'http://127.0.0.1:8787'; then
  echo "secure APK contains legacy loopback Gateway fallback" >&2
  exit 1
fi
if ! unzip -p "$secure_apk" 'classes*.dex' | strings | grep -Fq 'gateway.sock'; then
  echo "secure APK is missing the private Gateway socket contract" >&2
  exit 1
fi
printf '%s\n' "secure debug APK audit: PASS"
