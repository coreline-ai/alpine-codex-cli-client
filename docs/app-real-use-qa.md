# App real-use QA

기준일: `2026-08-16 KST`

이 문서는 module/unit/instrumentation 검증과 별도로, 실제 Android launcher와 system lifecycle을
통해 사용자가 앱을 여는 경로를 반복 검증하는 방법을 정의한다. 전체 항목과 진행 상태는
[`../dev-plan/implement_20260816_081114.md`](../dev-plan/implement_20260816_081114.md)를 기준으로
한다.

## 판정 원칙

- unit/Gradle/Python test가 green이어도 app-level QA는 자동으로 PASS가 아니다.
- 설치된 과거 APK, current source APK, signed release를 서로 다른 candidate로 관리한다.
- 결과에는 고정 Boolean, enum, count, duration, stable error code만 남긴다.
- OAuth URL/code, account, token, model 이름, prompt/response, screenshot, raw UI hierarchy와 broad
  logcat은 수집하거나 Git에 저장하지 않는다.
- 실계정 OAuth, 실제 turn, Stop, logout과 data 삭제는 이 자동 harness의 기능이 아니다.
- 다른 연결 기기에는 command를 보내지 않으며 모든 ADB action에 exact serial을 사용한다.

## Candidate

| ID | 정의 | 사용할 수 있는 판정 |
|---|---|---|
| `A-installed` | 이미 Samsung에 설치돼 사용 중인 APK | 데이터 보존형 비파괴 baseline만 |
| `B-current-source` | 현재 source로 빌드한 secureDebug APK | current-source full app QA |
| `C-signed-release` | production Python pack과 외부 signing 입력을 포함한 APK/AAB | 공개 배포 최종 인수 |

Candidate는 package, versionCode/versionName, source revision, dirty diff digest, APK SHA-256,
signing certificate로 고정한다. A의 성공을 B/C 결과로 대체하지 않는다.

## 자동 harness

[`../scripts/app_real_use_qa.py`](../scripts/app_real_use_qa.py)는 실제 launcher/system action만
수행한다.

| Scenario | 사용자 관점 동작 | 데이터 변경 |
|---|---|---|
| `baseline` | 기기·package·artifact·process·TCP 상태 확인 | 없음 |
| `launch` | MainActivity 열기, shell와 Runtime backend 준비 확인 | Activity foreground |
| `background-resume` | 앱 열기 → HOME → 앱 복귀 | Activity background/foreground |
| `force-stop-relaunch` | 앱 열기 → target package force-stop → 재실행 | process 재생성, app data 보존 |
| `selector-probe` | Agent/Model sheet를 열어 고정 heading만 확인하고 Back | 선택값 변경 없음 |
| `smoke` | launch/background/force-stop scenario 연속 실행 | 해당 lifecycle 항목의 합 |

`baseline` 외 scenario는 `--confirm-app-control` 없이는 fail-closed한다. 허용 package는 이
프로젝트의 labdebug/secureDebug/release application ID로 제한한다. model과 ABI가 예상값과
다르면 UI action 전에 중단한다.

```sh
python3 scripts/app_real_use_qa.py \
  --adb "$HOME/Library/Android/sdk/platform-tools/adb" \
  --serial '<승인된-Samsung-serial>' \
  --package dev.alpine.codexclient.debug \
  --expected-model SM-S931N \
  --expected-abi arm64-v8a \
  --scenario baseline

python3 scripts/app_real_use_qa.py \
  --adb "$HOME/Library/Android/sdk/platform-tools/adb" \
  --serial '<승인된-Samsung-serial>' \
  --package dev.alpine.codexclient.debug \
  --expected-model SM-S931N \
  --expected-abi arm64-v8a \
  --scenario smoke \
  --wait-seconds 45 \
  --confirm-app-control
```

JSON schema는 `alpine-app-real-use-qa/v1`이다. `--output`을 사용할 때도 같은 redacted
payload만 기록한다. UI hierarchy는 기기 임시 파일에서 allowlisted control Boolean으로 축약한
뒤 `finally`에서 삭제한다.

## Readiness 판정

앱 화면이 보이는 것과 채팅 backend가 준비된 것은 별도 상태다.

1. `shell ready`: Activity가 resumed이고 Agent/Model/Send의 정적 control이 관측된다.
2. `backend ready`: app, PRoot, Python Gateway가 각각 하나이고, 시작 전에 선택 Agent가 확인된
   경우 해당 Codex 또는 Grok process도 하나다.
3. lifecycle 동안 content-free turn audit count가 증가하지 않는다.
4. TCP `8787` listener가 없다.

shell만 ready인 중간 상태를 전체 앱 PASS로 판정하지 않는다. 복구 시간도
`shell_ready_time_ms`와 `backend_ready_time_ms`로 분리한다.

## 전체 검증 단계

1. **QA-0 계약**: target allowlist, safe evidence schema, redaction test를 고정한다.
2. **QA-1 결정적 app E2E**: 별도 `qaDebug` application ID와 fake Gateway로 실제 Activity에서
   OAuth 취소/만료, model empty, streaming, Stop race, process loss를 재현한다.
3. **QA-2 secureDebug 비유료 실기기**: data-preserving launch/background/force-stop/relaunch를
   최소 3회 반복하고 process cardinality와 p50/p95를 기록한다.
4. **QA-3 승인형 live 검증**: 명시 승인 뒤 Agent별 OAuth, model, 정상 turn 1회, Stop 1회만
   content-free audit로 확인한다.
5. **QA-4 release gate**: 동일 C candidate에서 지원 API/화면폭/device matrix와 핵심 live 인수를
   다시 실행한다.

## 아직 자동화하지 않은 영역

- browser에서의 실제 OAuth 승인과 앱 자동 복귀
- Agent/model 실제 선택값 변경과 conversation 화면 조작
- 유료 Provider를 호출하는 실제 chat/streaming/Stop
- TalkBack, font scale, rotation, 저메모리, 장시간 endurance
- fresh-install/update-install과 current source APK provenance

이 항목을 `A-installed`에서 임의 실행하지 않는다. 먼저 B candidate와 격리된 `qaDebug`
environment를 준비한 뒤 계획의 Phase 3~7 순서로 수행한다.
