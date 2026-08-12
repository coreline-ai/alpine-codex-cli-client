package dev.alpine.codexclient

import androidx.test.platform.app.InstrumentationRegistry
import dev.alpine.runtime.api.RuntimeCommandRequest
import dev.alpine.runtime.api.RuntimeInstallRequest
import dev.alpine.runtime.api.RuntimeLifecycleState
import dev.alpine.runtime.api.RuntimeStartRequest
import dev.alpine.runtime.api.RuntimeStopReason
import java.nio.charset.StandardCharsets
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Credential-free official Grok CLI version + ACP initialize smoke on app-private Alpine. */
class GrokRuntimeSmokeInstrumentedTest {
    @Test
    fun pinnedGrokInitializesWithoutCredentialAndLeavesNoRuntimeChild() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assertEquals("dev.alpine.codexclient.labdebug", context.packageName)
        val app = context.applicationContext as AlpineCodexApplication
        assertTrue(
            "Codex gateway must be idle before the Grok-only smoke",
            app.codexRuntimeController.currentState().lifecycle.name != "RUNNING",
        )

        app.stageGrokCli()
        app.stageGrokProfile()
        app.stageGrokGateway()
        if (app.runtimeManager.currentState().lifecycle == RuntimeLifecycleState.NOT_INSTALLED) {
            app.runtimeManager.install(RuntimeInstallRequest()).toCompletableFuture().get(120, TimeUnit.SECONDS)
        }
        assertEquals(RuntimeLifecycleState.READY, app.runtimeManager.currentState().lifecycle)

        val session = app.runtimeManager.start(
            RuntimeStartRequest(environment = mapOf("HOME" to GrokRuntimePaths.GUEST_HOME)),
        ).toCompletableFuture().get(30, TimeUnit.SECONDS)
        try {
            val result = session.execute(
                RuntimeCommandRequest(
                    executable = "/usr/bin/python3",
                    arguments = listOf("-m", "codex_gateway.grok_acp.smoke"),
                    workingDirectory = GrokRuntimePaths.GUEST_GATEWAY,
                    environment = mapOf(
                        "PYTHONPATH" to GrokRuntimePaths.GUEST_GATEWAY,
                        "PYTHONDONTWRITEBYTECODE" to "1",
                    ),
                    timeoutMillis = 50_000,
                ),
            ).toCompletableFuture().get(60, TimeUnit.SECONDS)
            val marker = result.standardOutput.toString(StandardCharsets.UTF_8).trim().let { raw ->
                raw.takeIf { it.matches(Regex("GROK_SMOKE_[A-Z_]+")) } ?: "GROK_SMOKE_OUTPUT_INVALID"
            }
            assertTrue("Grok smoke command timed out", !result.timedOut)
            assertEquals("Grok smoke command failed: $marker", 0, result.exitCode)
            assertEquals(
                "GROK_SMOKE_READY",
                marker,
            )
            assertTrue("Grok smoke emitted wrapper stderr", result.standardError.isEmpty())
            assertTrue(
                "Grok smoke left a tracked Runtime process",
                session.listProcesses().toCompletableFuture().get(5, TimeUnit.SECONDS).isEmpty(),
            )
        } finally {
            app.runtimeManager.stop(RuntimeStopReason.USER_REQUEST)
                .toCompletableFuture()
                .get(15, TimeUnit.SECONDS)
        }
        assertEquals(RuntimeLifecycleState.READY, app.runtimeManager.currentState().lifecycle)
    }
}
