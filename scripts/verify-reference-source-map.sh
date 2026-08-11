#!/bin/sh
set -eu

source_repo=${1:-/Volumes/ExternalSSD/projects_8/alpine-llm-gateway}

check_file() {
    relative_path=$1
    expected_sha=$2
    actual_sha=$(shasum -a 256 "$source_repo/$relative_path" | awk '{print $1}')
    if [ "$actual_sha" != "$expected_sha" ]; then
        printf '%s\n' "reference hash mismatch: $relative_path" >&2
        exit 1
    fi
}

check_file settings.gradle.kts 3ad2bdab1ceddde5cbcb468522a610256ff09cc6e3a825efb81df5d7c2c7b590
check_file build.gradle.kts dcd44456d0fe239253003cd3368310cdc3a7211f50a5517038025583d0bb4e67
check_file gradle.properties 29b224f68154a50422d91b10f6a95694a283b79802a01b7db7e3f7d0a851e79a
check_file integrated-app/build.gradle.kts 09c2ac661e70b31f68bfeb7d250ae36e1804e0f31931b32cc8864a84559d7f79
check_file alpine-runtime-api/build.gradle.kts 343d62bd030e12de181b73df68cd212f262d3a7577c36610ace9b9430334e859
check_file alpine-runtime-android/build.gradle.kts 0bddaad15acdedb962d64d0bae405048c22e2b1d4ae28878030e35188beafcc5
check_file alpine-runtime-host/build.gradle.kts baf059a45e19a8ab018358f1c6f09a5e7d0e9cf2acf4996a24ee396f9c375206
check_file alpine-runtime-background-android/build.gradle.kts 02b52485530d243cb05271fe19f9cdf94a151039cd70f09d60d11dcce965bf5e
check_file alpine-runtime-ui-compose/build.gradle.kts 2d9571d0cdf5156d5799e6a29aa3ad4b606d386a48929912c8f9bddf468b3f6f
check_file alpine-runtime-pack-bundled/build.gradle.kts 3f108be0501f1dd6bd45c1c33ded206d4d181ceeec28cadf9d00de24a268a981
check_file alpine-workspace-api/build.gradle.kts 08ff0bfada62181e8cd0edaf84880898bb76a8262badea5c03a32fba52a91064
check_file alpine-workspace-android/build.gradle.kts f936cb61cb87d6206d57e11b38c38b8920914b41f6559d41e1e45ccc5fa13034
check_file alpine-chat-routing/build.gradle.kts a9f8a977a272d0a978b16436b456e1cd08df4770200d4522c59ca98b6cdb4438
check_file alpine-chat-feature/build.gradle.kts a0b7e92268fc45defd7a2818509503ff6d4d2eb82bd5b77918f8e989d412a17d
check_file alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/ChatViewModel.kt 890cd820b3eef5a8a1daa3b378f17a188f6dbd7ad1a254680fc1796e84fbd070
check_file alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/designsystem/AlpineProductComponents.kt 522f8c06c80a86716f9f4c0e93f49ce587a7e61f5f0642e9ca86afef3eaf0fb9
check_file alpine-chat-feature/src/main/java/dev/alpine/chat/feature/ui/screens/chat/AlpineChatScreen.kt e8c9eb326ba3b25ceca58f35a638e22ce5288a20a8c3a5999698a9c5700187f8
check_file alpine-chat-feature/src/test/java/dev/alpine/chat/feature/ui/ChatGenerationStateTest.kt f7376c8f8f5e9eead6f03f1668fe4f3e7f794d121f497785f4d2bea09d529dcb

printf '%s\n' "reference source map: PASS"
