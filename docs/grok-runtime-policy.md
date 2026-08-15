# Grok Runtime launch policy

Date: `2026-08-12 KST`

## Boundary

The official pinned Grok CLI runs only inside the app-private Alpine Runtime. Grok state is rooted
at `/workspace/.alpine-grok`; existing Codex state remains under `/workspace/.alpine-codex`.
Neither tree is used as a fallback for the other.

The Android app creates and canonicalizes the root, home, staging, profile, work, and Gateway
directories. Existing symlink components or any path outside the app-private workspace fail before
process launch. Directories and the executable are mode `0700`; the profile and newly probed state
files are mode `0600`. The production Gateway fixes process-wide `umask 077` before creating request
threads, so every subsequently spawned CLI inherits the private mask without a fork-time callback.

## Fixed process contract

No Android or loopback request can supply an executable, path, argument, environment entry, or raw
ACP method. The fixed command is:

```text
/workspace/.alpine-grok/staging/grok-cli/1.0.0/grok
  --cwd /workspace/.alpine-grok/work
  --no-auto-update
  agent --no-leader
  --agent-profile /workspace/.alpine-grok/profile/chat-only.md
  stdio
```

The child environment is built from an empty mapping. It contains only the dedicated HOME/GROK_HOME,
Device Flow selection, the official key-auth kill switch, updater/subagent kill switches, and three
telemetry-disable switches. Ambient `PATH`, proxy, logging, plugin, leader, secret, token, and custom
endpoint values are not inherited.

Production Grok activation can originate in a `ThreadingHTTPServer` request worker. The supervisor
passes the fixed working directory and `close_fds=True`, selecting CPython's native fork/exec
implementation while closing unrelated Gateway and PRoot descriptors. No Python callback runs in the
child. A per-child umask argument is also prohibited; the Gateway's process-wide `077` mask is fixed
before request threads start and is inherited by the CLI.

PRoot real-device validation also showed that a Grok child launched directly by a short-lived HTTP
worker is lost when that worker returns, although the same supervisor is stable from a standalone
main thread. Production construction therefore starts one private, daemonized spawn-owner thread
before the Gateway begins serving requests. Fixed launch work is handed to that owner synchronously,
and the owner remains alive across Agent selections; it does not add another backend process or widen
the command/environment surface.

Pinned Grok CLI 1.0.0 can acknowledge ACP `initialize` before its extension handlers are ready. The
supervisor therefore requires a bounded 0.5-second no-exit interval and then completes one ordered
`_x.ai/auth/info` request before publishing `READY`. The response is shape-checked and discarded; no
account field is retained. This barrier prevents the first HTTP account request from racing the CLI's
post-initialize transition.

Device authentication is also asynchronous inside the official CLI. One `authenticate(grok.com)`
request is started per user action; `_x.ai/auth/get_url` alone may be polled for at most 15 seconds
while it returns the exact not-ready shape (`auth_url=null`, mode absent/device). A complete URL may
use only the exact production OAuth/account hosts `auth.x.ai` or `accounts.x.ai`; suffix lookalikes
and subdomains are rejected. A malformed mode, non-HTTPS URL, unapproved host, credentials in
authority, fragment, oversized value, ACP failure, or deadline expiry fails closed and cancels that
exact authentication sequence.

## Chat-only profile

`grok-profile/chat-only.md` is packaged only as a debug asset and locked by exact size and SHA-256.
It disables skill and AGENTS discovery, inherited skills, MCP servers, background work, and uses plan
permission mode. For pinned Grok CLI 1.0.0, the profile uses `task` as a deliberate allowlist sentinel
and also denies it explicitly:

1. The denylist removes `task` before allowlist resolution, independently of whether the upstream
   `GROK_SUBAGENTS=0` setting is applied early enough during session construction.
2. The still-recognized, non-empty `task` allowlist then removes the remaining default toolset.
3. Upstream preserves `search_tool` and `use_tool` specially during allowlisting, so both are explicitly
   denied before that step.

This behavior is version-specific. A CLI upgrade is prohibited until the upstream builder logic,
profile hash, negative tests, and Samsung smoke are reviewed again.

## Upstream capability limitation

The raw official ACP `initialize` response advertises generic file/MCP/session capabilities before
the per-session profile is built. Those broad declarations are not treated as effective Android
capabilities and will not be forwarded as-is. Phase 4 owns a method allowlist and reverse-request
denial; session creation always supplies an empty MCP server list and disabled client filesystem and
terminal capabilities.

A credential-free `session/new` probe was attempted after successful initialize and was rejected by
the official CLI before a session was created. Starting OAuth merely to test the profile would violate
the Phase 3 credential-free gate and G2. The authenticated real-session negative check is therefore
an explicit Phase 9 gate after the secure OAuth prerequisites: fixed-profile session creation must
succeed while tool, subagent, MCP, filesystem, and terminal event counts remain zero.

Phase 9 readiness now enforces this as code, not an operator-only observation. The ACP multiplexer
classifies the pinned tool/subagent/MCP/filesystem/terminal wire families per generation and fails the
generation on first observation. The first `session/prompt` checks the five counters while holding the
same lock used for the JSONL write. The terminal SSE carries only redacted counters, and Android emits
one fixed `AgentTurnAudit` line for post-turn evidence.

## Verification result

On the redacted Samsung target, the debug-signed app was update-installed without clearing data.
The pinned version command and credential-free ACP initialize succeeded. The initialize result exposed
only the interactive `grok.com` auth method under the official key-auth kill switch. Runtime stop left
zero Grok, PRoot, or app process matches. App-private mode checks returned `0700` for all inspected
directories/executable and `0600` for the profile.

No authenticate request, browser action, Device Code, account access, model inference, prompt, logout,
credential mutation, app uninstall, or app-data clear occurred in this phase.

## ACP supervisor boundary

The Phase 4 supervisor owns one process generation and implements the lifecycle
`STOPPED → STARTING → INITIALIZING → READY → STOPPING/FAILED`. JSONL decoding is strict UTF-8,
object-only, and limited to 1 MiB per line. Request IDs are monotonic, pending requests are bounded,
writes are serialized, timeouts are fixed by method, and unknown, duplicate, late, or reverse
agent-to-client requests fail the active generation.

The public supervisor has typed functions for only the pinned allowlist. It has no public
`request(method, params)` function. Session creation always sends an empty MCP list and the fixed
work directory. Android input cannot select an executable, environment, path, profile, or protocol
method. Unknown notifications are counted then dropped; recognized notifications carry a local
generation and sequence. Duplicate terminal notifications for the same session/prompt are emitted
once.

Raw initialize metadata is reduced to protocol version, the sole `grok.com` auth method, bounded
model summaries/current model, and load/resume/close booleans. Advertised MCP, filesystem,
terminal, tool, hostname, account, and other extension metadata are discarded. Stderr content is
never retained at all: only a bounded byte count and truncation bit are exposed as a stable
diagnostic, so a URL, account value, prompt, or credential split across chunks cannot survive.

The contract verifier fixes both allowed and forbidden methods to Grok CLI 1.0.0 and scans the
shipping Python package to ensure forbidden credential/key method strings are absent. Credential-free
fake ACP tests cover split/coalesced/malformed/oversized lines, unknown/duplicate IDs, pending bounds,
timeouts, late responses, process loss, generation mismatch, notification filtering/deduplication,
stderr chunk boundaries, and active-request shutdown/reaping. No OAuth or inference was performed.
