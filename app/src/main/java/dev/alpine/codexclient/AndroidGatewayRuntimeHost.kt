package dev.alpine.codexclient

import dev.alpine.codexclient.bridge.GatewayRuntimeHost
import dev.alpine.codexclient.bridge.GatewayRuntimeLease
import dev.alpine.runtime.api.AlpineRuntimeManager
import dev.alpine.runtime.api.RuntimeLifecycleState
import dev.alpine.runtime.api.RuntimeStartRequest
import dev.alpine.runtime.api.RuntimeStopReason
import dev.alpine.runtime.api.RuntimeTerminalRequest
import dev.alpine.runtime.api.RuntimeTerminalSession
import dev.alpine.runtime.host.RuntimeHostController
import java.util.concurrent.CompletionStage

/**
 * Bridges one raw Runtime session into the Codex gateway lifecycle without registering a host
 * terminal or storing its output. The only guest environment value is the CLI-owned HOME path.
 */
internal class AndroidGatewayRuntimeHost(
    private val runtimeManager: AlpineRuntimeManager,
    private val hostController: RuntimeHostController,
) : GatewayRuntimeHost {
    override fun startRuntime(homeDirectory: String): CompletionStage<GatewayRuntimeLease> =
        hostController.start(RuntimeStartRequest(environment = mapOf("HOME" to homeDirectory))).thenApply { session ->
            object : GatewayRuntimeLease {
                override fun openGatewayTerminal(request: RuntimeTerminalRequest): CompletionStage<RuntimeTerminalSession> =
                    session.openTerminal(request)
            }
        }

    override fun stopRuntime(): CompletionStage<Void> {
        return hostController.stop(RuntimeStopReason.USER_REQUEST)
    }

    override fun hasActiveRuntime(): Boolean =
        runtimeManager.currentState().lifecycle == RuntimeLifecycleState.RUNNING
}
