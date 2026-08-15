# Gradle supply-chain status

Date: `2026-08-15 KST`

## Distribution scope

Public distribution is the primary product goal. Phase 6 rootfs replacement policy is explicitly
excluded from that decision; this document covers the Kotlin/Gradle build inputs used to produce the
Android artifact.

## Enforced now

- `dependencyLocking { lockAllConfigurations() }` applies to every project.
- 14 module lockfiles plus `settings-gradle.lockfile` pin resolved Maven components across the
  debug, secureDebug, release, unit-test, instrumentation, lint and packaging configurations
  exercised by the milestone build.
- Dynamic catalog and literal dependency versions (`+`, ranges, `latest.*`, `SNAPSHOT`) are rejected.
- Project repositories are centrally restricted with `RepositoriesMode.FAIL_ON_PROJECT_REPOS`.
- Only `google()`, `mavenCentral()` and `gradlePluginPortal()` are accepted; custom Maven repositories
  are rejected by the local policy verifier.
- Gradle build cache, Kotlin task cache and Kotlin incremental compilation are disabled. No remote
  build cache is configured.
- Missing/malformed/non-deterministic lockfiles and cache re-enablement fail the milestone before APK
  acceptance.
- `alpine-python-pack-bundled` is a local-input-only Android module. Its task produces an explicit
  unavailable marker when no pack is present, while public packaging separately requires a verified
  production pack and never downloads one.

```bash
python3 scripts/verify-gradle-supply-chain.py --project-root .
./gradlew :app:assembleSecureDebug --offline --no-daemon --console=plain
```

## Reviewed lock update

Lock updates are never performed during a normal build. A dependency change must update the version
catalog or an explicit build-script version first, then run the same credential-free milestone task
set with `--write-locks`. Review must include the lockfile diff and generated APK component inventory.
Normal CI and local verification run without `--write-locks`, so an unreviewed selector cannot silently
rewrite the committed state.

## Pending inputs

Two Phase 7 items could not be generated under the current no-external-service rule:

1. `gradle/verification-metadata.xml`: Gradle requested plugin transitive POM/artifact metadata that is
   not present in the local cache when `--write-verification-metadata sha256 --offline` was attempted.
2. Wrapper `distributionSha256Sum`: the installed wrapper cache retains the extracted Gradle 8.11.1
   distribution but not its original ZIP, so the ZIP checksum cannot be derived locally.

No network fallback was used. The verifier reports these two states as `false` rather than claiming
checksum coverage that does not exist. Dependency version locks and cache isolation remain active.
The public release variant, external signing input boundary, and signed APK/AAB verifier are
implemented separately as documented in `public-release.md`.
