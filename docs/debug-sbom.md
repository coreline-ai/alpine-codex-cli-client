# SBOM and component inventory

Date: `2026-08-16 KST`

The project generates one deterministic inventory for `debug`, `secureDebug`, and public `release`
asset merge. It includes the official CLIs, Alpine Runtime, project Gateway, direct Maven dependencies,
and the Alpine Python package pack status. With a production pack, its SPDX document is added as a
second embedded SBOM; without one, the inventory records `not-bundled-release-blocked`.

| Artifact | Tracked | Integrity rule |
|---|---:|---|
| `alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json` | yes | `runtime-lock.json`과 deterministic regeneration으로 검증 |
| generated `component-inventory.json` | no; regenerated into APK/AAB | build-input dependent; verifier checks structure and payload linkage |

The inventory format is `alpine-codex-component-inventory/v1`; the embedded Runtime SBOM is
SPDX 2.3 and now contains all 15 rootfs APK packages plus PRoot/loader. The runtime integrity gate
records Alpine `3.21.3` exactly but does not make the optional Phase 6 patched-rootfs work a release
condition. CLI binaries remain generated assets and are not Git-tracked. The clean-room
verifier requires the inventory, validates both locked CLI hashes in the APK, and rejects forbidden
authentication/provider bytes. The release artifact verifier applies the same payload checks to a
signed APK/AAB and additionally pins the production package identity and expected certificate.

현재 작업 환경에는 Git-ignored 21-package production Python pack이 있어 생성 inventory에 pack ID,
lock과 별도 SPDX 2.3 문서가 포함된다. pack byte는 저장소에 commit하지 않으므로 새 checkout에
입력이 없으면 다시 `not-bundled-release-blocked`가 기록되고 공개 release를 fail-closed한다.
전체 제품 상태는 [`project-overview.md`](project-overview.md)를 참고한다.
