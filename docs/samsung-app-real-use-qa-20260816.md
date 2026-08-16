# Samsung app real-use QA evidence — 2026-08-16

이 문서는 기존 설치 candidate `A-installed`에 대한 비파괴 app-level baseline이다. 현재 source로
빌드한 `B-current-source` 또는 signed release `C`의 인수 증거가 아니다.

## 대상과 안전 경계

| 항목 | 값 |
|---|---|
| device | Samsung `SM-S931N`, API 36, `arm64-v8a` |
| package | `dev.alpine.codexclient.debug` |
| version | versionCode `2`, `0.2.0-secure-debug` |
| APK SHA-256 | `469e01c16c928e67917f5e5dac51ddd2ece72f879b91b5c229357eb4ecfcf3a5` |
| debuggable | `false` |
| QA schema | `alpine-app-real-use-qa/v1` |

연결된 다른 기기에는 command를 보내지 않았다. uninstall, clear-data, logout, OAuth, Agent/model
선택, 입력창 입력, Send, Stop, Runtime shutdown은 수행하지 않았다. account/model/prompt/response,
OAuth URL/code, screenshot, raw UI hierarchy, raw log line은 보존하지 않았다.

## 신규 독립 QA Agent 결과

신규 `fresh_app_qa` Agent는 기존 module 결과를 PASS 근거로 사용하지 않고 launcher에서 아래
사용자 lifecycle을 직접 실행했다.

| 항목 | 결과 |
|---|---|
| launcher component | PASS |
| cold/foreground launch | PASS |
| HOME background에서 app PID 보존 | PASS |
| foreground 복귀 | PASS |
| target package force-stop | PASS, app PID `1 → 0` |
| force-stop 후 relaunch | PASS |
| 안정화 후 app/PRoot/Python/Grok/Codex | PASS, `1/1/1/1/0` |

독립 Agent는 실제 secureDebug Activity→Runtime→Gateway 사용자 흐름의 자동 회귀 공백,
설치 artifact와 current working tree provenance 공백, Runtime 준비 시간의 변동 위험을 별도
발견사항으로 제출했다.

## 반복 harness 결과

[`../scripts/app_real_use_qa.py`](../scripts/app_real_use_qa.py)를 사용해 같은 target에서
baseline, launch, HOME→foreground, force-stop→relaunch를 실행했다.

| 항목 | 결과 |
|---|---|
| baseline | PASS |
| launcher shell | PASS, Agent/Model/Send 정적 control 관측 |
| 현재 foreground launch shell/backend | PASS, `2.188s` / `2.318s` |
| HOME→foreground | PASS, warm launch `20~22ms` |
| force-stop 후 Activity launch | PASS, corrected run `695~720ms` |
| force-stop 후 shell ready | PASS, corrected run `2.883~2.908s` |
| force-stop 후 selected Grok backend ready | PASS, corrected run `5.161~5.212s` |
| 최종 app/PRoot/Python/Grok/Codex | PASS, `1/1/1/1/0` |
| TCP `8787` listener | PASS, `0` |
| lifecycle 중 자동 turn audit 증가 | PASS, `0` |
| 기기 임시 UI dump 잔여 | PASS, `0` |

독립 QA 실행에서는 Runtime 역할 전체 관측에 약 `15s`가 걸린 사례도 있었다. candidate B에서는
같은 recovery를 3회 이상 반복해 p50/p95를 기록하고, 사용자에게 보이는 준비 상태/SLA를
확정해야 한다.

## QA 도중 발견하고 수정한 false PASS

최초 harness는 force-stop 후 Activity shell이 ready인 시점에 Grok process가 아직 `0`이어도
PASS할 수 있었다. UI shell과 backend 준비를 분리하고, force-stop 전 선택된 Agent process가
복구될 때까지 기다리도록 수정했다. 수정 뒤 shell `2.883s`, backend `5.161s`, 최종
`1/1/1/1/0`을 각각 확인했다.

## 승인형 Grok 실제 사용자 turn/Stop

사용자의 `실 테스트 진행` 지시에 따라 기존 인증을 유지한 상태에서 정상 synthetic turn과 별도
Stop turn을 각각 정확히 한 번 실행했다. 사용자 입력과 Provider 출력은 수집·출력·보존하지 않고
고정된 content-free audit만 검증했다.

| 항목 | 결과 |
|---|---|
| 정상 Grok turn | PASS — dispatch `1`, terminal `1`, cancel `0` |
| 정상 turn retry/profile | PASS — retry `none`, 금지 profile counter 모두 `0` |
| Stop lifecycle | PASS — started `1`, stop requested/dispatched `1`, duplicate Stop `0` |
| Stop terminal | PASS — dispatch `1`, terminal `1`, cancel `1`, retry `none` |
| late terminal/자동 replay | PASS — 증가 `0` |
| turn/Stop 직후 process | PASS — app/PRoot/Python/Grok/Codex `1/1/1/1/0` |
| post-turn HOME→foreground | PASS — audit 증가 `0` |
| post-turn force-stop→launch | PASS — backend 약 `5.16s`, audit 증가 `0` |
| 최종 교차 baseline | PASS — terminal audit line `2`, TCP `8787` listener `0` |
| Agent selector sheet | PASS — Codex/Grok option 존재, 선택 변경 없이 dismiss |
| Model selector sheet | PASS — 선택 Grok model sheet 존재, 선택 변경 없이 dismiss |
| selector probe 상태 영향 | PASS — process unchanged, 자동 turn `0`, TCP listener `0` |

Stop의 두 lifecycle message는
[`../scripts/verify-agent-turn-state-audit.py`](../scripts/verify-agent-turn-state-audit.py), terminal
line은 기존 `verify-agent-turn-audit.py`의 대응 mode로 검증한다.

## 판정

- `A-installed` 비파괴 lifecycle baseline: **PASS**
- 현재 source의 APK 내장 Python 변경: **검증하지 않음**
- 기존 Grok 인증 상태의 정상 turn/Stop: **A-installed PASS**
- Agent/Model sheet open/dismiss: **A-installed PASS**, 실제 선택값 변경은 미수행
- OAuth 재시작, Agent/model 값 변경, Codex live turn과 전체 오류/race: **검증하지 않음**
- current-source 별도 `labdebug` production pack fresh install/Gateway/force-stop: **PASS**
- `B-current-source` secureDebug full app QA: **BLOCKED — 빌드는 완료, 기존 실계정 앱 설치·전체 흐름 필요**
- `C-signed-release` 인수: **BLOCKED — release signing 입력 필요**

다음 실행은 [`app-real-use-qa.md`](app-real-use-qa.md)와
[`../dev-plan/implement_20260816_081114.md`](../dev-plan/implement_20260816_081114.md)의
QA-1→QA-4 순서를 따른다.
