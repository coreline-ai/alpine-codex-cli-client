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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage

data class RuntimeUiState(
    val lifecycle: RuntimeLifecycleState,
    val operation: RuntimeHostOperation,
    val sessionActive: Boolean,
    val busy: Boolean = false,
    val status: String = "RUNTIME_NOT_READY",
    val errorCode: RuntimeErrorCode? = null,
    val gatewayPythonBootstrap: GatewayPythonBootstrapOutcome? = null,
)

/** Closed state for the one fixed package bootstrap; guest output is never retained in UI state. */
enum class GatewayPythonBootstrapOutcome {
    ALREADY_AVAILABLE,
    PREFLIGHT_FAILED,
    INSTALL_FAILED,
    INSTALLED,
    VERIFICATION_FAILED,
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
                )
            }
        }
    }

    fun install() = runOperation("RUNTIME_INSTALLING") { app.runtimeController.install() }

    fun start() = runOperation("RUNTIME_STARTING") { app.startRuntime() }

    fun refresh() = runOperation("RUNTIME_HEALTH_CHECKING") { app.runtimeController.refreshHealth() }

    fun stop() = runOperation("RUNTIME_STOPPING") { app.runtimeController.stop() }

    /**
     * User-initiated, fixed gateway bootstrap and smoke checks. Only exact `apk add` argv for
     * `python3` may run; arbitrary package or terminal input is never accepted. A fresh Alpine
     * rootfs has no package index, so the preflight deliberately uses `--no-cache` too.
     * Guest output is not rendered or logged.
     */
    fun runSmoke() {
        if (_state.value.busy) return
        _state.update { it.copy(gatewayPythonBootstrap = null) }
        runOperation("RUNTIME_SMOKE_RUNNING") {
            runBoundedSmoke().thenCompose { pythonAvailable ->
                if (pythonAvailable) {
                    setGatewayPythonBootstrap(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
                    CompletableFuture.completedFuture(Unit)
                } else {
                    bootstrapGatewayPython()
                }
            }
        }
    }

    /** Executes fixed argv smoke checks and returns only a closed availability result. */
    private fun runBoundedSmoke(): CompletionStage<Boolean> =
        app.runtimeController.execute(
            RuntimeCommandRequest(
                executable = "/bin/uname",
                arguments = listOf("-m"),
                timeoutMillis = SMOKE_TIMEOUT_MILLIS,
            ),
        ).thenCompose { uname ->
            if (uname.exitCode != 0 || uname.timedOut) {
                throw RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED)
            }
            app.runtimeController.execute(
                RuntimeCommandRequest(
                    executable = "/usr/bin/python3",
                    arguments = listOf("--version"),
                    timeoutMillis = SMOKE_TIMEOUT_MILLIS,
                ),
            )
        }.thenApply { python ->
            python.exitCode == 0 && !python.timedOut
        }

    private fun bootstrapGatewayPython(): CompletionStage<Unit> =
        app.runtimeController.execute(
            RuntimeCommandRequest(
                executable = "/sbin/apk",
                arguments = listOf("add", "--no-cache", "--simulate", "--no-progress", GATEWAY_PYTHON_PACKAGE),
                timeoutMillis = PYTHON_BOOTSTRAP_TIMEOUT_MILLIS,
            ),
        ).thenCompose { preflight ->
            if (preflight.exitCode != 0 || preflight.timedOut) {
                setGatewayPythonBootstrap(GatewayPythonBootstrapOutcome.PREFLIGHT_FAILED)
                failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
            } else {
                app.runtimeController.execute(
                    RuntimeCommandRequest(
                        executable = "/sbin/apk",
                        arguments = listOf("add", "--no-cache", "--no-progress", GATEWAY_PYTHON_PACKAGE),
                        timeoutMillis = PYTHON_BOOTSTRAP_TIMEOUT_MILLIS,
                    ),
                ).thenCompose { install ->
                    if (install.exitCode != 0 || install.timedOut) {
                        setGatewayPythonBootstrap(GatewayPythonBootstrapOutcome.INSTALL_FAILED)
                        failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                    } else {
                        runBoundedSmoke().thenCompose { pythonAvailable ->
                            if (pythonAvailable) {
                                setGatewayPythonBootstrap(GatewayPythonBootstrapOutcome.INSTALLED)
                                CompletableFuture.completedFuture(Unit)
                            } else {
                                setGatewayPythonBootstrap(GatewayPythonBootstrapOutcome.VERIFICATION_FAILED)
                                failedStage(RuntimeOperationException(RuntimeErrorCode.COMMAND_FAILED))
                            }
                        }
                    }
                }
            }
        }

    private fun setGatewayPythonBootstrap(outcome: GatewayPythonBootstrapOutcome) {
        _state.update { it.copy(gatewayPythonBootstrap = outcome) }
    }

    private fun runOperation(
        pendingStatus: String,
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
                                "RUNTIME_OPERATION_COMPLETED"
                            },
                            gatewayPythonBootstrap = current.gatewayPythonBootstrap,
                        )
                    },
                    onFailure = { error ->
                        app.runtimeController.currentState().toUiState(
                            busy = false,
                            status = "RUNTIME_OPERATION_FAILED",
                            errorCode = error.findRuntimeErrorCode(),
                            gatewayPythonBootstrap = current.gatewayPythonBootstrap,
                        )
                    },
                )
            }
        }
    }

    override fun onCleared() {
        stateSubscription.close()
        super.onCleared()
    }

    private fun RuntimeHostState.toUiState(
        busy: Boolean = false,
        status: String = runtimeState.lifecycle.name,
        errorCode: RuntimeErrorCode? = lastErrorCode ?: runtimeState.detailCode,
        gatewayPythonBootstrap: GatewayPythonBootstrapOutcome? = null,
    ) = RuntimeUiState(
        lifecycle = runtimeState.lifecycle,
        operation = operation,
        sessionActive = sessionActive,
        busy = busy,
        status = status,
        errorCode = errorCode,
        gatewayPythonBootstrap = gatewayPythonBootstrap,
    )

    private fun Throwable.findRuntimeErrorCode(): RuntimeErrorCode = generateSequence(this) { it.cause }
        .filterIsInstance<RuntimeOperationException>()
        .firstOrNull()
        ?.errorCode
        ?: RuntimeErrorCode.INTERNAL_ERROR

    private fun <T> failedStage(error: Throwable): CompletionStage<T> =
        CompletableFuture<T>().also { it.completeExceptionally(error) }

    private companion object {
        const val SMOKE_TIMEOUT_MILLIS = 15_000L
        const val PYTHON_BOOTSTRAP_TIMEOUT_MILLIS = 5 * 60_000L
        const val GATEWAY_PYTHON_PACKAGE = "python3"
    }
}
