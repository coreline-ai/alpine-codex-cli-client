package dev.alpine.codexclient

import dev.alpine.runtime.api.RuntimeCommandRequest
import dev.alpine.runtime.host.RuntimeHostController
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage

/** Closed outcomes for the one user-triggered, allowlisted Gateway dependency bootstrap. */
enum class GatewayPythonBootstrapOutcome {
    ALREADY_AVAILABLE,
    PREFLIGHT_FAILED,
    INSTALL_FAILED,
    INSTALLED,
    VERIFICATION_FAILED,
}

/** Installs only the exact `python3` package after a fixed simulate preflight. */
internal class GatewayPythonBootstrapper(
    private val runtimeController: RuntimeHostController,
) {
    fun prepare(): CompletionStage<GatewayPythonBootstrapOutcome> =
        pythonAvailable().thenCompose { available ->
            if (available) {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
            } else {
                bootstrap()
            }
        }

    private fun pythonAvailable(): CompletionStage<Boolean> =
        runtimeController.execute(
            RuntimeCommandRequest(
                executable = "/bin/uname",
                arguments = listOf("-m"),
                timeoutMillis = SMOKE_TIMEOUT_MILLIS,
            ),
        ).thenCompose { uname ->
            if (uname.exitCode != 0 || uname.timedOut) {
                throw IllegalStateException("gateway_python_smoke_failed")
            }
            runtimeController.execute(
                RuntimeCommandRequest(
                    executable = "/usr/bin/python3",
                    arguments = listOf("--version"),
                    timeoutMillis = SMOKE_TIMEOUT_MILLIS,
                ),
            )
        }.thenApply { python -> python.exitCode == 0 && !python.timedOut }

    private fun bootstrap(): CompletionStage<GatewayPythonBootstrapOutcome> =
        runtimeController.execute(
            RuntimeCommandRequest(
                executable = "/sbin/apk",
                arguments = listOf("add", "--no-cache", "--simulate", "--no-progress", PACKAGE),
                timeoutMillis = BOOTSTRAP_TIMEOUT_MILLIS,
            ),
        ).thenCompose { preflight ->
            if (preflight.exitCode != 0 || preflight.timedOut) {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.PREFLIGHT_FAILED)
            } else {
                runtimeController.execute(
                    RuntimeCommandRequest(
                        executable = "/sbin/apk",
                        arguments = listOf("add", "--no-cache", "--no-progress", PACKAGE),
                        timeoutMillis = BOOTSTRAP_TIMEOUT_MILLIS,
                    ),
                ).thenCompose { install ->
                    if (install.exitCode != 0 || install.timedOut) {
                        CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.INSTALL_FAILED)
                    } else {
                        pythonAvailable().thenApply { available ->
                            if (available) GatewayPythonBootstrapOutcome.INSTALLED
                            else GatewayPythonBootstrapOutcome.VERIFICATION_FAILED
                        }
                    }
                }
            }
        }

    private companion object {
        const val PACKAGE = "python3"
        const val SMOKE_TIMEOUT_MILLIS = 15_000L
        const val BOOTSTRAP_TIMEOUT_MILLIS = 5 * 60_000L
    }
}
