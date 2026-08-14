# Grok Phase 9 handoff

Date: `2026-08-14 KST`

이 문서는 현재 작업 트리와 Samsung secure-debug 상태를 다음 개발자에게 넘기기 위한 기준선이다.
실제 계정 정보, OAuth URL/challenge, model 내용, prompt/response, raw log와 capture는 포함하지 않는다.

## 현재 인계 요약

| 항목 | 현재 상태 |
|---|---|
| 제품 경로 | 공식 Grok CLI `1.0.0` → pinned ACP → app-private Python Gateway → Android Compose |
| 최신 수정 | Grok account UI의 exact `accounts.x.ai` host 허용, 클릭 즉시 `LOGIN STARTING` 진행 표시 |
| secure APK | `161137250` bytes, SHA-256 `1a396b48ad1035250f0792541079689e9c4f76006436f0eb8495044d7c18a7f1` |
| 설치 방식 | `dev.alpine.codexclient.debug`에 데이터 보존 update-install; version code `2`, non-debuggable 유지 |
| Samsung 상태 | `SM-S931N`, API 36, app/PRoot/Python/Grok/Codex `1/1/1/1/0` |
| 전면 UI | Grok 선택, `DEVICE OAUTH`, 활성 `Grok 로그인` 버튼 |
| 실제 OAuth | host 보정 뒤에는 시작하지 않음; 브라우저 계정 승인이 다음 사용자 동작 |
| 실제 모델/턴 | dynamic catalog, 실제 1턴, Stop, lifecycle, logout, cleanup 미실행 |

## 완료된 구현과 검증

- Gateway와 Android는 Grok 로그인 URL에 exact `auth.x.ai`와 `accounts.x.ai`만 허용한다.
  subdomain/suffix lookalike, userinfo, 비표준 port, fragment와 non-HTTPS URL은 계속 차단한다.
- 로그인 시작 중 `refreshing` 상태를 `LOGIN STARTING`, progress indicator,
  `로그인 주소 요청 중`, 비활성 `로그인 준비 중…`으로 표시한다.
- 공식 CLI의 `authenticate`는 사용자 action당 한 번만 시작하고, URL 준비 polling은 동일
  sequence와 전체 15초 deadline 안에서만 수행한다.
- Python 전체 `103/103` PASS.
- 전체 JVM/Android unit, `lintDebug`, `lintSecureDebug`, debug/test/secure APK build PASS.
- Samsung Android 16의 credential-free `AgentChatSurfaceInstrumentedTest` `6/6` PASS.
- Codex/Grok protocol, clean-room, Grok artifact/profile/ACP, secure APK, sensitive evidence,
  source map, Runtime manifest 검증 PASS.
- `git diff --check`와 sensitive evidence scan PASS.

전체 credential-free 검증 명령:

```bash
sh scripts/verify-secure-debug-milestone.sh
```

## 다음 작업 순서

1. 입력 직전에 approved Samsung alias, target package focus와
   app/PRoot/Python/Grok/Codex `1/1/1/1/0`을 다시 확인한다.
2. 사용자가 앱의 `Grok 로그인`을 정확히 한 번 누른다.
3. 앱에서 `LOGIN STARTING` 진행 상태가 즉시 표시되고 공식 xAI 브라우저로 전환되는지만 확인한다.
4. 브라우저 승인은 사용자가 직접 완료한다. 앱에서는 `authenticated=true` Boolean만 확인한다.
5. dynamic model catalog가 1개 이상이며 선택값이 실제 catalog에 포함되는지만 확인한다.
6. 금지 profile event 5종이 모두 0인 상태에서만 별도 승인된 무민감 실제 1턴과 Stop 검증으로
   진행한다.
7. lifecycle/Codex 무과금 회귀/Grok 재선택 뒤, 별도 승인을 받아 Grok logout과 Runtime cleanup을
   수행한다.

실제 turn은 [Samsung secure-debug runbook](samsung-grok-secure-debug-runbook.md)의 audit 절차를
따르고, 결과는 [redacted E2E evidence](samsung-grok-secure-debug-e2e.md)에 count와 enum만 기록한다.

## 중단 및 승인 경계

- OAuth 실패·취소·만료 시 자동 재시작하거나 두 번 누르지 않는다.
- 실제 Codex 유료 turn과 Grok logout은 각각 별도 사용자 승인이 필요하다.
- uninstall, clear-data, credential 파일 읽기/삭제, release signing, 다른 기기·앱 mutation은 금지한다.
- OAuth 시작 뒤 screenshot, screen recording, UI hierarchy dump, broad logcat, browser 내용 기록을
  하지 않는다.
- prompt 자동 retry/replay 또는 다른 Agent fallback이 관찰되면 즉시 중단한다.

## Git 인계 상태

- Branch: `codex/implement-grok-agent`
- 기능 통합 commit: `7c0f88a` (`feat: finalize Grok agent secure debug client`)
- Base: `origin/main` `72e88c1`
- 기능 통합 commit에는 Phase 9 후속 수정, Alpine Agent 테마, 회귀 테스트와 handoff 문서가
  함께 포함됐다.
- 2026-08-14 사용자 요청으로 기존 no-push 제한이 해제되어, 이 문서를 포함한 최종 HEAD를
  `origin/main`에 fast-forward push했다.
- 동일 이름의 remote feature branch는 만들지 않았다.
- 기존 사용자 변경을 분리하거나 되돌리기 위해 reset/stash/rebase하지 않는다.

인계 시 `git status --short --branch`가 clean이고 `git rev-parse HEAD origin/main`이 동일한지
확인한다. 실제 OAuth·model·유료 turn·logout gate는 아래 완료되지 않은 Gate로 계속 추적한다.

## 주요 파일

- [Grok URL 검증](../codex_gateway/agents/grok.py)
- [Android URL 검증·로그인 상태](../app/src/main/java/dev/alpine/codexclient/AgentChatViewModel.kt)
- [로그인 진행 UI](../app/src/main/java/dev/alpine/codexclient/AgentChatScreen.kt)
- [Gateway 회귀](../tests/test_grok_agent_adapter.py)
- [Compose UI 회귀](../app/src/androidTest/java/dev/alpine/codexclient/AgentChatSurfaceInstrumentedTest.kt)
- [Agent 워크플로 회귀](../app/src/androidTest/java/dev/alpine/codexclient/AgentChatWorkflowInstrumentedTest.kt)
- [실행 계획](../dev-plan/implement_20260812_130217.md)

## 완료되지 않은 Gate

- [ ] host 보정 build에서 official Grok Device OAuth 성공
- [ ] actual dynamic model catalog 표시·선택
- [ ] 실제 Grok streaming 1턴과 content-free chat audit
- [ ] 별도 Stop과 content-free stop audit
- [ ] background/foreground와 force-stop 안전 복구
- [ ] Codex 무과금 회귀와 Grok 재선택
- [ ] 별도 승인 후 official Grok logout
- [ ] Runtime 종료 후 관련 child process 0
- [x] 누적 working tree 검토 후 기능 통합 commit `7c0f88a`
- [x] 사용자 명시 요청 후 최종 HEAD를 `origin/main`에 fast-forward push
