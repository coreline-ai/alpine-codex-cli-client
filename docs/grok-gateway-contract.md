# Grok normalized Gateway contract

작성일: `2026-08-12 KST`

## Boundary

The Grok integration is a typed adapter over the official pinned Grok CLI ACP process. Android
never selects an ACP method, reads the CLI-owned authentication files, supplies an OAuth client
identifier, or calls an xAI HTTPS inference endpoint. The only authentication method accepted from
`initialize` is `grok.com`.

The production path remains:

```text
Android -> authenticated loopback HTTP/SSE -> Agent Gateway
        -> typed Grok adapter -> bounded JSONL -> official Grok CLI ACP
```

The normalized HTTP handler cannot be constructed without an injected request authorizer. Phase 8
will supply the session-capability/HMAC verifier to this interface; the Phase 5 fake lifecycle uses
an explicit test authorizer. There is no unsigned default or compatibility bypass.

## Normalized operations

| Method | Target | Result boundary |
|---|---|---|
| `GET` | `/healthz` | selected Agent and readiness only |
| `GET` | `/v1/agents` | Agent IDs, selection, readiness, bounded capabilities |
| `POST` | `/internal/agents/select` | idle-only Agent selection |
| `GET` | `/internal/agents/{agent}/account` | authenticated/requires-auth booleans only |
| `POST` | `/internal/agents/{agent}/login/device` | opaque request ID, stable status, one validated complete URL at start |
| `GET` | `/internal/agents/{agent}/login/{request}` | stable status; no URL or separate code |
| `POST` | `/internal/agents/{agent}/login/{request}/cancel` | exact pending request cancellation |
| `GET` | `/v1/models?agent_id={agent}` | bounded live catalog; no fallback model |
| `POST` | `/v1/chat/completions` | selected-Agent-only one-user-message SSE turn |
| `POST` | `/internal/agents/{agent}/turn/{request}/interrupt` | exact active turn cancellation |
| `POST` | `/internal/agents/{agent}/logout` | official logout result reduced to `logged_out` |

All targets, bodies, and response shapes are bounded. Agent, login, turn, conversation, process
generation, and backend session identities are checked before resume or prompt. Login and chat are
single-flight at both adapter and router levels. Switching is rejected while either operation is
active, and no backend fallback or prompt replay exists.

## Device OAuth state machine

`authenticate(grok.com)` starts in a dedicated thread with a monotonically increasing
`request_seq`. `x.ai/auth/get_url` runs after the attempt has started and waits in parallel for the
official CLI to publish its challenge. Only `mode=device` and a complete HTTPS URL on the fixed xAI
host allowlist are returned. The adapter does not inspect a user code and stores neither the URL nor
any challenge after the start response.

```text
pending -> authenticated
        -> failed
        -> cancelled
        -> expired
```

Cancellation sends `x.ai/auth/cancel` at most once for the exact sequence. A response that arrives
after cancellation or expiry cannot change the terminal state. Authentication-info and logout
extension responses are reduced immediately to non-sensitive status; all other response fields are
discarded.

## Model and session rules

- `x.ai/models/list` is the sole post-login model source.
- Zero, one, or many model rows are valid; malformed rows fail closed and duplicates keep the first
  official row.
- Removed models disappear on the next refresh. No hardcoded fallback is synthesized.
- Every conversation binding includes `agent_id`, backend session ID, selected model, and Grok
  process generation.
- An Agent or generation mismatch fails before `session/resume` or `session/prompt`.
- Logout closes current-generation sessions and drops in-memory Grok bindings.

## Credential-free verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_agent_gateway \
  tests.test_grok_agent_adapter \
  tests.test_grok_acp_supervisor
```

The fixture uses synthetic values only. The test asserts authenticate-before-URL ordering,
single-flight login, exact cancellation, discarded late success, invalid URL rejection, bounded
catalog behavior, generation/Agent binding checks, mandatory per-route authorization, one fake SSE
turn, and status-only logout.
