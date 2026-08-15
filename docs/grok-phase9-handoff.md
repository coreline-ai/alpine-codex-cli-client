# Grok Phase 9 handoff

Date: `2026-08-15 KST`

이 문서는 현재 누적 작업 트리와 Samsung secure-debug 검증 상태의 인계 기준선이다. 실제 계정
정보, OAuth URL/challenge, model 내용, prompt/response, raw log와 capture는 포함하지 않는다.

## 현재 인계 요약

| 항목 | 현재 상태 |
|---|---|
| 제품 경로 | 공식 Grok CLI `1.0.0` -> pinned ACP -> app-private Python Gateway -> Android Compose |
| secure APK | `161150250` bytes, SHA-256 `d104be3f1d3e6aff21953356fe41e45e1e30bd2ca3832406f82783cc407ca769`, version code 2 |
| 설치 방식 | 같은 서명/패키지의 `install -r`; app-private 데이터와 OAuth 상태 보존 |
| Samsung 상태 | 승인된 `SM-S931N`에서 앱/PRoot/Python/Grok/Codex `1/1/1/1/0`; 다른 연결 기기 미변경 |
| 실제 OAuth/모델 | 공식 브라우저 OAuth 완료, authenticated Boolean과 live model readiness 확인 |
| 실제 채팅 | 승인된 합성 Grok turn과 content-free chat audit 완료 |
| 복구/전환 | force-stop 2회, background/foreground, Codex 무과금 선택 후 Grok 재선택 완료 |
| 실제 Stop | 최종 dispatch `1`, terminal `1`, cancel `1`, retry `none`, profile `clean` |

Runtime/Gateway의 실행 의도는 credential과 분리된 app-private Boolean으로 유지한다. 앱 프로세스
종료나 APK update-install 뒤 foreground가 되면 Runtime 설치/복구, Python 실재 확인, Gateway
시작을 자동 직렬화하며 OAuth `authenticate`를 다시 호출하지 않는다. 사용자가 `Runtime 종료`나
foreground 알림의 중지를 명시적으로 누르면 자동 복구가 꺼지고 다음 수동 시작 때 다시 켜진다.

## 완료된 핵심 수정

### Persisted OAuth와 브라우저 복귀

- 공식 OAuth-only `authMethods`의 신규 로그인 및 `cached_token` shape만 허용한다. 그 밖의
  method, 순서, 부분 shape는 fail-closed한다.
- `cached_token`은 READY 공개 전에 공식 ACP 동작과 같은 eager authenticate를 거치며
  account/profile 응답 본문은 저장하지 않는다.
- 브라우저 완료 직후 `auth/info.methodId` 반영 지연은 같은 사용자 attempt 안에서 최대 15초
  content-free bounded polling으로 확인한다. 자동 재인증은 하지 않는다.
- 브라우저에서 앱으로 복귀하면 request status와 account Boolean을 즉시 reconciliation하고,
  authenticated이면 pending UI를 닫고 model catalog를 다시 로드한다.

### Session과 Runtime 복구

- Android history와 Grok ACP binding을 분리했다. Gateway는 prompt, response, OAuth 값 없이
  bounded conversation-to-session binding만 private Grok home에 `0600`으로 저장한다.
- 같은 generation은 `session/resume`, Gateway 재생성은 공식 `session/load`를 사용한다.
- Runtime start future는 Host session/state publication 후에만 완료되고, 복구 health probe가
  실패하면 Runtime -> Python -> Gateway chain을 완전히 재구성한다.
- loopback listener는 `SO_REUSEADDR`만 사용해 closed port의 `TIME_WAIT` 재기동을 허용하고,
  두 번째 live listener는 계속 거부한다.

### Stop terminal 계약

- Grok CLI 1.0.0은 `session/cancel`을 승인한 뒤에도 outstanding `session/prompt` RPC를 Android
  SSE read limit보다 오래 유지할 수 있었다.
- `GrokAgentAdapter.interrupt()`는 cancel 승인 직후 redacted profile/prompt counter를 갱신하고
  `turn_interrupted` terminal을 정확히 한 번 게시한다. 늦은 prompt/notification은 기존
  `active.terminal` guard가 차단한다.
- Android `AgentTurnStateAudit`는 내용 없이 request-bound `started`와 실제
  `stop_requested dispatched` checkpoint만 기록한다. terminal line은 기존
  `AgentTurnAudit` 검증기를 그대로 통과해야 한다.

## 검증 기준선

| Gate | 결과 |
|---|---|
| Python | PASS: 전체 116 tests; non-socket 93 + loopback HTTP/HMAC 23 |
| Android/JVM | PASS: Runtime Host, debug/secureDebug unit, lint, APK 및 test APK; 516 Gradle tasks |
| Grok | PASS: protocol, clean-room, CLI artifact, locked chat-only profile, ACP contract |
| Security | PASS: non-debuggable secure APK audit, sensitive evidence scanner |
| Reference | PASS: source map와 Runtime manifest/adaptation gate |
| Formatting | PASS: `git diff --check` 기준 |

전체 credential-free 검증 명령:

```bash
sh scripts/verify-secure-debug-milestone.sh
```

이번 검증 중 reference 저장소의 live working tree가 다른 작업에 의해 변경되고 있었으므로 문서화된
기준 commit `b81a7d8ee12af72ff95180bfeadabe68e5be950e`의 immutable snapshot을
`ALPINE_REFERENCE_REPO`로 지정해 재현했다. source-map gate는 실제 import 대상만 비교하도록
정정했고, Runtime Host의 intentional adaptation 두 건은 destination hash와 회귀 근거를 manifest에
동기화했다. live reference working tree는 수정하거나 정리하지 않았다.

## 실제 Samsung 완료 상태

1. 데이터 보존 update-install 뒤 OAuth, live model readiness, history, composer를 복구했다.
2. 승인된 합성 Grok 채팅과 content-free chat audit를 완료했다.
3. immediate force-stop/relaunch 2회 뒤 process 단일성, history, composer, official
   `session/load` 기반 후속 turn을 확인했다.
4. background/foreground 뒤 자동 prompt 없이 인증/history/composer와 `1/1/1/1/0`을 유지했다.
5. Codex를 선택해 readiness까지만 확인하고 실제 메시지를 보내지 않은 뒤 Grok을 재선택했다.
6. pre-fix 실제 Stop에서 cancel-ack 후 terminal 지연을 재현하고 위 결함을 수정했다.
7. 최종 승인된 Stop은 state checkpoint와 terminal audit 모두 통과했고, HOME 복귀 후 history와
   composer가 복구됐다.

세부 redacted 근거는 [Samsung Grok E2E evidence](samsung-grok-secure-debug-e2e.md), 재현 절차는
[Samsung secure-debug runbook](samsung-grok-secure-debug-runbook.md)을 따른다.

## 잔여 항목과 승인 경계

- [x] official Grok Device OAuth 성공
- [x] live dynamic model catalog 준비/선택
- [x] 실제 Grok streaming turn과 content-free chat audit
- [x] force-stop, background/foreground 안전 복구
- [x] Codex 무과금 선택과 Grok 재선택
- [x] 실제 Grok Stop과 content-free stop audit
- [ ] 별도 사용자 승인 후 official Grok logout
- [ ] 별도 사용자 승인 후 Runtime 종료와 관련 child process `0`
- [ ] 누적 working tree 검토 후 별도 요청에 따른 Git commit/push

logout, Runtime 종료, uninstall, clear-data, credential 파일 읽기/삭제, release signing, 다른
기기/app mutation은 이번 구현 범위에서 수행하지 않았다. 실제 Codex 유료 turn, prompt 자동
retry/replay, 다른 Agent fallback도 발생하지 않았다.

## Git 인계 상태

- Branch: `main`
- 작업 시작 기준 HEAD와 `origin/main`: `ec94f99e45982af8fffc6d1b9e96b7362c2c3d43`
- 현재 변경은 Phase 9 누적 구현과 이번 lifecycle/Stop/reference/document 수정이 함께 있는
  uncommitted working tree다.
- reset, stash, rebase, checkout으로 사용자 변경을 분리하거나 되돌리지 않았다.
- 이번 구현 요청에서는 commit, pull, push를 수행하지 않았다.

## 주요 파일

- [Grok adapter와 Stop 계약](../codex_gateway/agents/grok.py)
- [Android Agent 상태와 audit 연결](../app/src/main/java/dev/alpine/codexclient/AgentChatViewModel.kt)
- [Content-free turn audit](../app/src/main/java/dev/alpine/codexclient/AgentTurnAudit.kt)
- [Gateway regression](../tests/test_grok_agent_adapter.py)
- [Android audit regression](../app/src/test/java/dev/alpine/codexclient/AgentTurnAuditTest.kt)
- [Reference source map](reference-source-map.md)
- [Reference Runtime adaptations](reference-runtime-adaptations.md)
- [현재 개발 계획](../dev-plan/implement_20260815_070753.md)
