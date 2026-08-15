package dev.alpine.codexclient.bridge

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext

data class GatewayAgent(
    val agentId: AgentId,
    val selected: Boolean,
    val ready: Boolean,
    val capabilities: AgentCapabilities,
)

data class AgentGatewayHealth(
    val runtime: String,
    val gateway: String,
    val selectedAgent: AgentId,
    val backendReady: Boolean,
)

data class AgentSelection(val selectedAgent: AgentId, val backendReady: Boolean)

data class AgentGatewayChatRequest(
    val agentId: AgentId,
    val conversationId: String?,
    val model: String,
    val text: String,
    val resumeExisting: Boolean = false,
)

/** One-shot Stop state is bound to one Agent before the first SSE event arrives. */
class AgentGatewayStreamControl internal constructor(private val agentId: AgentId) {
    private var stopped = false
    private var terminal = false
    private var requestId: String? = null

    @Synchronized
    internal fun observeRequestId(value: String) {
        val current = requestId
        if (current == null) {
            requestId = value
        } else if (current != value) {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
        }
    }

    @Synchronized
    internal fun markTerminal(value: String) {
        observeRequestId(value)
        terminal = true
    }

    @Synchronized
    fun stop(client: AgentGatewayClient): Boolean {
        val id = requestId ?: return false
        if (terminal || stopped) return false
        stopped = true
        client.interrupt(agentId, id)
        return true
    }
}

/**
 * Typed client for the normalized selected-Agent Gateway. Endpoint and Agent values are closed;
 * callers cannot supply a host, arbitrary path, header, credential, retry, or fallback.
 */
open class AgentGatewayClient(
    private val requestSigner: GatewayRequestSigner = GatewayRequestSigner.ephemeral(),
    private val transport: GatewayTransport = DisabledGatewayTransport,
) : GatewayRuntimeHealthClient {
    override fun isRuntimeHealthy(): Boolean = health().let {
        it.runtime == "ready" && it.gateway == "ready" && it.backendReady
    }
    open fun health(): AgentGatewayHealth {
        val value = getJson("/healthz")
        return AgentGatewayHealth(
            runtime = value.requiredString("runtime"),
            gateway = value.requiredString("gateway"),
            selectedAgent = requiredAgent(value, "selected_agent"),
            backendReady = value.requiredBoolean("backend_ready"),
        )
    }

    open fun agents(): List<GatewayAgent> {
        val value = getJson("/v1/agents")
        if (value.requiredString("object") != "list") malformed()
        val agents = value["data"]?.asArray() ?: malformed()
        if (agents.isEmpty() || agents.size > AgentId.entries.size) malformed()
        val parsed = agents.map { row ->
            val item = row.asObject()
            val agentId = requiredAgent(item, "id")
            val capabilities = item["capabilities"]?.asObject() ?: malformed()
            GatewayAgent(
                agentId = agentId,
                selected = item.requiredBoolean("selected"),
                ready = item.requiredBoolean("ready"),
                capabilities = AgentCapabilities(
                    deviceOAuth = capabilities.requiredBoolean("device_oauth"),
                    dynamicModels = capabilities.requiredBoolean("dynamic_models"),
                    streaming = capabilities.requiredBoolean("streaming"),
                    stop = capabilities.requiredBoolean("stop"),
                    resume = capabilities.requiredBoolean("resume"),
                ),
            )
        }
        if (parsed.map { it.agentId }.distinct().size != parsed.size || parsed.count { it.selected } != 1) malformed()
        return parsed
    }

    open fun selectAgent(agentId: AgentId): AgentSelection {
        val body = BoundedJson.encode(
            JsonValue.ObjectValue(linkedMapOf("agent_id" to JsonValue.StringValue(agentId.wireValue))),
        )
        val value = postJson("/internal/agents/select", body)
        val selected = requiredAgent(value, "selected_agent")
        if (selected != agentId) malformed()
        return AgentSelection(selected, value.requiredBoolean("backend_ready"))
    }

    open fun account(agentId: AgentId): AgentAccount {
        val value = getJson("/internal/agents/${agentId.wireValue}/account")
        return AgentAccount(
            agentId = matchingAgent(value, agentId),
            authenticated = value.requiredBoolean("authenticated"),
            requiresAuth = value.requiredBoolean("requires_auth"),
        )
    }

    open fun startDeviceLogin(agentId: AgentId): AgentLogin = parseLogin(
        postJson("/internal/agents/${agentId.wireValue}/login/device", null),
        agentId,
        start = true,
    )

    open fun loginStatus(agentId: AgentId, requestId: String): AgentLogin = parseLogin(
        getJson("/internal/agents/${agentId.wireValue}/login/${opaquePath(requestId)}"),
        agentId,
        start = false,
    )

    open fun cancelLogin(agentId: AgentId, requestId: String): AgentLogin = parseLogin(
        postJson(
            "/internal/agents/${agentId.wireValue}/login/${opaquePath(requestId)}/cancel",
            null,
        ),
        agentId,
        start = false,
    )

    open fun cancelActiveDeviceLogin(agentId: AgentId) {
        val value = postJson("/internal/agents/${agentId.wireValue}/login/active/cancel", null)
        if (matchingAgent(value, agentId) != agentId || value.requiredString("status") != "cancelled") malformed()
    }

    open fun logout(agentId: AgentId) {
        val value = postJson("/internal/agents/${agentId.wireValue}/logout", null)
        if (matchingAgent(value, agentId) != agentId || value.requiredString("status") != "logged_out") malformed()
    }

    open fun models(agentId: AgentId): List<AgentModel> {
        val value = getJson("/v1/models")
        if (value.requiredString("object") != "list" || matchingAgent(value, agentId) != agentId) malformed()
        val rows = value["data"]?.asArray() ?: malformed()
        if (rows.size > MAX_MODELS) malformed()
        val models = rows.map { row ->
            val item = row.asObject()
            AgentModel(
                agentId = agentId,
                id = item.requiredString("id"),
                displayName = item.requiredString("display_name"),
                isDefault = item.requiredBoolean("is_default"),
            )
        }
        if (models.map { it.id }.distinct().size != models.size) malformed()
        return models
    }

    open fun newStreamControl(agentId: AgentId): AgentGatewayStreamControl =
        AgentGatewayStreamControl(agentId)

    open fun stream(
        request: AgentGatewayChatRequest,
        control: AgentGatewayStreamControl = newStreamControl(request.agentId),
    ): Flow<AgentTurnEvent> = flow {
        validateChatRequest(request)
        val values = linkedMapOf<String, JsonValue>(
            "agent_id" to JsonValue.StringValue(request.agentId.wireValue),
            "model" to JsonValue.StringValue(request.model),
            "stream" to JsonValue.BooleanValue(true),
            "messages" to JsonValue.ArrayValue(
                listOf(
                    JsonValue.ObjectValue(
                        linkedMapOf(
                            "role" to JsonValue.StringValue("user"),
                            "content" to JsonValue.StringValue(request.text),
                        ),
                    ),
                ),
            ),
        )
        request.conversationId?.let { values["conversation_id"] = JsonValue.StringValue(it) }
        if (request.resumeExisting) values["resume_existing"] = JsonValue.BooleanValue(true)
        val body = BoundedJson.encode(JsonValue.ObjectValue(values))
        if (body.size > MAX_REQUEST_BYTES) failure(GatewayClientErrorCode.REQUEST_TOO_LARGE)

        var connection: HttpURLConnection? = null
        val activeConnection = AtomicReference<HttpURLConnection?>(null)
        val cancellationHandle = checkNotNull(currentCoroutineContext()[Job]).invokeOnCompletion { cause ->
            if (cause is CancellationException) activeConnection.get()?.disconnect()
        }
        var completed = false
        try {
            connection = open("/v1/chat/completions")
            activeConnection.set(connection)
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setFixedLengthStreamingMode(body.size)
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Accept", "text/event-stream")
            requestSigner.authorize(connection, "POST", "/v1/chat/completions", body)
            connection.outputStream.use { it.write(body) }
            val status = connection.responseCode
            if (status !in 200..299) throw httpFailure(connection, status)
            if (!connection.contentType.orEmpty().lowercase().startsWith("text/event-stream")) {
                failure(GatewayClientErrorCode.MALFORMED_SSE)
            }
            val dataLines = mutableListOf<ByteArray>()
            var totalBytes = 0
            var started = false
            var terminal = false
            connection.inputStream.use { input ->
                while (true) {
                    val line = readLine(input, MAX_STREAM_EVENT_BYTES) ?: break
                    totalBytes += line.size + 1
                    if (totalBytes > MAX_STREAM_BYTES) failure(GatewayClientErrorCode.STREAM_TOO_LARGE)
                    if (line.isEmpty()) {
                        val event = parseSseEvent(dataLines)
                        dataLines.clear()
                        if (event == null) continue
                        if (event.contentEquals(DONE_MARKER)) {
                            if (!terminal) failure(GatewayClientErrorCode.MALFORMED_SSE)
                            completed = true
                            break
                        }
                        val parsed = parseStreamEvent(event, request.agentId)
                        if (terminal) failure(GatewayClientErrorCode.MALFORMED_SSE)
                        control.observeRequestId(parsed.requestId)
                        when (parsed) {
                            is AgentTurnEvent.Started -> {
                                if (started) failure(GatewayClientErrorCode.MALFORMED_SSE)
                                started = true
                            }
                            is AgentTurnEvent.Delta -> {
                                if (!started) failure(GatewayClientErrorCode.MALFORMED_SSE)
                            }
                            is AgentTurnEvent.Completed, is AgentTurnEvent.Failed -> {
                                if (!started) failure(GatewayClientErrorCode.MALFORMED_SSE)
                                terminal = true
                                control.markTerminal(parsed.requestId)
                            }
                        }
                        emit(parsed)
                    } else if (line.hasPrefix(DATA_PREFIX)) {
                        val data = line.copyOfRange(DATA_PREFIX.size, line.size).dropLeadingSpace()
                        if (dataLines.sumOf { it.size } + data.size > MAX_STREAM_EVENT_BYTES) {
                            failure(GatewayClientErrorCode.MALFORMED_SSE)
                        }
                        dataLines += data
                    } else if (!line.hasPrefix(COLON_PREFIX)) {
                        failure(GatewayClientErrorCode.MALFORMED_SSE)
                    }
                }
            }
            if (!completed) {
                currentCoroutineContext().ensureActive()
                failure(GatewayClientErrorCode.MALFORMED_SSE)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: GatewayClientException) {
            throw error
        } catch (_: Exception) {
            currentCoroutineContext().ensureActive()
            failure(GatewayClientErrorCode.CONNECTION_FAILED)
        } finally {
            cancellationHandle.dispose()
            activeConnection.getAndSet(null)?.disconnect()
            if (!completed) withContext(NonCancellable) {
                runCatching { control.stop(this@AgentGatewayClient) }
            }
        }
    }

    open fun interrupt(agentId: AgentId, requestId: String) {
        val value = postJson(
            "/internal/agents/${agentId.wireValue}/turn/${opaquePath(requestId)}/interrupt",
            null,
        )
        if (
            matchingAgent(value, agentId) != agentId ||
            value.requiredString("id") != requestId ||
            value.requiredString("status") != "interrupt_requested"
        ) malformed()
    }

    private fun parseLogin(value: Map<String, JsonValue>, agentId: AgentId, start: Boolean): AgentLogin {
        val state = value.requiredString("status")
        if (state !in LOGIN_STATES) malformed()
        return AgentLogin(
            agentId = matchingAgent(value, agentId),
            requestId = value.requiredString("request_id"),
            state = state,
            verificationUrl = if (start) value.optionalString("verification_url") else null,
            userCode = if (start) value.optionalString("user_code") else null,
            expiresInSeconds = if (start) value.optionalPositiveInt("expires_in_seconds", MAX_LOGIN_EXPIRY_SECONDS) else null,
            pollIntervalSeconds = if (start) value.optionalPositiveInt("poll_interval_seconds", MAX_LOGIN_POLL_SECONDS) else null,
        )
    }

    private fun getJson(path: String): Map<String, JsonValue> {
        val connection = open(path)
        return try {
            connection.requestMethod = "GET"
            requestSigner.authorize(connection, "GET", path, EMPTY_BODY)
            readJson(connection)
        } finally {
            connection.disconnect()
        }
    }

    private fun postJson(path: String, body: ByteArray?): Map<String, JsonValue> {
        val connection = open(path)
        return try {
            connection.requestMethod = "POST"
            if (body == null) {
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(0)
                connection.setRequestProperty("Content-Type", "application/json")
                requestSigner.authorize(connection, "POST", path, EMPTY_BODY)
                connection.outputStream.close()
            } else {
                if (body.size > MAX_REQUEST_BYTES) failure(GatewayClientErrorCode.REQUEST_TOO_LARGE)
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(body.size)
                connection.setRequestProperty("Content-Type", "application/json")
                requestSigner.authorize(connection, "POST", path, body)
                connection.outputStream.use { it.write(body) }
            }
            readJson(connection)
        } finally {
            connection.disconnect()
        }
    }

    private fun readJson(connection: HttpURLConnection): Map<String, JsonValue> {
        val status = connection.responseCode
        if (status !in 200..299) throw httpFailure(connection, status)
        if (!connection.contentType.orEmpty().lowercase().startsWith("application/json")) malformed()
        return BoundedJson.parse(
            connection.inputStream.use { readLimited(it, MAX_RESPONSE_BYTES) },
            MAX_RESPONSE_BYTES,
        ).asObject()
    }

    private fun httpFailure(connection: HttpURLConnection, status: Int): GatewayClientException {
        val code = runCatching {
            connection.errorStream?.use { stream ->
                BoundedJson.parse(readLimited(stream, MAX_RESPONSE_BYTES), MAX_RESPONSE_BYTES)
                    .asObject()["error"]?.asObject()?.requiredString("code")
            }
        }.getOrNull()
        return GatewayClientException(GatewayClientErrorCode.HTTP_ERROR, status, code)
    }

    private fun open(path: String): HttpURLConnection {
        if (!allowedPath(path)) failure(GatewayClientErrorCode.INVALID_ENDPOINT)
        return try {
            transport.open(path)
        } catch (_: Exception) {
            failure(GatewayClientErrorCode.CONNECTION_FAILED)
        }
    }

    private fun parseStreamEvent(value: ByteArray, expectedAgent: AgentId): AgentTurnEvent {
        val item = BoundedJson.parse(value, MAX_STREAM_EVENT_BYTES).asObject()
        val type = item.requiredString("type")
        val id = item.requiredString("id")
        val agentId = matchingAgent(item, expectedAgent)
        return when (type) {
            "start" -> {
                if (item.containsKey("diagnostics")) malformed()
                AgentTurnEvent.Started(agentId, id, item.optionalString("conversation_id"))
            }
            "delta" -> {
                if (item.containsKey("diagnostics")) malformed()
                AgentTurnEvent.Delta(agentId, id, item.requiredString("text").takeIf { it.isNotEmpty() } ?: malformed())
            }
            "done" -> AgentTurnEvent.Completed(agentId, id, parseTurnDiagnostics(item, agentId))
            "error" -> AgentTurnEvent.Failed(
                agentId,
                id,
                item.requiredString("code"),
                parseTurnDiagnostics(item, agentId),
            )
            else -> malformed()
        }
    }

    private fun parseTurnDiagnostics(
        event: Map<String, JsonValue>,
        agentId: AgentId,
    ): AgentTurnDiagnostics? {
        val raw = event["diagnostics"] ?: return null
        if (agentId != AgentId.GROK) malformed()
        val value = raw.asObject()
        if (value.keys != TURN_DIAGNOSTIC_FIELDS) malformed()
        val result = AgentTurnDiagnostics(
            promptDispatchCount = value.requiredBoundedCount("prompt_dispatch_count", 1),
            visibleDeltaCount = value.requiredBoundedCount("visible_delta_count", MAX_STREAM_EVENTS),
            terminalCount = value.requiredBoundedCount("terminal_count", 1),
            cancelDispatchCount = value.requiredBoundedCount("cancel_dispatch_count", 1),
            retryClassification = value.requiredString("retry_classification"),
            retryAttempts = value.requiredBoundedCount("retry_attempts", MAX_RETRY_ATTEMPTS),
            retryMax = value.requiredBoundedCount("retry_max", MAX_RETRY_ATTEMPTS),
            toolEventCount = value.requiredBoundedCount("tool_event_count", MAX_PROFILE_EVENTS),
            subagentEventCount = value.requiredBoundedCount("subagent_event_count", MAX_PROFILE_EVENTS),
            mcpEventCount = value.requiredBoundedCount("mcp_event_count", MAX_PROFILE_EVENTS),
            filesystemEventCount = value.requiredBoundedCount("filesystem_event_count", MAX_PROFILE_EVENTS),
            terminalEventCount = value.requiredBoundedCount("terminal_event_count", MAX_PROFILE_EVENTS),
        )
        if (
            result.terminalCount != 1 ||
            result.retryClassification !in RETRY_CLASSIFICATIONS ||
            result.retryAttempts > result.retryMax && result.retryMax != 0
        ) malformed()
        return result
    }

    private fun parseSseEvent(lines: List<ByteArray>): ByteArray? {
        if (lines.isEmpty()) return null
        val size = lines.sumOf { it.size } + lines.size - 1
        if (size > MAX_STREAM_EVENT_BYTES) failure(GatewayClientErrorCode.MALFORMED_SSE)
        return ByteArrayOutputStream(size).also { output ->
            lines.forEachIndexed { index, line ->
                if (index > 0) output.write('\n'.code)
                output.write(line)
            }
        }.toByteArray()
    }

    private fun validateChatRequest(request: AgentGatewayChatRequest) {
        if (request.model.isBlank() || request.model.length > 512 || request.text.isBlank()) malformed()
        if (request.text.toByteArray(Charsets.UTF_8).size > MAX_TEXT_BYTES) {
            failure(GatewayClientErrorCode.REQUEST_TOO_LARGE)
        }
        request.conversationId?.let(::opaquePath)
        if (request.resumeExisting && request.conversationId == null) malformed()
    }

    private fun opaquePath(value: String): String {
        if (value.isBlank() || value.length > 512 || !OPAQUE_PATH.matches(value)) malformed()
        return value
    }

    private fun allowedPath(path: String): Boolean =
        path in EXACT_PATHS || AGENT_MANAGEMENT_PATH.matches(path) || LOGIN_STATUS_PATH.matches(path) ||
            LOGIN_CANCEL_PATH.matches(path) || ACTIVE_LOGIN_CANCEL_PATH.matches(path) || INTERRUPT_PATH.matches(path)

    private fun readLine(input: InputStream, maxBytes: Int): ByteArray? {
        val output = ByteArrayOutputStream()
        while (true) {
            val next = input.read()
            if (next < 0) return output.toByteArray().takeIf { it.isNotEmpty() }
            if (next == '\n'.code) {
                val value = output.toByteArray()
                return if (value.lastOrNull() == '\r'.code.toByte()) value.copyOf(value.size - 1) else value
            }
            output.write(next)
            if (output.size() > maxBytes) failure(GatewayClientErrorCode.STREAM_TOO_LARGE)
        }
    }

    private fun readLimited(input: InputStream, maxBytes: Int): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return output.toByteArray()
            output.write(buffer, 0, count)
            if (output.size() > maxBytes) failure(GatewayClientErrorCode.RESPONSE_TOO_LARGE)
        }
    }

    private fun ByteArray.hasPrefix(prefix: ByteArray): Boolean =
        size >= prefix.size && prefix.indices.all { this[it] == prefix[it] }

    private fun ByteArray.dropLeadingSpace(): ByteArray =
        if (firstOrNull() == ' '.code.toByte()) copyOfRange(1, size) else this

    private fun matchingAgent(value: Map<String, JsonValue>, expected: AgentId): AgentId =
        requiredAgent(value, "agent_id").also { if (it != expected) malformed() }

    private fun requiredAgent(value: Map<String, JsonValue>, field: String): AgentId =
        AgentId.fromWire(value.requiredString(field)) ?: malformed()

    private fun Map<String, JsonValue>.optionalPositiveInt(field: String, maximum: Int): Int? =
        get(field)?.let { value ->
            val number = (value as? JsonValue.NumberValue)?.value?.toIntOrNull() ?: malformed()
            number.takeIf { it in 1..maximum } ?: malformed()
        }

    private fun Map<String, JsonValue>.requiredBoundedCount(field: String, maximum: Int): Int =
        get(field)?.let { value ->
            val number = (value as? JsonValue.NumberValue)?.value?.toIntOrNull() ?: malformed()
            number.takeIf { it in 0..maximum } ?: malformed()
        } ?: malformed()

    private fun malformed(): Nothing = failure(GatewayClientErrorCode.MALFORMED_RESPONSE)

    private fun failure(code: GatewayClientErrorCode): Nothing = throw GatewayClientException(code)

    private companion object {
        const val MAX_REQUEST_BYTES = 32 * 1024
        const val MAX_RESPONSE_BYTES = 128 * 1024
        const val MAX_STREAM_EVENT_BYTES = 32 * 1024
        const val MAX_STREAM_BYTES = 512 * 1024
        const val MAX_LOGIN_EXPIRY_SECONDS = 60 * 60
        const val MAX_LOGIN_POLL_SECONDS = 60
        const val MAX_TEXT_BYTES = 16 * 1024
        const val MAX_MODELS = 128
        const val MAX_STREAM_EVENTS = 128
        const val MAX_RETRY_ATTEMPTS = 32
        const val MAX_PROFILE_EVENTS = 128
        val DATA_PREFIX = "data:".toByteArray(Charsets.US_ASCII)
        val COLON_PREFIX = ":".toByteArray(Charsets.US_ASCII)
        val DONE_MARKER = "[DONE]".toByteArray(Charsets.US_ASCII)
        val EMPTY_BODY = byteArrayOf()
        val OPAQUE_PATH = Regex("[A-Za-z0-9_-]+")
        val EXACT_PATHS = setOf("/healthz", "/v1/agents", "/internal/agents/select", "/v1/models", "/v1/chat/completions")
        val AGENT_MANAGEMENT_PATH = Regex("/internal/agents/(codex|grok)/(account|logout|login/device)")
        val LOGIN_STATUS_PATH = Regex("/internal/agents/(codex|grok)/login/[A-Za-z0-9_-]+")
        val LOGIN_CANCEL_PATH = Regex("/internal/agents/(codex|grok)/login/[A-Za-z0-9_-]+/cancel")
        val ACTIVE_LOGIN_CANCEL_PATH = Regex("/internal/agents/(codex|grok)/login/active/cancel")
        val INTERRUPT_PATH = Regex("/internal/agents/(codex|grok)/turn/[A-Za-z0-9_-]+/interrupt")
        val LOGIN_STATES = setOf("pending", "authenticated", "completed", "failed", "cancelled", "expired")
        val RETRY_CLASSIFICATIONS = setOf(
            "none",
            "pre_output",
            "post_output",
            "strict_blocked",
            "exhausted",
            "auth_failed",
            "failed",
        )
        val TURN_DIAGNOSTIC_FIELDS = setOf(
            "prompt_dispatch_count",
            "visible_delta_count",
            "terminal_count",
            "cancel_dispatch_count",
            "retry_classification",
            "retry_attempts",
            "retry_max",
            "tool_event_count",
            "subagent_event_count",
            "mcp_event_count",
            "filesystem_event_count",
            "terminal_event_count",
        )
    }
}

class AgentGatewayChatBackend(private val gatewayClient: AgentGatewayClient) {
    fun startTurn(request: AgentGatewayChatRequest): AgentGatewayChatTurn {
        val control = gatewayClient.newStreamControl(request.agentId)
        return AgentGatewayChatTurn(
            agentId = request.agentId,
            events = gatewayClient.stream(request, control).map { event ->
                control.observeRequestId(event.requestId)
                event
            },
            stopAction = { control.stop(gatewayClient) },
        )
    }
}

class AgentGatewayChatTurn internal constructor(
    val agentId: AgentId,
    val events: Flow<AgentTurnEvent>,
    private val stopAction: () -> Boolean,
) {
    fun stop(): Boolean = stopAction()
}
