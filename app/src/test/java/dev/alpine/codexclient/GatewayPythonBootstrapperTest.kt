package dev.alpine.codexclient

import dev.alpine.codexclient.gatewaypack.StagedCodexGateway
import dev.alpine.pythonpack.bundled.PythonPackagePackException
import dev.alpine.pythonpack.bundled.PythonPackagePackFailure
import dev.alpine.pythonpack.bundled.StagedPythonPackagePack
import dev.alpine.runtime.api.RuntimeCommandRequest
import dev.alpine.runtime.api.RuntimeCommandResult
import java.util.ArrayDeque
import java.util.concurrent.CompletableFuture
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GatewayPythonBootstrapperTest {
    @Test
    fun `existing Python skips package staging and apk`() {
        val executor = RecordingExecutor(ok(), ok(), ok())
        var staged = false
        val outcome = bootstrapper(executor, packageStager = {
            staged = true
            pack()
        }).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE, outcome)
        assertFalse(staged)
        assertEquals(
            listOf("/bin/uname", "/usr/bin/python3", "/usr/bin/python3"),
            executor.requests.map { it.executable },
        )
        assertEquals(listOf("-c", "import codex_gateway"), executor.requests.last().arguments)
    }

    @Test
    fun `missing embedded pack fails closed without apk command`() {
        val executor = RecordingExecutor(ok(), failed())
        val outcome = bootstrapper(executor, packageStager = {
            throw PythonPackagePackException(PythonPackagePackFailure.UNAVAILABLE)
        }).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.BUNDLE_UNAVAILABLE, outcome)
        assertTrue(executor.requests.none { it.executable == "/sbin/apk" })
    }

    @Test
    fun `invalid embedded pack fails closed without apk command`() {
        val executor = RecordingExecutor(ok(), failed())
        val outcome = bootstrapper(executor, packageStager = {
            throw PythonPackagePackException(PythonPackagePackFailure.INVALID)
        }).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.BUNDLE_INVALID, outcome)
        assertTrue(executor.requests.none { it.executable == "/sbin/apk" })
    }

    @Test
    fun `install uses only no-network absolute locked package files then imports Gateway`() {
        val executor = RecordingExecutor(ok(), failed(), ok(), ok(), ok(), ok(), ok())
        val outcome = bootstrapper(executor).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.INSTALLED, outcome)
        val apk = executor.requests.filter { it.executable == "/sbin/apk" }
        assertEquals(2, apk.size)
        assertTrue("--simulate" in apk.first().arguments)
        assertFalse("--simulate" in apk.last().arguments)
        apk.forEach { request ->
            assertTrue("--no-network" in request.arguments)
            assertTrue("--no-cache" in request.arguments)
            assertFalse("python3" in request.arguments)
            assertTrue(request.arguments.none { it.contains("://") })
            assertEquals(PACKAGE_PATH, request.arguments.last())
        }
        val import = executor.requests.last()
        assertEquals("/usr/bin/python3", import.executable)
        assertEquals(listOf("-c", "import codex_gateway"), import.arguments)
        assertEquals("/workspace/.alpine-codex/gateway", import.environment["PYTHONPATH"])
    }

    @Test
    fun `preflight failure never attempts install`() {
        val executor = RecordingExecutor(ok(), failed(), failed())
        val outcome = bootstrapper(executor).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.PREFLIGHT_FAILED, outcome)
        assertEquals(1, executor.requests.count { it.executable == "/sbin/apk" })
    }

    @Test
    fun `post-install Gateway import failure rejects completion`() {
        val executor = RecordingExecutor(ok(), failed(), ok(), ok(), ok(), ok(), failed())
        val outcome = bootstrapper(executor).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.VERIFICATION_FAILED, outcome)
    }

    @Test
    fun `unsafe staged guest path is rejected before apk`() {
        val executor = RecordingExecutor(ok(), failed())
        val outcome = bootstrapper(executor, packageStager = {
            pack().copy(guestPackagePaths = listOf("/workspace/../../python3.apk"))
        }).prepare().toCompletableFuture().get()

        assertEquals(GatewayPythonBootstrapOutcome.BUNDLE_INVALID, outcome)
        assertTrue(executor.requests.none { it.executable == "/sbin/apk" })
    }

    private fun bootstrapper(
        executor: RecordingExecutor,
        packageStager: () -> StagedPythonPackagePack = ::pack,
    ) = GatewayPythonBootstrapper(
        commandExecutor = executor::execute,
        packageStager = packageStager,
        gatewayStager = { StagedCodexGateway("/workspace/.alpine-codex/gateway/codex_gateway") },
    )

    private class RecordingExecutor(vararg responses: RuntimeCommandResult) {
        private val responses = ArrayDeque(responses.toList())
        val requests = mutableListOf<RuntimeCommandRequest>()

        fun execute(request: RuntimeCommandRequest) = CompletableFuture.completedFuture(
            responses.removeFirst().also { requests += request },
        )
    }

    private companion object {
        const val PACKAGE_PATH =
            "/workspace/.alpine-codex/staging/python-pack/alpine-3.21-python3/packages/python3-3.12-r0.apk"

        fun pack() = StagedPythonPackagePack(
            packId = "alpine-3.21-python3",
            alpineVersion = "3.21.3",
            guestPackagePaths = listOf(PACKAGE_PATH),
        )

        fun ok() = RuntimeCommandResult(exitCode = 0)
        fun failed() = RuntimeCommandResult(exitCode = 1)
    }
}
