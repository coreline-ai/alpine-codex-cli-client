# Grok Runtime launch policy

Date: `2026-08-12 KST`

## Boundary

The official pinned Grok CLI runs only inside the app-private Alpine Runtime. Grok state is rooted
at `/workspace/.alpine-grok`; existing Codex state remains under `/workspace/.alpine-codex`.
Neither tree is used as a fallback for the other.

The Android app creates and canonicalizes the root, home, staging, profile, work, and Gateway
directories. Existing symlink components or any path outside the app-private workspace fail before
process launch. Directories and the executable are mode `0700`; the profile and newly probed state
files are mode `0600`. The child applies `umask 077` before `exec`.

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

## Chat-only profile

`grok-profile/chat-only.md` is packaged only as a debug asset and locked by exact size and SHA-256.
It disables skill and AGENTS discovery, inherited skills, MCP servers, background work, and uses plan
permission mode. For pinned Grok CLI 1.0.0, the profile uses `task` as a deliberate allowlist sentinel:

1. `GROK_SUBAGENTS=0` removes `task` and its dependent tools.
2. A non-empty recognized allowlist then removes the remaining default toolset.
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

## Verification result

On the redacted Samsung target, the debug-signed app was update-installed without clearing data.
The pinned version command and credential-free ACP initialize succeeded. The initialize result exposed
only the interactive `grok.com` auth method under the official key-auth kill switch. Runtime stop left
zero Grok, PRoot, or app process matches. App-private mode checks returned `0700` for all inspected
directories/executable and `0600` for the profile.

No authenticate request, browser action, Device Code, account access, model inference, prompt, logout,
credential mutation, app uninstall, or app-data clear occurred in this phase.
