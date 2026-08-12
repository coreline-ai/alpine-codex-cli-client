# Debug SBOM and component inventory

Date: `2026-08-12 KST`

The project generates a deterministic debug-only inventory during both `debug` and `secureDebug`
asset merge. It currently contains 19 direct components, including official Codex CLI `0.147.0`,
official Grok CLI `1.0.0`, Alpine Runtime `3.21.3`, the project Gateway, and direct Maven dependencies.

| Artifact | Tracked | SHA-256 at Phase 9 readiness |
|---|---:|---|
| `alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json` | yes | `678ed604a09a22d5e63c3f2289225de0a85b7c868f05e78817f7a54e4d1d42bc` |
| generated `debug-component-inventory.json` | no; regenerated into APK | `dfad5428020b7c213b727ce705780090a718f3d4ccfbeb479545e499fa201a2b` |

The inventory format is `alpine-codex-debug-component-inventory/v1`; the embedded Runtime SBOM is
SPDX 2.3. CLI binaries remain generated debug assets and are not Git-tracked. The clean-room verifier
requires the inventory, validates both locked CLI hashes in the APK, and rejects release artifacts or
forbidden authentication/provider bytes.
