package dev.alpine.codexclient

import android.app.Application
import android.os.Handler
import android.os.Looper
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.alpine.runtime.api.RuntimeCommandRequest
import dev.alpine.runtime.api.RuntimeErrorCode
import dev.alpine.runtime.api.RuntimeLifecycleState
import dev.alpine.runtime.api.RuntimeOperationException
import dev.alpine.runtime.host.RuntimeHostOperation
import dev.alpine.runtime.host.RuntimeHostState
import dev.alpine.codexclient.cli.CodexCliArtifactException
import dev.alpine.codexclient.grokcli.GrokCliArtifactException
import dev.alpine.codexclient.gatewaypack.CodexGatewayArtifactException
import dev.alpine.codexclient.bridge.CodexRuntimeErrorCode
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

data class RuntimeUiState(
    val lifecycle: RuntimeLifecycleState,
    val operation: RuntimeHostOperation,
    val sessionActive: Boolean,
    val busy: Boolean = false,
    val status: String = "RUNTIME_NOT_READY",
    val errorCode: RuntimeErrorCode? = null,
    val gatewayPythonBootstrap: GatewayPythonBootstrapOutcome? = null,
    val codexCliBootstrap: CodexCliBootstrapOutcome? = null,
    val appServerSmoke: AppServerSmokeOutcome? = null,
    val grokAcpSmoke: GrokAcpSmokeOutcome? = null,
    val gatewayLifecycle: CodexRuntimeLifecycle = CodexRuntimeLifecycle.STOPPED,
    val gatewayErrorCode: CodexRuntimeErrorCode? = null,
)

/** Closed result of staging and fixed-version verification for the official debug CLI. */
enum class CodexCliBootstrapOutcome {
    READY,
    STAGING_FAILED,
    VERSION_CHECK_FAILED,
}

/** Closed outcome of the fixed initialize/account-read smoke; account details are discarded. */
enum class AppServerSmokeOutcome {
    READY,
    STAGING_FAILED,
    INITIALIZE_OR_ACCOUNT_FAILED,
}

/** Content-free result of the fixed official Grok version and ACP initialize probe. */
enum class GrokAcpSmokeOutcome {
    READY,
    STAGING_FAILED,
    POLICY_FAILED,
    VERSION_FAILED,
    PROCESS_FAILED,
    INITIALIZE_FAILED,
    LIFECYCLE_FAILED,
    ACCOUNT_FAILED,
    OUTPUT_INVALID,
}

internal object GrokAcpSmokeParser {
    fun parse(
        exitCode: Int,
        timedOut: Boolean,
        standardOutput: ByteArray,
        standardError: ByteArray,
    ): GrokAcpSmokeOutcome {
        if (timedOut || standardError.isNotEmpty() || standardOutput.size > MAX_OUTPUT_BYTES) {
            return GrokAcpSmokeOutcome.OUTPUT_INVALID
        }
        val marker = runCatching {
            Charsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(standardOutput))
                .toString()
                .removeSuffix("\n")
                .removeSuffix("\r")
        }.getOrNull() ?: return GrokAcpSmokeOutcome.OUTPUT_INVALID
        val outcome = when (marker) {
            "GROK_SMOKE_READY" -> GrokAcpSmokeOutcome.READY
            "GROK_SMOKE_FAILED_POLICY" -> GrokAcpSmokeOutcome.POLICY_FAILED
            "GROK_SMOKE_FAILED_VERSION" -> GrokAcpSmokeOutcome.VERSION_FAILED
            "GROK_SMOKE_FAILED_PROCESS" -> GrokAcpSmokeOutcome.PROCESS_FAILED
            "GROK_SMOKE_FAILED_INITIALIZE" -> GrokAcpSmokeOutcome.INITIALIZE_FAILED
            "GROK_SMOKE_FAILED_LIFECYCLE" -> GrokAcpSmokeOutcome.LIFECYCLE_FAILED
            "GROK_SMOKE_FAILED_ACCOUNT" -> GrokAcpSmokeOutcome.ACCOUNT_FAILED
            else -> GrokAcpSmokeOutcome.OUTPUT_INVALID
        }
        return when {
            outcome == GrokAcpSmokeOutcome.READY && exitCode == 0 -> outcome
            outcome != GrokAcpSmokeOutcome.READY &&
                outcome != GrokAcpSmokeOutcome.OUTPUT_INVALID &&
                exitCode != 0 -> outcome
            else -> GrokAcpSmokeOutcome.OUTPUT_INVALID
        }
    }

    private const val MAX_OUTPUT_BYTES = 64
}

class RuntimeViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as AlpineCodexApplication
    private val mainHandler = Handler(Looper.getMainLooper())
    private val _state = MutableStateFlow(app.runtimeController.currentState().toUiState())
    val state: StateFlow<RuntimeUiState> = _state.asStateFlow()

    private val stateSubscription = app.runtimeController.addStateListener { runtimeState ->
        mainHandler.post {
            _state.update { current ->
                runtimeState.toUiState(
                    busy = current.busy,
                    status = current.status,
                    gatewayPythonBootstrap = current.gatewayPythonBootstrap,
                    codexCliBootstrap = current.codexCliBootstrap,
                    appServerSmoke = current.appServerSmoke,
                    grokAcpSmoke = current.grokAcpSmoke,
                    gatewayLifecycle = current.gatewayLifecycle,
                    gatewayErrorCode = current.gatewayErrorCode,
                )
            }
        }
    }
    private val gatewayStateSubscription = app.codexRuntimeController.addStateListener { gatewayState ->
        mainHandler.post {
            _state.update { current ->
                current.copy(
                    gatewayLifecycle = gatewayState.lifecycle,
                    gatewayErrorCode = gatewayState.errorCode,
                )
            }
        }
    }

    fun install() = runOperation("RUNTIME_INSTALLING") {
        app.runtimeController.install().thenCompose {
            app.setRuntimeRestoreRequested(true)
            app.restoreConfiguredRuntime()
        }
    }

    fun startAlpine() = startConfiguredRuntime()

    fun startGateway() = startConfiguredRuntime()

    /** Recreates the configured local stack after app process death or a data-preserving update. */
    fun onHostResumed() {
        if (_state.value.busy || !app.shouldRestoreConfiguredRuntime()) return
        // A controller can still report RUNNING after the app-private child processes have been
        // reclaimed. The configured starter performs a bounded private-UDS health probe and turns
        // that stale logical state into a complete Runtime/Gateway restart.
        runOperation("RUNTIME_RESTORING") { app.restoreConfiguredRuntime() }
    }

    fun refresh() = runOperation("RUNTIME_HEALTH_CHECKING") { app.runtimeController.refreshHealth() }

    fun stop() {
        app.setRuntimeRestoreRequested(false)
        runOperation("RUNTIME_STOPPING") { app.stopRuntime() }
    }

    private fun startConfiguredRuntime() {
        app.setRuntimeRestoreRequested(true)
        runOperation("GATEWAY_STARTING") { app.restoreConfiguredRuntime() }
    }

    /**
     * Stages the checksum-pinned CLI and runs only its fixed `--version` argv inside Alpine.
     * The command output is compared in memory and is never rendered or logged.
     */
    fun prepareCodexCli() {
        if (_state.value.busy || !_state.value.sessionActive) return
        _state.update { it.copy(codexCliBootstrap = null) }
        runOperation(
            pendingStatus = "CODEX_CLI_STAGING",
            successStatus = "CODEX_CLI_READY",
        ) {
            val staged = try {
                app.stageCodexCli()
            } catch (_: CodexCliArtifactException) {
                setCodexCliBootstrap(CodexCliBootstrapOutcome.STAGING_FAILED)
                return@runOperation failedStage<Unit>(
                    RuntimeOperationException(RuntimeErrorCode.ARTIFACT_INTEGRITY_FAILED),
                )
            }
            app.runtimeController.execute(
                RuntimeCommandRequest(
                    executable = staged.guestExecutablePath,
                    arguments = listOf("--version"),
                    timeoutMillis = CODEX_CLI_VERSION_TIMEOUT_MILLIS,
                ),
            ).thenCompose { result ->
                val actualVersion = result.standardOutput
                    .toString(Charsets.UTF_8)
                    .lineSequence()
                    .firstOrNull()
                    ?.trim()
                if (result.exitCode == 0 && !result.timedOut && actualVersion == "codex-cli ${staged.version}") {
                    setCodexCliBootstrap(CodexCliBootstrapOutcome.READY)
                    CompletableFuture.completedFuture(Unit)
                } else {
                    setCodexCliBootstrap(CodexCliBootstrapOutcome.VERSION_CHECK_FAILED)
                    failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                }
            }
        }
    }

    /** Runs exactly initialize then account/read through the staged Python supervisor. */
    fun runAppServerSmoke() {
        if (_state.value.busy || !_state.value.sessionActive) return
        _state.update { it.copy(appServerSmoke = null) }
        runOperation(
            pendingStatus = "APP_SERVER_SMOKE_STARTING",
            successStatus = "APP_SERVER_SMOKE_READY",
        ) {
            val cli = try {
                app.stageCodexCli()
            } catch (_: CodexCliArtifactException) {
                setAppServerSmoke(AppServerSmokeOutcome.STAGING_FAILED)
                return@runOperation failedStage<Unit>(
                    RuntimeOperationException(RuntimeErrorCode.ARTIFACT_INTEGRITY_FAILED),
                )
            }
            val gateway = try {
                app.stageCodexGateway()
            } catch (_: CodexGatewayArtifactException) {
                setAppServerSmoke(AppServerSmokeOutcome.STAGING_FAILED)
                return@runOperation failedStage<Unit>(
                    RuntimeOperationException(RuntimeErrorCode.ARTIFACT_INTEGRITY_FAILED),
                )
            }
            app.runtimeController.execute(
                RuntimeCommandRequest(
                    executable = "/usr/bin/python3",
                    arguments = listOf(
                        "-m",
                        "codex_gateway.supervisor_probe",
                        "--codex",
                        cli.guestExecutablePath,
                        "--home",
                        CodexRuntimePaths.GUEST_HOME,
                        "--workdir",
                        "/workspace",
                    ),
                    workingDirectory = gateway.guestPackageDirectory.substringBeforeLast("/codex_gateway"),
                    timeoutMillis = APP_SERVER_SMOKE_TIMEOUT_MILLIS,
                ),
            ).thenCompose { result ->
                val marker = result.standardOutput.toString(Charsets.UTF_8).trim()
                if (result.exitCode == 0 && !result.timedOut && marker == "APP_SERVER_SMOKE_OK") {
                    setAppServerSmoke(AppServerSmokeOutcome.READY)
                    CompletableFuture.completedFuture(Unit)
                } else {
                    setAppServerSmoke(AppServerSmokeOutcome.INITIALIZE_OR_ACCOUNT_FAILED)
                    failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                }
            }
        }
    }

    /** Runs only the pinned, credential-free Grok version and ACP initialize smoke. */
    fun runGrokAcpSmoke() {
        if (_state.value.busy || !_state.value.sessionActive) return
        _state.update { it.copy(grokAcpSmoke = null) }
        runOperation(
            pendingStatus = "GROK_ACP_SMOKE_STARTING",
            successStatus = "GROK_ACP_SMOKE_READY",
        ) {
            try {
                app.stageGrokCli()
                app.stageGrokProfile()
            } catch (_: GrokCliArtifactException) {
                setGrokAcpSmoke(GrokAcpSmokeOutcome.STAGING_FAILED)
                return@runOperation failedStage<Unit>(
                    RuntimeOperationException(RuntimeErrorCode.ARTIFACT_INTEGRITY_FAILED),
                )
            }
            val gateway = try {
                app.stageGrokGateway()
            } catch (_: CodexGatewayArtifactException) {
                setGrokAcpSmoke(GrokAcpSmokeOutcome.STAGING_FAILED)
                return@runOperation failedStage<Unit>(
                    RuntimeOperationException(RuntimeErrorCode.ARTIFACT_INTEGRITY_FAILED),
                )
            }
            app.runtimeController.execute(
                RuntimeCommandRequest(
                    executable = "/usr/bin/python3",
                    arguments = listOf("-m", "codex_gateway.grok_acp.smoke"),
                    workingDirectory = gateway.guestPackageDirectory.substringBeforeLast("/codex_gateway"),
                    environment = mapOf(
                        "PYTHONPATH" to gateway.guestPackageDirectory.substringBeforeLast("/codex_gateway"),
                        "PYTHONDONTWRITEBYTECODE" to "1",
                    ),
                    timeoutMillis = GROK_ACP_SMOKE_TIMEOUT_MILLIS,
                ),
            ).thenCompose { result ->
                val outcome = GrokAcpSmokeParser.parse(
                    exitCode = result.exitCode,
                    timedOut = result.timedOut,
                    standardOutput = result.standardOutput,
                    standardError = result.standardError,
                )
                setGrokAcpSmoke(outcome)
                if (outcome == GrokAcpSmokeOutcome.READY) {
                    CompletableFuture.completedFuture(Unit)
                } else {
                    failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                }
            }
        }
    }

    /**
     * User-initiated, fixed Gateway preparation. Only hash-locked `.apk` files staged from this
     * APK are accepted, and `apk add --no-network` prevents repository access. Arbitrary package,
     * URL, repository, or terminal input is never accepted. Guest output is not rendered or logged.
     */
    fun runSmoke() {
        if (_state.value.busy) return
        _state.update { it.copy(gatewayPythonBootstrap = null) }
        runOperation("RUNTIME_SMOKE_RUNNING") {
            app.prepareGatewayPython().thenCompose { outcome ->
                setGatewayPythonBootstrap(outcome)
                if (
                    outcome == GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE ||
                    outcome == GatewayPythonBootstrapOutcome.INSTALLED
                ) {
                    CompletableFuture.completedFuture(Unit)
                } else {
                    failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                }
            }
        }
    }

    private fun setGatewayPythonBootstrap(outcome: GatewayPythonBootstrapOutcome) {
        _state.update { it.copy(gatewayPythonBootstrap = outcome) }
    }

    private fun setCodexCliBootstrap(outcome: CodexCliBootstrapOutcome) {
        _state.update { it.copy(codexCliBootstrap = outcome) }
    }

    private fun setAppServerSmoke(outcome: AppServerSmokeOutcome) {
        _state.update { it.copy(appServerSmoke = outcome) }
    }

    private fun setGrokAcpSmoke(outcome: GrokAcpSmokeOutcome) {
        _state.update { it.copy(grokAcpSmoke = outcome) }
    }

    private fun runOperation(
        pendingStatus: String,
        successStatus: String = "RUNTIME_OPERATION_COMPLETED",
        operation: () -> CompletionStage<*>,
    ) {
        if (_state.value.busy) return
        _state.update { it.copy(busy = true, status = pendingStatus, errorCode = null) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { operation().toCompletableFuture().get() }
            }
            _state.update { current ->
                result.fold(
                    onSuccess = {
                        app.runtimeController.currentState().toUiState(
                            busy = false,
                            status = if (pendingStatus == "RUNTIME_SMOKE_RUNNING") {
                                "RUNTIME_SMOKE_PASSED"
                            } else {
                                successStatus
                            },
                            gatewayPythonBootstrap = current.gatewayPythonBootstrap,
                            codexCliBootstrap = current.codexCliBootstrap,
                            appServerSmoke = current.appServerSmoke,
                            grokAcpSmoke = current.grokAcpSmoke,
                            gatewayLifecycle = current.gatewayLifecycle,
                            gatewayErrorCode = current.gatewayErrorCode,
                        )
                    },
                    onFailure = { error ->
                        app.runtimeController.currentState().toUiState(
                            busy = false,
                            status = "RUNTIME_OPERATION_FAILED",
                            errorCode = error.findRuntimeErrorCode(),
                            gatewayPythonBootstrap = current.gatewayPythonBootstrap,
                            codexCliBootstrap = current.codexCliBootstrap,
                            appServerSmoke = current.appServerSmoke,
                            grokAcpSmoke = current.grokAcpSmoke,
                            gatewayLifecycle = current.gatewayLifecycle,
                            gatewayErrorCode = current.gatewayErrorCode,
                        )
                    },
                )
            }
        }
    }

    override fun onCleared() {
        stateSubscription.close()
        gatewayStateSubscription.close()
        super.onCleared()
    }

    private fun RuntimeHostState.toUiState(
        busy: Boolean = false,
        status: String = runtimeState.lifecycle.name,
        errorCode: RuntimeErrorCode? = lastErrorCode ?: runtimeState.detailCode,
        gatewayPythonBootstrap: GatewayPythonBootstrapOutcome? = null,
        codexCliBootstrap: CodexCliBootstrapOutcome? = null,
        appServerSmoke: AppServerSmokeOutcome? = null,
        grokAcpSmoke: GrokAcpSmokeOutcome? = null,
        gatewayLifecycle: CodexRuntimeLifecycle = CodexRuntimeLifecycle.STOPPED,
        gatewayErrorCode: CodexRuntimeErrorCode? = null,
    ) = RuntimeUiState(
        lifecycle = runtimeState.lifecycle,
        operation = operation,
        sessionActive = sessionActive,
        busy = busy,
        status = status,
        errorCode = errorCode,
        gatewayPythonBootstrap = gatewayPythonBootstrap,
        codexCliBootstrap = codexCliBootstrap,
        appServerSmoke = appServerSmoke,
        grokAcpSmoke = grokAcpSmoke,
        gatewayLifecycle = gatewayLifecycle,
        gatewayErrorCode = gatewayErrorCode,
    )

    private fun Throwable.findRuntimeErrorCode(): RuntimeErrorCode = generateSequence(this) { it.cause }
        .filterIsInstance<RuntimeOperationException>()
        .firstOrNull()
        ?.errorCode
        ?: RuntimeErrorCode.INTERNAL_ERROR

    private fun <T> failedStage(error: Throwable): CompletionStage<T> =
        CompletableFuture<T>().also { it.completeExceptionally(error) }

    private companion object {
        const val CODEX_CLI_VERSION_TIMEOUT_MILLIS = 30_000L
        const val APP_SERVER_SMOKE_TIMEOUT_MILLIS = 60_000L
        const val GROK_ACP_SMOKE_TIMEOUT_MILLIS = 60_000L
    }
}
