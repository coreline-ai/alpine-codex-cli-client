# Grok Phase 8 security gate (redacted)

Date: `2026-08-12 KST`

> 이 문서는 실제 OAuth 전 Phase 8 artifact의 역사적 evidence다. 이후 Grok 실제 OAuth/turn/Stop,
> private UDS, no-backup migration과 release 공급망이 구현됐다. 현재 기준은
> [`project-overview.md`](project-overview.md)를 참고한다.

## Privacy and mutation boundary

- Device alias: `SAMSUNG_TARGET`; Samsung `SM-S931N`, `arm64-v8a`.
- Secure target: `dev.alpine.codexclient.debug`, version code `2`, version
  `0.2.0-secure-debug`.
- The exact device serial, OAuth challenge, complete verification URL, account fields, dynamic model
  contents, chat text, browser state, and credential payload are not recorded.
- The existing secure app was update-installed with the same debug certificate. Its data was not
  uninstalled or cleared. The reference app and reference repository were not changed.
- No real Grok login, account mutation, model request, inference turn, Stop request, or logout was
  performed in Phase 8.

## Host gate

`sh scripts/verify-secure-debug-milestone.sh` completed successfully after the final source change.

| Gate | Redacted result |
|---|---|
| Python unit/integration/adversarial | 80 passed |
| Kotlin/JVM and Android unit | passed |
| Android lint | lab and secure variants passed |
| APK builds | lab debug, lab test, secure debug passed |
| Gateway authentication | unsigned, replay, tamper, browser-origin and malformed request fixtures rejected |
| Grok contract | binary, chat-only profile, ACP allowlist and forbidden-method checks passed |
| Clean-room and artifact | API-key/client/fingerprint/direct-fallback/release-artifact gates passed |
| Evidence/source inventory | sensitive evidence, source map, Runtime manifest and SBOM gates passed |

The secure APK was `161099966` bytes with SHA-256
`b4cc6e8ceac3280facdfa598678cf989c080ed22274698b1d311192a00819f05`. The signing certificate is
the project debug certificate; the secure APK itself is non-debuggable.

## Samsung credential-free gate

Instrumentation used only the separate lab application ID. Each group ran in a separate process to
isolate Compose Activity startup from Runtime-only test classes.

| Group | Result |
|---|---|
| Authenticated Runtime/Gateway and pinned Grok smoke | 2/2 passed |
| Agent lifecycle, login-state, model, stream, Stop and recreation workflow | 8/8 passed |
| Agent selector, long dynamic model list, login privacy, composer and secure-window UI | 5/5 passed |
| Existing Codex login/model/turn/Stop/logout workflow | 3/3 passed |
| Existing Codex model/composer/conversation/login UI | 5/5 passed |

The signed production Gateway consumed its raw one-time capability, retained only the
Keystore-wrapped session value while active, rejected an unsigned request, and removed transient
capability state on Runtime stop. Codex and Grok selection preserved one active backend process.

After testing, the lab app and its instrumentation package were removed, screen-stay-awake was reset,
and no target or lab process remained. The final secure APK was update-installed; `run-as` failed as
expected, no instrumentation targeted the secure package, and the existing reference app remained
installed.

## Phase 9 authorization gate

Credential-free security and regression prerequisites are satisfied. Starting official Grok Device
OAuth still requires explicit user approval. Final logout requires a separate user approval. Failed
real chat requests must not be replayed automatically.
