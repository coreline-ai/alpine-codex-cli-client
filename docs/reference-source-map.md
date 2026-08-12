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

Phase 1의 허용 파일 목록과 source/destination hash는
[`reference-runtime-files.tsv`](reference-runtime-files.tsv)에 생성한다. 이 파일은
`scripts/import-runtime-reference.sh`가 8개 허용 module에서 build/cache/.cxx를 제외한 file만
선별 이식하면서 작성한다. destination hash가 source hash와 다르면 script는 overwrite하지 않고 실패한다.
이식 후에는 `scripts/verify-runtime-reference-manifest.sh`가 source와 destination hash를 모두 검증한다.

## Dirty UI snapshot — import requires explicit review

| Reference path | Planned destination | SHA-256 | 상태 |
|---|---|---|---|
| `alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/ChatViewModel.kt` | 동일 상대 경로 | `890cd820b3eef5a8a1daa3b378f17a188f6dbd7ad1a254680fc1796e84fbd070` | not imported |
| `alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/designsystem/AlpineProductComponents.kt` | 동일 상대 경로 | `522f8c06c80a86716f9f4c0e93f49ce587a7e61f5f0642e9ca86afef3eaf0fb9` | not imported |
| `alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/screens/chat/AlpineChatScreen.kt` | 동일 상대 경로 | `e8c9eb326ba3b25ceca58f35a638e22ce5288a20a8c3a5999698a9c5700187f8` | not imported |
| `alpine-chat-feature/src/test/java/dev/alpine/chat/feature/ui/ChatGenerationStateTest.kt` | 동일 상대 경로 | `f7376c8f8f5e9eead6f03f1668fe4f3e7f794d121f497785f4d2bea09d529dcb` | not imported |

## Official Grok source and artifact preflight

No Grok source file or binary is copied or tracked in this Phase 0 entry. It records the reviewed upstream identity that Phase 2 must reproduce in a debug-generated artifact pack.

| Item | Official source | Locked identity | Project state |
|---|---|---|---|
| Grok source snapshot | `https://github.com/xai-org/grok-build` | repository HEAD `be713136d2a69080743a3f6b3c72077057e5948f`; embedded source revision `5d08d7e4123092567ccd584cd9f99afa2972065c`; `LICENSE` SHA-256 `116f7778b9802e569b7fa3a532b17bd80eb13c67837def01eed093d4ea472f28`; `THIRD-PARTY-NOTICES` SHA-256 `7b7c315403c596f9b7a13bb562553ee4fd4c05da8672f95bcaa02a125eea2947` | reviewed; no source copied; notice links pinned to commit |
| Grok Linux AArch64 artifact | `https://x.ai/cli/grok-1.0.0-linux-aarch64` | version `1.0.0`; size `133745832`; SHA-256 `bb7c51116564a2219f6a49850815060f416918ac407f1f2ba82c53c0b0d4383f`; observed version `grok 1.0.0 (3cd0d0cbce)` | lock tracked; binary generated into debug asset only and not tracked |
| Official installer | `https://x.ai/cli/install.sh` | Linux AArch64 selection and version smoke reviewed; no signed checksum validation observed | not copied or executed by the Android app |

The upstream checksum is not a published signature. Phase 2 now fails closed on the project lock,
static AArch64 ELF validation, app-private staging revalidation, APK hash audit, and Git exclusion.
Every future upgrade remains a separate source/artifact review.

### Phase 3 reviewed Grok launch/profile sources

The following files were reviewed from clean upstream commit
`be713136d2a69080743a3f6b3c72077057e5948f`. No upstream source file was copied into the project;
the project implementation is an independently written, narrower policy.

| Official source path | SHA-256 | Working tree | Reviewed contract |
|---|---|---|---|
| `crates/codegen/xai-grok-pager/src/app/cli.rs` | `f38cf8001db82e625c996608a2433a81700f30e47b108218a6f77b9a29cae160` | clean | `--cwd`, `--no-auto-update`, `agent stdio`, `--no-leader`, `--agent-profile` |
| `crates/codegen/xai-grok-agent/src/config.rs` | `68178e65fe71291f7842002fe0e8beaacf45a79d56a303d6973a0137a8ba650a` | clean | camelCase profile fields, skill/MCP/subagent defaults |
| `crates/codegen/xai-grok-agent/src/builder.rs` | `dd0dd24100ad64ac9b18c92905db227fba62adf112fc26d19937732cd8573348` | clean | allowlist sentinel, special integration tools, subagent stripping |
| `crates/codegen/xai-grok-shell/src/agent/auth_method.rs` | `805e37750429a65cdfb0c50d78996880d80c4742eb270cde75d2af6a4aadd1b5` | clean | official key-auth disable gate |
| `crates/codegen/xai-grok-shell/src/auth/flow.rs` | `7560e34d74a3d26d2275763aeb2161f8139435ff156fa11d17392ce3223a3dc3` | clean | Device Flow environment selection |
| `crates/codegen/xai-grok-shell/src/agent/mvp_agent/acp_agent.rs` | `1235616b00d9ea96dcd74c5019e3430db8b64bb180cea0b1e1eb9713f0e5a001` | clean | raw initialize capability breadth and authenticated session boundary |

## Verification

```bash
sh scripts/verify-reference-source-map.sh \
  /Volumes/ExternalSSD/projects_8/alpine-llm-gateway
```
