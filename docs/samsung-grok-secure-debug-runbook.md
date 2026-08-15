# Samsung Grok secure-debug runbook

Date: `2026-08-15 KST`

This runbook is the only approved real-account path for Phase 9. It never uninstalls the target app,
clears app data, copies credential files, or reads OAuth/account payloads.

## Verified baseline

- Sections 1-6 completed on the approved Samsung target on `2026-08-15 KST`.
- Final candidate: `161150250` bytes; SHA-256
  `d104be3f1d3e6aff21953356fe41e45e1e30bd2ca3832406f82783cc407ca769`.
- Credential-free milestone: 116 Python tests and 516 Gradle tasks plus protocol, clean-room,
  artifact, profile, ACP, APK, evidence, source-map, and Runtime-manifest gates passed.
- Section 7 remains unexecuted because logout and Runtime shutdown require separate approval.

## 1. Credential-free gate

1. Confirm the expected branch/HEAD and review the cumulative working tree without reset, stash,
   rebase, or checkout of unrelated changes.
2. Run `sh scripts/verify-secure-debug-milestone.sh`.
3. Confirm the secure APK is debug-signed, non-debuggable, and has application ID
   `dev.alpine.codexclient.debug`.
4. Confirm the connected target is the approved Samsung alias, model `SM-S931N`, ABI `arm64-v8a`.
5. If serial/model/ABI/package/version/signing differs, stop before installation or app mutation.

## 2. Update install and Runtime

1. Update-install only `app/build/outputs/apk/secureDebug/app-secureDebug.apk` on the exact target.
2. Do not use uninstall, clear-data, release tasks, store keys, or the lab package for real OAuth.
3. Bring the app to the foreground. The configured state automatically restores the fixed
   `Runtime install/repair -> Alpine -> Gateway Python check -> Gateway` chain. The app performs
   one settled retry after the 30-second Gateway timeout boundary; do not tap repeatedly.
   An explicit `Runtime 종료` persists opt-out until `Runtime · Gateway 시작` is tapped again.
4. Require authenticated Gateway readiness before selecting Grok.
5. Select Grok and confirm Codex is idle and only the Grok ACP process is active.

## 3. OAuth approval gate

1. Obtain the user's explicit `Grok OAuth 시작 승인`.
2. Start Device OAuth exactly once in the app.
3. Require immediate `LOGIN STARTING`/`로그인 주소 요청 중` feedback, followed by an official xAI
   browser handoff. The exact production hosts are `auth.x.ai` and `accounts.x.ai`; do not accept a
   subdomain or suffix lookalike.
4. The user completes the official xAI browser approval physically.
5. Observe only `authenticated=true`; do not inspect or retain the URL, challenge, account fields, or
   browser contents.
6. On cancellation, expiry, `grok_login_challenge_invalid`, or browser failure, do not restart
   automatically.

## 4. Model and first real turn

1. Refresh the live model catalog and require at least one row.
2. Select one live model; do not synthesize a fallback.
3. Start a dedicated `-v raw` logcat view restricted to the `AgentTurnAudit` tag. Pipe exactly the new
   terminal line to `python3 scripts/verify-agent-turn-audit.py --mode chat`. The verifier reads stdin
   only, retains nothing, and rejects extra fields or lines. Do not use a broad log dump, screenshot,
   screen recording, or UI hierarchy dump after entering the test message.
4. Enter one predetermined non-sensitive general-knowledge message and press Send once.
5. Do not retry or resend on failure.
6. Success requires one user node, one assistant node, and one terminal state in the app plus one
   audit line with `prompt_dispatch=1`, `terminal=1`, `cancel=0`, all five profile counters `0`, and no
   retry outside the G1 pre-output allowance.

## 5. Separate Stop turn

1. Enter one separate non-sensitive request expected to stream long enough for Stop.
2. Press Send once, wait for the content-free request-bound `AgentTurnStateAudit` `started`
   checkpoint, then press Stop once.
3. Require exactly one `stop_requested dispatched=1` state checkpoint. A missing or duplicate
   checkpoint is a failure; do not retry the prompt.
4. Pipe exactly the new dedicated terminal audit line to
   `python3 scripts/verify-agent-turn-audit.py --mode stop`. Success requires one terminal state,
   `prompt_dispatch=1`, `cancel=1`, no automatic replay, all profile counters `0`, and only the G1
   pre-output retry classification if a CLI-internal retry occurred.
5. An accepted `session/cancel` must publish one `turn_interrupted` terminal immediately. The
   official CLI may leave the old prompt RPC open; any late prompt/notification result must be
   ignored and must not create a second terminal.

## 6. Lifecycle and Agent regression

1. Background and foreground the app; require account/model/conversation state and one-process
   invariant to remain stable.
2. Force-stop and relaunch the secure package; require no automatic prompt and no duplicate process.
3. Select Codex and check existing account Boolean, live model readiness, and process selection without
   sending another paid turn.
4. Re-select Grok; require account/session summary restoration without automatic chat.

## 7. Logout approval and cleanup

1. Obtain separate explicit approval before Grok logout.
2. Use only the app's official Grok logout action; do not delete CLI files.
3. Require Grok unauthenticated and existing Codex account state unchanged.
4. Stop Runtime and require Gateway, Codex, Grok, and PRoot child count `0`.
5. Record only redacted status and count evidence in
   `docs/samsung-grok-secure-debug-e2e.md`, rerun the evidence scanner, and never commit captures or
   logs.

현재 실행 기준선과 Git 상태는 `docs/grok-phase9-handoff.md`를 따른다. 완료 근거는
`docs/samsung-grok-secure-debug-e2e.md`에 redacted count와 enum으로만 기록한다.
