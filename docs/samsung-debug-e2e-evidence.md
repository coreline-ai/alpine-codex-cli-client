# Samsung debug E2E evidence (redacted)

Date: 2026-08-12

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

## OAuth progress and pending actions

The official Device Code challenge was started once and its browser approval page was opened. The app's code-copy action and a single browser paste/submit attempt were performed without reading, logging, recording, or retaining the code or URL. After user browser approval, the app-private loopback account check returned only the authenticated boolean. No account identifier or auth payload was read, recorded, or retained. Dynamic model selection, independently observed real chat, Stop, force-stop recovery, logout, and Runtime shutdown remain pending.
