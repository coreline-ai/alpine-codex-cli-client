# Samsung Grok secure-debug E2E evidence (redacted)

Date: `2026-08-15 KST`

> 아래 APK hash와 116/516 수치는 Grok OAuth/turn/Stop을 완료한 당시 artifact evidence다. 이후
> transport가 private UDS로 전환되고 backup migration, 공급망, APK 내장 Python 경로가 추가됐다.
> 구현 기준선 `e15b808`의 credential-free gate는 Python 160 tests와 Gradle 884 tasks를 통과했으며,
> 최신 전체 상태는 [`project-overview.md`](project-overview.md)를 따른다.

## Privacy and mutation boundary

- Target alias: `SAMSUNG_TARGET`; Samsung `SM-S931N`, `arm64-v8a`.
- Secure package: `dev.alpine.codexclient.debug`; debug certificate; non-debuggable.
- Target data was update-preserved. Clear-data, credential-file reads, OAuth URL/challenge capture,
  account-field capture, screenshots, raw UI dumps, and raw child output were prohibited.
- Only fixed synthetic prompts/responses named in this document and content-free audit counters were
  retained as E2E evidence. No user conversation content was recorded.
- The other connected Android device was not mutated.

## Final candidate

| Check | Result |
|---|---|
| Secure APK | PASS: `161150250` bytes; SHA-256 `d104be3f1d3e6aff21953356fe41e45e1e30bd2ca3832406f82783cc407ca769`; version code 2 |
| Data-preserving install | PASS on `SAMSUNG_TARGET`; OAuth and encrypted conversation history retained |
| Full Python suite | PASS: 116 tests total; 93 non-socket plus 23 loopback HTTP/HMAC tests |
| Android verification | PASS: Runtime Host tests plus `testSecureDebugUnitTest`, `lintSecureDebug`, `assembleSecureDebug`; 516 Gradle tasks |
| Locked Grok profile | PASS: fixed size/hash and verifier; `task`, `search_tool`, and `use_tool` denied |
| Runtime process cardinality | PASS after turn and after recovery: app/PRoot/Python/Grok/Codex `1/1/1/1/0` |
| Real Grok chat turn | PASS: exact synthetic response `GROK_TRACKED_START_OK` |
| Force-stop recovery turn | PASS: exact synthetic response `GROK_REBIND_FINAL_OK` after two immediate data-preserving force-stop/relaunch cycles |
| Content-free audit | PASS: dispatch `1`, terminal `1`, cancel `0`, retry `none`, all five forbidden profile-event counts `0` |
| Background/foreground recovery | PASS: authenticated history/composer restored, no prompt audit increase, process cardinality stayed `1/1/1/1/0` |
| Codex -> Grok regression | PASS: Codex readiness inspected without a turn; Grok history/composer restored with no automatic prompt |
| Real Grok Stop | PASS: state checkpoints `started=1`, `stop_requested dispatched=1`; terminal dispatch `1`, terminal `1`, cancel `1`, retry `none`, forbidden profile-event counts `0` |
| Conversation history persistence | PASS: prior synthetic response visible and composer enabled after Gateway and app-process recreation |
| Repeated automatic recovery | PASS: two consecutive force-stop/relaunch cycles restored app/PRoot/Python/Grok to `1/1/1/1` without a login action |

## Corrected causes

1. **Grok session construction failed before the first prompt.** The upstream agent builder still
   received the `task` tool sentinel and failed while constructing its dependent tool registry.
   The locked chat-only profile now explicitly denies `task` before using it as the non-empty
   allowlist sentinel. Real `session/new` and prompt dispatch then completed.
2. **Android history outlived an in-memory Grok binding.** The Gateway now persists only the
   bounded conversation-to-ACP-session binding under the private Grok home with mode `0600` and
   directory mode `0700`. It stores no prompt, response, OAuth value, or credential. A recreated
   Gateway uses official ACP `session/load`; a live same-generation binding uses `session/resume`.
3. **Runtime start completed before its Host session was published.** The controller returned the
   manager's source future while its own completion callback still had to bind the session. A
   chained Python preflight could therefore observe `PROCESS_EXITED`. `track()` now returns a
   separate future that completes only after the Host state and session projection are published.
4. **Immediate restart could hit the old loopback socket's `TIME_WAIT`.** The new Gateway then
   failed closed as `GATEWAY_BIND_FAILED` until the port aged out. The loopback server now enables
   `SO_REUSEADDR` so a closed `127.0.0.1:8787` listener is reclaimable; it does not enable
   `SO_REUSEPORT`, and a second live listener remains rejected by test.
5. **A logical `RUNNING` state could outlive reclaimed child processes.** Host resume no longer
   trusts the enum alone. `ConfiguredRuntimeStarter` performs an authenticated loopback health
   probe and fully resets any non-healthy Runtime -> Python -> Gateway chain. The Runtime status
   sheet exposes only the closed `CodexRuntimeErrorCode` when a Gateway start fails.
6. **Remote session failures lacked a load/resume discriminator.** Stable, content-free
   `session_new`, `session_load`, `session_resume`, and `set_model` codes are now normalized and
   covered by unit and HTTP tests.
7. **An acknowledged Grok cancel could leave the prompt RPC open.** Grok CLI 1.0.0 acknowledged
   `session/cancel` but could keep the outstanding `session/prompt` RPC open beyond the Android SSE
   read limit. The adapter now emits exactly one `turn_interrupted` terminal immediately after an
   accepted cancel, refreshes only redacted counters, and ignores late prompt/notification results
   through the existing terminal guard.

## Real-device sequence

1. Installed the checksum-verified secure-debug APK with `adb install -r`.
2. Confirmed automatic Runtime/Gateway/Grok restoration and process cardinality `1/1/1/1/0`.
3. Sent one fixed synthetic turn and observed exact response `GROK_TRACKED_START_OK`.
4. Force-stopped and relaunched the target package twice in immediate succession without clearing
   data; both restarts reclaimed the fixed loopback port and reached `1/1/1/1/0` in 15 seconds.
5. Confirmed history restoration, enabled composer, no stable Grok error, and process cardinality
   `1/1/1/1/0`.
6. Sent a follow-up turn in the restored conversation and observed exact response
   `GROK_REBIND_FINAL_OK`. This exercises the persisted binding and official `session/load` path.
7. Verified the terminal `AgentTurnAudit` line with
   `scripts/verify-agent-turn-audit.py --mode chat`.
8. Confirmed the final Runtime/Gateway/Grok processes remained singular at `1/1/1/1/0`.
9. Backgrounded and foregrounded the app. Authentication, history, and the enabled composer were
   restored without a new prompt audit or duplicate process.
10. Selected Codex only through its readiness state, sent no Codex message, and re-selected Grok.
    Grok history and the enabled composer were restored without automatic chat.
11. A pre-fix Stop reproduction showed an accepted cancel with no timely terminal state. After the
    adapter fix, unit/build verification, and data-preserving update-install, one final approved
    Stop turn completed with dispatch `1`, terminal `1`, cancel `1`, retry `none`, and a clean
    profile audit.
12. Background/foreground recovery after Stop again restored history and the enabled composer; the
    final process cardinality remained `1/1/1/1/0`.

## Current state

- Grok remains authenticated and ready on the Samsung target.
- The latest synthetic conversation history is restored and the composer is enabled.
- App, PRoot, Python Gateway, and one official Grok CLI process are running exactly once.
- Background/foreground, Codex-to-Grok re-selection, and actual Stop validation are complete.
- The full secure-debug milestone passes 116 Python tests and 516 Gradle tasks, including artifact,
  profile, ACP, APK, evidence, reference-source, and Runtime-manifest gates.
- No logout or Runtime cleanup was executed because both are separate destructive lifecycle
  actions and were not required for the approved real-turn and persistence verification.
- The working tree remains cumulative and uncommitted; no commit or push was requested in this
  turn.

## Permitted terminal evidence

Only the content-free `AgentTurnAudit` line may be summarized after a real turn. It must first pass
`scripts/verify-agent-turn-audit.py` in the matching `chat` or `stop` mode and must never contain an
identifier, credential, prompt, response, URL, account value, or child-process output.
