package dev.alpine.codexclient

/**
 * Fixed guest paths backed by the new app's private workspace. OAuth state remains owned by the
 * official CLI under [HOME]; Android never reads, copies, or parses that state.
 */
object CodexRuntimePaths {
    const val PRIVATE_WORKSPACE_DIRECTORY = ".alpine-codex"
    const val HOME_DIRECTORY = "home"
    const val STAGING_DIRECTORY = "staging"
    const val GATEWAY_DIRECTORY = "gateway"
    const val GUEST_HOME = "/workspace/.alpine-codex/$HOME_DIRECTORY"
    const val GUEST_STAGING = "/workspace/.alpine-codex/$STAGING_DIRECTORY"
    const val GUEST_GATEWAY = "/workspace/.alpine-codex/$GATEWAY_DIRECTORY"
}
