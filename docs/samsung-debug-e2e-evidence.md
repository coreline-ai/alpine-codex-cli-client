# Samsung debug E2E evidence (redacted)

Date: 2026-08-11

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

## Pending user-authorized actions

Device Code challenge creation, browser sign-in/approval, authenticated account check, dynamic model selection, real chat, Stop, force-stop recovery, logout, and Runtime shutdown remain pending. The app is left at the authenticated-action boundary with Runtime/Gateway running and no login challenge started.
