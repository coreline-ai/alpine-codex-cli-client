# Runtime supply-chain status

Date: `2026-08-16 KST`

## Security boundary

Runtime verification is offline and content-addressed. It does not contact Alpine repositories,
OSV, secdb, backup services, analytics, or any other external service. It verifies the exact runtime
that this project ships; it does not require a different rootfs or perform implicit mutation.

The checked-in `security/alpine-vulnerability-snapshot.json` is intentionally marked incomplete. It
is scoped to the exact current rootfs/package-inventory hashes and records two findings from the local
security report. Snapshot/source/hash drift fails the integrity build, but completeness and finding
severity are inventory information rather than a release decision. Phase 6 patched-rootfs work is
explicitly excluded from the public-distribution gate.

## Current compatibility matrix

| Rootfs | ABI | PRoot | Python | Codex/Grok status | Distribution policy |
|---|---|---|---|---|---|
| Alpine `3.21.3` | `aarch64` / Android `arm64-v8a` | OpenMinis `8cf13e9` | APK-local locked 21-package pack | Fresh offline `labdebug` install/Gateway/force-stop PASS; signed release pending | Exact rootfs + production Python pack required; patched replacement is not required |

The local environment contains the locked Alpine `3.21.3` asset used by the application. The verifier
describes and protects that artifact without turning optional replacement work into a release gate.

The current local environment contains a production-marked 21-package Python pack in the Git-ignored
default input directory. Gradle generates `available: true`, embeds the pack and blocks on any hash,
metadata, SBOM or coverage drift. A fresh checkout without that local input still generates explicit
`available: false` and fails public packaging rather than using a network fallback.

## APK-contained Python package pack

`alpine-python-pack-bundled` accepts only `ALPINE_PYTHON_PACKAGE_DIR` or its local default directory.
`scripts/python_package_pack.py` verifies exact root entries, lock schema, production marker, package
count, sizes, SHA-256 hashes, signed Alpine APK member structure, `.PKGINFO` name/version and
`aarch64`/`noarch` identity, and SPDX 2.3 SBOM. It then copies the verified bytes into
`assets/alpine-python-pack`.

On Android, `BundledPythonPackageProvider` rechecks status, lock and every package hash while atomically
staging into `/workspace/.alpine-codex/staging/python-pack/<pack-id>`. The only install sequence is:

```text
/sbin/apk add --no-network --no-cache --simulate --no-progress <absolute local .apk paths...>
/sbin/apk add --no-network --no-cache --no-progress <absolute local .apk paths...>
/usr/bin/python3 --version
/usr/bin/python3 -c 'import codex_gateway'
```

There is no package-name, repository, URL, `curl`, or `wget` fallback. Missing/invalid assets, failed
simulate/install, Python smoke, or Gateway import return closed outcomes and do not mark preparation
complete. `--allow-untrusted` is absent so the rootfs Alpine keyring remains the package trust anchor.

## Locked inventory

`alpine-runtime-pack-bundled/runtime-lock.json` is the machine-readable decision record. It fixes
the rootfs, PRoot, loader, deterministic SPDX document, package count, Alpine/APK architecture,
Python presence, and runtime version.

The rootfs currently contains 15 APK packages:

| Package | Version | Declared license |
|---|---|---|
| alpine-baselayout | `3.6.8-r1` | GPL-2.0-only |
| alpine-baselayout-data | `3.6.8-r1` | GPL-2.0-only |
| alpine-keys | `2.5-r0` | MIT |
| alpine-release | `3.21.3-r0` | MIT |
| apk-tools | `2.14.6-r3` | GPL-2.0-only |
| busybox | `1.37.0-r12` | GPL-2.0-only |
| busybox-binsh | `1.37.0-r12` | GPL-2.0-only |
| ca-certificates-bundle | `20241121-r1` | MPL-2.0 AND MIT |
| libcrypto3 | `3.3.3-r0` | Apache-2.0 |
| libssl3 | `3.3.3-r0` | Apache-2.0 |
| musl | `1.2.5-r9` | MIT |
| musl-utils | `1.2.5-r9` | MIT AND BSD-2-Clause AND GPL-2.0-or-later |
| scanelf | `1.3.8-r1` | GPL-2.0-only |
| ssl_client | `1.37.0-r12` | GPL-2.0-only |
| zlib | `1.3.1-r2` | Zlib |

Every APK package has a deterministic SPDX ID, version, declared license, architecture-specific
package URL, origin, aports revision, upstream URL, and installed APK checksum. PRoot and its loader
remain separate SPDX packages with SHA-256 checksums.

## Gates

```bash
python3 scripts/generate-runtime-sbom.py \
  --project-root . \
  --output alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json
python3 scripts/verify-runtime-supply-chain.py --project-root .
```

The command fails on artifact, package inventory, SBOM, vulnerability-evidence scope, or source hash
drift. Gradle `preBuild` and both milestone scripts run this integrity gate automatically. It does not
make the optional patched-rootfs policy a distribution condition.

The parser never extracts the rootfs. It bounds archive size, entry count, metadata size and package
count; rejects absolute/traversal paths, duplicate metadata/packages and malformed revisions; and
compares the checked-in SPDX document byte-for-structure with a deterministic regeneration.

## Optional offline rootfs builder

`scripts/build-offline-alpine-rootfs.py` accepts only a fully staged local rootfs, a reviewed package
lock, and content-addressed source inputs. It performs no download or package installation. The
package lock must fix:

- Alpine version and APK architecture
- exact package-inventory digest and Python package version
- exact staging-tree digest, including file hashes, modes and symlink targets
- every reviewed local source input path, size and SHA-256
- fixed source-date epoch

The builder requires executable prebundled Python and an empty/comment-only `/etc/apk/repositories`,
rejects special/setuid/setgid/world-writable files, unsafe links, hard links, drift and output paths
inside staging, then writes a root-owned deterministic gzip/tar with embedded provenance. Two builds
from the same reviewed inputs are byte-identical in the unit fixture.

```bash
python3 scripts/build-offline-alpine-rootfs.py \
  --staging-root /path/to/reviewed-root \
  --package-lock /path/to/package-lock.json \
  --source-artifacts-root /path/to/reviewed-inputs \
  --output /path/to/alpine-python-rootfs.tar.gz
```

This builder is retained as optional maintenance tooling. No replacement rootfs is required by the
current public-distribution plan; the separate APK-contained Python package pack is required instead,
and neither build path fetches an artifact implicitly.

## Runtime generation retention

Android installation uses a bounded active/previous two-generation layout:

| Path | Role |
|---|---|
| `rootfs.installing` | archive extraction and pre-activation `/bin/sh` smoke |
| `rootfs` + `runtime.properties` | active generation |
| `rootfs.previous` + `runtime.properties.previous` | immediate prior generation retained for rollback |
| `rootfs.rollback` + `runtime.properties.rollback` | process-death-safe temporary swap only |
| `activation.pending` | expected marker fingerprint and install/rollback operation journal |

A successful update no longer deletes the immediate prior generation. A third validated update
replaces only the oldest previous generation after the new staging smoke has passed. Explicit
rollback first validates both complete generation pairs and packaged native hashes, then swaps the
pairs atomically; incomplete or unhealthy previous generations are rejected without touching the
active runtime. Recovery tests cover interruption before and after rollback marker activation.

The installer only moves fixed rootfs and marker paths under the Runtime directory. Tests pin that
upgrade and rollback preserve the Runtime workspace and sibling no-backup credential/session
sentinels. Automatic rollback is not part of the requested product behavior; explicit verified
rollback remains available without changing the normal startup path.
