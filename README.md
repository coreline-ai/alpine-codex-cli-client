# Alpine Agent CLI Client

`Alpine Agent CLI Client`는 공식 Codex CLI와 공식 Grok CLI를 Android의 app-private Alpine
Runtime에서 실행하는 공개 배포 목적의 멀티 Agent 채팅 클라이언트입니다. 앱은 API key나
Provider 직접 호출을 사용하지 않고, 각 CLI가 소유한 Device OAuth와 JSONL 프로토콜만
사용합니다.

## 현재 상태

- Codex: Device Code OAuth, 동적 모델, 스트리밍 채팅, Stop, 대화 복구 구현 완료
- Grok: 공식 CLI 1.0.0 artifact, Device OAuth, 동적 모델, ACP 스트리밍, Stop 구현 완료
- 보안 gate: non-debuggable `secureDebug`, 상호 UID 검증 private UDS transport, `allowBackup=false`와
  cloud/D2D 전면 제외, versioned `noBackupFilesDir` credential/session migration, chat-only profile,
  금지 ACP 이벤트 fail-closed, 민감 화면 보호 구현 완료
- Runtime 공급망: 15개 Alpine APK package-level SPDX, artifact lock/integrity gate, 활성/직전
  rootfs 2-slot 보존과 중단 복구 가능한 명시적 롤백 경계 적용 완료. Python은 전체 rootfs를
  교체하지 않고 별도 APK 내장 Alpine 패키지 팩으로 공급하며, 앱 실행 중 저장소 다운로드를
  금지한다. 실제 고정 Python 패키지 바이트가 없으면 공개 release가 fail-closed한다. Phase 6의
  patched rootfs·완전한 취약점 DB는 사용자 결정에 따라 배포 필수 조건에서 제외
- Samsung: 공식 Grok OAuth, live model, 실제 채팅, 실제 Stop, force-stop 2회,
  background/foreground, Codex 무과금 선택 뒤 Grok 재선택까지 완료. Runtime/Gateway/Grok과
  대화 기록/composer 복구 및 process 단일성을 확인했으며, 별도 승인 대상인 logout과 Runtime
  shutdown만 미실행

## 고정 실행 경로

```text
Android Compose UI
  -> authenticated HTTP/SSE over private Unix domain socket
  -> app-private Alpine Python Gateway
  -> selected Agent JSONL stdio
  -> official Codex app-server | official Grok ACP
  -> CLI-owned OAuth account
```

한 시점에는 Runtime/Gateway 하나와 선택된 Agent process 하나만 실행됩니다. Agent 전환 중
login이나 turn이 활성 상태면 전환을 거부하며, 자동 retry·prompt replay·다른 Agent fallback은
없습니다.

## Variant

| Variant | Application ID | 디버거 | 실제 OAuth |
|---|---|---:|---:|
| `debug` | `dev.alpine.codexclient.labdebug` | 허용 | 차단 |
| `secureDebug` | `dev.alpine.codexclient.debug` | 차단 | 허용 |
| `release` | `dev.alpine.codexclient` | 차단 | 허용 |

`debug`와 `secureDebug`는 프로젝트 debug certificate를 사용합니다. `release`는 Codex/Grok/
Gateway/Runtime, 검증된 Alpine Python 패키지 팩과 공통 component inventory를 포함하며,
빌드 머신의 `ALPINE_PYTHON_PACKAGE_DIR`에 production lock/SBOM/로컬 패키지가 있고 외부에서 네 개의
`ALPINE_RELEASE_*` 환경 변수를 모두 제공한 경우에만 APK/AAB 패키징을 허용합니다. 일부만
제공하거나 서명 파일이 없으면 fail-closed합니다. 저장소는 private signing key를 생성하거나
커밋하지 않으며 패키지 팩을 다운로드하지도 않습니다. 자세한 절차는
[공개 배포 경계](docs/public-release.md)를 참고하세요.

## 개발 검증

JDK 17과 Android SDK 36이 필요합니다. 전체 credential-free gate는 다음 명령으로 실행합니다.

```bash
sh scripts/verify-secure-debug-milestone.sh
```

실제 OAuth는 credential-free gate 통과 후 Samsung runbook의 별도 승인 지점에서만 시작합니다.

## 문서

- [Architecture](docs/architecture.md)
- [Security model](docs/security-model.md)
- [Backup/D2D 및 민감 상태 migration](docs/backup-migration.md)
- [Samsung backup migration evidence](docs/samsung-backup-migration-evidence.md)
- [Samsung Grok secure-debug runbook](docs/samsung-grok-secure-debug-runbook.md)
- [Grok Phase 9 handoff](docs/grok-phase9-handoff.md)
- [Samsung Grok redacted E2E evidence](docs/samsung-grok-secure-debug-e2e.md)
- [Grok Gateway contract](docs/grok-gateway-contract.md)
- [Grok Runtime policy](docs/grok-runtime-policy.md)
- [SBOM and component inventory](docs/debug-sbom.md)
- [공개 배포 경계](docs/public-release.md)
- [Runtime supply-chain status](docs/runtime-supply-chain.md)
- [Gradle supply-chain status](docs/gradle-supply-chain.md)
- [APK 내장 Python 런타임 개발 계획](dev-plan/implement_20260815_141917.md)
- [Reference source map](docs/reference-source-map.md)
- [최신 Grok lifecycle/Stop 개발 계획](dev-plan/implement_20260815_070753.md)
- [Grok 기반 구현 계획](dev-plan/implement_20260812_130217.md)

상세 구현 순서와 검증 기준은 [개발 계획](dev-plan/implement_20260811_133123.md)을 따른다.

## 라이선스

이 프로젝트는 **GNU General Public License v3.0 (GPL-3.0)**으로 배포됩니다.
