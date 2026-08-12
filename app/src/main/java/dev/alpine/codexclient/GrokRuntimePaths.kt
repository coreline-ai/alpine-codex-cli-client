package dev.alpine.codexclient

/** Fixed Grok paths that never overlap the existing Codex CLI home or staging tree. */
object GrokRuntimePaths {
    const val PRIVATE_WORKSPACE_DIRECTORY = ".alpine-grok"
    const val HOME_DIRECTORY = "home"
    const val STAGING_DIRECTORY = "staging"
    const val PROFILE_DIRECTORY = "profile"
    const val WORK_DIRECTORY = "work"
    const val GATEWAY_DIRECTORY = "gateway"
    const val PROFILE_FILE = "chat-only.md"

    const val GUEST_ROOT = "/workspace/$PRIVATE_WORKSPACE_DIRECTORY"
    const val GUEST_HOME = "$GUEST_ROOT/$HOME_DIRECTORY"
    const val GUEST_STAGING = "$GUEST_ROOT/$STAGING_DIRECTORY"
    const val GUEST_PROFILE_DIRECTORY = "$GUEST_ROOT/$PROFILE_DIRECTORY"
    const val GUEST_PROFILE = "$GUEST_PROFILE_DIRECTORY/$PROFILE_FILE"
    const val GUEST_WORK = "$GUEST_ROOT/$WORK_DIRECTORY"
    const val GUEST_GATEWAY = "$GUEST_ROOT/$GATEWAY_DIRECTORY"
}
