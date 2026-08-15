package dev.alpine.codexclient

import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.runtime.api.RuntimeErrorCode
import dev.alpine.runtime.api.RuntimeOperationException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage

/**
 * Coalesces the complete app-private Runtime recovery path.
 *
 * Python availability is rechecked rather than persisted because the Alpine filesystem is the
 * authority. No credential, command, URL, or alternate package name crosses this boundary.
 */
internal class ConfiguredRuntimeStarter(
    private val gatewayLifecycle: () -> CodexRuntimeLifecycle,
    private val gatewayHealthy: () -> Boolean,
    private val stopStaleGateway: () -> CompletionStage<*>,
    private val startAlpine: () -> CompletionStage<*>,
    private val preparePython: () -> CompletionStage<GatewayPythonBootstrapOutcome>,
    private val startGateway: () -> CompletionStage<*>,
) {
    private val lock = Any()
    private var inFlight: CompletableFuture<Unit>? = null

    fun start(): CompletionStage<Unit> = synchronized(lock) {
        inFlight?.takeUnless { it.isDone }?.let { return@synchronized it }

        val result = CompletableFuture<Unit>()
        inFlight = result
        if (
            gatewayLifecycle() == CodexRuntimeLifecycle.RUNNING &&
            runCatching(gatewayHealthy).getOrDefault(false)
        ) {
            result.complete(Unit)
            inFlight = null
            return@synchronized result
        }
        // Any non-healthy state owns a full reset. This also clears a raw Alpine session left
        // behind when Python preparation failed before the Gateway lifecycle ever left STOPPED.
        val gatewayRecovery = runCatching { stopStaleGateway().toUnit() }
            .getOrElse { failedStage(it) }
        gatewayRecovery
            .thenCompose {
                runCatching { startAlpine().toUnit() }.getOrElse { failedStage(it) }
            }
            .thenCompose { preparePython() }
            .thenCompose { outcome ->
                if (outcome.isReady()) {
                    runCatching { startGateway().toUnit() }.getOrElse { failedStage(it) }
                } else {
                    failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                }
            }
            .whenComplete { _, error ->
                synchronized(lock) {
                    if (error == null) result.complete(Unit) else result.completeExceptionally(error)
                    if (inFlight === result) inFlight = null
                }
            }
        result
    }

    private fun GatewayPythonBootstrapOutcome.isReady(): Boolean =
        this == GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE ||
            this == GatewayPythonBootstrapOutcome.INSTALLED

    private fun CompletionStage<*>.toUnit(): CompletionStage<Unit> = thenApply { Unit }

    private fun <T> failedStage(error: Throwable): CompletionStage<T> =
        CompletableFuture<T>().also { it.completeExceptionally(error) }
}
