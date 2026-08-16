# 공개 배포 경계

작성일: `2026-08-16 KST`

## 현재 구현

공개 배포가 이 프로젝트의 주요 목적이다. `release` variant는 application ID
`dev.alpine.codexclient`, version `2`/`0.2.0`, non-debuggable, 실제 Codex/Grok OAuth 허용으로
고정된다. Codex CLI, Grok CLI, chat-only profile, Python Gateway, Alpine Runtime, component
inventory는 `main` asset source를 통해 검증 variant와 release에 동일하게 포함된다.

Python 실행 파일과 전이 의존성은 전체 rootfs 교체 대신 `alpine-python-pack-bundled`가 생성하는
별도 APK asset pack으로 포함한다. 앱은 lock에 고정된 로컬 `.apk` 파일만 app-private staging에
복사하고 `/sbin/apk add --no-network`로 설치한다. URL, repository 또는 `python3` 패키지명 해석
경로는 없다. 실제 production pack이 없으면 공개 release 패키징이 실패한다.

현재 작업 환경에는 pack ID `alpine-3.21.3-python3-3.12.14-r0`, Python `3.12.14-r0`,
21-package production pack이 `alpine-python-pack-bundled/src/main/python-pack`에 준비되어 있다.
이 경로는 의도적으로 Git-ignored이므로 저장소 clone만으로 복원되지는 않는다. 상세 provenance와
Samsung 최초 설치 결과는 [`python-pack-preparation-20260816.md`](python-pack-preparation-20260816.md)에 기록한다.

Phase 6의 patched rootfs와 완전한 취약점 DB는 사용자 결정에 따라 공개 배포 필수 조건이 아니다.
Python을 **rootfs 자체에** 사전 설치하는 작업도 필수가 아니지만, APK 내부의 별도 Python 패키지
팩은 필수이다. 현재 Runtime의 exact artifact hash, package SBOM, 2-slot rollback 검증은 그대로
유지된다.

## Python 패키지 팩 입력

빌드 머신에 이미 존재하는 로컬 디렉터리를 지정한다. Gradle과 검증기는 이 과정에서 어떤 파일도
다운로드하지 않는다.

```text
ALPINE_PYTHON_PACKAGE_DIR=/absolute/path/to/alpine-python-pack

alpine-python-pack/
  python-pack.lock.json
  sbom.spdx.json
  packages/*.apk
```

lock은 `schema: 1`, `production: true`, Alpine/architecture, pack ID, 모든 패키지의 name/version/
size/SHA-256과 SBOM size/SHA-256을 고정해야 한다. 검증기는 `aarch64`, `python3` 존재, 중복·추가·
누락·symlink·경로 이탈을 거부하고 각 Alpine `.apk`의 signed member와 `.PKGINFO` name/version을
lock과 대조한다. package arch는 `aarch64` 또는 Alpine의 architecture-independent `noarch`만
허용한다. 런타임 `apk`가 기존 Alpine 키링으로 최종 패키지 서명을 확인하며
`--allow-untrusted`는 사용하지 않는다.

## 서명 입력

저장소는 private key를 생성하거나 저장하지 않는다. APK/AAB 패키징 시 다음 환경변수 네 개를
모두 외부에서 제공해야 한다.

```text
ALPINE_RELEASE_STORE_FILE
ALPINE_RELEASE_STORE_PASSWORD
ALPINE_RELEASE_KEY_ALIAS
ALPINE_RELEASE_KEY_PASSWORD
```

서명 입력이 일부만 존재하면 Gradle configuration이 실패한다. 서명 입력 또는 production Python
pack이 없으면 release compile/lint와 unavailable asset 상태 검사는 가능하지만 `assembleRelease`,
`bundleRelease`, `packageRelease`, 내부 APK/AAB package/sign task는 각각
`verifyReleaseSigningInputs` 또는 `verifyReleasePythonPackagePack`에서 실패한다. 저장소의
`.gitignore`는 일반적인 keystore/private-key 확장자와 APK/AAB 산출물을 제외한다.

## 검증 명령

서명 없이 release 코드와 asset 구성을 검증한다.

```bash
./gradlew :app:compileReleaseKotlin :app:lintRelease :app:mergeReleaseAssets \
  --offline --no-daemon --console=plain
python3 scripts/verify-release-policy.py --project-root .
python3 scripts/verify-python-package-pack.py \
  --verify-source --source "$ALPINE_PYTHON_PACKAGE_DIR" --require-production
```

외부 서명 입력으로 APK 또는 AAB를 만든 뒤, 배포자가 별도 보관하는 예상 인증서 SHA-256으로
최종 산출물을 검증한다.

```bash
./gradlew :app:assembleRelease :app:bundleRelease --offline --no-daemon --console=plain
python3 scripts/verify-release-artifact.py \
  --artifact app/build/outputs/apk/release/app-release.apk \
  --expected-certificate-sha256 "$ALPINE_RELEASE_CERT_SHA256"
```

최종 verifier는 package/version/non-debuggable/backup manifest, 예상 인증서, 두 CLI lock/hash,
Grok profile, Gateway manifest 전체 coverage, Runtime/PRoot/native asset, Python pack의 status/lock/
모든 package/SBOM coverage와 hash, component inventory, 금지된 API key·직접 Provider fallback
byte를 검사한다. AAB도 동일 CLI로 검증할 수 있다.

## 남은 배포 작업

- 실제 배포 주체가 signing keystore와 예상 certificate SHA-256을 외부 보안 저장소에서 제공
- 서명된 release APK에 `verify-release-artifact.py` 실행
- APK 내장 offline Python 팩을 포함한 최종 Samsung release 후보에서 설치·Runtime·Gateway smoke 확인
- Play Store/AAB 제출은 현재 배포 범위가 아니며 검증된 APK 파일을 직접 배포

별도 UID broker, patched rootfs 전체 교체와 완전한 온라인 취약점 DB는 현재 공개 배포 필수
조건이 아니다. 같은 UID 내부의 논리적 Agent 분리와 현재 Runtime package 위험은
[`security-model.md`](security-model.md) 및
[`../security_best_practices_report.md`](../security_best_practices_report.md)에 잔여 위험으로
기록한다. 전체 프로젝트 상태는 [`project-overview.md`](project-overview.md)를 기준으로 한다.
