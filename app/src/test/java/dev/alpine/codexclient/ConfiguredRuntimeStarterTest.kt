package dev.alpine.codexclient

import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.runtime.api.RuntimeLifecycleState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.CompletableFuture

class ConfiguredRuntimeStarterTest {
    @Test
    fun `all stable states migrate to automatic restore`() {
        assertTrue(defaultRestoreRequested(RuntimeLifecycleState.NOT_INSTALLED))
        assertFalse(defaultRestoreRequested(RuntimeLifecycleState.INSTALLING))
        assertTrue(defaultRestoreRequested(RuntimeLifecycleState.READY))
        assertTrue(defaultRestoreRequested(RuntimeLifecycleState.RUNNING))
        assertTrue(defaultRestoreRequested(RuntimeLifecycleState.FAILED))
    }

    @Test
    fun `recovery runs Alpine then Python then Gateway`() {
        val calls = mutableListOf<String>()
        val starter = ConfiguredRuntimeStarter(
            gatewayLifecycle = { CodexRuntimeLifecycle.STOPPED },
            gatewayHealthy = { false },
            stopStaleGateway = {
                calls += "reset"
                CompletableFuture.completedFuture(Unit)
            },
            startAlpine = {
                calls += "alpine"
                CompletableFuture.completedFuture(Unit)
            },
            preparePython = {
                calls += "python"
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
            },
            startGateway = {
                calls += "gateway"
                CompletableFuture.completedFuture(Unit)
            },
        )

        starter.start().toCompletableFuture().get()

        assertEquals(listOf("reset", "alpine", "python", "gateway"), calls)
    }

    @Test
    fun `concurrent recovery requests share one operation`() {
        val alpine = CompletableFuture<Unit>()
        var alpineStarts = 0
        val starter = ConfiguredRuntimeStarter(
            gatewayLifecycle = { CodexRuntimeLifecycle.STOPPED },
            gatewayHealthy = { false },
            stopStaleGateway = { CompletableFuture.completedFuture(Unit) },
            startAlpine = {
                alpineStarts += 1
                alpine
            },
            preparePython = {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
            },
            startGateway = { CompletableFuture.completedFuture(Unit) },
        )

        val first = starter.start().toCompletableFuture()
        val second = starter.start().toCompletableFuture()

        assertSame(first, second)
        assertEquals(1, alpineStarts)
        alpine.complete(Unit)
        first.get()
    }

    @Test
    fun `failed Python preparation never starts Gateway`() {
        var gatewayStarts = 0
        val starter = ConfiguredRuntimeStarter(
            gatewayLifecycle = { CodexRuntimeLifecycle.STOPPED },
            gatewayHealthy = { false },
            stopStaleGateway = { CompletableFuture.completedFuture(Unit) },
            startAlpine = { CompletableFuture.completedFuture(Unit) },
            preparePython = {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.VERIFICATION_FAILED)
            },
            startGateway = {
                gatewayStarts += 1
                CompletableFuture.completedFuture(Unit)
            },
        )

        val failed = runCatching { starter.start().toCompletableFuture().get() }.isFailure

        assertTrue(failed)
        assertEquals(0, gatewayStarts)
    }

    @Test
    fun `running Gateway is an immediate no-op`() {
        var started = false
        val starter = ConfiguredRuntimeStarter(
            gatewayLifecycle = { CodexRuntimeLifecycle.RUNNING },
            gatewayHealthy = { true },
            stopStaleGateway = { CompletableFuture.completedFuture(Unit) },
            startAlpine = {
                started = true
                CompletableFuture.completedFuture(Unit)
            },
            preparePython = {
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
            },
            startGateway = { CompletableFuture.completedFuture(Unit) },
        )

        starter.start().toCompletableFuture().get()

        assertFalse(started)
    }

    @Test
    fun `stale running Gateway is stopped and completely restored`() {
        val calls = mutableListOf<String>()
        val starter = ConfiguredRuntimeStarter(
            gatewayLifecycle = { CodexRuntimeLifecycle.RUNNING },
            gatewayHealthy = {
                calls += "health"
                false
            },
            stopStaleGateway = {
                calls += "stop"
                CompletableFuture.completedFuture(Unit)
            },
            startAlpine = {
                calls += "alpine"
                CompletableFuture.completedFuture(Unit)
            },
            preparePython = {
                calls += "python"
                CompletableFuture.completedFuture(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE)
            },
            startGateway = {
                calls += "gateway"
                CompletableFuture.completedFuture(Unit)
            },
        )

        starter.start().toCompletableFuture().get()

        assertEquals(listOf("health", "stop", "alpine", "python", "gateway"), calls)
    }
}
