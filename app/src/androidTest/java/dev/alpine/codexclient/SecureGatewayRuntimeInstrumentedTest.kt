package dev.alpine.codexclient

import android.net.LocalSocket
import android.net.LocalSocketAddress
import android.os.Process
import android.system.Os
import android.system.OsConstants
import androidx.test.platform.app.InstrumentationRegistry
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.runtime.api.RuntimeInstallRequest
import dev.alpine.runtime.api.RuntimeLifecycleState
import java.io.File
import java.io.IOException
import java.net.URI
import java.util.concurrent.TimeUnit
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Credential-free device gate for the production authenticated Gateway entrypoint. */
class SecureGatewayRuntimeInstrumentedTest {
    @Test
    fun privateGatewayStartsRejectsUnsignedAndClearsSessionOnStop() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        val context = instrumentation.targetContext
        assertEquals("dev.alpine.codexclient.labdebug", context.packageName)
        val app = context.applicationContext as AlpineCodexApplication
        val rawCapability = File(app.gatewaySecurityDirectory, "gateway-capability.v1")
        val wrappedCapability = File(app.gatewayWrappedDirectory, "gateway-session.v1")
        val socketFile = File(app.gatewayTransportDirectory, CodexRuntimePaths.GATEWAY_SOCKET_FILE)

        assertTrue(
            "sensitive migration must commit: ${app.sensitiveStateMigrationFailureCode}",
            app.sensitiveStateNoBackupCommitted,
        )
        listOf(
            app.codexHomeDirectory,
            app.grokHomeDirectory,
            app.gatewaySecurityDirectory,
            app.gatewayWrappedDirectory,
            app.conversationStateDirectory,
        ).forEach { directory ->
            assertEquals(context.noBackupFilesDir.canonicalFile, directory.canonicalFile.parentFile)
            assertEquals(448, Os.stat(directory.absolutePath).st_mode and 511)
        }
        assertFalse(File(context.filesDir, "gateway-security").exists())
        assertFalse(File(context.filesDir, "codex-chat-state.v1").exists())
        assertFalse(File(context.filesDir, "codex-chat-state.v2").exists())

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
            assertEquals(448, Os.stat(app.gatewayTransportDirectory.absolutePath).st_mode and 511)
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

            assertEquals(OsConstants.S_IFSOCK, Os.lstat(socketFile.absolutePath).st_mode and OsConstants.S_IFMT)
            assertEquals(384, Os.stat(socketFile.absolutePath).st_mode and 511)
            val unsigned = LocalSocket()
            unsigned.connect(LocalSocketAddress(socketFile.absolutePath, LocalSocketAddress.Namespace.FILESYSTEM))
            assertEquals(Process.myUid(), unsigned.peerCredentials.uid)
            unsigned.soTimeout = 2_000
            unsigned.outputStream.apply {
                write(
                    "GET /v1/agents HTTP/1.1\r\nHost: 127.0.0.1:8787\r\nConnection: close\r\n\r\n"
                        .toByteArray(Charsets.US_ASCII),
                )
                flush()
            }
            val status = unsigned.inputStream.bufferedReader(Charsets.US_ASCII).use { it.readLine() }
            assertTrue(status.startsWith("HTTP/1.0 401"))
            unsigned.close()

            val tcpFailure = runCatching {
                URI("http://127.0.0.1:8787/healthz").toURL().openConnection().apply {
                    connectTimeout = 1_000
                    readTimeout = 1_000
                }.getInputStream().close()
            }.exceptionOrNull()
            assertTrue(tcpFailure is IOException)

            assertFalse("raw capability must be consumed once before ready", rawCapability.exists())
            assertTrue("wrapped capability must exist only for the active Runtime", wrappedCapability.isFile)
            assertTrue(
                File(app.codexWorkspaceDirectory, CodexRuntimePaths.HOME_DIRECTORY)
                    .listFiles()
                    .isNullOrEmpty(),
            )
            assertTrue(
                File(app.grokWorkspaceDirectory, GrokRuntimePaths.HOME_DIRECTORY)
                    .listFiles()
                    .isNullOrEmpty(),
            )
        } finally {
            app.stopRuntime().toCompletableFuture().get(30, TimeUnit.SECONDS)
        }

        assertEquals(CodexRuntimeLifecycle.STOPPED, app.codexRuntimeController.currentState().lifecycle)
        assertEquals(RuntimeLifecycleState.READY, app.runtimeManager.currentState().lifecycle)
        assertFalse(rawCapability.exists())
        assertFalse(wrappedCapability.exists())
        assertFalse(socketFile.exists())
    }
}
