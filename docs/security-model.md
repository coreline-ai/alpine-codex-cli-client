# Security model

Date: `2026-08-15 KST`

## Protected assets

- CLI-owned Codex and Grok OAuth credentials
- short-lived Device OAuth challenge material
- Gateway session capability
- prompt, response, model selection, and encrypted conversation state
- app-private CLI binaries, profiles, config, sessions, and Runtime filesystem

## Trust boundaries and controls

| Boundary | Control |
|---|---|
| Android to Gateway | app-private filesystem UDS, bidirectional peer UID verification, strict Host/Origin/body shape, per-request HMAC, timestamp and nonce replay window |
| Gateway to CLI | fixed executable/arguments/environment, closed typed method enum, bounded JSONL and timeouts |
| Codex vs Grok | separate HOME, config, session and Agent-tagged conversation state; logical same-UID separation, not a kernel sandbox |
| OAuth to Android | official complete Device URL only, fixed HTTPS host allowlist, memory-only browser handoff; Grok exact hosts are `auth.x.ai` and `accounts.x.ai` |
| Conversation at rest | Android Keystore-backed AES-GCM with application-bound AAD and versioned migration |
| Backup/D2D | `allowBackup=false`, Android 12+ cloud/device-transfer 전면 exclusion, legacy full-backup 전면 exclusion, 민감 상태는 versioned `noBackupFilesDir` direct child |
| Sensitive UI | global `FLAG_SECURE`; no OAuth URL persistence; Codex code clipboard is marked sensitive |
| Build artifact | `debug`/`secureDebug`와 분리된 non-debuggable `release`; 외부 환경변수 4종의 완전한 signing 입력만 허용하고 package/certificate/locked payload를 APK/AAB gate로 검증 |
| Runtime supply chain | rootfs/PRoot/loader/SPDX SHA-256 lock, deterministic 15-package APK inventory, Gradle/milestone integrity gate; patched rootfs 정책은 배포 gate에서 제외 |
| Runtime update | staged rootfs 검증 뒤 활성/직전 세대만 원자적으로 교체, process-death 복구, 불완전 세대 fail-closed, workspace/credential/session 경로 비접촉 |

The Gateway capability is generated once per Runtime session, wrapped by Android Keystore, consumed
through an app-private file, and removed from the transient handoff path. Unsigned, replayed, tampered,
browser-origin, malformed, or cross-Agent requests fail before adapter work.

## External service policy

External communication is limited to official Codex/Grok CLI-owned OAuth and the authenticated Agent
traffic explicitly initiated by the user. Android and the Gateway do not directly integrate unrelated
backup, device transfer, sync, analytics, telemetry, or cloud storage services. Security tests must
not invoke those services either; local static, APK, instrumented, and on-device state checks replace
any test that would create a new egress path.

The UDS carrier is bounded to 8 concurrent connections/backlog entries, 5 seconds from accept to
successful authentication, a 4 KiB request line, 16 KiB aggregate headers, and a 32 KiB body. The
nonce replay set uses conservative 5-second buckets sized for four legal requests/second with a 2x
burst allowance. A full bucket recovers on the next interval without evicting any live replay entry.
Only fixed, saturating counters record peer, capacity, timeout, header, authentication, replay, and
nonce-capacity rejection; request content and identifiers are never recorded.

## Backup and migration boundary

Codex HOME, Grok HOME, Gateway handoff/wrapped capability, and encrypted conversation state are
stored in fixed versioned direct children of `noBackupFilesDir`. PRoot receives only three
factory-configured binds for the two CLI HOME directories and the transient Gateway handoff. The
rootfs, package cache, CLI staging, Gateway code, work directory, and UDS transport stay in the
ordinary app-private Runtime workspace and are never copied by the sensitive migration.

Migration runs only before Runtime construction and when no non-Android same-UID child process is
active. It performs bounded lstat/UID/GID/mode/type/size/free-space preflight, opaque copy with
SHA-256 manifest comparison, atomic per-target rename, and a fixed commit marker before relocating
legacy sources into a no-backup rollback tree. Existing CLI-safe `0755`/`0644` modes are normalized
to `0700`/`0600` only in the copy; later same-UID/GID, non-world-writable CLI mutations remain valid
while each destination root stays `0700`. Symlinks are rejected except for non-followed `/dev/null` and
normalized absolute `/workspace` links, which preserve an existing app-private CLI layout without
opening a host path. See [backup-migration.md](backup-migration.md).

## Runtime generation and rollback boundary

Runtime 설치는 `rootfs.installing`에서 archive 검증과 `/bin/sh` smoke를 끝낸 뒤에만 고정된
`rootfs`/`rootfs.previous` 두 세대를 교체한다. 새 marker가 커밋된 뒤에도 직전 rootfs와 marker를
삭제하지 않으며, 다음 검증된 설치가 시작될 때만 가장 오래된 세대를 제거한다. 명시적 내부
rollback은 active/previous rootfs와 marker를 함께 swap하고 `activation.pending`으로 각 이동
단계의 process death를 복구한다. marker/rootfs 중 하나만 남은 불완전 이전 세대는 사용하지
않고 fail-closed한다.

이 트랜잭션이 이동·삭제할 수 있는 경로는 Runtime root의 고정 rootfs/marker 파일뿐이다.
workspace는 Runtime reset에서도 보존되며, `noBackupFilesDir`의 Codex/Grok credential과 session,
암호화 대화 상태는 Runtime root 밖에 있어 update/rollback 대상이 아니다. 이 경계는 active→new,
new→rollback, rollback 중단 전/후 단위 테스트의 sentinel로 고정한다. 자동 rollback은 제품
동작 변경 요청이 없으므로 추가하지 않고, 검증된 명시적 rollback만 유지한다.

## Grok chat-only enforcement

The project-owned profile disables inherited skills, AGENTS discovery, MCP servers, background work,
subagents, telemetry, updater, and key-based authentication. Session creation supplies an empty MCP
list and declares filesystem and terminal client capabilities disabled.

The pinned ACP process additionally audits forbidden activity per process generation:

- `tool_call`, `tool_call_update`, and tool permission reverse requests
- `subagent_*` events
- `x.ai/mcp/*` methods or MCP-tagged updates
- `x.ai/fs/*` and filesystem-tagged updates
- `x.ai/terminal/*` and terminal-tagged updates

Any such event increments only its category counter and terminates the process generation. The first
`session/prompt` checks all five counters under the same JSONL writer lock before writing the request,
so an event racing with session creation cannot pass between the check and prompt dispatch.

## Redacted observability

The only production turn audit is the fixed `AgentTurnAudit` line. It contains Agent/outcome enums and
bounded counts. It never contains request/session/conversation/model identifiers, account metadata,
OAuth data, prompt/response text, private retry reason, or stderr. Grok stderr retains only observed
byte count and truncation state. Real-device verification must inspect only the dedicated audit tag,
not dump a post-chat UI hierarchy or broad logcat buffer.
The host verifier consumes exactly one audit line through stdin, accepts no file output, and rejects
unknown suffixes, multiple lines, out-of-range counts, profile activity, and retry classes outside G1.

## Explicitly absent paths

- API key input, storage, environment propagation, or key-auth method
- app-owned OAuth client ID or CLI fingerprint
- bearer/token export endpoint
- direct OpenAI/xAI Provider HTTPS fallback
- automatic prompt retry, replay, or cross-Agent fallback
- arbitrary terminal, filesystem, package, Git, MCP, subagent, or tool surface
- 저장소에 포함된 private release signing key 또는 무승인 외부 배포 자동화

## Residual risks

- Codex, Grok, Gateway and PRoot children execute under the same Android application UID. Separate
  HOME/bind/protocol contracts prevent accidental state mixing but do not stop a compromised
  same-UID native process from probing sibling app-private files. A separate UID broker is not a
  current release requirement and this trust assumption remains explicit.
- The locked Alpine `3.21.3` rootfs and its local vulnerability snapshot remain visible in the SBOM
  and inventory. By explicit product decision, patched rootfs, Python preinstalled inside that rootfs,
  and a complete vulnerability database are excluded Phase 6 work. A separately locked, APK-contained
  Python package pack is now mandatory for public distribution and never uses a runtime repository.
- The Grok artifact lock is a project checksum, not an upstream signature.
- The official CLI may perform a bounded pre-output internal auth recovery allowed by G1. Android and
  Gateway still dispatch once; post-output recovery is rejected.
- The external browser may retain a short-lived Device URL in browser history; the app cannot erase it.
- Production signing credentials and the expected certificate digest must be supplied externally;
  the project never generates or commits the private key.
- The repository does not contain production Python package bytes. This workstation has a reviewed
  Git-ignored pack whose fresh offline Samsung install passed; a new checkout still fails closed
  until the same class of local input is supplied. Signed-release Samsung acceptance remains pending.
- `FLAG_SECURE` does not control an already-compromised OS or privileged device instrumentation.
- Google/Samsung/other external backup, restore, and D2D services are outside the permitted security
  test boundary and must not be invoked. Static manifest/APK gates, instrumented migration checks,
  and active no-backup binds provide the backup/D2D evidence without creating an export channel.
