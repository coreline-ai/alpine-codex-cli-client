# Grok CLI integration preflight

Date: `2026-08-12 KST`

## Scope and privacy boundary

This is a credential-free feasibility record for the official Grok CLI in the existing Android app-private Alpine/PRoot environment. It does not contain a device serial, Device Code, verification URL, account identifier, credential, prompt, response, browser capture, or retained ACP transcript.

No Grok `authenticate` request, Device Code issuance, browser approval, credential write, real model inference, logout, app install, app data clear, or credential mutation was performed during this preflight.

## Locked upstream identity

| Item | Verified value |
|---|---|
| Official source | `https://github.com/xai-org/grok-build` |
| Source repository HEAD | `be713136d2a69080743a3f6b3c72077057e5948f` |
| Embedded source revision | `5d08d7e4123092567ccd584cd9f99afa2972065c` |
| Stable CLI version | `1.0.0` |
| Official artifact | `https://x.ai/cli/grok-1.0.0-linux-aarch64` |
| Artifact size | `133745832` bytes |
| Artifact SHA-256 | `bb7c51116564a2219f6a49850815060f416918ac407f1f2ba82c53c0b0d4383f` |
| Runtime version output | `grok 1.0.0 (3cd0d0cbce)` |

The official installer did not publish or validate a signed checksum at the time of review. The pinned size and SHA-256 are therefore a project lock and not an upstream signature. Upgrades require a separate source/artifact review.

## Test environment

| Item | Redacted result |
|---|---|
| Device alias | `SAMSUNG_TARGET` |
| Device | Samsung `SM-S931N` |
| ABI | `arm64-v8a` |
| OS | Android 16 / API 36 |
| App | `dev.alpine.codexclient.debug 0.1.0-debug` |
| Runtime | Existing app-private Alpine 3.21 under PRoot |

## Credential-free checks

| Check | Result |
|---|---|
| Artifact size and SHA-256 | PASS |
| ELF architecture | AArch64 PASS |
| Dynamic interpreter | None; static target PASS |
| Android shell execution | Version command exit 0 |
| Existing app-private Alpine/PRoot execution | Version command exit 0 |
| ACP `initialize` over stdio | protocolVersion 1 PASS |
| OAuth-only advertisement | `grok.com` present, `xai.api_key` absent with API-key auth disabled |
| Dynamic model contract | `modelState.availableModels` observed without hardcoded Android fallback |
| Session lifecycle | new/resume/close contract present |
| Stop | `session/cancel` implementation present |
| Logout | `x.ai/auth/logout` implementation present |
| Auto-update kill switch | command flag and environment gate present |
| Subagent kill switch | environment/config gate present |
| Telemetry/trace kill switches | environment gates present |
| Idle Grok ACP RSS | approximately 29 MiB; selected-Agent lazy start required |

## ACP OAuth contract fixed for implementation

1. Start one asynchronous `authenticate` request with method ID exactly `grok.com`.
2. While that request is pending, obtain `x.ai/auth/get_url` for the same request sequence.
3. Accept only `mode=device` and a bounded complete HTTPS URL on the approved xAI host.
4. Open the URL in the external browser without parsing stderr or separately storing a user code.
5. Observe only normalized pending/authenticated/failed/cancelled state.
6. Cancel with `x.ai/auth/cancel` for the current request sequence only.
7. Logout with `x.ai/auth/logout` and discard email/profile/token-shaped fields.

The implementation must block `x.ai/getApiKey`, `x.ai/setApiKey`, `x.ai/auth/getBearerToken`, unknown future `x.ai/*` methods, and arbitrary tool/filesystem/terminal/package/Git extensions.

## Fixed launch policy

```text
grok --no-auto-update agent --no-leader --agent-profile <fixed-chat-only-profile> stdio
```

Allowed environment names are limited to `HOME`, `GROK_HOME`, `GROK_LOGIN_DEVICE_FLOW`, `GROK_DISABLE_API_KEY_AUTH`, `GROK_DISABLE_AUTOUPDATER`, `GROK_SUBAGENTS`, `GROK_TELEMETRY_ENABLED`, `GROK_TELEMETRY_TRACE_UPLOAD`, and `GROK_EXTERNAL_OTEL`, with the values fixed by the development plan.

## Known limitations and next gate

- The official CLI can internally recover authentication and resubmit before user-visible output. Android and Gateway dispatch remain exactly once; post-output retry must fail the turn.
- The official artifact has no upstream signed checksum in the reviewed installer.
- The project-owned chat-only profile is now size/hash locked with static negative tests. The
  authenticated real-session no-tool assertion remains gated on secure OAuth in Phase 9 because the
  official CLI rejects credential-free `session/new`.
- Actual Device OAuth, dynamic account model listing, one real stream turn, Stop, force-stop recovery, and logout remain Phase 9 work.
- The secure/non-debuggable app, authenticated Gateway, and sensitive OAuth UI gates passed in
  Phase 8. Actual Grok OAuth remains blocked only on the explicit Phase 9 user approval.

All temporary device and host preflight binaries, homes, and APK copies created for read-only checks were removed after verification. Existing app data, Codex credential, reference app, and reference repository were not modified.
