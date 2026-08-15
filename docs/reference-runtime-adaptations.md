# Runtime reference adaptations

이 문서는 Phase 1에서 원본 hash와 다른 destination hash를 의도적으로 허용한 최소 변경을 기록한다.
모든 원본·대상 hash는 [reference-runtime-files.tsv](reference-runtime-files.tsv)가 기준이다.

| Destination path | 변경 | 이유 |
|---|---|---|
| `alpine-runtime-android/build.gradle.kts` | native ABI를 `arm64-v8a`만 허용 | 1차 debug milestone의 Samsung arm64 지원 범위를 강제하고 x86_64 native build를 배제 |
| `alpine-runtime-background-android/src/main/AndroidManifest.xml` | foreground service special-use 설명 변경 | terminal/workspace 제품 표현을 Codex Gateway 실행 상태로 변경 |
| `alpine-runtime-background-android/src/main/kotlin/dev/alpine/runtime/background/android/RuntimeForegroundService.kt` | notification channel, label, action, ID 변경 | 참조 앱과의 UI·notification namespace 혼동을 방지하고 Codex Runtime 소유를 표시 |
| `alpine-runtime-host/src/main/kotlin/dev/alpine/runtime/host/RuntimeHostController.kt` | tracked operation future를 `onSuccess` state 게시 뒤 완료 | Runtime start 직후 chained Gateway 명령이 session 공개보다 먼저 실행되는 race 방지 |
| `alpine-runtime-host/src/test/kotlin/dev/alpine/runtime/host/RuntimeHostControllerTest.kt` | start completion 뒤 즉시 execute하는 회귀 테스트 추가 | completion/state publication 순서 계약 고정 |
| `alpine-runtime-android/src/main/kotlin/dev/alpine/runtime/android/AndroidAlpineRuntimeFactory.kt` | app `noBackupFilesDir` direct-child와 고정 guest path로 제한된 private bind 계약 추가 | credential/session을 backup/D2D 제외 저장소로 옮기되 임의 host bind 확장을 차단 |
| `alpine-runtime-android/src/main/kotlin/dev/alpine/runtime/android/internal/AndroidAlpineRuntimeManager.kt` | private bind의 canonical parent, symlink, UID, type, `0700` 검증 추가 | 조작된 host directory가 Runtime에 노출되는 것을 fail-closed |
| `alpine-runtime-android/src/main/kotlin/dev/alpine/runtime/android/internal/ProotProcessLauncher.kt` | private UDS host alias와 고정 no-backup bind를 빈 workspace mount point에 추가 | AF_UNIX 경로 호환과 backup 제외 credential home을 유지하면서 legacy 파일 은닉 방지 |
| `alpine-runtime-android/src/test/kotlin/dev/alpine/runtime/android/internal/ProotProcessLauncherTest.kt` | private UDS/no-backup bind 및 non-empty mount point 거부 테스트 추가 | 원본 Runtime의 기본 실행 계약과 보안 adaptation 경계를 함께 고정 |
| `alpine-runtime-android/src/main/kotlin/dev/alpine/runtime/android/internal/RuntimeArtifactInstaller.kt` | 활성/직전 rootfs+marker 세대 보존, 명시적 원자 rollback, install/rollback process-death journal 복구 추가 | 검증 직후 기존 rootfs 삭제를 막고 불완전 이전 세대를 fail-closed하면서 workspace/민감 상태 경계를 유지 |
| `alpine-runtime-android/src/test/kotlin/dev/alpine/runtime/android/internal/RuntimeArtifactInstallerTest.kt` | 3세대 교체, explicit rollback, 중단 전/후 복구, 불완전 previous, workspace/no-backup sentinel 테스트 추가 | rootfs rollback과 민감 경로 비접촉을 자동 회귀 계약으로 고정 |
| `alpine-runtime-pack-bundled/build.gradle.kts` | deterministic runtime supply-chain verifier를 `preBuild`에 연결하고 새 SPDX hash 고정 | 현재 배포 Runtime의 rootfs/package/SBOM drift가 APK build 전에 fail-closed; Phase 6 교체 정책은 배포 판정에서 제외 |
| `alpine-runtime-pack-bundled/src/main/kotlin/dev/alpine/runtime/pack/bundled/BundledRuntimeArtifactProvider.kt` | package-level SPDX hash 갱신 | Runtime manifest와 15개 APK package inventory의 동일성 유지 |
| `alpine-runtime-pack-bundled/src/main/resources/META-INF/alpine-runtime/sbom.spdx.json` | rootfs aggregate-only 문서를 15개 APK package, PRoot, loader 단위 SPDX로 교체 | package version/license/aports revision/installed checksum 가시성과 deterministic 검증 확보 |

이 문서에 없는 source/destination hash 차이는 `scripts/verify-runtime-reference-manifest.sh`에서 실패한다.
