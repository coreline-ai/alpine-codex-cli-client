# Runtime reference adaptations

이 문서는 Phase 1에서 원본 hash와 다른 destination hash를 의도적으로 허용한 최소 변경을 기록한다.
모든 원본·대상 hash는 [reference-runtime-files.tsv](reference-runtime-files.tsv)가 기준이다.

| Destination path | 변경 | 이유 |
|---|---|---|
| `alpine-runtime-android/build.gradle.kts` | native ABI를 `arm64-v8a`만 허용 | 1차 debug milestone의 Samsung arm64 지원 범위를 강제하고 x86_64 native build를 배제 |
| `alpine-runtime-background-android/src/main/AndroidManifest.xml` | foreground service special-use 설명 변경 | terminal/workspace 제품 표현을 Codex Gateway 실행 상태로 변경 |
| `alpine-runtime-background-android/src/main/kotlin/dev/alpine/runtime/background/android/RuntimeForegroundService.kt` | notification channel, label, action, ID 변경 | 참조 앱과의 UI·notification namespace 혼동을 방지하고 Codex Runtime 소유를 표시 |

이 문서에 없는 source/destination hash 차이는 `scripts/verify-runtime-reference-manifest.sh`에서 실패한다.
