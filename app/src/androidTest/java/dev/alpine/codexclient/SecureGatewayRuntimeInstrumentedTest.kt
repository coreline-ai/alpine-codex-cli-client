package dev.alpine.codexclient

import androidx.test.platform.app.InstrumentationRegistry
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.runtime.api.RuntimeInstallRequest
import dev.alpine.runtime.api.RuntimeLifecycleState
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Credential-free device gate for the production authenticated Gateway entrypoint. */
class SecureGatewayRuntimeInstrumentedTest {
    @Test
    fun signedGatewayStartsRejectsUnsignedAndClearsSessionOnStop() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assertEquals("dev.alpine.codexclient.labdebug", context.packageName)
        val app = context.applicationContext as AlpineCodexApplication
        val rawCapability = File(app.gatewaySecurityDirectory, "gateway-capability.v1")
        val wrappedCapability = File(context.filesDir, "gateway-security/gateway-session.v1")

        if (app.runtimeManager.currentState().lifecycle == RuntimeLifecycleState.NOT_INSTALLED) {
            app.runtimeManager.install(RuntimeInstallRequest())
                .toCompletableFuture()
                .get(120, TimeUnit.SECONDS)
        }
        assertEquals(RuntimeLifecycleState.READY, app.runtimeManager.currentState().lifecycle)

        try {
            app.startAlpineRuntime().toCompletableFuture().get(30, TimeUnit.SECONDS)
            val python = app.prepareGatewayPython().toCompletableFuture().get(5, TimeUnit.MINUTES)
            assertTrue(
                python == GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE ||
                    python == GatewayPythonBootstrapOutcome.INSTALLED,
            )
            val started = app.startRuntime().toCompletableFuture().get(60, TimeUnit.SECONDS)
            assertEquals(CodexRuntimeLifecycle.RUNNING, started.lifecycle)
            val health = app.agentGatewayClient.health()
            assertEquals("ready", health.runtime)
            assertEquals("ready", health.gateway)
            assertEquals(AgentId.CODEX, health.selectedAgent)
            assertTrue(health.backendReady)

            val agents = app.agentGatewayClient.agents()
            assertEquals(setOf(AgentId.CODEX, AgentId.GROK), agents.map { it.agentId }.toSet())
            assertEquals(AgentId.CODEX, agents.single { it.selected }.agentId)
            assertTrue(agents.single { it.agentId == AgentId.CODEX }.ready)
            assertFalse(agents.single { it.agentId == AgentId.GROK }.ready)

            val unsigned = URL("http://127.0.0.1:8787/v1/agents")
                .openConnection() as HttpURLConnection
            try {
                unsigned.requestMethod = "GET"
                unsigned.connectTimeout = 2_000
                unsigned.readTimeout = 2_000
                assertEquals(401, unsigned.responseCode)
            } finally {
                unsigned.disconnect()
            }

            assertFalse("raw capability must be consumed once before ready", rawCapability.exists())
            assertTrue("wrapped capability must exist only for the active Runtime", wrappedCapability.isFile)
        } finally {
            app.stopRuntime().toCompletableFuture().get(30, TimeUnit.SECONDS)
        }

        assertEquals(CodexRuntimeLifecycle.STOPPED, app.codexRuntimeController.currentState().lifecycle)
        assertEquals(RuntimeLifecycleState.READY, app.runtimeManager.currentState().lifecycle)
        assertFalse(rawCapability.exists())
        assertFalse(wrappedCapability.exists())
    }
}
