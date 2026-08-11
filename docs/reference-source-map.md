# Reference source map

작성일: `2026-08-11 KST`

## Source baseline

| 항목 | 값 |
|---|---|
| 참조 저장소 | `/Volumes/ExternalSSD/projects_8/alpine-llm-gateway` |
| Git branch | `main` |
| Git HEAD | `b81a7d8ee12af72ff95180bfeadabe68e5be950e` |
| 작업 트리 | dirty; 아래 수정 파일은 Phase 1 이식 전에 별도 review 필요 |
| 신규 저장소 | `/Volumes/ExternalSSD/projects_8/alpine-codex-cli-client` |

이 문서는 허용된 참조 범위만 기록한다. `android/`, `alpine-chat-provider-android`,
`alpine-chat-backend-direct`, `CodexOAuthContract`, OAuth registration, CLI fingerprint,
credential 및 Provider direct adapter는 이식 금지다.

## Phase 0 configuration references

| Reference path | Destination path | SHA-256 | Working tree | 용도 |
|---|---|---|---|---|
| `settings.gradle.kts` | `settings.gradle.kts` | `3ad2bdab1ceddde5cbcb468522a610256ff09cc6e3a825efb81df5d7c2c7b590` | clean | repository와 plugin resolution 구조 참고 |
| `build.gradle.kts` | `build.gradle.kts` | `dcd44456d0fe239253003cd3368310cdc3a7211f50a5517038025583d0bb4e67` | clean | AGP/Kotlin version 참고; publication 설정은 이식하지 않음 |
| `gradle.properties` | `gradle.properties` | `29b224f68154a50422d91b10f6a95694a283b79802a01b7db7e3f7d0a851e79a` | clean | JVM/AndroidX/Kotlin 기본값 참고 |
| `integrated-app/build.gradle.kts` | `app/build.gradle.kts` | `09c2ac661e70b31f68bfeb7d250ae36e1804e0f31931b32cc8864a84559d7f79` | clean | Android Compose/debug ABI 구조 참고; Provider modules는 제외 |

## Phase 1 controlled import queue

| Reference path | Destination path | SHA-256 | Working tree | 이식 상태 |
|---|---|---|---|---|
| `alpine-runtime-api/build.gradle.kts` | `alpine-runtime-api/build.gradle.kts` | `343d62bd030e12de181b73df68cd212f262d3a7577c36610ace9b9430334e859` | clean | pending |
| `alpine-runtime-android/build.gradle.kts` | `alpine-runtime-android/build.gradle.kts` | `0bddaad15acdedb962d64d0bae405048c22e2b1d4ae28878030e35188beafcc5` | clean | pending |
| `alpine-runtime-host/build.gradle.kts` | `alpine-runtime-host/build.gradle.kts` | `baf059a45e19a8ab018358f1c6f09a5e7d0e9cf2acf4996a24ee396f9c375206` | clean | pending |
| `alpine-runtime-background-android/build.gradle.kts` | `alpine-runtime-background-android/build.gradle.kts` | `02b52485530d243cb05271fe19f9cdf94a151039cd70f09d60d11dcce965bf5e` | clean | pending |
| `alpine-runtime-ui-compose/build.gradle.kts` | `alpine-runtime-ui-compose/build.gradle.kts` | `2d9571d0cdf5156d5799e6a29aa3ad4b606d386a48929912c8f9bddf468b3f6f` | clean | pending |
| `alpine-runtime-pack-bundled/build.gradle.kts` | `alpine-runtime-pack-bundled/build.gradle.kts` | `3f108be0501f1dd6bd45c1c33ded206d4d181ceeec28cadf9d00de24a268a981` | clean | pending |
| `alpine-workspace-api/build.gradle.kts` | `alpine-workspace-api/build.gradle.kts` | `08ff0bfada62181e8cd0edaf84880898bb76a8262badea5c03a32fba52a91064` | clean | pending |
| `alpine-workspace-android/build.gradle.kts` | `alpine-workspace-android/build.gradle.kts` | `f936cb61cb87d6206d57e11b38c38b8920914b41f6559d41e1e45ccc5fa13034` | clean | pending |
| `alpine-chat-routing/build.gradle.kts` | `alpine-chat-routing/build.gradle.kts` | `a9f8a977a272d0a978b16436b456e1cd08df4770200d4522c59ca98b6cdb4438` | clean | pending |
| `alpine-chat-feature/build.gradle.kts` | `alpine-chat-feature/build.gradle.kts` | `a0b7e92268fc45defd7a2818509503ff6d4d2eb82bd5b77918f8e989d412a17d` | clean | pending |

Phase 1에서는 실제로 복사하는 모든 source/resource/binary 파일을 이 표 아래에 추가한다.
directory 단위 복사는 금지하고, 각 항목의 source SHA-256과 destination SHA-256이 같은지
`scripts/verify-reference-source-map.sh`로 확인한다.

## Dirty UI snapshot — import requires explicit review

| Reference path | Planned destination | SHA-256 | 상태 |
|---|---|---|---|
| `alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/ChatViewModel.kt` | 동일 상대 경로 | `890cd820b3eef5a8a1daa3b378f17a188f6dbd7ad1a254680fc1796e84fbd070` | not imported |
| `alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/designsystem/AlpineProductComponents.kt` | 동일 상대 경로 | `522f8c06c80a86716f9f4c0e93f49ce587a7e61f5f0642e9ca86afef3eaf0fb9` | not imported |
| `alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/screens/chat/AlpineChatScreen.kt` | 동일 상대 경로 | `e8c9eb326ba3b25ceca58f35a638e22ce5288a20a8c3a5999698a9c5700187f8` | not imported |
| `alpine-chat-feature/src/test/java/dev/alpine/chat/feature/ui/ChatGenerationStateTest.kt` | 동일 상대 경로 | `f7376c8f8f5e9eead6f03f1668fe4f3e7f794d121f497785f4d2bea09d529dcb` | not imported |

## Verification

```bash
sh scripts/verify-reference-source-map.sh \
  /Volumes/ExternalSSD/projects_8/alpine-llm-gateway
```
