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
    const val SECURITY_DIRECTORY = "security"
    // Keep the host-side AF_UNIX pathname below Linux's 108-byte sockaddr_un limit even for
    // the longest lab application id. The directory is still owner-only inside /workspace.
    const val TRANSPORT_DIRECTORY = ".gateway"
    const val GUEST_HOME = "/workspace/.alpine-codex/$HOME_DIRECTORY"
    const val GUEST_STAGING = "/workspace/.alpine-codex/$STAGING_DIRECTORY"
    const val GUEST_GATEWAY = "/workspace/.alpine-codex/$GATEWAY_DIRECTORY"
    const val GUEST_SECURITY = "/workspace/.alpine-codex/$SECURITY_DIRECTORY"
    const val GUEST_CAPABILITY_FILE = "$GUEST_SECURITY/gateway-capability.v1"
    const val GATEWAY_SOCKET_FILE = "gateway.sock"
}
