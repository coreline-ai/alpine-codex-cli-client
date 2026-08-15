# SBOM and component inventory

Date: `2026-08-12 KST`

The project generates one deterministic inventory for `debug`, `secureDebug`, and public `release`
asset merge. It includes the official CLIs, Alpine Runtime, project Gateway, direct Maven dependencies,
and the Alpine Python package pack status. With a production pack, its SPDX document is added as a
second embedded SBOM; without one, the inventory records `not-bundled-release-blocked`.

| Artifact | Tracked | SHA-256 at Phase 9 readiness |
|---|---:|---|
| `alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json` | yes | `f9e0842e72e5a3ff35a89ec1d46ced293844d5538de0df1a5a5dfa4134947b89` |
| generated `component-inventory.json` | no; regenerated into APK/AAB | build-input dependent; verifier checks structure and payload linkage |

The inventory format is `alpine-codex-component-inventory/v1`; the embedded Runtime SBOM is
SPDX 2.3 and now contains all 15 rootfs APK packages plus PRoot/loader. The runtime integrity gate
records Alpine `3.21.3` exactly but does not make the optional Phase 6 patched-rootfs work a release
condition. CLI binaries remain generated assets and are not Git-tracked. The clean-room
verifier requires the inventory, validates both locked CLI hashes in the APK, and rejects forbidden
authentication/provider bytes. The release artifact verifier applies the same payload checks to a
signed APK/AAB and additionally pins the production package identity and expected certificate.
