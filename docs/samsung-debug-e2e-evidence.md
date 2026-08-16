# Samsung debug E2E evidence (redacted)

Date: 2026-08-15

> 이 문서는 시점별 Samsung artifact evidence다. 표의 package version, APK 상태와 116/516 수치는
> 당시 build에만 적용된다. 현재 source 기준선과 private UDS/offline Python/release 상태는
> [`project-overview.md`](project-overview.md)를 참고한다.

## Scope and privacy boundary

- Device: Samsung `SM-S931N` (`arm64-v8a`; serial redacted).
- Target: `dev.alpine.codexclient.debug` version `0.1.0-debug` only.
- Existing reference debug app observed without mutation: `dev.alpine.integrated.debug` version `0.3.0-debug`.
- Build/install path: debug APK only; no release task, store key, release signing, API key, or app OAuth client ID.
- No Device Code challenge, verification code, account data, dynamic-model contents, user prompt, assistant response, screenshot, or retained UI dump is included here.

## Completed pre-authentication checks

| Check | Result |
|---|---|
| E2E-IN-01 | Target debug APK installed while the existing reference debug app remained present. |
| MainActivity launch | Resumed successfully after the ViewModel factory constructor fix. |
| E2E-RT-01 | Alpine Runtime `RUNNING`; loopback Gateway `RUNNING`. |
| E2E-CLI-01 | Pinned CLI version check returned the closed `CODEX_CLI_READY` state. |
| app-server smoke | Official CLI `initialize → account/read` returned the closed `APP_SERVER_SMOKE_READY` state. |

## Grok post-authentication completion

The official Device Code challenge was started once and its browser approval page was opened. The
app's code-copy action and a single browser paste/submit attempt were performed without reading,
logging, recording, or retaining the code or URL. After user browser approval, the app-private
loopback account check returned only the authenticated Boolean. No account identifier or auth
payload was read, recorded, or retained.

| Check | Result |
|---|---|
| OAuth and catalog | Authenticated state and live model readiness restored without automatic re-authentication. |
| Real chat | One approved synthetic Grok turn completed; content-free audit was dispatch `1`, terminal `1`, cancel `0`, retry `none`, profile `clean`. |
| Force-stop recovery | Two data-preserving relaunch cycles restored Runtime/Gateway/Grok, conversation history, and composer without a login action or automatic prompt. |
| Background/foreground | Account/session/history/composer remained available; audit count did not increase and processes remained singular. |
| Codex -> Grok | Codex readiness was inspected without a paid turn; re-selecting Grok restored its history and composer without automatic chat. |
| Real Stop | Final approved Stop audit passed with dispatch `1`, terminal `1`, cancel `1`, retry `none`, profile `clean`. |
| Final cardinality | App/PRoot/Python/Grok/Codex `1/1/1/1/0`. |
| Credential-free gate | 116 Python tests and 516 Gradle tasks passed with protocol, clean-room, artifact, profile, ACP, secure APK, evidence, reference-source, and Runtime-manifest checks. |

## Remaining separately approved actions

- Official Grok logout is not executed; it requires a separate destructive lifecycle approval.
- Runtime shutdown and child-process-zero verification are not executed; they require a separate
  cleanup approval.
- No uninstall, clear-data, credential-file access, release build, other-device mutation, Git
  commit, or Git push was performed in this implementation run.
