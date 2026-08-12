# Grok CLI debug artifact notice

This debug-only Android project packages a checksum-pinned copy of the official Grok CLI as a
generated asset. The executable is never committed to Git, and no release source set, signing
configuration, APK, or AAB is created by the Grok artifact module.

| Field | Locked value |
|---|---|
| Version | `1.0.0` |
| Target | Linux AArch64, static ELF |
| Official artifact | <https://x.ai/cli/grok-1.0.0-linux-aarch64> |
| Executable size | `133745832` bytes |
| Executable SHA-256 | `bb7c51116564a2219f6a49850815060f416918ac407f1f2ba82c53c0b0d4383f` |
| Observed version output | `grok 1.0.0 (3cd0d0cbce)` |
| Source repository | <https://github.com/xai-org/grok-build> |
| Reviewed repository commit | `be713136d2a69080743a3f6b3c72077057e5948f` |
| Embedded source revision | `5d08d7e4123092567ccd584cd9f99afa2972065c` |
| License | Apache-2.0 |

The build validates the exact source URL and filename, byte size, SHA-256, ELF64 little-endian
format, AArch64 machine ID, and absence of an ELF `PT_INTERP` program header. Runtime staging
copies to a temporary app-private file, flushes it, sets owner-only executable mode, atomically
renames it, and verifies size, hash, and ELF properties again before returning an executable path.

The upstream installer reviewed for this lock did not publish or validate a signed checksum.
Accordingly, this SHA-256 is a project review lock, not an upstream signature. Every version change
requires a new source/artifact review and a separate lock update.

The official source snapshot contains the authoritative
[Apache-2.0 license](https://github.com/xai-org/grok-build/blob/be713136d2a69080743a3f6b3c72077057e5948f/LICENSE)
and [third-party notices](https://github.com/xai-org/grok-build/blob/be713136d2a69080743a3f6b3c72077057e5948f/THIRD-PARTY-NOTICES).
Their reviewed SHA-256 values are respectively
`116f7778b9802e569b7fa3a532b17bd80eb13c67837def01eed093d4ea472f28` and
`7b7c315403c596f9b7a13bb562553ee4fd4c05da8672f95bcaa02a125eea2947`.

OAuth, account state, model discovery, and process launch are not responsibilities of this artifact
pack. Those boundaries are implemented and tested in later phases.
