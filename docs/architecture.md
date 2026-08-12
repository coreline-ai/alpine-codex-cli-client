# Architecture

Date: `2026-08-12 KST`

## Runtime topology

```mermaid
flowchart TD
    UI["Android Compose UI"] -->|"signed loopback HTTP/SSE"| GW["Python Agent Gateway"]
    GW --> RT["Agent router"]
    RT -->|"selected = Codex"| CX["Official codex app-server"]
    RT -->|"selected = Grok"| GR["Official Grok ACP 1.0.0"]
    CX --> CO["Codex CLI-owned OAuth state"]
    GR --> GO["Grok CLI-owned OAuth state"]
    HOST["App-private Alpine / PRoot"] --> GW
    HOST --> CX
    HOST --> GR
```

Android never calls an OpenAI or xAI inference endpoint. It sends a normalized request to the
loopback Gateway. The Gateway owns one selected typed adapter, and that adapter writes only the
closed protocol methods of the selected official CLI process.

## Module ownership

| Layer | Primary locations | Responsibility |
|---|---|---|
| Compose app | `app/src/main` | Runtime actions, Agent/model selection, OAuth browser handoff, chat and Stop UI |
| Android bridge | `codex-runtime-bridge/src/main` | signed HTTP client, strict JSON/SSE state machine, one-shot Stop control |
| Agent Gateway | `codex_gateway/agents` | Agent-neutral contract, single-flight router/service, normalized HTTP/SSE |
| Codex adapter | `codex_gateway/agents/codex.py` | existing app-server account/model/turn mapping |
| Grok adapter | `codex_gateway/agents/grok.py` | Device OAuth, dynamic model/session binding, stream/retry/Stop mapping |
| Grok ACP | `codex_gateway/grok_acp` | pinned launch policy, strict JSONL, method allowlist, profile-event audit |
| Binary packs | `codex-cli-pack`, `grok-cli-pack` | debug-generated locked official binaries; binaries are not tracked by Git |
| Runtime packs | `alpine-runtime-*` | app-private Alpine installation and bounded lifecycle |

## Lifecycle and invariants

```text
Runtime stopped
  -> Alpine started
  -> Gateway Python prepared
  -> authenticated Gateway started
  -> selected Agent process ready
  -> OAuth/account/model/turn operations
  -> selected Agent stopped
  -> Gateway and Runtime stopped
```

- One Runtime, one Gateway, and at most one selected Agent process are active.
- Login and turn are independently single-flight and block Agent switching.
- Conversation bindings include Agent ID, backend session ID, model ID, and process generation.
- Resume fails before backend I/O when the Agent or process generation does not match.
- Android/Gateway do not retry or replay a prompt and do not fall back to the other Agent.
- Grok's permitted pre-output CLI-internal auth recovery is observed, not initiated by Android.
  Any retry after visible output fails the turn.

## Terminal evidence flow

For Grok only, the adapter attaches a content-free `diagnostics` object to the existing authenticated
terminal SSE event. The Android parser accepts only fixed integer fields and a small retry enum.
`AgentTurnAudit` emits one content-free log line with dispatch, delta, terminal, cancel, retry, and
forbidden-profile counts. Request IDs, conversation IDs, model IDs, URLs, account fields, prompts,
and responses are absent.
