package dev.alpine.codexclient.bridge

import dev.alpine.runtime.api.RuntimeSubscription
import dev.alpine.runtime.api.RuntimeTerminalRequest
import dev.alpine.runtime.api.RuntimeTerminalSession
import dev.alpine.runtime.api.RuntimeTerminalSignal
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

enum class CodexRuntimeLifecycle {
    STOPPED,
    STARTING,
    RUNNING,
    STOPPING,
    FAILED,
}

enum class CodexRuntimeErrorCode {
    BUSY,
    RUNTIME_START_FAILED,
    ARTIFACT_STAGING_FAILED,
    GATEWAY_START_FAILED,
    GATEWAY_READY_TIMEOUT,
    GATEWAY_HEALTH_FAILED,
    RUNTIME_STOP_FAILED,
}

class CodexRuntimeException(val errorCode: CodexRuntimeErrorCode) : RuntimeException(errorCode.name)

data class CodexRuntimeState(
    val lifecycle: CodexRuntimeLifecycle,
    val generation: Long,
    val errorCode: CodexRuntimeErrorCode? = null,
)

/** App adapter for a raw Runtime session; no host terminal UI is involved. */
interface GatewayRuntimeLease {
    fun openGatewayTerminal(request: RuntimeTerminalRequest): CompletionStage<RuntimeTerminalSession>
}

/** Android app owns the runtime manager; this controller owns only the gateway child lifecycle. */
interface GatewayRuntimeHost {
    fun startRuntime(homeDirectory: String): CompletionStage<GatewayRuntimeLease>
    fun stopRuntime(): CompletionStage<Void>
    fun hasActiveRuntime(): Boolean
}

/** Fixed, validated artifact paths needed to launch the bundled Python gateway. */
data class GatewayLaunchSpec(
    val codexExecutable: String,
    val gatewayRootDirectory: String,
    val homeDirectory: String,
    val workspaceDirectory: String,
) {
    init {
        listOf(codexExecutable, gatewayRootDirectory, homeDirectory, workspaceDirectory).forEach { value ->
            require(GUEST_PATH.matches(value)) { "gateway launch path is invalid" }
        }
    }

    fun command(): String =
        "exec /usr/bin/python3 -m codex_gateway.gateway --codex $codexExecutable " +
            "--home $homeDirectory --workdir $workspaceDirectory --port 8787"

    private companion object {
        val GUEST_PATH = Regex("/[A-Za-z0-9_./+-]+")
    }
}

fun interface GatewayArtifactStager {
    fun stage(): GatewayLaunchSpec
}

fun interface CodexRuntimeStateListener {
    fun onStateChanged(state: CodexRuntimeState)
}

/**
 * Serializes Runtime → verified artifact staging → gateway terminal start. It never accepts a
 * command string, endpoint, environment map, token, or alternate backend from callers.
 */
class CodexRuntimeController(
    private val runtimeHost: GatewayRuntimeHost,
    private val stager: GatewayArtifactStager,
    private val gatewayClient: CodexGatewayClient,
    private val homeDirectory: String,
    private val gatewayReadyTimeoutMillis: Long = 30_000L,
) : AutoCloseable {
    private val lock = Any()
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "codex-runtime-bridge").apply { isDaemon = true }
    }
    private val scheduler = Executors.newSingleThreadScheduledExecutor { runnable ->
        Thread(runnable, "codex-gateway-ready-timeout").apply { isDaemon = true }
    }
    private val listeners = linkedSetOf<CodexRuntimeStateListener>()
    private var state = CodexRuntimeState(CodexRuntimeLifecycle.STOPPED, generation = 0)
    private var lease: GatewayRuntimeLease? = null
    private var terminal: RuntimeTerminalSession? = null
    private var outputSubscription: RuntimeSubscription? = null
    private var startFuture: CompletableFuture<CodexRuntimeState>? = null
    private var stopFuture: CompletableFuture<CodexRuntimeState>? = null

    init {
        require(homeDirectory.startsWith('/'))
        require(gatewayReadyTimeoutMillis > 0)
    }

    fun currentState(): CodexRuntimeState = synchronized(lock) { state }

    fun addStateListener(listener: CodexRuntimeStateListener): RuntimeSubscription {
        synchronized(lock) {
            listeners += listener
            listener.onStateChanged(state)
        }
        return RuntimeSubscription { synchronized(lock) { listeners -= listener } }
    }

    fun start(): CompletionStage<CodexRuntimeState> = synchronized(lock) {
        when (state.lifecycle) {
            CodexRuntimeLifecycle.RUNNING -> CompletableFuture.completedFuture(state)
            CodexRuntimeLifecycle.STARTING -> startFuture ?: failed(CodexRuntimeErrorCode.BUSY)
            CodexRuntimeLifecycle.STOPPING -> failed(CodexRuntimeErrorCode.BUSY)
            CodexRuntimeLifecycle.STOPPED, CodexRuntimeLifecycle.FAILED -> {
                val generation = state.generation + 1
                val future = CompletableFuture<CodexRuntimeState>()
                startFuture = future
                updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.STARTING, generation))
                executor.execute { startRuntime(generation, future) }
                future
            }
        }
    }

    /** Reattaches only when a Runtime survives host recreation; it never starts a new gateway. */
    fun reconnectIfRuntimeActive(): CompletionStage<CodexRuntimeState> = synchronized(lock) {
        if (state.lifecycle == CodexRuntimeLifecycle.RUNNING) return CompletableFuture.completedFuture(state)
        if (!runtimeHost.hasActiveRuntime()) return CompletableFuture.completedFuture(state)
        val generation = state.generation + 1
        val future = CompletableFuture<CodexRuntimeState>()
        updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.STARTING, generation))
        executor.execute {
            val outcome = runCatching { gatewayClient.health() }
            synchronized(lock) {
                if (outcome.isSuccess && isHealthy(outcome.getOrThrow())) {
                    updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.RUNNING, generation))
                    future.complete(state)
                } else {
                    updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.FAILED, generation, CodexRuntimeErrorCode.GATEWAY_HEALTH_FAILED))
                    future.completeExceptionally(CodexRuntimeException(CodexRuntimeErrorCode.GATEWAY_HEALTH_FAILED))
                }
            }
        }
        future
    }

    fun stop(): CompletionStage<CodexRuntimeState> = synchronized(lock) {
        if (state.lifecycle == CodexRuntimeLifecycle.STOPPED) return CompletableFuture.completedFuture(state)
        if (state.lifecycle == CodexRuntimeLifecycle.STOPPING) return stopFuture ?: CompletableFuture.completedFuture(state)
        val generation = state.generation + 1
        val future = CompletableFuture<CodexRuntimeState>()
        stopFuture = future
        updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.STOPPING, generation))
        startFuture?.takeIf { !it.isDone }?.completeExceptionally(CodexRuntimeException(CodexRuntimeErrorCode.BUSY))
        executor.execute { stopRuntime(generation, future) }
        future
    }

    private fun startRuntime(generation: Long, future: CompletableFuture<CodexRuntimeState>) {
        runtimeHost.startRuntime(homeDirectory).whenComplete { openedLease, startError ->
            executor.execute {
                if (!isStarting(generation)) {
                    // A user stop already owns Runtime cleanup.  A late start callback must not
                    // issue another stop or overwrite the stable stopping/stopped state.
                    return@execute
                }
                if (startError != null || openedLease == null) {
                    failStart(generation, future, CodexRuntimeErrorCode.RUNTIME_START_FAILED)
                    return@execute
                }
                lease = openedLease
                val spec = runCatching { stager.stage() }.getOrElse {
                    failStart(generation, future, CodexRuntimeErrorCode.ARTIFACT_STAGING_FAILED)
                    return@execute
                }
                openedLease.openGatewayTerminal(
                    RuntimeTerminalRequest(
                        workingDirectory = spec.gatewayRootDirectory,
                        environment = mapOf("HOME" to spec.homeDirectory),
                    ),
                ).whenComplete { openedTerminal, terminalError ->
                    executor.execute {
                        if (!isStarting(generation)) {
                            // stop() is already serialized on this controller's executor.
                            return@execute
                        }
                        if (terminalError != null || openedTerminal == null) {
                            failStart(generation, future, CodexRuntimeErrorCode.GATEWAY_START_FAILED)
                            return@execute
                        }
                        terminal = openedTerminal
                        launchGateway(generation, future, openedTerminal, spec)
                    }
                }
            }
        }
    }

    private fun launchGateway(
        generation: Long,
        future: CompletableFuture<CodexRuntimeState>,
        openedTerminal: RuntimeTerminalSession,
        spec: GatewayLaunchSpec,
    ) {
        val ready = CompletableFuture<Unit>()
        var tail = byteArrayOf()
        outputSubscription = openedTerminal.addOutputListener { bytes ->
            synchronized(ready) {
                if (!ready.isDone) {
                    val combined = tail + bytes
                    if (combined.containsBytes(GATEWAY_READY_MARKER)) ready.complete(Unit)
                    tail = combined.takeLastBytes((GATEWAY_READY_MARKER.size - 1).coerceAtLeast(0))
                }
            }
        }
        val timeout: ScheduledFuture<*> = scheduler.schedule(
            { ready.completeExceptionally(CodexRuntimeException(CodexRuntimeErrorCode.GATEWAY_READY_TIMEOUT)) },
            gatewayReadyTimeoutMillis,
            TimeUnit.MILLISECONDS,
        )
        openedTerminal.write((spec.command() + "\n").toByteArray(Charsets.UTF_8)).whenComplete { _, writeError ->
            if (writeError != null) ready.completeExceptionally(writeError)
        }
        ready.whenComplete { _, readyError ->
            timeout.cancel(false)
            executor.execute {
                if (!isStarting(generation) || readyError != null) {
                    failStart(
                        generation,
                        future,
                        if (readyError is CodexRuntimeException) readyError.errorCode else CodexRuntimeErrorCode.GATEWAY_START_FAILED,
                    )
                    return@execute
                }
                val health = runCatching { gatewayClient.health() }
                if (health.isFailure || !isHealthy(health.getOrThrow())) {
                    failStart(generation, future, CodexRuntimeErrorCode.GATEWAY_HEALTH_FAILED)
                    return@execute
                }
                synchronized(lock) {
                    if (!isStarting(generation)) {
                        failStart(generation, future, CodexRuntimeErrorCode.GATEWAY_START_FAILED)
                        return@synchronized
                    }
                    updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.RUNNING, generation))
                    future.complete(state)
                }
            }
        }
    }

    private fun failStart(
        generation: Long,
        future: CompletableFuture<CodexRuntimeState>,
        code: CodexRuntimeErrorCode,
    ) {
        cleanupGatewayTerminal()
        runtimeHost.stopRuntime().whenComplete { _, _ ->
            synchronized(lock) {
                if (state.generation == generation || state.lifecycle == CodexRuntimeLifecycle.STARTING) {
                    updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.FAILED, generation, code))
                }
                future.completeExceptionally(CodexRuntimeException(code))
            }
        }
    }

    private fun stopRuntime(generation: Long, future: CompletableFuture<CodexRuntimeState>) {
        cleanupGatewayTerminal()
        runtimeHost.stopRuntime().whenComplete { _, error ->
            synchronized(lock) {
                if (error == null) {
                    updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.STOPPED, generation))
                    future.complete(state)
                } else {
                    updateLocked(CodexRuntimeState(CodexRuntimeLifecycle.FAILED, generation, CodexRuntimeErrorCode.RUNTIME_STOP_FAILED))
                    future.completeExceptionally(CodexRuntimeException(CodexRuntimeErrorCode.RUNTIME_STOP_FAILED))
                }
            }
        }
    }

    private fun cleanupGatewayTerminal() {
        outputSubscription?.close()
        outputSubscription = null
        val active = terminal
        terminal = null
        lease = null
        if (active != null) {
            runCatching { active.signal(RuntimeTerminalSignal.TERMINATE) }
            runCatching { active.closeAsync() }
        }
    }

    private fun isStarting(generation: Long): Boolean = synchronized(lock) {
        state.lifecycle == CodexRuntimeLifecycle.STARTING && state.generation == generation
    }

    private fun isHealthy(health: GatewayHealth): Boolean =
        health.runtime == "ready" && health.gateway == "ready" && health.codex == "ready"

    private fun updateLocked(next: CodexRuntimeState) {
        state = next
        listeners.toList().forEach { listener -> runCatching { listener.onStateChanged(next) } }
    }

    private fun failed(code: CodexRuntimeErrorCode): CompletionStage<CodexRuntimeState> =
        CompletableFuture<CodexRuntimeState>().also { it.completeExceptionally(CodexRuntimeException(code)) }

    private fun ByteArray.takeLastBytes(limit: Int): ByteArray =
        if (size <= limit) this else copyOfRange(size - limit, size)

    private fun ByteArray.containsBytes(needle: ByteArray): Boolean =
        indices.any { start -> start + needle.size <= size && needle.indices.all { offset -> this[start + offset] == needle[offset] } }

    override fun close() {
        runCatching { stop().toCompletableFuture().get(10, TimeUnit.SECONDS) }
        executor.shutdownNow()
        scheduler.shutdownNow()
    }

    private companion object {
        val GATEWAY_READY_MARKER = "CODEX_GATEWAY_READY".toByteArray(Charsets.US_ASCII)
    }
}
