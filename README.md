# Alpine Agent CLI Client

![Android 앱 안에서 Codex와 Grok을 실행하는 Alpine Agent CLI Client](docs/images/readme-hero-agent-runtime.webp)

공식 **OpenAI Codex CLI**와 **xAI Grok CLI**를 Android 앱 내부의 app-private Alpine
Runtime에서 실행하는 멀티 Agent 채팅 클라이언트입니다. Android가 Provider API를 직접 호출하거나
API key를 보관하지 않고, 각 공식 CLI가 소유한 Device OAuth와 관리형 프로토콜을 사용합니다.

> **배포 상태 — 서명 입력 대기**
> 이 작업 환경에는 검증된 production Alpine Python pack이 Git-ignored 로컬 입력으로 준비되어
> 있고, 이를 포함한 `debug`/`secureDebug` APK 빌드와 Samsung 실기기 최초 설치가 통과했습니다.
> 공개 배포용 signed release APK를 만들려면 이제 외부 release keystore와 예상 인증서
> SHA-256만 제공하면 됩니다. 새 checkout에서는 production pack도 다시 제공해야 합니다.

## 핵심 특징

- **CLI 소유 OAuth**: Codex와 Grok의 자격 증명을 Android UI나 Gateway가 직접 읽거나 복사하지 않음
- **두 공식 Agent**: Codex `app-server`와 Grok ACP의 계정, 동적 모델, 스트리밍 채팅, Stop 지원
- **완전한 APK 실행 경로**: Runtime 실행 중 Alpine 저장소나 별도 서버에서 Python을 다운로드하지 않음
- **private UDS**: Android↔Gateway를 app-private Unix domain socket으로 연결하고 양방향 peer UID 확인
- **요청 인증**: 세션별 HMAC, timestamp, nonce replay 방지와 엄격한 HTTP/SSE 크기·상태 제한
- **상태 복구**: Agent별 암호화 대화, 모델, Grok 세션 바인딩과 Runtime/Gateway 재시작 복구
- **fail-closed 배포**: 고정된 CLI/Runtime/Python lock, SBOM, dependency lock, 서명·산출물 검증
- **백업 차단**: OAuth 및 대화 상태를 `noBackupFilesDir`에 두고 cloud backup과 D2D transfer를 제외

## 지원 범위

| 항목 | Codex | Grok |
|---|---|---|
| 공식 실행 파일 | Codex CLI `0.147.0` | Grok CLI `1.0.0` |
| 인증 | 공식 CLI Device OAuth | 공식 CLI Device OAuth |
| 프로토콜 | `codex app-server` JSONL | 고정 ACP JSONL |
| 모델 | CLI가 반환한 동적 목록 | CLI가 반환한 동적 목록 |
| 채팅 | 스트리밍 및 Stop | 스트리밍, Stop, 제한된 사전 출력 auth recovery |
| 도구 범위 | read-only / approval-never 경계 | chat-only profile, 금지 tool event fail-closed |

이 프로젝트는 범용 OpenAI/xAI REST SDK, API key 클라이언트, 원격 Gateway 서비스 또는
Termux 프런트엔드가 아닙니다. Gateway는 앱 내부에서 두 CLI를 고정된 typed contract로 연결하는
로컬 구성요소입니다.

## 동작 구조

![Private UDS와 분리된 CLI OAuth를 사용하는 보안 구조](docs/images/readme-private-oauth-architecture.webp)

```text
Android Compose UI
  -> HMAC HTTP/SSE over app-private UDS
  -> Python Agent Gateway
  -> single-flight Agent router
  -> official Codex app-server | official Grok ACP
  -> CLI-owned Device OAuth and Provider traffic
```

한 시점에는 Runtime/Gateway 하나와 선택된 Agent process 하나만 실행됩니다. 로그인이나 turn이
진행 중이면 Agent 전환을 거부하고, prompt 자동 재전송이나 다른 Agent로의 fallback을 수행하지
않습니다. 상세한 경계는 [프로젝트 개요](docs/project-overview.md)와
[아키텍처](docs/architecture.md)를 참고하세요.

## 최신 업데이트 — 2026-08-16

![외부 다운로드 없이 APK에 포함되는 Alpine Python Runtime](docs/images/readme-offline-apk-runtime.webp)

- Alpine `3.21.3` / Python `3.12.14-r0` production pack을 공식 Alpine 패키지 21개와
  SPDX 2.3/lock으로 준비했습니다.
- `debug`와 non-debuggable `secureDebug` APK 빌드, signature와 clean-room audit를 통과했습니다.
- Samsung `SM-S931N`, API 36에서 별도 `labdebug` 신규 설치로 APK-contained Python 설치,
  PRoot/Python Gateway 기동과 force-stop 복구를 검증했습니다.
- Runtime Python 준비는 `/sbin/apk add --no-network`만 사용하며 TCP `8787` listener는 `0`입니다.
- Python 단위·통합·보안 회귀 `176` tests와 release compile/lint/assets `356` tasks가 통과했습니다.
- production pack 준비는 완료됐으며 공개 signed release APK에는 외부 release keystore와 예상
  certificate SHA-256만 남았습니다.

## 요구 환경

- macOS 또는 Linux 개발 환경
- JDK 17
- Android SDK 36
- Python 3
- Android `arm64-v8a`, API 26 이상 기기
- 첫 의존성/CLI asset 준비 후 offline Gradle 검증이 가능한 로컬 캐시
- 실제 Runtime 실행 시 검증된 Alpine `aarch64` Python 패키지 팩

현재 APK ABI는 `arm64-v8a`만 지원합니다.

## 빌드와 검증

### 1. 저장소 준비

```bash
git clone https://github.com/coreline-ai/alpine-codex-cli-client.git
cd alpine-codex-cli-client
```

Android SDK 경로는 `ANDROID_HOME` 또는 Git에 포함되지 않는 `local.properties`로 지정합니다.
JDK는 `JAVA_HOME`으로 JDK 17을 가리켜야 합니다.

### 2. 공식 CLI asset 준비

Codex/Grok 실행 파일은 Git에 포함되지 않습니다. 완전한 offline 빌드에서는 lock과 일치하는 로컬
파일을 지정합니다.

```bash
export CODEX_CLI_ARCHIVE_PATH=/absolute/path/to/codex-aarch64-unknown-linux-musl.tar.gz
export GROK_CLI_BINARY_PATH=/absolute/path/to/grok-1.0.0-linux-aarch64
```

명시적 파일과 Gradle user cache가 모두 없으면 build task가 lock에 고정된 **공식 URL에서만**
CLI를 내려받아 크기, SHA-256과 AArch64 ELF를 검증합니다. 이는 APK 생성 시점의 입력 준비이며,
설치된 Android Runtime은 CLI나 Python을 다운로드하지 않습니다.

### 3. Python 패키지 팩 연결

앱에서 Runtime을 실제 실행하려면 다음 로컬 입력을 준비합니다. 빌드는 이 팩을 다운로드하지
않습니다.

```text
ALPINE_PYTHON_PACKAGE_DIR=/absolute/path/to/alpine-python-pack

alpine-python-pack/
  python-pack.lock.json
  sbom.spdx.json
  packages/*.apk
```

정확한 스키마와 검증 규칙은
[`alpine-python-pack-bundled/PACKAGING.md`](alpine-python-pack-bundled/PACKAGING.md)에 있습니다.
입력이 없어도 일부 개발·정적 검증 variant는 unavailable marker로 빌드할 수 있지만, Runtime의
Python 준비는 실패하며 public release 패키징은 항상 차단됩니다.

### 4. credential-free 전체 검증

```bash
sh scripts/verify-secure-debug-milestone.sh
```

이 gate는 Python/Kotlin 테스트, Android lint와 variant 빌드, Codex/Grok 프로토콜, private UDS,
백업 정책, CLI·Runtime·Python·Gradle 공급망, clean-room APK, release 정책, 민감 evidence와 기준
소스 drift를 검사합니다. 실제 OAuth나 유료 turn은 수행하지 않습니다.

### 5. 개발 APK

```bash
./gradlew :app:assembleDebug --offline --no-daemon --console=plain
./gradlew :app:assembleSecureDebug --offline --no-daemon --console=plain
```

`debug`는 credential-free 실험용이며 실제 OAuth를 코드 수준에서 차단합니다. 실제 계정 검증은
non-debuggable `secureDebug`와 [Samsung runbook](docs/samsung-grok-secure-debug-runbook.md)의
승인 경계를 따라야 합니다.

## Build variant

| Variant | Application ID | 디버거 | 실제 OAuth | 목적 |
|---|---|---:|---:|---|
| `debug` | `dev.alpine.codexclient.labdebug` | 허용 | 차단 | credential-free 개발·계측 |
| `secureDebug` | `dev.alpine.codexclient.debug` | 차단 | 허용 | 실제 단말 보안/E2E 검증 |
| `release` | `dev.alpine.codexclient` | 차단 | 허용 | 외부 서명 공개 배포 |

`release`는 다음 조건을 모두 요구합니다.

1. `production: true`로 검증된 APK 내장 Python 패키지 팩
2. 네 개의 `ALPINE_RELEASE_*` 서명 환경변수
3. 서명 후 예상 certificate SHA-256을 사용한 최종 APK/AAB 감사

저장소는 private signing key나 production Python package를 생성·다운로드·커밋하지 않습니다.
전체 절차는 [공개 배포 가이드](docs/public-release.md)를 참고하세요.

## 주요 모듈

| 경로 | 역할 |
|---|---|
| `app` | Compose UI, Runtime orchestration, Keystore, UDS transport, 상태 복구 |
| `codex-runtime-bridge` | Agent-neutral Gateway client, HMAC, JSON/SSE parsing |
| `codex_gateway` | Python Gateway, Codex/Grok adapter, Agent router, ACP supervisor |
| `alpine-runtime-*` | Alpine/PRoot 설치·실행·background lifecycle·2-slot rollback |
| `alpine-workspace-*` | app-private workspace 계약과 Android 구현 |
| `codex-cli-pack` | 고정 Codex CLI asset 생성·검증 |
| `grok-cli-pack` | 고정 Grok CLI와 chat-only profile 생성·검증 |
| `codex-gateway-pack-bundled` | Python Gateway를 APK asset으로 패키징 |
| `alpine-python-pack-bundled` | 로컬 Alpine Python 패키지 팩 검증·APK 포함 |
| `scripts`, `tests` | 공급망, 산출물, 보안 회귀 및 Python 테스트 |

14개 Gradle module의 상세 소유권은 [프로젝트 개요](docs/project-overview.md#모듈-구성)에
정리되어 있습니다.

## 현재 제약과 남은 배포 입력

- 현재 작업 환경의 production Python pack은 21개 Alpine 패키지와 SPDX/lock으로 준비되어 있으나
  Git에 포함되지 않으므로 새 checkout에는 자동으로 따라가지 않음
- release signing keystore와 인증서 fingerprint는 배포자가 외부 보안 저장소에서 제공해야 함
- APK 내장 offline Python 팩의 별도 `labdebug` 최초 설치·Gateway 기동·강제 종료 복구는 Samsung
  실기기에서 통과했으며, 실제 계정을 보존한 `secureDebug` 업데이트와 signed release 최종 smoke는 남아 있음
- 동일 앱 UID 안의 Codex/Grok HOME 분리는 정상 동작 격리이며 악성 동일-UID 프로세스에 대한
  커널 보안 경계는 아님
- Gradle dependency lock은 적용됐지만 verification metadata와 wrapper ZIP checksum은 로컬 입력
  부재로 아직 생성되지 않음

## 문서

- [문서 인덱스](docs/README.md)
- [프로젝트 개요](docs/project-overview.md)
- [Architecture](docs/architecture.md)
- [Security model](docs/security-model.md)
- [보안 검토 및 조치 현황](security_best_practices_report.md)
- [앱 실사용 QA 전략과 실행 방법](docs/app-real-use-qa.md)
- [Samsung 앱 실사용 QA evidence](docs/samsung-app-real-use-qa-20260816.md)
- [공개 배포 가이드](docs/public-release.md)
- [Runtime 공급망](docs/runtime-supply-chain.md)
- [Gradle 공급망](docs/gradle-supply-chain.md)
- [SBOM과 component inventory](docs/debug-sbom.md)
- [Production Python pack 준비 기록](docs/python-pack-preparation-20260816.md)
- [AnyClaw 비교 분석](docs/anyclaw-analysis.md)
- [Samsung Grok secure-debug runbook](docs/samsung-grok-secure-debug-runbook.md)
- [기여 가이드](CONTRIBUTING.md)
- [보안 정책과 취약점 보고](SECURITY.md)

개발 계획은 `dev-plan/`에 보존되어 있으며, 현재 제품 설명의 기준은 README와 `docs/`의 상태
문서입니다.

## 라이선스와 제3자 구성요소

프로젝트 자체 코드는 [GNU General Public License v3.0](LICENSE)으로 배포됩니다.

번들 또는 생성되는 Codex CLI, Grok CLI, Alpine 패키지, PRoot 및 Maven 구성요소에는 각자의
라이선스가 적용됩니다. 자세한 내용은 [Codex CLI notice](docs/codex-cli-notice.md),
[Grok CLI notice](docs/grok-cli-notice.md), [SBOM 문서](docs/debug-sbom.md)와 APK 안의 component
inventory를 확인하세요.
