# Samsung Grok secure-debug E2E evidence (redacted)

Date: `2026-08-14 KST`

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
| Current credential-free milestone | PASS on current source: Python 103 tests; full JVM/Android unit, lint, APK, artifact, protocol and security gates |
| Secure APK identity | Current installed build: `161137250` bytes; SHA-256 `1a396b48ad1035250f0792541079689e9c4f76006436f0eb8495044d7c18a7f1` |
| Exact Samsung identity precheck | PASS immediately before update-install: model, ABI, API, package, version, non-debuggable and signing matched |
| Secure APK update-install | PASS with data preservation; version code 2 and non-debuggable retained |
| Official Grok Device OAuth action authority | APPROVED by user on `2026-08-13 KST`; the host-corrected build still awaits a fresh user click and browser completion |
| Runtime UI foreground | PASS at resume: unrelated process absent and target app focused; no force-stop used |
| Alpine Runtime installation | PASS: app-managed install reached `READY / RUNTIME_OPERATION_COMPLETED` |
| Runtime and Gateway start | PASS: Alpine running, Python smoke passed, authenticated Gateway running; app/PRoot/Python count `1/1/1` |
| Grok backend lifecycle fix | PASS: a pre-server long-lived spawn owner preserves the PRoot child after the selecting HTTP worker returns; fixed argv/env/cwd/fd/umask policy retained |
| Production-supervisor Grok smoke | PASS: product supervisor initialize and bounded READY stability succeeded; no raw child output observed |
| Historical adapter/account Grok smoke | FAILED at content-free `ACCOUNT_FAILED`; the ACP extension-wire cause was subsequently fixed and is no longer the current blocker |
| ACP extension-wire diagnosis | FIXED and update-installed: checksum-verified ACP 0.10.4 adds one `_` to outbound extension names; exact `_x.ai/*` fixture/verifier and 44 focused tests PASS |
| Corrected-build Runtime resume | BLOCKED before new ACP diagnostic: unrelated test app repeatedly reclaimed focus; target focus guards prevented unsafe input. Runtime was recovered to READY/STOPPED with app/PRoot/Python/Codex/Grok count `1/0/0/0/0`; no unrelated app was stopped |
| Current-source update-install | PASS for the accounts-host/UI correction with data preservation after package/version/non-debuggable/certificate match; other connected device mutation `0` |
| Current-source Runtime resume | PASS without data reset: app/PRoot/Python/Grok/Codex count `1/1/1/1/0`; foreground `DEVICE OAUTH` state ready |
| First approved Grok login action | EXECUTED exactly once after target focus and `1/1/1/1/0` precheck; no browser handoff observed, no automatic authenticate retry, and no sensitive capture/logging |
| OAuth URL readiness race | FIXED: one authenticate per user action; exact not-ready `get_url` response alone is polled for at most 15 seconds, and every ACP request receives only the remaining deadline; invalid/timeout cancels the same sequence |
| Corrected OAuth retry | USER RETRY OBSERVED: previous build reached `grok_login_challenge_invalid`; official source comparison found the production account UI host `accounts.x.ai` missing from both Gateway and Android exact allowlists |
| Accounts-host correction | FIXED and update-installed: exact `auth.x.ai` + `accounts.x.ai` only, lookalike/subdomain rejection retained; click now shows immediate `LOGIN STARTING` progress; real OAuth was not automatically restarted |
| Post-install readiness | PASS: app-private Runtime/Gateway restored; app/PRoot/Python/Grok/Codex count `1/1/1/1/0`; foreground screen shows `DEVICE OAUTH` and enabled `Grok 로그인` |
| Dynamic model catalog | NOT RUN |
| Real one-turn stream | NOT RUN |
| Separate Stop turn | NOT RUN |
| Background/foreground | NOT RUN |
| Force-stop recovery | NOT RUN |
| Codex selection regression | PARTIAL PASS: post-failure recovery reached Codex login-required readiness with Codex child `1`, Grok child `0`; no paid turn sent |
| Grok re-selection | PARTIAL PASS: catalog recovery and same-selected failed-backend stop→start are implemented; authenticated lifecycle re-selection remains untested |
| Grok logout | PENDING SEPARATE USER APPROVAL |
| Runtime child cleanup | NOT RUN |

## Current handoff state

- Current source, installed APK identity, Runtime/Gateway/Grok process state and foreground login UI
  agree with `docs/grok-phase9-handoff.md`.
- The next external action is one user click on `Grok 로그인`, followed by physical completion in the
  official xAI browser. No post-correction OAuth attempt has been started automatically.
- Dynamic model, real chat, Stop, lifecycle, logout and final cleanup remain pending and must follow
  the approval and redaction boundaries in `docs/samsung-grok-secure-debug-runbook.md`.
- The working tree is cumulative and uncommitted; no final handoff commit or push has been made.

## Permitted terminal evidence

Only the content-free `AgentTurnAudit` line may be summarized here after each real turn. The line must
first pass `scripts/verify-agent-turn-audit.py` in the matching `chat` or `stop` mode. The summary must
include dispatch, terminal, cancel, retry classification/counts, and the five profile-event counts. It
must not include any identifier or message content.
