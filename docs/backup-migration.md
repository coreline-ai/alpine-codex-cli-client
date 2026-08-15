# Backup/D2D and sensitive-state migration

Date: `2026-08-15 KST`

## Policy

- The application manifest sets `android:allowBackup="false"`.
- Security verification must not invoke Google, Samsung, or any other external backup, sync, or
  device-transfer service. `bmgr backupnow`, restore, and Smart Switch transfer are prohibited for
  this project; Codex/Grok authentication is not a reason to enable an unrelated backup channel.
- Android 12+ `dataExtractionRules` excludes every supported root/file/database/shared-preference/
  external domain from both cloud backup and device transfer.
- Legacy `fullBackupContent` excludes the same domains.
- `scripts/verify-backup-policy.py` and the secure APK audit fail closed when these declarations or
  fixed no-backup routes drift.

## Versioned destinations

| Directory | Owner |
|---|---|
| `alpine-codex-home-v1` | official Codex CLI |
| `alpine-grok-home-v1` | official Grok CLI |
| `alpine-gateway-handoff-v1` | transient Android→Gateway capability handoff |
| `alpine-gateway-wrapped-v1` | Android Keystore-wrapped Gateway secret |
| `alpine-conversation-state-v1` | Android Keystore AES-GCM conversation state |

All are direct `noBackupFilesDir` children with mode `0700`; regular copied files are normalized to
`0600`, while existing owner-executable files remain `0700`. After commit, official CLIs may create
same-UID/GID, non-world-writable `0755`/`0644` children; the versioned root itself stays `0700`.
The rootfs, package cache, CLI/Gateway staging, work tree, and UDS stay outside this migration.

## State machine

```text
no valid marker
  -> prove no non-Android same-UID Runtime child
  -> validate roots and every legacy entry without following links
  -> bound entries/file bytes/total bytes and require free-space headroom
  -> copy each category to a private stage
  -> compare normalized type/mode/size/SHA-256 manifests
  -> atomic rename each category
  -> fsync and validate fixed commit marker
  -> relocate, never erase first, legacy sources into no-backup rollback
```

Before the marker, any failure returns the untouched legacy layout. A later launch removes only an
owned, bounded, non-world-writable partial stage and retries. After the marker, destination
validation is mandatory; rollback sources can be moved back if the committed layout is unusable.
Empty PRoot mount points are removed during cleanup. An ambiguous non-empty post-commit legacy tree
is atomically preserved under a fixed no-backup conflict name rather than hidden or deleted.
No credential, OAuth value, account field, prompt, response, filename, or symlink target is logged.

## Symlink rule

External, relative, traversal, unreadable, oversized, or wrong-owner links fail closed. Two existing
CLI-safe forms are copied as links without dereferencing: exact `/dev/null`, and normalized absolute
`/workspace` paths with a strict ASCII segment allowlist. The latter remains inside the already
app-private guest workspace and grants no new host path.

## Automated and device evidence

- JVM: new/existing install, active Runtime fallback, interrupted copy, stale-stage retry, low space,
  oversized/special/world-writable entries, external link rejection, safe link preservation,
  corrupt marker rollback, idempotent restart.
- Samsung lab: real Android `lstat`/UID/GID/chmod/atomic rename, private modes, safe-link copy.
- Samsung secure update: `adb install -r`, fixed `committed=true`, all three Runtime no-backup binds,
  Grok OAuth/history/composer preservation, and singular app/PRoot/Python/Grok processes.
- External backup/export commands are intentionally not part of acceptance testing. Backup/D2D
  closure is proved locally by manifest/resource/APK audits, no-backup path assertions, migration
  tests, and the data-preserving Samsung update. No backup archive is created or restored.
