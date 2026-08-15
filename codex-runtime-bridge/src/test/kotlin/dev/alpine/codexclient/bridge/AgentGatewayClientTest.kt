package dev.alpine.codexclient.bridge

import java.io.BufferedInputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentGatewayClientTest {
    @Test
    fun `typed Agent lifecycle uses normalized paths and preserves Agent identity`() = FakeAgentServer(
        listOf(
            Response.json("""{"runtime":"ready","gateway":"ready","selected_agent":"codex","backend_ready":true}"""),
            Response.json(
                """{"object":"list","data":[{"id":"codex","selected":true,"ready":true,"capabilities":{"device_oauth":true,"dynamic_models":true,"streaming":true,"stop":true,"resume":true}},{"id":"grok","selected":false,"ready":false,"capabilities":{"device_oauth":true,"dynamic_models":true,"streaming":true,"stop":true,"resume":true}}]}""",
            ),
            Response.json("""{"selected_agent":"grok","backend_ready":true}"""),
            Response.json("""{"agent_id":"grok","authenticated":false,"requires_auth":true}"""),
            Response.json("""{"agent_id":"grok","request_id":"login-1","status":"pending","verification_url":"https://auth.x.ai/device?challenge=fixture","expires_in_seconds":600}"""),
            Response.json("""{"agent_id":"grok","request_id":"login-1","status":"authenticated"}"""),
            Response.json("""{"object":"list","agent_id":"grok","data":[{"id":"model-a","display_name":"Model A","is_default":true,"modalities":["text"]}]}"""),
            Response.sse(
                    "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"start\",\"conversation_id\":\"conversation-1\"}\n\n" +
                    "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"delta\",\"text\":\"fixture\"}\n\n" +
                    "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"done\",\"diagnostics\":{\"prompt_dispatch_count\":1,\"visible_delta_count\":1,\"terminal_count\":1,\"cancel_dispatch_count\":0,\"retry_classification\":\"none\",\"retry_attempts\":0,\"retry_max\":0,\"tool_event_count\":0,\"subagent_event_count\":0,\"mcp_event_count\":0,\"filesystem_event_count\":0,\"terminal_event_count\":0}}\n\n" +
                    "data: [DONE]\n\n",
            ),
            Response.json("""{"agent_id":"grok","status":"cancelled","request_id":"login-recovered"}"""),
            Response.json("""{"agent_id":"grok","status":"logged_out"}"""),
        ),
    ).use { server ->
        val client = AgentGatewayClient(transport = LoopbackTestGatewayTransport)
        assertEquals(AgentId.CODEX, client.health().selectedAgent)
        assertEquals(listOf(AgentId.CODEX, AgentId.GROK), client.agents().map { it.agentId })
        assertEquals(AgentId.GROK, client.selectAgent(AgentId.GROK).selectedAgent)
        assertFalse(client.account(AgentId.GROK).authenticated)
        val login = client.startDeviceLogin(AgentId.GROK)
        assertEquals("pending", login.state)
        assertNull(login.userCode)
        assertEquals("authenticated", client.loginStatus(AgentId.GROK, login.requestId).state)
        assertEquals(listOf("model-a"), client.models(AgentId.GROK).map { it.id })

        val control = client.newStreamControl(AgentId.GROK)
        val events = runBlocking {
            client.stream(
                AgentGatewayChatRequest(AgentId.GROK, null, "model-a", "fixture input"),
                control,
            ).toList()
        }
        assertEquals(3, events.size)
        assertTrue(events.all { it.agentId == AgentId.GROK })
        val diagnostics = (events.last() as AgentTurnEvent.Completed).diagnostics
        assertEquals(1, diagnostics?.promptDispatchCount)
        assertEquals(0, diagnostics?.toolEventCount)
        assertFalse(control.stop(client))
        client.cancelActiveDeviceLogin(AgentId.GROK)
        client.logout(AgentId.GROK)

        assertEquals(
            listOf(
                "GET /healthz",
                "GET /v1/agents",
                "POST /internal/agents/select",
                "GET /internal/agents/grok/account",
                "POST /internal/agents/grok/login/device",
                "GET /internal/agents/grok/login/login-1",
                "GET /v1/models",
                "POST /v1/chat/completions",
                "POST /internal/agents/grok/login/active/cancel",
                "POST /internal/agents/grok/logout",
            ),
            server.requests,
        )
        assertTrue(server.bodies[2].contains("\"agent_id\":\"grok\""))
        assertTrue(server.bodies[7].contains("\"agent_id\":\"grok\""))
    }

    @Test
    fun `response or SSE Agent mismatch fails closed`() {
        FakeAgentServer(
            listOf(Response.json("""{"agent_id":"codex","authenticated":true,"requires_auth":false}""")),
        ).use {
            val error = assertThrows(GatewayClientException::class.java) {
                AgentGatewayClient(transport = LoopbackTestGatewayTransport).account(AgentId.GROK)
            }
            assertEquals(GatewayClientErrorCode.MALFORMED_RESPONSE, error.errorCode)
        }
        FakeAgentServer(
            listOf(
                Response.sse(
                    "data: {\"id\":\"turn-1\",\"agent_id\":\"codex\",\"type\":\"start\"}\n\n" +
                        "data: [DONE]\n\n",
                ),
            ),
        ).use {
            val error = assertThrows(GatewayClientException::class.java) {
                runBlocking {
                    AgentGatewayClient(transport = LoopbackTestGatewayTransport).stream(
                        AgentGatewayChatRequest(AgentId.GROK, null, "model-a", "fixture"),
                    ).toList()
                }
            }
            assertEquals(GatewayClientErrorCode.MALFORMED_RESPONSE, error.errorCode)
        }
    }

    @Test
    fun `terminal diagnostics reject sensitive shape invalid counts and Codex injection`() {
        val invalidDiagnostics = listOf(
            "{\"prompt_dispatch_count\":1}",
            "{\"prompt_dispatch_count\":2,\"visible_delta_count\":0,\"terminal_count\":1,\"cancel_dispatch_count\":0,\"retry_classification\":\"none\",\"retry_attempts\":0,\"retry_max\":0,\"tool_event_count\":0,\"subagent_event_count\":0,\"mcp_event_count\":0,\"filesystem_event_count\":0,\"terminal_event_count\":0}",
            "{\"prompt_dispatch_count\":1,\"visible_delta_count\":0,\"terminal_count\":1,\"cancel_dispatch_count\":0,\"retry_classification\":\"private reason\",\"retry_attempts\":0,\"retry_max\":0,\"tool_event_count\":0,\"subagent_event_count\":0,\"mcp_event_count\":0,\"filesystem_event_count\":0,\"terminal_event_count\":0}",
        )
        invalidDiagnostics.forEach { diagnostics ->
            FakeAgentServer(
                listOf(
                    Response.sse(
                        "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"start\"}\n\n" +
                            "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"done\",\"diagnostics\":$diagnostics}\n\n" +
                            "data: [DONE]\n\n",
                    ),
                    Response.json("""{"agent_id":"grok","id":"turn-1","status":"interrupt_requested"}"""),
                ),
            ).use {
                assertThrows(GatewayClientException::class.java) {
                    runBlocking {
                        AgentGatewayClient(transport = LoopbackTestGatewayTransport).stream(
                            AgentGatewayChatRequest(AgentId.GROK, null, "model-a", "fixture"),
                        ).toList()
                    }
                }
            }
        }

        FakeAgentServer(
            listOf(
                Response.sse(
                    "data: {\"id\":\"turn-1\",\"agent_id\":\"codex\",\"type\":\"start\"}\n\n" +
                        "data: {\"id\":\"turn-1\",\"agent_id\":\"codex\",\"type\":\"done\",\"diagnostics\":{}}\n\n" +
                        "data: [DONE]\n\n",
                ),
                Response.json("""{"agent_id":"codex","id":"turn-1","status":"interrupt_requested"}"""),
            ),
        ).use {
            assertThrows(GatewayClientException::class.java) {
                runBlocking {
                    AgentGatewayClient(transport = LoopbackTestGatewayTransport).stream(
                        AgentGatewayChatRequest(AgentId.CODEX, null, "model-a", "fixture"),
                    ).toList()
                }
            }
        }
    }

    @Test
    fun `SSE requires one start one request identity one terminal then done marker`() {
        val invalidStreams = listOf(
            "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"delta\",\"text\":\"early\"}\n\n" +
                "data: [DONE]\n\n",
            "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"start\"}\n\n" +
                "data: {\"id\":\"turn-2\",\"agent_id\":\"grok\",\"type\":\"delta\",\"text\":\"wrong\"}\n\n",
            "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"start\"}\n\n" +
                "data: [DONE]\n\n",
            "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"start\"}\n\n" +
                "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"done\"}\n\n" +
                "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"done\"}\n\n" +
                "data: [DONE]\n\n",
        )
        invalidStreams.forEach { stream ->
            FakeAgentServer(
                listOf(
                    Response.sse(stream),
                    Response.json("""{"agent_id":"grok","id":"turn-1","status":"interrupt_requested"}"""),
                ),
            ).use { server ->
                val error = assertThrows(GatewayClientException::class.java) {
                    runBlocking {
                        AgentGatewayClient(transport = LoopbackTestGatewayTransport).stream(
                            AgentGatewayChatRequest(AgentId.GROK, null, "model-a", "fixture"),
                        ).toList()
                    }
                }
                assertTrue(
                    error.errorCode in setOf(
                        GatewayClientErrorCode.MALFORMED_RESPONSE,
                        GatewayClientErrorCode.MALFORMED_SSE,
                    ),
                )
                assertEquals(1, server.requests.count { it == "POST /v1/chat/completions" })
            }
        }
    }

    @Test
    fun `incomplete stream dispatches one exact interrupt and never replays prompt`() = FakeAgentServer(
        listOf(
            Response.sse(
                "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"start\"}\n\n" +
                    "data: {\"id\":\"turn-1\",\"agent_id\":\"grok\",\"type\":\"delta\",\"text\":\"partial\"}\n\n",
            ),
            Response.json("""{"agent_id":"grok","id":"turn-1","status":"interrupt_requested"}"""),
        ),
    ).use { server ->
        val error = assertThrows(GatewayClientException::class.java) {
            runBlocking {
                AgentGatewayClient(transport = LoopbackTestGatewayTransport).stream(
                    AgentGatewayChatRequest(AgentId.GROK, null, "model-a", "one user prompt"),
                ).toList()
            }
        }
        assertEquals(GatewayClientErrorCode.MALFORMED_SSE, error.errorCode)
        assertEquals(
            listOf(
                "POST /v1/chat/completions",
                "POST /internal/agents/grok/turn/turn-1/interrupt",
            ),
            server.requests,
        )
        assertEquals(1, server.bodies.count { it.contains("\"content\":\"one user prompt\"") })
    }

    private data class Response(val status: Int, val contentType: String, val body: ByteArray) {
        companion object {
            fun json(value: String, status: Int = 200) =
                Response(status, "application/json", value.toByteArray())

            fun sse(value: String) = Response(200, "text/event-stream", value.toByteArray())
        }
    }

    private class FakeAgentServer(private val responses: List<Response>) : AutoCloseable {
        private val socket = ServerSocket(8787, 16, InetAddress.getByName("127.0.0.1"))
        private val responseIndex = AtomicInteger(0)
        val requests = Collections.synchronizedList(mutableListOf<String>())
        val bodies = Collections.synchronizedList(mutableListOf<String>())
        private val acceptThread = thread(isDaemon = true, name = "fake-agent-gateway") {
            while (!socket.isClosed) {
                runCatching { socket.accept() }.getOrNull()?.let { connection ->
                    thread(isDaemon = true) { handle(connection) }
                }
            }
        }

        private fun handle(connection: Socket) {
            connection.use { active ->
                val input = BufferedInputStream(active.getInputStream())
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
                val body = input.readNBytes(contentLength).toString(Charsets.UTF_8)
                requests += "${parts[0]} ${parts[1]}"
                bodies += body
                val response = responses.getOrElse(responseIndex.getAndIncrement()) {
                    Response.json("{}", status = 500)
                }
                val header = buildString {
                    append("HTTP/1.1 ${response.status} OK\r\n")
                    append("Content-Type: ${response.contentType}\r\n")
                    append("Content-Length: ${response.body.size}\r\n")
                    append("Connection: close\r\n\r\n")
                }.toByteArray(Charsets.US_ASCII)
                active.getOutputStream().apply {
                    write(header)
                    write(response.body)
                    flush()
                }
            }
        }

        override fun close() {
            socket.close()
            acceptThread.join(1_000)
        }
    }

    private companion object {
        fun readAsciiLine(input: BufferedInputStream): String? {
            val output = StringBuilder()
            while (true) {
                val next = input.read()
                if (next < 0) return null
                if (next == '\n'.code) return output.toString().removeSuffix("\r")
                output.append(next.toChar())
            }
        }
    }
}
