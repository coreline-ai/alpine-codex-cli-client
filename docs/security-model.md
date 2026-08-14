# Security model

Date: `2026-08-14 KST`

## Protected assets

- CLI-owned Codex and Grok OAuth credentials
- short-lived Device OAuth challenge material
- Gateway session capability
- prompt, response, model selection, and encrypted conversation state
- app-private CLI binaries, profiles, config, sessions, and Runtime filesystem

## Trust boundaries and controls

| Boundary | Control |
|---|---|
| Android to Gateway | loopback only, strict Host/Origin/body shape, per-request HMAC, timestamp and nonce replay window |
| Gateway to CLI | fixed executable/arguments/environment, closed typed method enum, bounded JSONL and timeouts |
| Codex vs Grok | separate HOME, credential, config, session, and Agent-tagged conversation state |
| OAuth to Android | official complete Device URL only, fixed HTTPS host allowlist, memory-only browser handoff; Grok exact hosts are `auth.x.ai` and `accounts.x.ai` |
| Conversation at rest | Android Keystore-backed AES-GCM with application-bound AAD and versioned migration |
| Sensitive UI | global `FLAG_SECURE`; no OAuth URL persistence; Codex code clipboard is marked sensitive |
| Build artifact | debug certificate only, secure app non-debuggable, lab app blocks real OAuth, release path absent |

The Gateway capability is generated once per Runtime session, wrapped by Android Keystore, consumed
through an app-private file, and removed from the transient handoff path. Unsigned, replayed, tampered,
browser-origin, malformed, or cross-Agent requests fail before adapter work.

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
byte count and truncation state. Phase 9 must inspect the dedicated audit tag, not dump a post-chat UI
hierarchy or broad logcat buffer.
The host verifier consumes exactly one audit line through stdin, accepts no file output, and rejects
unknown suffixes, multiple lines, out-of-range counts, profile activity, and retry classes outside G1.

## Explicitly absent paths

- API key input, storage, environment propagation, or key-auth method
- app-owned OAuth client ID or CLI fingerprint
- bearer/token export endpoint
- direct OpenAI/xAI Provider HTTPS fallback
- automatic prompt retry, replay, or cross-Agent fallback
- arbitrary terminal, filesystem, package, Git, MCP, subagent, or tool surface
- release signing, store key, release APK, or AAB

## Residual risks

- The Grok artifact lock is a project checksum, not an upstream signature.
- The official CLI may perform a bounded pre-output internal auth recovery allowed by G1. Android and
  Gateway still dispatch once; post-output recovery is rejected.
- The external browser may retain a short-lived Device URL in browser history; the app cannot erase it.
- A debug certificate protects separation from release, not production distribution trust.
- `FLAG_SECURE` does not control an already-compromised OS or privileged device instrumentation.
