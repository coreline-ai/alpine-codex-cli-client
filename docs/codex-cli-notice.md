# Codex CLI debug artifact notice

This debug-only Android project packages a verified copy of the official Codex CLI solely as a
generated debug asset. The executable is not committed to this Git repository and no release
variant consumes it.

| Field | Locked value |
|---|---|
| Version | `0.147.0` |
| Target | `aarch64-unknown-linux-musl` |
| Official source | <https://github.com/openai/codex/releases/download/rust-v0.147.0/codex-aarch64-unknown-linux-musl.tar.gz> |
| Archive SHA-256 | `eb677c80f666b1ab8b4b1d083b66e8d614b1281d960bb6f9fd8ca98f58b38b90` |
| Executable SHA-256 | `e23d0be344d2496986c985cd3db61e6f649b1ddd900e6afc1b5aaabbffcbb4e2` |

The project validates archive and executable size, SHA-256, and AArch64 ELF header before
embedding the generated debug asset. At first runtime use, it copies the asset atomically to the
app-private Alpine workspace, checks its hash again, sets executable permission, and runs only
the fixed `codex --version` smoke command. OAuth remains owned by the official CLI in later
phases; this artifact pack neither accepts nor stores API keys, OAuth client IDs, or credentials.

Codex CLI source and license notices are published by OpenAI in the
[official Codex repository](https://github.com/openai/codex).
