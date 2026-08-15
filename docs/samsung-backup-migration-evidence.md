# Samsung backup migration evidence (redacted)

Date: `2026-08-15 KST`

## Boundary

- Device alias: `SAMSUNG_TARGET`; model `SM-S931N`.
- Package: non-debuggable `dev.alpine.codexclient.debug`.
- Installation used only `adb install -r`; no clear-data, uninstall, logout, credential read,
  account-field read, OAuth URL capture, screenshot, prompt, response, or paid turn.
- UI inspection emitted only fixed Booleans; the transient hierarchy was deleted immediately.

## Result

| Check | Result |
|---|---|
| Final secure APK | PASS: `161182122` bytes; SHA-256 `469e01c16c928e67917f5e5dac51ddd2ece72f879b91b5c229357eb4ecfcf3a5` |
| Migration commit | PASS: fixed audit `committed=true`, failure `none` |
| Runtime binds | PASS: Codex HOME, Grok HOME, Gateway handoff all use versioned `no_backup` paths |
| Existing Grok OAuth | PASS: login action absent and composer enabled after Grok re-selection |
| Existing history | PASS: fixed role-label Boolean present; no message content inspected |
| Stable error | PASS: absent |
| Process cardinality | PASS: app/PRoot/Python/Grok `1/1/1/1`; Codex process `0` |
| Automatic prompt | PASS: none dispatched during migration/reselection |

## External-service boundary

- No `bmgr backupnow`, backup restore, Smart Switch, or other external backup/device-transfer command
  was executed. Such commands are prohibited rather than approval-gated because they may export app
  data or replace target state.
- A fresh OAuth-disabled lab package was temporarily installed only to establish the test boundary,
  then removed. The attempted export command was rejected before its process was created; no archive
  or package payload was sent. The secure package was not modified by this check.
- Backup/D2D acceptance therefore uses only local manifest/resource/APK audits, instrumented
  no-backup-path assertions, and the already completed data-preserving Samsung update.
