# Alpine Agent CLI Client

`Alpine Agent CLI Client`는 공식 Codex CLI와 공식 Grok CLI를 Android의 app-private Alpine
Runtime에서 실행하는 debug 전용 멀티 Agent 채팅 클라이언트입니다. 앱은 API key나 Provider
직접 호출을 사용하지 않고, 각 CLI가 소유한 Device OAuth와 JSONL 프로토콜만 사용합니다.

## 현재 상태

- Codex: Device Code OAuth, 동적 모델, 스트리밍 채팅, Stop, 대화 복구 구현 완료
- Grok: 공식 CLI 1.0.0 artifact, Device OAuth, 동적 모델, ACP 스트리밍, Stop 구현 완료
- 보안 gate: non-debuggable `secureDebug`, 인증된 loopback transport, app-private credential 격리,
  chat-only profile, 금지 ACP 이벤트 fail-closed, 민감 화면 보호 구현 완료
- Samsung: 최신 host/UI 보정 secure APK 설치와 credential-free 전체 gate 완료. Runtime/Gateway/Grok
  준비 상태이며, host 보정 뒤 공식 브라우저 OAuth 승인·실제 1턴·Stop·force-stop·logout은 미실행

## 고정 실행 경로

```text
Android Compose UI
  -> authenticated loopback HTTP/SSE
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

두 variant 모두 프로젝트 debug certificate만 사용합니다. release signing, store key,
release APK/AAB는 이 저장소의 실행 경로가 아닙니다.

## 개발 검증

JDK 17과 Android SDK 36이 필요합니다. 전체 credential-free gate는 다음 명령으로 실행합니다.

```bash
sh scripts/verify-secure-debug-milestone.sh
```

실제 OAuth는 credential-free gate 통과 후 Samsung runbook의 별도 승인 지점에서만 시작합니다.

## 문서

- [Architecture](docs/architecture.md)
- [Security model](docs/security-model.md)
- [Samsung Grok secure-debug runbook](docs/samsung-grok-secure-debug-runbook.md)
- [Grok Phase 9 handoff](docs/grok-phase9-handoff.md)
- [Samsung Grok redacted E2E evidence](docs/samsung-grok-secure-debug-e2e.md)
- [Grok Gateway contract](docs/grok-gateway-contract.md)
- [Grok Runtime policy](docs/grok-runtime-policy.md)
- [Debug SBOM and component inventory](docs/debug-sbom.md)
- [Reference source map](docs/reference-source-map.md)
- [Grok 개발 계획](dev-plan/implement_20260812_130217.md)

상세 구현 순서와 검증 기준은 [개발 계획](dev-plan/implement_20260811_133123.md)을 따른다.

## 라이선스

이 프로젝트는 **GNU General Public License v3.0 (GPL-3.0)**으로 배포됩니다.
