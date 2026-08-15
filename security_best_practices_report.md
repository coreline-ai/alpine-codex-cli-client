# Alpine Codex CLI Client 보안 전문가 검토 보고서

- 검토일: 2026-08-15 (KST)
- 검토 대상: Android 앱, Alpine/PRoot 런타임, Python Gateway, Codex/Grok CLI 연동, OAuth 자격 증명 경계, 빌드·배포 공급망
- 기준 리비전: `ec94f99` (`main`)
- 검토 방식: 소스 정적 감사, 기존 보안 설계 문서 대조, 자동화 테스트·APK 감사, 번들 런타임 패키지 및 공개 보안 권고 확인
- 주의: 검토 당시 작업 트리에 기존 미커밋 변경 사항이 존재했다. 본 보고서는 침투 테스트, 루팅 단말 테스트, 실제 악성 앱과의 경쟁 조건 재현을 포함하지 않는다.

## 1. 결론

현재 프로젝트는 일반적인 모바일 프로토타입보다 강한 보안 통제를 이미 갖추고 있다. 특히 OAuth 토큰을 Android UI 계층에서 직접 읽지 않는 구조, 요청 HMAC, Android Keystore 기반 대화 암호화, 고정된 CLI 실행 계약, Grok의 chat-only 제한, 로그 비밀정보 제거는 좋은 설계다.

그러나 **현재 상태는 공개 프로덕션 배포 기준으로 `NO-GO`**다. 가장 큰 이유는 다음 세 가지다.

1. Android 앱이 고정된 평문 loopback TCP 포트의 Gateway 서버 신원을 확인하지 않아, 로컬 악성 앱이 포트를 선점하면 프롬프트를 탈취하고 응답을 위조할 수 있다.
2. Codex와 Grok의 분리된 `HOME`은 같은 Android UID와 같은 PRoot/workspace 안의 논리적 분리일 뿐, 강한 자격 증명 보안 경계가 아니다.
3. 번들 Alpine 3.21.3 rootfs에 현재 공개 취약점과 연결되는 구버전 패키지가 포함되어 있다.

통제된 개발용 Samsung 단말에서 제한적으로 사용하는 것은 가능하지만, P0 항목을 해결하기 전에는 사용자 자격 증명과 대화 내용을 다루는 공개 배포판으로 판단하면 안 된다.

## 2. 위험 요약

| ID | 심각도 | 발견 사항 | 현재 판단 |
|---|---:|---|---|
| SEC-001 | High | Gateway 서버 인증·전송 기밀성 부재 및 고정 loopback 포트 선점 | 배포 차단 |
| SEC-002 | High | Codex/Grok 자격 증명 격리가 동일 UID·동일 PRoot 내부의 논리적 분리에 그침 | 구조 변경 또는 위험 수용 필요 |
| SEC-003 | High | 알려진 취약 패키지가 포함된 구버전 Alpine rootfs 번들 | 배포 차단 |
| SEC-004 | Medium | Android 백업·기기 간 전송 제외 규칙 미완성 | Samsung/OEM 포함 검증 필요 |
| SEC-005 | Medium | 인증 전 무제한 threaded HTTP 처리로 로컬 DoS 가능 | Gateway 경계 강화 필요 |
| SEC-006 | Medium | Kotlin Gradle Plugin 2.2.21 빌드 캐시 역직렬화 권고 영향 | CI 완화 후 고정 버전 업그레이드 |
| SEC-007 | Medium | Gradle 의존성 검증·잠금·Wrapper 배포본 해시 미적용 | 빌드 공급망 강화 필요 |
| SEC-008 | Low | HMAC nonce 저장소 포화 시 최대 약 120초간 정상 요청도 거절 | 내구성 개선 |

심각도는 현재 앱이 OAuth 세션과 사용자 프롬프트를 처리한다는 점, 공격에 필요한 권한, 공개 배포 시 영향 범위를 함께 고려했다.

## 3. 위협 모델

이번 검토는 다음 공격자를 고려했다.

- 동일 Android 단말에 설치된 일반 권한의 악성 앱
- 변조되거나 취약한 CLI, Gateway, Alpine 패키지 또는 빌드 의존성
- 사용자 프롬프트를 통해 비정상 동작을 유도하는 원격 콘텐츠
- 백업 또는 기기 간 전송 과정에서 자격 증명 파일에 접근하는 주체
- 로그, 클립보드, 브라우저 이동, IPC/네트워크 경계에서 민감정보를 수집하는 주체

다음은 별도 검토가 필요한 범위다.

- 루팅·커스텀 커널·디버거가 장악한 단말
- OpenAI, xAI 및 브라우저 자체의 계정 보안
- 네이티브 바이너리 전체 역공학과 퍼징
- 실제 배포 서명키 관리 시스템과 CI/CD 운영 환경

## 4. 상세 발견 사항

### SEC-001 — Gateway 서버 인증·전송 기밀성 부재

**심각도: High / 배포 차단**

#### 근거

- `codex-runtime-bridge/src/main/kotlin/dev/alpine/codexclient/bridge/AgentGatewayClient.kt:330-392`는 서명된 요청을 `http://127.0.0.1`로 보내지만 응답의 출처나 무결성을 검증하지 않는다.
- `codex-runtime-bridge/src/main/kotlin/dev/alpine/codexclient/bridge/AgentGatewayClient.kt:537`에 Gateway endpoint가 고정되어 있다.
- `codex-runtime-bridge/src/main/kotlin/dev/alpine/codexclient/bridge/GatewayRequestSigner.kt:24-55`의 HMAC은 앱에서 Gateway로 보내는 요청만 인증한다.
- `app/src/main/java/dev/alpine/codexclient/ConfiguredRuntimeStarter.kt:31-37`과 `codex-runtime-bridge/src/main/kotlin/dev/alpine/codexclient/bridge/CodexRuntimeController.kt:180-198`은 health 응답을 근거로 연결 상태를 신뢰한다.

#### 공격 시나리오와 영향

실제 Gateway가 시작되기 전이거나 종료된 순간, 동일 단말의 다른 앱이 고정 포트 `8787`을 먼저 열 수 있다. Android loopback은 앱 전용 네트워크가 아니다. 가짜 서버는 요청 HMAC을 위조하지 못하더라도 **앱이 보내는 평문 프롬프트와 메타데이터를 그대로 수신**할 수 있고, 서명되지 않은 정상 형태의 응답이나 health 응답을 만들어 UI를 속일 수 있다.

현재 HMAC은 실제 Gateway에 대한 무단 호출 방지에는 유효하지만, 클라이언트가 연결한 서버가 실제 Gateway인지 증명하거나 요청 내용을 암호화하지 않는다.

#### 권고

1. Android↔Gateway 전송을 앱 private/no-backup 디렉터리의 **filesystem Unix domain socket**으로 전환한다.
2. Android 측에서 `LocalSocket.getPeerCredentials()` 등으로 peer UID를 확인하고, 서버 측에서도 private socket 경로·권한·peer를 제한한다.
3. 가능하면 앱이 socket을 생성한 뒤 FD를 자식 Gateway에 전달해 포트/경로 선점 창을 제거한다.
4. 임시로 TCP를 유지해야 한다면 임의의 ephemeral port, 상호 challenge, 응답 인증, 요청 암호화를 함께 적용한다. 응답 MAC만 추가하는 것은 가짜 서버가 요청 본문을 읽는 문제를 해결하지 못한다.

#### 완료 기준

- 다른 UID의 테스트 앱이 Gateway endpoint를 선점하거나 연결할 수 없다.
- 가짜 server health/turn 응답이 항상 거부된다.
- Gateway가 비정상 종료된 뒤 재연결하는 경쟁 조건 테스트가 자동화된다.
- 네트워크 캡처에서 사용자 프롬프트 평문이 노출되지 않는다.

### SEC-002 — 동일 UID 안의 논리적 자격 증명 분리

**심각도: High / 구조적 위험**

#### 근거

- `app/src/main/java/dev/alpine/codexclient/AlpineCodexApplication.kt:113-164`에서 Codex와 Grok 런타임이 같은 앱 `filesDir` 아래에 구성된다.
- `alpine-runtime-android/src/main/kotlin/dev/alpine/runtime/android/internal/ProotProcessLauncher.kt:78-102,232-260`은 동일 workspace를 PRoot의 `/workspace`에 bind하고, 모든 하위 프로세스는 같은 Android 앱 UID 권한으로 실행된다.
- `docs/security-model.md:19`와 `README.md:11`의 자격 증명 격리 표현은 이 한계를 충분히 설명하지 않는다.

#### 공격 시나리오와 영향

PRoot는 커널 또는 별도 UID 기반 sandbox가 아니라 사용자 공간 경로 가상화다. 별도 `HOME`을 사용해 정상 동작 중의 우발적 혼용은 방지할 수 있지만, 손상되거나 악성인 CLI/Gateway/의존성에 대한 강한 보안 경계는 아니다. 동일 UID 프로세스는 원칙적으로 같은 앱 private 파일 접근 권한을 가지므로 sibling agent의 세션 파일, 공용 workspace 또는 capability material을 노릴 수 있다.

#### 권고

1. 보안 경계가 필요한 agent runtime을 별도 Android UID가 적용되는 컴포넌트 또는 별도 helper package로 분리한다.
2. 단순히 별도 process만 쓰는 것은 같은 UID이면 충분하지 않다. isolated UID를 사용하고 Binder/pipe/사전 개방 FD 같은 최소 계약만 전달하는 구조를 검토한다.
3. 공유 `/workspace` 전체 bind를 제거하고 agent별 최소 디렉터리만 노출한다.
4. 단기적으로 문서에 현재 분리가 **논리적 오염 방지이며 악성 프로세스 격리는 아님**을 명시하고, CLI 무결성 실패 시 모든 관련 세션을 폐기·재로그인하도록 한다.

#### 완료 기준

- 손상된 Grok 테스트 런타임이 Codex `HOME`, 앱의 다른 private 디렉터리, Gateway capability를 읽지 못한다.
- agent별 허용 파일과 IPC 메시지 계약이 문서화되고 자동 테스트로 고정된다.
- 구조 변경을 하지 않는 경우에는 공식 CLI와 Gateway를 완전 신뢰한다는 전제를 명시적으로 위험 수용 기록에 남긴다.

### SEC-003 — 구버전 Alpine rootfs의 알려진 취약 패키지

**심각도: High / 배포 차단**

#### 근거

- `alpine-runtime-pack-bundled/src/main/kotlin/dev/alpine/runtime/pack/bundled/BundledRuntimeArtifactProvider.kt:123-168`은 Alpine 3.21.3 기반 rootfs를 고정한다.
- 번들 rootfs 검사에서 `openssl/libcrypto/libssl 3.3.3-r0`, `busybox 1.37.0-r12`, `musl 1.2.5-r9`, `zlib 1.3.1-r2`가 확인됐다.
- 공개 권고와 패키지 버전 대조 결과 OpenSSL 3.3.3에 적용되는 항목이 다수 확인됐으며, 예를 들어 `CVE-2025-15467`은 Alpine에서 수정된 상위 패키지가 제공된다.
- `app/src/main/java/dev/alpine/codexclient/GatewayPythonBootstrapper.kt:50-76`은 Python 설치를 수행하지만 전체 base package를 재현 가능하게 upgrade하는 패치 절차가 아니다.
- 현재 `sbom.spdx.json`은 rootfs 내부 APK 패키지 전체를 개별 component로 나타내지 않아 취약점 탐지 가시성이 낮다.

#### 영향

각 CVE가 현재 사용 경로에서 실제로 도달 가능한지는 별도 검증이 필요하다. 그러나 인증 CLI와 네트워크 통신을 수행하는 런타임 이미지에 알려진 취약 패키지를 고정 배포하는 것은 공개 출시 기준으로 허용하기 어렵다. 첫 실행 시 네트워크 패키지 설치에 의존하는 방식도 재현성과 공급망 신뢰를 약화한다.

#### 권고

1. 지원 중인 최신 Alpine patch release로 rootfs를 재생성한다. 최소 3.21.7 이상을 검토하고, 호환성이 확보되면 최신 안정 계열을 우선한다.
2. Python과 필요한 패키지를 빌드 시점에 정확한 버전으로 포함하고, 실행 시 무제한 `apk add/upgrade`에 의존하지 않는다.
3. APK package 단위 SBOM과 checksum/provenance를 생성한다.
4. CI에서 Alpine secdb 또는 OSV 스캔을 실행하고 High/Critical 알려진 취약점이 있으면 release를 차단한다.

#### 완료 기준

- 새 rootfs에서 알려진 High/Critical 항목이 0건이거나, 도달 불가 분석과 기한이 있는 예외 승인이 존재한다.
- rootfs 내부 package/version/license가 SBOM에 포함된다.
- 오프라인 재설치에서도 동일한 runtime hash와 패키지 구성이 재현된다.

### SEC-004 — 백업 및 기기 간 전송 제외 규칙 미완성

**심각도: Medium**

#### 근거

- `app/src/main/AndroidManifest.xml:6-11`에는 `android:allowBackup="false"`가 있지만 `android:dataExtractionRules`와 legacy `fullBackupContent` 규칙이 없다.
- OAuth session, runtime home, 대화 저장소, wrapped capability가 `filesDir` 계열에 존재한다.
- Android 12 이상에서는 일부 제조사가 device-to-device 전송에서 `allowBackup="false"`를 다르게 취급할 수 있으며, 현재 lint도 `DataExtractionRules` 누락을 경고한다.

#### 영향

특정 Samsung/OEM 전송 경로에서 OAuth 자격 증명이나 runtime 상태가 새 단말로 복사될 가능성을 명시적으로 차단하지 못한다. Keystore key가 함께 이전되지 않으면 대화 암호문은 복호화되지 않더라도, session 파일 자체의 이동은 별도 위험이다.

#### 권고

1. credential home, Gateway secret, runtime 상태를 `noBackupFilesDir`로 이동한다.
2. Android 12+ `dataExtractionRules`에서 cloud backup과 device transfer 모두 민감 domain을 제외한다.
3. 구버전용 `fullBackupContent="false"` 또는 명시적 exclusion을 추가한다.
4. `bmgr`와 실제 Samsung Smart Switch/기기 이전 시나리오를 테스트한다.

#### 완료 기준

- cloud backup과 D2D 추출 결과에 OAuth session, capability, 대화 평문/암호문, runtime home이 포함되지 않는다.
- 재설치 또는 기기 이전 후에는 명시적인 재인증이 요구된다.

### SEC-005 — 인증 전 무제한 ThreadingHTTPServer 자원 소모

**심각도: Medium**

#### 근거

- `codex_gateway/gateway.py:905-917`은 `ThreadingHTTPServer`를 사용하며 연결 수, worker 수, socket/header read timeout을 제한하지 않는다.
- `codex_gateway/agents/http.py:63-71,143-156`은 HMAC 인증 완료 전에 요청 body를 읽는다.

#### 공격 시나리오와 영향

동일 단말의 다른 앱이 다수의 연결을 열고 header 또는 선언된 body를 매우 느리게 전송하면, 인증 전에 thread와 file descriptor를 점유할 수 있다. 요청 본문 크기 제한은 유효하지만 느린 연결과 동시성 고갈을 막지는 못한다. Gateway 응답 불능과 agent session 중단으로 이어질 수 있다.

#### 권고

- SEC-001의 private Unix socket 전환과 함께 bounded worker/semaphore, connection backlog, header/body read timeout, 동시 연결 제한을 적용한다.
- 민감한 범용 HTTP server 대신 길이 제한이 명확한 최소 framed protocol을 고려한다.
- timeout, 포화, 인증 실패는 내용 없는 계수형 telemetry만 남긴다.

#### 완료 기준

- 수백 개의 partial connection을 생성하는 로컬 부하 테스트에서도 process thread/FD가 설정된 상한을 넘지 않는다.
- 정상 요청은 설정된 복구 시간 안에 처리되고, 실패 시 세션 비밀이나 prompt가 로그에 남지 않는다.

### SEC-006 — Kotlin Gradle Plugin 빌드 캐시 역직렬화 권고

**심각도: Medium / 빌드 환경**

#### 근거

- `gradle/libs.versions.toml:3`은 Kotlin 2.2.21을 사용한다.
- 공개 권고 `GHSA-r937-wjx7-w2jp` / `CVE-2026-53914`는 영향을 받는 Kotlin build cache를 통한 unsafe deserialization 및 빌드 시 코드 실행 위험을 설명한다.
- 저장소에는 remote build cache 구성이 확인되지 않아 현재 노출 가능성은 낮아지지만, 공유되거나 신뢰할 수 없는 CI cache를 사용하는 경우 위험이 커진다.

#### 권고

1. 수정 버전이 프로젝트 호환성을 충족하는 즉시 Kotlin plugin을 업그레이드한다.
2. 그 전까지 untrusted branch/PR에서 shared remote build cache를 사용하지 않는다.
3. CI runner와 cache를 신뢰 경계별로 분리하고, 외부 기여 코드는 깨끗한 cache에서 빌드한다.

#### 완료 기준

- 권고상 수정된 Kotlin 버전으로 build/test/lint가 통과한다.
- CI cache 신뢰 정책과 삭제·격리 절차가 문서화된다.

### SEC-007 — Gradle 의존성 검증과 잠금 부재

**심각도: Medium / 빌드 공급망**

#### 근거

- `gradle/verification-metadata.xml`과 Gradle dependency lockfile이 없다.
- `gradle/wrapper/gradle-wrapper.properties:1-7`에 `distributionSha256Sum`이 없다.
- 프로젝트 자체 CLI/rootfs artifact에는 hash 검증이 적용되어 있지만 Maven/plugin/Gradle 배포본에는 동일 수준의 검증이 적용되지 않는다.

#### 영향

저장소 오염, mirror/CDN 변조, dependency confusion, 손상된 build cache 같은 사건이 발생했을 때 빌드가 예상하지 않은 artifact를 받아 패키징할 가능성이 커진다. OAuth 자격 증명을 다루는 앱이므로 일반 UI 앱보다 빌드 공급망 무결성 요구 수준이 높다.

#### 권고

- Gradle dependency verification metadata를 생성한 뒤 SHA-256/서명을 사람이 검토해 커밋한다.
- 가능한 configuration에 dependency locking을 적용한다.
- Gradle wrapper 배포본 checksum을 고정한다.
- CI 산출물에 provenance, SBOM, source commit, toolchain version을 연결한다.

#### 완료 기준

- 검증되지 않은 dependency/plugin/wrapper artifact로 빌드하면 CI가 실패한다.
- lock 또는 verification metadata 변경은 보안 검토가 필요한 변경으로 CODEOWNERS/PR 정책에 포함된다.

### SEC-008 — nonce 저장소 포화 시 fail-closed 서비스 거부

**심각도: Low**

#### 근거

- `codex_gateway/security.py:174-183`은 최대 256개 nonce를 120초 유지하며, 저장소가 가득 차면 새로 인증된 요청도 거부한다.

#### 영향과 권고

일반적인 단일 요청 흐름에서는 발생 가능성이 낮고, 인증되지 않은 공격자가 cache를 직접 채울 수는 없다. 다만 client retry 오류나 합법적인 burst가 있으면 최대 약 120초간 자기 서비스 거부가 발생할 수 있다. 정상 최대 요청률을 근거로 용량을 산정하고, 포화 telemetry와 복구 테스트를 추가한다. replay 보장을 약화시키는 단순 eviction은 보안 분석 없이 적용하지 않는다.

## 5. 확인된 우수 통제

다음 항목은 유지하고 회귀 테스트로 고정할 가치가 있다.

- Android UI가 Codex/Grok OAuth token 파일을 직접 읽지 않고 관리 프로세스가 인증을 소유한다.
- Gateway capability는 32-byte 난수로 생성되고 Android Keystore AES-GCM으로 감싸며, one-time handoff 파일에 `0600`, `O_NOFOLLOW`, type/owner 검사와 unlink를 적용한다.
- Gateway 요청은 method, path, timestamp, nonce, body digest를 HMAC-SHA256으로 정규화하고 시간 창과 replay 방지를 적용한다.
- HTTP parser는 Host, Origin, query/fragment, Transfer-Encoding을 엄격히 제한하고 body 크기와 JSON 깊이를 제한한다.
- 대화 저장소는 Android Keystore AES-256-GCM, random IV, AAD, atomic replace, `0600`, 크기 제한을 사용한다.
- 화면 캡처 방지를 위해 전역 `FLAG_SECURE`를 적용한다.
- 브라우저 로그인 URL은 HTTPS, exact host, 표준 port, userinfo/fragment 금지를 검사한다.
- 코드 복사 시 Android의 sensitive clipboard 표시와 60초 자동 삭제를 적용한다.
- Codex/Grok 실행 파일, 인자, 환경, 허용 method가 고정되고 입력 길이가 제한된다.
- Codex tool 실행은 read-only/approval-never로 제한되고, Grok은 chat-only profile 및 forbidden tool event fail-closed 정책을 갖는다.
- stderr와 운영 로그는 내용이 아닌 상태 중심으로 redaction하며, 저장소 추적 파일에서 명백한 token/keystore 후보가 발견되지 않았다.
- CLI와 Gateway asset checksum 검증, rootfs checksum, 기초 SBOM이 이미 존재한다.

## 6. 자동 검증 결과

실행 명령:

```text
env JAVA_HOME=<Android Studio JBR> ANDROID_HOME=<Android SDK> \
  sh scripts/verify-secure-debug-milestone.sh
```

| 검증 | 결과 |
|---|---|
| Python Gateway 단위 테스트 | PASS — 116 tests |
| Gradle test/lint/assemble | PASS — 516 tasks, BUILD SUCCESSFUL |
| Codex protocol fixture | PASS |
| debug clean-room scan | PASS |
| Grok CLI artifact 검사 | PASS |
| Grok chat-only profile 검사 | PASS |
| Grok ACP contract | PASS |
| secureDebug APK 감사 | PASS |
| 민감정보 evidence scan | PASS |
| `git diff --check` | PASS |
| 전체 milestone gate | **FAIL** — reference source map 기본 경로가 현재 환경에 없고, 실제 `alpine-llm-gateway` 경로로 재시도하면 `settings.gradle.kts` reference hash가 불일치 |

`secureDebug` lint는 오류 0건, 경고 11건이었다. 보안과 직접 연결되는 경고는 `dataExtractionRules` 누락이며 SEC-004에 반영했다. 전체 gate 실패는 제품 취약점의 직접 증거는 아니지만, 기준 소스 drift를 감지하는 보안 검증이 현재 완전한 녹색 상태가 아니라는 의미다. 배포 전에 reference path와 승인된 hash를 명시적으로 갱신해야 한다.

## 7. 권장 수정 순서

### P0 — 공개 배포 전 필수

1. **SEC-001:** Android↔Gateway를 private Unix domain socket/peer UID 검증 구조로 전환한다.
2. **SEC-003:** patched Alpine rootfs, package-level SBOM, CI 취약점 gate를 적용한다.
3. **SEC-004:** credential/runtime을 `noBackupFilesDir`로 이동하고 cloud/D2D exclusion을 추가한다.
4. reference source map 경로와 hash drift를 검토해 전체 security milestone gate를 녹색으로 만든다.
5. production release variant와 배포 서명키 관리, 재현 가능한 provenance를 별도 확정한다. 현재 `secureDebug`는 non-debuggable이지만 debug key 기반 검증용 빌드다.

### P1 — 첫 공개 버전 전 권장

1. **SEC-002:** 별도 UID/최소 IPC 기반 runtime 격리를 설계한다. 당장 적용하지 않으면 위협 모델과 공식 CLI 신뢰 전제를 명확히 문서화한다.
2. **SEC-005:** connection timeout, 동시성 상한, 부하/slowloris 회귀 테스트를 추가한다.
3. **SEC-006/007:** Kotlin 보안 업데이트와 Gradle dependency verification/locking/wrapper checksum을 적용한다.

### P2 — 방어 심화

1. **SEC-008:** nonce cache 용량과 복구 정책을 실측 요청률에 맞게 조정한다.
2. 현재 사용하지 않는 legacy device-login UI와 clipboard 경로를 제거해 향후 실수로 재활성화되는 위험을 줄인다.
3. 실제 Samsung 단말에 악성 loopback server 테스트 앱, D2D/Smart Switch, process crash/restart, 반복 OAuth 회귀 시나리오를 추가한다.

## 8. 배포 판정 기준

| 환경 | 현재 판정 | 조건 |
|---|---|---|
| 개발자 소유 Samsung 테스트 단말 | 제한적 허용 | 신뢰된 앱만 설치, USB/무선 ADB 통제, 민감한 실데이터 최소화 |
| 내부 제한 베타 | 조건부 보류 | P0 완료, 전체 gate PASS, 세션 폐기·사고 대응 절차 필요 |
| 공개 프로덕션 배포 | NO-GO | P0와 주요 P1 완료, release signing/provenance, 침투·부하 테스트 필요 |

## 9. 참고 기준 및 공개 권고

- [Android 보안 체크리스트](https://developer.android.com/privacy-and-security/security-tips)
- [Android 애플리케이션 샌드박스와 UID](https://developer.android.com/guide/components/fundamentals)
- [Android Auto Backup 및 data extraction rules](https://developer.android.com/identity/data/autobackup)
- [Android 12 D2D `allowBackup` 호환성 변경](https://developer.android.com/about/versions/12/reference/compat-framework-changes)
- [Android `LocalSocket` 및 peer credentials](https://developer.android.com/reference/android/net/LocalSocket)
- [Python `http.server` 보안 주의사항](https://docs.python.org/3/library/http.server.html)
- [Gradle Dependency Verification](https://docs.gradle.org/current/userguide/dependency_verification.html)
- [Gradle 보안 가이드](https://docs.gradle.org/current/userguide/security.html)
- [Alpine Linux release branches](https://www.alpinelinux.org/releases/)
- [Alpine v3.21 `libcrypto3` 패키지](https://pkgs.alpinelinux.org/package/v3.21/main/aarch64/libcrypto3)
- [OpenSSL 3.3 취약점 목록](https://www.openssl-library.org/news/vulnerabilities-3.3/)
- [Alpine CVE-2025-15467 권고](https://security.alpinelinux.org/vuln/CVE-2025-15467)
- [Kotlin build cache 권고 GHSA-r937-wjx7-w2jp](https://osv.dev/vulnerability/GHSA-r937-wjx7-w2jp)
- [Kotlin release 정보](https://kotlinlang.org/docs/releases.html)

---

본 보고서의 우선순위는 "현재 통제를 유지하면서 가장 먼저 실제 credential/prompt 노출 경로를 제거한다"는 원칙으로 정했다. 코드 수정은 별도 승인 후 P0부터 단계적으로 진행하는 것이 적절하다.
