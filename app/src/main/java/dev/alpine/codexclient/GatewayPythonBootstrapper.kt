package dev.alpine.codexclient

import dev.alpine.codexclient.gatewaypack.StagedCodexGateway
import dev.alpine.pythonpack.bundled.PythonPackagePackException
import dev.alpine.pythonpack.bundled.PythonPackagePackFailure
import dev.alpine.pythonpack.bundled.StagedPythonPackagePack
import dev.alpine.runtime.api.RuntimeCommandRequest
import dev.alpine.runtime.api.RuntimeCommandResult
import dev.alpine.runtime.host.RuntimeHostController
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage

/** Closed outcomes for the user-triggered, APK-contained Gateway dependency preparation. */
enum class GatewayPythonBootstrapOutcome {
    ALREADY_AVAILABLE,
    BUNDLE_UNAVAILABLE,
    BUNDLE_INVALID,
    PREFLIGHT_FAILED,
    INSTALL_FAILED,
    INSTALLED,
    VERIFICATION_FAILED,
}

/** Installs only hash-locked local `.apk` files staged from this application's APK assets. */
internal class GatewayPythonBootstrapper private constructor(
    private val execute: (RuntimeCommandRequest) -> CompletionStage<RuntimeCommandResult>,
    private val stagePackagePack: () -> StagedPythonPackagePack,
    private val stageGateway: () -> StagedCodexGateway,
) {
    constructor(
        runtimeController: RuntimeHostController,
        stagePackagePack: () -> StagedPythonPackagePack,
        stageGateway: () -> StagedCodexGateway,
    ) : this(runtimeController::execute, stagePackagePack, stageGateway)

    internal constructor(
        commandExecutor: (RuntimeCommandRequest) -> CompletionStage<RuntimeCommandResult>,
        packageStager: () -> StagedPythonPackagePack,
        gatewayStager: () -> StagedCodexGateway,
        @Suppress("UNUSED_PARAMETER") testing: Unit = Unit,
    ) : this(commandExecutor, packageStager, gatewayStager)

    fun prepare(): CompletionStage<GatewayPythonBootstrapOutcome> =
        pythonAvailable().thenCompose { available ->
            if (available) {
                gatewayImportAvailable(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
            } else {
                installBundledPack()
            }
        }

    private fun pythonAvailable(): CompletionStage<Boolean> =
        execute(
            RuntimeCommandRequest(
                executable = "/bin/uname",
                arguments = listOf("-m"),
                timeoutMillis = SMOKE_TIMEOUT_MILLIS,
            ),
        ).thenCompose { uname ->
            if (uname.exitCode != 0 || uname.timedOut) {
                throw IllegalStateException("gateway_python_smoke_failed")
            }
            execute(
                RuntimeCommandRequest(
                    executable = "/usr/bin/python3",
                    arguments = listOf("--version"),
                    timeoutMillis = SMOKE_TIMEOUT_MILLIS,
                ),
            )
        }.thenApply { python -> python.exitCode == 0 && !python.timedOut }

    private fun installBundledPack(): CompletionStage<GatewayPythonBootstrapOutcome> {
        val pack = try {
            stagePackagePack()
        } catch (error: PythonPackagePackException) {
            return CompletableFuture.completedFuture(
                if (error.failure == PythonPackagePackFailure.UNAVAILABLE) {
                    GatewayPythonBootstrapOutcome.BUNDLE_UNAVAILABLE
                } else {
                    GatewayPythonBootstrapOutcome.BUNDLE_INVALID
                },
            )
        } catch (_: Exception) {
            return CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.BUNDLE_INVALID)
        }
        if (!isSafePack(pack)) {
            return CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.BUNDLE_INVALID)
        }
        return execute(apkRequest(pack.guestPackagePaths, simulate = true)).thenCompose { preflight ->
            if (preflight.exitCode != 0 || preflight.timedOut) {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.PREFLIGHT_FAILED)
            } else {
                execute(apkRequest(pack.guestPackagePaths, simulate = false)).thenCompose { install ->
                    if (install.exitCode != 0 || install.timedOut) {
                        CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.INSTALL_FAILED)
                    } else {
                        verifyInstalledGateway()
                    }
                }
            }
        }
    }

    private fun verifyInstalledGateway(): CompletionStage<GatewayPythonBootstrapOutcome> =
        pythonAvailable().thenCompose { pythonReady ->
            if (!pythonReady) {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.VERIFICATION_FAILED)
            } else {
                gatewayImportAvailable(GatewayPythonBootstrapOutcome.INSTALLED)
            }
        }

    private fun gatewayImportAvailable(
        successOutcome: GatewayPythonBootstrapOutcome,
    ): CompletionStage<GatewayPythonBootstrapOutcome> {
        val gateway = try {
            stageGateway()
        } catch (_: Exception) {
            return CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.VERIFICATION_FAILED)
        }
        val pythonPath = gateway.guestPackageDirectory.substringBeforeLast("/codex_gateway")
        if (!SAFE_GATEWAY_PARENT.matches(pythonPath)) {
            return CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.VERIFICATION_FAILED)
        }
        return execute(
            RuntimeCommandRequest(
                executable = "/usr/bin/python3",
                arguments = listOf("-c", GATEWAY_IMPORT_SMOKE),
                workingDirectory = pythonPath,
                environment = mapOf(
                    "PYTHONPATH" to pythonPath,
                    "PYTHONDONTWRITEBYTECODE" to "1",
                ),
                timeoutMillis = SMOKE_TIMEOUT_MILLIS,
            ),
        ).thenApply { result ->
            if (result.exitCode == 0 && !result.timedOut) {
                successOutcome
            } else {
                GatewayPythonBootstrapOutcome.VERIFICATION_FAILED
            }
        }
    }

    private fun apkRequest(paths: List<String>, simulate: Boolean): RuntimeCommandRequest {
        val options = buildList {
            add("add")
            add("--no-network")
            add("--no-cache")
            if (simulate) add("--simulate")
            add("--no-progress")
            addAll(paths)
        }
        return RuntimeCommandRequest(
            executable = "/sbin/apk",
            arguments = options,
            timeoutMillis = INSTALL_TIMEOUT_MILLIS,
        )
    }

    private fun isSafePack(pack: StagedPythonPackagePack): Boolean =
        SAFE_PACK_ID.matches(pack.packId) &&
            pack.guestPackagePaths.size in 1..MAX_PACKAGES &&
            pack.guestPackagePaths.distinct().size == pack.guestPackagePaths.size &&
            pack.guestPackagePaths.all { path ->
                SAFE_GUEST_PACKAGE.matches(path) &&
                    path.startsWith("${CodexRuntimePaths.GUEST_STAGING}/python-pack/${pack.packId}/packages/")
            }

    private companion object {
        const val SMOKE_TIMEOUT_MILLIS = 15_000L
        const val INSTALL_TIMEOUT_MILLIS = 5 * 60_000L
        const val MAX_PACKAGES = 128
        const val GATEWAY_IMPORT_SMOKE = "import codex_gateway"
        val SAFE_PACK_ID = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        val SAFE_GUEST_PACKAGE = Regex(
            "/workspace/\\.alpine-codex/staging/python-pack/" +
                "[A-Za-z0-9][A-Za-z0-9._-]{0,127}/packages/[A-Za-z0-9][A-Za-z0-9._+~-]*\\.apk",
        )
        val SAFE_GATEWAY_PARENT = Regex("/workspace/\\.alpine-codex/gateway")
    }
}
