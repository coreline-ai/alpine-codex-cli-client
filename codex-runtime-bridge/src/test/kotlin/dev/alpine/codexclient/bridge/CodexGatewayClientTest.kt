package dev.alpine.codexclient.bridge

import dev.alpine.runtime.api.RuntimeSubscription
import dev.alpine.runtime.api.RuntimeTerminalResizeSupport
import dev.alpine.runtime.api.RuntimeTerminalRequest
import dev.alpine.runtime.api.RuntimeTerminalSession
import dev.alpine.runtime.api.RuntimeTerminalSignal
import java.io.BufferedInputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CompletionStage
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ExecutionException
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class CodexGatewayClientTest {
    @Test
    fun `maps health account login and model contracts without exposing response extras`() = FakeGatewayServer(
        listOf(
            Response.json("{\"runtime\":\"ready\",\"gateway\":\"ready\",\"codex\":\"ready\",\"ignored\":\"x\"}"),
            Response.json("{\"authenticated\":false,\"requires_openai_auth\":true}"),
            Response.json("{\"login_id\":\"login-1\",\"verification_url\":\"https://auth.openai.com/device\",\"user_code\":\"TEST-CODE\",\"expires_in_seconds\":600,\"poll_interval_seconds\":2}"),
            Response.json("{\"object\":\"list\",\"data\":[{\"id\":\"model-a\",\"display_name\":\"Model A\",\"is_default\":true}]}"),
        ),
    ).use { server ->
        val client = CodexGatewayClient(transport = LoopbackTestGatewayTransport)
        assertEquals("ready", client.health().codex)
        assertFalse(client.account().authenticated)
        assertEquals(2, client.startDeviceLogin().pollIntervalSeconds)
        assertEquals(listOf("model-a"), client.models().map { it.id })
        assertEquals(
            listOf(
                "GET /healthz",
                "GET /internal/codex/account",
                "POST /internal/codex/login/device",
                "GET /v1/models",
            ),
            server.requests,
        )
    }

    @Test
    fun `cancels a recovered login without exposing its opaque identifier`() = FakeGatewayServer(
        listOf(Response.json("{\"status\":\"cancelled\",\"login_id\":\"ignored\"}")),
    ).use { server ->
        CodexGatewayClient(transport = LoopbackTestGatewayTransport).cancelActiveDeviceLogin()
        assertEquals(listOf("POST /internal/codex/login/active/cancel"), server.requests)
    }

    @Test
    fun `rejects HTTP error oversized malformed and invalid utf8 data fail closed`() {
        FakeGatewayServer(listOf(Response.json("{\"error\":{\"code\":\"authentication_required\"}}", status = 401))).use {
            val error = assertThrows(GatewayClientException::class.java) { CodexGatewayClient(transport = LoopbackTestGatewayTransport).models() }
            assertEquals(GatewayClientErrorCode.HTTP_ERROR, error.errorCode)
            assertEquals(401, error.statusCode)
            assertEquals("authentication_required", error.gatewayCode)
        }
        FakeGatewayServer(listOf(Response.raw("application/json", ByteArray(129 * 1024) { 'x'.code.toByte() }))).use {
            val error = assertThrows(GatewayClientException::class.java) { CodexGatewayClient(transport = LoopbackTestGatewayTransport).health() }
            assertEquals(GatewayClientErrorCode.RESPONSE_TOO_LARGE, error.errorCode)
        }
        FakeGatewayServer(listOf(Response.raw("application/json", byteArrayOf('{'.code.toByte(), '\ufffd'.code.toByte(), '}'.code.toByte())))).use {
            val error = assertThrows(GatewayClientException::class.java) { CodexGatewayClient(transport = LoopbackTestGatewayTransport).health() }
            assertEquals(GatewayClientErrorCode.MALFORMED_RESPONSE, error.errorCode)
        }
        val error = assertThrows(GatewayClientException::class.java) {
            runBlocking { CodexGatewayClient(transport = LoopbackTestGatewayTransport).stream(GatewayChatRequest(null, "model-a", "x".repeat(17 * 1024))).toList() }
        }
        assertEquals(GatewayClientErrorCode.REQUEST_TOO_LARGE, error.errorCode)
    }

    @Test
    fun `streams start delta done and DONE exactly once`() = FakeGatewayServer(
        listOf(
            Response.sse(
                "data: {\"id\":\"chat-1\",\"type\":\"start\",\"model\":\"model-a\",\"conversation_id\":\"conversation-1\"}\n\n" +
                    "data: {\"id\":\"chat-1\",\"type\":\"delta\",\"text\":\"hello\"}\n\n" +
                    "data: {\"id\":\"chat-1\",\"type\":\"done\"}\n\n" +
                    "data: [DONE]\n\n",
            ),
        ),
    ).use { server ->
        val events = runBlocking {
            CodexGatewayClient(transport = LoopbackTestGatewayTransport)
                .stream(GatewayChatRequest("conversation-1", "model-a", "hello"))
                .toList()
        }
        assertEquals(listOf("start", "delta", "done"), events.map { it.type })
        assertEquals("hello", events[1].text)
        assertEquals(listOf("POST /v1/chat/completions"), server.requests)
    }

    @Test
    fun `Codex chat backend exposes one loopback stream with no alternate dispatch`() = FakeGatewayServer(
        listOf(
            Response.sse(
                "data: {\"id\":\"chat-1\",\"type\":\"start\",\"conversation_id\":\"conversation-1\"}\n\n" +
                    "data: {\"id\":\"chat-1\",\"type\":\"delta\",\"text\":\"hello\"}\n\n" +
                    "data: {\"id\":\"chat-1\",\"type\":\"done\"}\n\n" +
                    "data: [DONE]\n\n",
            ),
        ),
    ).use { server ->
        val events = runBlocking {
            CodexGatewayChatBackend(CodexGatewayClient(transport = LoopbackTestGatewayTransport))
                .startTurn(GatewayChatRequest("conversation-1", "model-a", "hello"))
                .events
                .toList()
        }
        assertEquals(
            listOf(
                CodexGatewayChatEvent.Started("chat-1", "conversation-1"),
                CodexGatewayChatEvent.Delta("chat-1", "hello"),
                CodexGatewayChatEvent.Completed("chat-1"),
            ),
            events,
        )
        assertEquals(listOf("POST /v1/chat/completions"), server.requests)
    }

    @Test
    fun `coroutine cancellation and explicit Stop issue at most one interrupt`() = FakeGatewayServer(
        listOf(
            Response.sse(
                "data: {\"id\":\"chat-1\",\"type\":\"start\"}\n\n",
                holdOpenMillis = 2_000,
            ),
            Response.json("{\"status\":\"interrupt_requested\"}"),
        ),
    ).use { server ->
        val client = CodexGatewayClient(transport = LoopbackTestGatewayTransport)
        val control = client.newStreamControl()
        val receivedStart = CompletableFuture<Unit>()
        runBlocking {
            val job = launch(Dispatchers.IO) {
                client.stream(GatewayChatRequest(null, "model-a", "hello"), control).collect {
                    receivedStart.complete(Unit)
                }
            }
            receivedStart.get(2, TimeUnit.SECONDS)
            job.cancelAndJoin()
        }
        assertTrue(server.awaitRequests(2))
        assertEquals(
            listOf(
                "POST /v1/chat/completions",
                "POST /internal/codex/turn/chat-1/interrupt",
            ),
            server.requests,
        )
        assertFalse(control.stop(client))
    }

    @Test
    fun `explicit Stop is one-shot and duplicate starts share a single lifecycle`() = FakeGatewayServer(
        listOf(
            Response.json("{\"status\":\"interrupt_requested\"}"),
            Response.json("{\"runtime\":\"ready\",\"gateway\":\"ready\",\"codex\":\"ready\"}"),
        ),
    ).use { server ->
        val client = CodexGatewayClient(transport = LoopbackTestGatewayTransport)
        val control = client.newStreamControl()
        control.observeRequestId("chat-1")
        assertTrue(control.stop(client))
        assertFalse(control.stop(client))
        assertEquals(listOf("POST /internal/codex/turn/chat-1/interrupt"), server.requests)

        val terminal = FakeTerminal(emitReadyOnWrite = true)
        val host = FakeRuntimeHost(terminal)
        CodexRuntimeController(
            runtimeHost = host,
            stager = GatewayArtifactStager { launchSpec() },
            gatewayClient = client,
            homeDirectory = "/workspace/.alpine-codex/home",
        ).use { controller ->
            val first = controller.start()
            val second = controller.start()
            assertSame(first, second)
            assertEquals(CodexRuntimeLifecycle.RUNNING, first.toCompletableFuture().get(2, TimeUnit.SECONDS).lifecycle)
            assertEquals(CodexRuntimeLifecycle.STOPPED, controller.stop().toCompletableFuture().get(2, TimeUnit.SECONDS).lifecycle)
            assertTrue(terminal.terminated)
            assertEquals(1, host.startCalls)
            assertEquals(1, host.stopCalls)
        }
    }

    @Test
    fun `start then immediate stop settles once even when Runtime callback arrives late`() {
        val terminal = FakeTerminal(emitReadyOnWrite = false)
        val delayedStart = CompletableFuture<GatewayRuntimeLease>()
        val host = FakeRuntimeHost(terminal, delayedStart)
        CodexRuntimeController(
            runtimeHost = host,
            stager = GatewayArtifactStager { launchSpec() },
            gatewayClient = CodexGatewayClient(transport = LoopbackTestGatewayTransport),
            homeDirectory = "/workspace/.alpine-codex/home",
        ).use { controller ->
            val starting = controller.start()
            val stopping = controller.stop()
            delayedStart.complete(host.lease())
            assertThrows(ExecutionException::class.java) {
                starting.toCompletableFuture().get(2, TimeUnit.SECONDS)
            }
            assertEquals(CodexRuntimeLifecycle.STOPPED, stopping.toCompletableFuture().get(2, TimeUnit.SECONDS).lifecycle)
            assertEquals(CodexRuntimeLifecycle.STOPPED, controller.currentState().lifecycle)
            assertEquals(1, host.startCalls)
            assertEquals(1, host.stopCalls)
            assertFalse(terminal.terminated)
        }
    }

    @Test
    fun `closed startup failure marker fails fast without terminal output detail`() {
        val terminal = FakeTerminal(
            emitReadyOnWrite = false,
            markerOnWrite = "AGENT_GATEWAY_FAILED_CODEX\n",
        )
        val host = FakeRuntimeHost(terminal)
        CodexRuntimeController(
            runtimeHost = host,
            stager = GatewayArtifactStager { launchSpec() },
            gatewayClient = CodexGatewayClient(transport = LoopbackTestGatewayTransport),
            homeDirectory = "/workspace/.alpine-codex/home",
        ).use { controller ->
            val error = assertThrows(ExecutionException::class.java) {
                controller.start().toCompletableFuture().get(2, TimeUnit.SECONDS)
            }
            assertEquals(
                CodexRuntimeErrorCode.CODEX_BACKEND_START_FAILED,
                (error.cause as CodexRuntimeException).errorCode,
            )
            assertEquals(1, host.stopCalls)
        }
    }

    @Test
    fun `process recreation only reconnects to an already active healthy loopback gateway`() = FakeGatewayServer(
        listOf(Response.json("{\"runtime\":\"ready\",\"gateway\":\"ready\",\"codex\":\"ready\"}")),
    ).use { server ->
        val host = FakeRuntimeHost(FakeTerminal(emitReadyOnWrite = false), runtimeAlreadyActive = true)
        CodexRuntimeController(
            runtimeHost = host,
            stager = GatewayArtifactStager { launchSpec() },
            gatewayClient = CodexGatewayClient(transport = LoopbackTestGatewayTransport),
            homeDirectory = "/workspace/.alpine-codex/home",
        ).use { controller ->
            assertEquals(
                CodexRuntimeLifecycle.RUNNING,
                controller.reconnectIfRuntimeActive().toCompletableFuture().get(2, TimeUnit.SECONDS).lifecycle,
            )
            assertEquals(listOf("GET /healthz"), server.requests)
            assertEquals(0, host.startCalls)
            assertEquals(0, host.stopCalls)
        }
    }

    private fun launchSpec() = GatewayLaunchSpec(
        codexExecutable = "/workspace/.alpine-codex/staging/codex-0.147.0/codex",
        gatewayRootDirectory = "/workspace/.alpine-codex/gateway",
        homeDirectory = "/workspace/.alpine-codex/home",
        workspaceDirectory = "/workspace",
        socketPath = "/data/user/0/dev.alpine.codexclient.debug/files/" +
            "alpine-codex-runtime/workspace/.gateway/gateway.sock",
        expectedPeerUid = 12345,
    )

    private data class Response(
        val status: Int,
        val contentType: String,
        val body: ByteArray,
        val holdOpenMillis: Long = 0,
    ) {
        companion object {
            fun json(value: String, status: Int = 200) = Response(status, "application/json", value.toByteArray())
            fun sse(value: String, holdOpenMillis: Long = 0) =
                Response(200, "text/event-stream", value.toByteArray(), holdOpenMillis)
            fun raw(contentType: String, body: ByteArray) = Response(200, contentType, body)
        }
    }

    private class FakeGatewayServer(private val responses: List<Response>) : AutoCloseable {
        private val socket = ServerSocket(8787, 16, InetAddress.getByName("127.0.0.1"))
        val requests = Collections.synchronizedList(mutableListOf<String>())
        private val responseIndex = java.util.concurrent.atomic.AtomicInteger(0)
        private val acceptThread = thread(isDaemon = true, name = "fake-gateway") {
            while (!socket.isClosed) {
                runCatching { socket.accept() }.getOrNull()?.let { connection ->
                    thread(isDaemon = true) { handle(connection) }
                }
            }
        }

        private fun handle(connection: Socket) {
            connection.use { socket ->
                val input = BufferedInputStream(socket.getInputStream())
                val request = readAsciiLine(input) ?: return
                val parts = request.split(' ')
                if (parts.size < 2) return
                var contentLength = 0
                while (true) {
                    val header = readAsciiLine(input) ?: return
                    if (header.isEmpty()) break
                    if (header.startsWith("Content-Length:", ignoreCase = true)) {
                        contentLength = header.substringAfter(':').trim().toIntOrNull() ?: return
                    }
                }
                input.readNBytes(contentLength)
                requests += "${parts[0]} ${parts[1]}"
                val response = responses.getOrElse(responseIndex.getAndIncrement()) { Response.json("{}", status = 500) }
                writeResponse(socket.getOutputStream(), response)
                if (response.holdOpenMillis > 0) Thread.sleep(response.holdOpenMillis)
            }
        }

        fun awaitRequests(expected: Int): Boolean {
            val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2)
            while (System.nanoTime() < deadline) {
                if (requests.size >= expected) return true
                Thread.sleep(10)
            }
            return requests.size >= expected
        }

        override fun close() {
            socket.close()
            acceptThread.join(1_000)
        }
    }

    private class FakeRuntimeHost(
        private val terminal: FakeTerminal,
        private val startFuture: CompletableFuture<GatewayRuntimeLease>? = null,
        private val runtimeAlreadyActive: Boolean = false,
    ) : GatewayRuntimeHost {
        var startCalls = 0
        var stopCalls = 0
        override fun startRuntime(homeDirectory: String): CompletionStage<GatewayRuntimeLease> {
            startCalls++
            return startFuture ?: CompletableFuture.completedFuture(lease())
        }

        fun lease(): GatewayRuntimeLease = object : GatewayRuntimeLease {
            override fun openGatewayTerminal(request: RuntimeTerminalRequest): CompletionStage<RuntimeTerminalSession> =
                CompletableFuture.completedFuture(terminal)
        }

        override fun stopRuntime(): CompletionStage<Void> {
            stopCalls++
            return CompletableFuture.completedFuture(null)
        }

        override fun hasActiveRuntime(): Boolean = runtimeAlreadyActive
    }

    private class FakeTerminal(
        private val emitReadyOnWrite: Boolean,
        private val markerOnWrite: String? = null,
    ) : RuntimeTerminalSession {
        override val id: String = "gateway-terminal"
        override val isOpen: Boolean = true
        override val resizeSupport: RuntimeTerminalResizeSupport = RuntimeTerminalResizeSupport.INITIAL_SIZE_ONLY
        private val listeners = CopyOnWriteArrayList<(ByteArray) -> Unit>()
        var terminated = false

        override fun addOutputListener(listener: dev.alpine.runtime.api.RuntimeTerminalOutputListener): RuntimeSubscription {
            val callback: (ByteArray) -> Unit = { bytes -> listener.onOutput(bytes) }
            listeners += callback
            return RuntimeSubscription { listeners -= callback }
        }

        override fun write(bytes: ByteArray): CompletionStage<Void> {
            val marker = markerOnWrite ?: "AGENT_GATEWAY_READY\n".takeIf { emitReadyOnWrite }
            if (marker != null) listeners.forEach { it(marker.toByteArray()) }
            return CompletableFuture.completedFuture(null)
        }

        override fun resize(columns: Int, rows: Int): CompletionStage<Void> = CompletableFuture.completedFuture(null)

        override fun signal(signal: RuntimeTerminalSignal): CompletionStage<Void> {
            if (signal == RuntimeTerminalSignal.TERMINATE) terminated = true
            return CompletableFuture.completedFuture(null)
        }

        override fun closeAsync(): CompletionStage<Void> = CompletableFuture.completedFuture(null)
    }

    private companion object {
        fun readAsciiLine(input: BufferedInputStream): String? {
            val bytes = mutableListOf<Byte>()
            while (true) {
                val value = input.read()
                if (value < 0) return bytes.takeIf { it.isNotEmpty() }?.toByteArray()?.toString(Charsets.US_ASCII)
                if (value == '\n'.code) {
                    val line = bytes.toByteArray()
                    val trimmed = if (line.lastOrNull() == '\r'.code.toByte()) line.copyOf(line.size - 1) else line
                    return trimmed.toString(Charsets.US_ASCII)
                }
                bytes += value.toByte()
            }
        }

        fun writeResponse(output: OutputStream, response: Response) {
            val reason = if (response.status in 200..299) "OK" else "Error"
            val isOpenStream = response.holdOpenMillis > 0
            val headers = "HTTP/1.1 ${response.status} $reason\r\n" +
                    "Content-Type: ${response.contentType}\r\n" +
                    (if (isOpenStream) "Transfer-Encoding: chunked\r\nConnection: keep-alive\r\n\r\n"
                    else "Content-Length: ${response.body.size}\r\nConnection: close\r\n\r\n")
            output.write(headers.toByteArray(Charsets.US_ASCII))
            if (isOpenStream) {
                output.write(response.body.size.toString(16).toByteArray(Charsets.US_ASCII))
                output.write("\r\n".toByteArray(Charsets.US_ASCII))
                output.write(response.body)
                output.write("\r\n".toByteArray(Charsets.US_ASCII))
            } else {
                output.write(response.body)
            }
            output.flush()
        }
    }
}
