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
    GATEWAY_CAPABILITY_FAILED,
    CODEX_BACKEND_START_FAILED,
    GATEWAY_BIND_FAILED,
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
    val grokExecutable: String = "/workspace/.alpine-grok/staging/grok-cli/1.0.0/grok",
    val grokHomeDirectory: String = "/workspace/.alpine-grok/home",
    val grokWorkDirectory: String = "/workspace/.alpine-grok/work",
    val capabilityFile: String = "/workspace/.alpine-codex/security/gateway-capability.v1",
    val socketPath: String,
    val expectedPeerUid: Int,
) {
    init {
        listOf(
            codexExecutable,
            gatewayRootDirectory,
            homeDirectory,
            workspaceDirectory,
            grokExecutable,
            grokHomeDirectory,
            grokWorkDirectory,
            capabilityFile,
            socketPath,
        ).forEach { value ->
            require(GUEST_PATH.matches(value)) { "gateway launch path is invalid" }
        }
        require(HOST_SOCKET_PATH.matches(socketPath)) { "gateway socket path is invalid" }
        require(socketPath.toByteArray(Charsets.UTF_8).size <= 107) {
            "gateway socket path is too long"
        }
        require(expectedPeerUid >= 0) { "gateway peer UID is invalid" }
    }

    fun command(): String =
        "exec /usr/bin/python3 -m codex_gateway.agent_gateway --codex $codexExecutable " +
            "--grok $grokExecutable --codex-home $homeDirectory --grok-home $grokHomeDirectory " +
            "--grok-work $grokWorkDirectory --workdir $workspaceDirectory " +
            "--capability-file $capabilityFile --socket-path $socketPath --peer-uid $expectedPeerUid"

    private companion object {
        val GUEST_PATH = Regex("/[A-Za-z0-9_./+-]+")
        val HOST_SOCKET_PATH = Regex(
            "/data/(?:user/[0-9]+|data)/[A-Za-z0-9._-]{1,128}/files/" +
                "alpine-codex-runtime/workspace/" +
                "\\.gateway/gateway\\.sock",
        )
    }
}

fun interface GatewayArtifactStager {
    fun stage(): GatewayLaunchSpec
}

interface GatewayRuntimeHealthClient {
    fun isRuntimeHealthy(): Boolean
}

interface GatewaySessionLifecycle {
    fun onGatewayStartFailed()
    fun onRuntimeStopped()

    companion object {
        val NO_OP = object : GatewaySessionLifecycle {
            override fun onGatewayStartFailed() = Unit
            override fun onRuntimeStopped() = Unit
        }
    }
}

fun interface CodexRuntimeStateListener {
    fun onStateChanged(state: CodexRuntimeState)
}

/** Minimal lifecycle view consumed by the chat UI; test fakes cannot start a Runtime session. */
interface CodexRuntimeStateSource {
    fun currentState(): CodexRuntimeState
    fun addStateListener(listener: CodexRuntimeStateListener): RuntimeSubscription
}

/**
 * Serializes Runtime → verified artifact staging → gateway terminal start. It never accepts a
 * command string, endpoint, environment map, token, or alternate backend from callers.
 */
class CodexRuntimeController(
    private val runtimeHost: GatewayRuntimeHost,
    private val stager: GatewayArtifactStager,
    private val gatewayClient: GatewayRuntimeHealthClient,
    private val homeDirectory: String,
    private val gatewayReadyTimeoutMillis: Long = 30_000L,
    private val sessionLifecycle: GatewaySessionLifecycle = GatewaySessionLifecycle.NO_OP,
) : AutoCloseable, CodexRuntimeStateSource {
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

    override fun currentState(): CodexRuntimeState = synchronized(lock) { state }

    override fun addStateListener(listener: CodexRuntimeStateListener): RuntimeSubscription {
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
            val outcome = runCatching { gatewayClient.isRuntimeHealthy() }
            synchronized(lock) {
                if (outcome.getOrDefault(false)) {
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
                    val failure = GATEWAY_FAILURE_MARKERS.entries.firstOrNull { (marker, _) ->
                        combined.containsBytes(marker)
                    }
                    when {
                        failure != null -> ready.completeExceptionally(CodexRuntimeException(failure.value))
                        combined.containsBytes(GATEWAY_READY_MARKER) -> ready.complete(Unit)
                    }
                    tail = combined.takeLastBytes((MAX_GATEWAY_MARKER_BYTES - 1).coerceAtLeast(0))
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
                val healthy = runCatching { gatewayClient.isRuntimeHealthy() }.getOrDefault(false)
                if (!healthy) {
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
        runCatching { sessionLifecycle.onGatewayStartFailed() }
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
            runCatching { sessionLifecycle.onRuntimeStopped() }
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
        val GATEWAY_READY_MARKER = "AGENT_GATEWAY_READY".toByteArray(Charsets.US_ASCII)
        val GATEWAY_FAILURE_MARKERS = linkedMapOf(
            "AGENT_GATEWAY_FAILED_CAPABILITY".toByteArray(Charsets.US_ASCII) to
                CodexRuntimeErrorCode.GATEWAY_CAPABILITY_FAILED,
            "AGENT_GATEWAY_FAILED_CODEX".toByteArray(Charsets.US_ASCII) to
                CodexRuntimeErrorCode.CODEX_BACKEND_START_FAILED,
            "AGENT_GATEWAY_FAILED_BIND".toByteArray(Charsets.US_ASCII) to
                CodexRuntimeErrorCode.GATEWAY_BIND_FAILED,
        )
        val MAX_GATEWAY_MARKER_BYTES =
            (GATEWAY_FAILURE_MARKERS.keys + GATEWAY_READY_MARKER).maxOf(ByteArray::size)
    }
}
