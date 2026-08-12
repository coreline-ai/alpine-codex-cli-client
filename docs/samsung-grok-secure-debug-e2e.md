# Samsung Grok secure-debug E2E evidence (redacted)

Date: `2026-08-12 KST`

## Privacy and mutation boundary

- Target alias: `SAMSUNG_TARGET`; Samsung `SM-S931N`, `arm64-v8a`.
- Secure package: `dev.alpine.codexclient.debug`; debug certificate; non-debuggable.
- Target data is update-preserved. Uninstall, clear-data, credential file access, and release artifacts
  are prohibited.
- Serial, OAuth URL/challenge, account fields, dynamic model contents, chat text, browser state,
  prompt/response text, screenshots, UI dumps, and raw logs are not recorded.

## Credential-free readiness

| Check | Result |
|---|---|
| Phase 8 secure/debug gate | PASS |
| Final OAuth-readiness milestone | PASS: Python 87 tests, full JVM/Android/lint/APK/artifact/security gates |
| Secure APK identity | `161105686` bytes; SHA-256 `fa5d97b53756f15eb8a195ea88a64b38dd47bede1629dbd6d9c0c11ed698b10a` |
| Exact Samsung identity precheck | PASS at last credential-free audit; must repeat immediately before mutation |
| Official Grok Device OAuth approval | PENDING USER APPROVAL |
| Dynamic model catalog | NOT RUN |
| Real one-turn stream | NOT RUN |
| Separate Stop turn | NOT RUN |
| Background/foreground | NOT RUN |
| Force-stop recovery | NOT RUN |
| Codex selection regression | NOT RUN |
| Grok re-selection | NOT RUN |
| Grok logout | PENDING SEPARATE USER APPROVAL |
| Runtime child cleanup | NOT RUN |

## Permitted terminal evidence

Only the content-free `AgentTurnAudit` line may be summarized here after each real turn. The line must
first pass `scripts/verify-agent-turn-audit.py` in the matching `chat` or `stop` mode. The summary must
include dispatch, terminal, cancel, retry classification/counts, and the five profile-event counts. It
must not include any identifier or message content.
