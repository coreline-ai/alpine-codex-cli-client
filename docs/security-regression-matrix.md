# Security regression matrix

Date: `2026-08-15 KST`

The security hardening baseline is the user's existing dirty `main` working tree rooted at commit
`ec94f99e45982af8fffc6d1b9e96b7362c2c3d43`. It is intentionally not reset, stashed, or treated as a
clean Git snapshot. The reproducible product baseline is instead fixed by the tests, immutable
reference-source commit, artifact locks, and redacted Samsung evidence below.

| ID | Automated contract | Samsung/manual evidence |
|---|---|---|
| R-01 Runtime/Gateway start | `SecureGatewayRuntimeInstrumentedTest`, Runtime controller/host tests, `ConfiguredRuntimeStarterTest` | `docs/samsung-debug-e2e-evidence.md` process cardinality |
| R-02 Codex login | `CodexChatWorkflowInstrumentedTest`, `tests/test_gateway.py`, app-server fixture verifier | Samsung Codex readiness check without a paid turn |
| R-03 Grok login | `AgentChatWorkflowInstrumentedTest`, `tests/test_grok_agent_adapter.py`, `tests/test_agent_gateway.py` | `docs/samsung-grok-secure-debug-e2e.md` official OAuth result |
| R-04 Models | Agent/Codex workflow instrumentation and Gateway adapter tests | Samsung live model evidence |
| R-05 Streaming | bridge client tests, Agent/Codex workflow instrumentation, Python Gateway/adapter tests | one approved synthetic Grok turn and redacted audit |
| R-06 Stop | bridge/client tests, Agent/Codex workflow instrumentation, adapter cancellation tests | Samsung real Stop and terminal-once evidence |
| R-07 Agent switch | `tests/test_agent_router.py`, `AgentChatWorkflowInstrumentedTest` | Samsung Codex→Grok selection and `1/1/1/1/0` cardinality |
| R-08 Conversation restore | encrypted store tests, ViewModel tests, Agent workflow instrumentation | Samsung history/composer recovery evidence |
| R-09 Lifecycle | Runtime host/controller and configured starter tests | two force-stop cycles plus background/foreground evidence |
| R-10 Sensitive UI/data | secure-window/UI tests, URL/profile/APK/evidence gates | non-debuggable package and redacted evidence audit |
| R-11 Failure contract | malformed/oversized/timeout/retry tests across bridge, Gateway, app-server, ACP | stable content-free error evidence; no automatic prompt |
| R-12 Data-preserving install | backup policy gate, JVM/Android migration tests, manifest/APK gates | Samsung `adb install -r`; committed no-backup binds and Grok OAuth/history/composer retained |

The normalized golden carrier is fixed by `tests/test_agent_gateway.py`,
`tests/test_gateway_security.py`, bridge transport/client tests, `docs/grok-gateway-contract.md`, and
the Codex/Grok protocol/profile/artifact verification scripts. Fixtures contain no credential,
OAuth URL/code, account metadata, prompt, or response from a real session.

## Phase gates

- Credential-free full gate: `scripts/verify-secure-debug-milestone.sh`
- Private UDS device gate: `SecureGatewayRuntimeInstrumentedTest`
- Reference source: immutable commit `b81a7d8ee12af72ff95180bfeadabe68e5be950e`
- Phase 1 baseline: Python 118 tests and 516 Gradle tasks, all protocol/artifact/APK/evidence/reference gates PASS
- Phase 3 device result: Samsung `SM-S931N` private UDS/peer UID/HMAC/TCP-negative/socket-cleanup test PASS
- Phase 5 device result: Samsung data-preserving migration committed; Codex/Grok/handoff no-backup
  binds active; Grok OAuth/history/composer and `1/1/1/1` app/PRoot/Python/Grok cardinality retained
