package dev.alpine.codexclient.bridge

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.withContext

enum class GatewayClientErrorCode {
    INVALID_ENDPOINT,
    REQUEST_TOO_LARGE,
    RESPONSE_TOO_LARGE,
    MALFORMED_RESPONSE,
    MALFORMED_SSE,
    STREAM_TOO_LARGE,
    HTTP_ERROR,
    CONNECTION_FAILED,
    CANCELLED,
}

class GatewayClientException(
    val errorCode: GatewayClientErrorCode,
    val statusCode: Int? = null,
    val gatewayCode: String? = null,
) : RuntimeException(errorCode.name)

data class GatewayHealth(val runtime: String, val gateway: String, val codex: String)

data class GatewayAccount(val authenticated: Boolean, val requiresOpenaiAuth: Boolean)

data class GatewayLoginStart(
    val loginId: String,
    val verificationUrl: String,
    val userCode: String,
    val expiresInSeconds: Int,
    val pollIntervalSeconds: Int,
)

data class GatewayLoginStatus(val loginId: String, val status: String)

data class GatewayModel(val id: String, val displayName: String, val isDefault: Boolean)

data class GatewayChatRequest(
    val conversationId: String?,
    val model: String,
    val text: String,
    val resumeExisting: Boolean = false,
)

data class GatewayStreamEvent(
    val id: String,
    val type: String,
    val text: String = "",
    val code: String? = null,
    val conversationId: String? = null,
)

/** One-shot Stop coordinator shared by explicit UI Stop and coroutine cancellation. */
class GatewayStreamControl internal constructor() {
    private val stopped = AtomicBoolean(false)
    @Volatile private var requestId: String? = null

    internal fun observeRequestId(value: String) {
        if (requestId == null) requestId = value
    }

    fun stop(client: CodexGatewayClient): Boolean {
        val id = requestId ?: return false
        if (!stopped.compareAndSet(false, true)) return false
        client.interrupt(id)
        return true
    }
}

/**
 * Bounded client for the one gateway address owned by this app. It has no configuration surface
 * for remote endpoints, headers, credentials, alternate transports, retries, or fallbacks.
 */
open class CodexGatewayClient(
    private val requestSigner: GatewayRequestSigner = GatewayRequestSigner.ephemeral(),
    private val transport: GatewayTransport = DisabledGatewayTransport,
) : GatewayRuntimeHealthClient {

    override fun isRuntimeHealthy(): Boolean = health().let {
        it.runtime == "ready" && it.gateway == "ready" && it.codex == "ready"
    }

    open fun health(): GatewayHealth {
        val objectValue = getJson("/healthz")
        return GatewayHealth(
            runtime = objectValue.requiredString("runtime"),
            gateway = objectValue.requiredString("gateway"),
            codex = objectValue.requiredString("codex"),
        )
    }

    open fun account(): GatewayAccount {
        val objectValue = getJson("/internal/codex/account")
        return GatewayAccount(
            authenticated = objectValue.requiredBoolean("authenticated"),
            requiresOpenaiAuth = objectValue.requiredBoolean("requires_openai_auth"),
        )
    }

    open fun startDeviceLogin(): GatewayLoginStart {
        val objectValue = postJson("/internal/codex/login/device", null)
        return GatewayLoginStart(
            loginId = objectValue.requiredString("login_id"),
            verificationUrl = objectValue.requiredString("verification_url"),
            userCode = objectValue.requiredString("user_code"),
            expiresInSeconds = objectValue.requiredPositiveInt("expires_in_seconds", MAX_LOGIN_EXPIRY_SECONDS),
            pollIntervalSeconds = objectValue.requiredPositiveInt("poll_interval_seconds", MAX_LOGIN_POLL_SECONDS),
        )
    }

    open fun loginStatus(loginId: String): GatewayLoginStatus {
        val objectValue = getJson("/internal/codex/login/${opaquePath(loginId)}")
        return GatewayLoginStatus(
            loginId = objectValue.requiredString("login_id"),
            status = objectValue.requiredString("status"),
        )
    }

    open fun cancelLogin(loginId: String): GatewayLoginStatus {
        val objectValue = postJson("/internal/codex/login/${opaquePath(loginId)}/cancel", null)
        return GatewayLoginStatus(
            loginId = objectValue.requiredString("login_id"),
            status = objectValue.requiredString("status"),
        )
    }

    /** Cancels only a recovered pending login; it never returns its opaque ID or Device Code. */
    open fun cancelActiveDeviceLogin() {
        val status = postJson("/internal/codex/login/active/cancel", null).requiredString("status")
        if (status != "cancelled") throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
    }

    open fun logout() {
        val status = postJson("/internal/codex/logout", null).requiredString("status")
        if (status != "logged_out") throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
    }

    open fun models(): List<GatewayModel> {
        val objectValue = getJson("/v1/models")
        if (objectValue.requiredString("object") != "list") {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
        }
        return (objectValue["data"] ?: throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE))
            .asArray()
            .map { item ->
                val model = item.asObject()
                GatewayModel(
                    id = model.requiredString("id"),
                    displayName = model.requiredString("display_name"),
                    isDefault = model.requiredBoolean("is_default"),
                )
            }
    }

    open fun newStreamControl(): GatewayStreamControl = GatewayStreamControl()

    open fun stream(
        request: GatewayChatRequest,
        control: GatewayStreamControl = newStreamControl(),
    ): Flow<GatewayStreamEvent> = flow {
        validateChatRequest(request)
        val values = linkedMapOf<String, JsonValue>(
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
        if (body.size > MAX_REQUEST_BYTES) throw GatewayClientException(GatewayClientErrorCode.REQUEST_TOO_LARGE)
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
                throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
            }
            val dataLines = mutableListOf<ByteArray>()
            var totalBytes = 0
            connection.inputStream.use { input ->
                while (true) {
                    val line = readLine(input, MAX_STREAM_EVENT_BYTES) ?: break
                    totalBytes += line.size + 1
                    if (totalBytes > MAX_STREAM_BYTES) throw GatewayClientException(GatewayClientErrorCode.STREAM_TOO_LARGE)
                    if (line.isEmpty()) {
                        val event = parseSseEvent(dataLines)
                        dataLines.clear()
                        if (event == null) continue
                        if (event.contentEquals(DONE_MARKER)) {
                            completed = true
                            break
                        }
                        val parsed = parseStreamEvent(event)
                        control.observeRequestId(parsed.id)
                        emit(parsed)
                    } else if (line.hasPrefix(DATA_PREFIX)) {
                        val value = line.copyOfRange(DATA_PREFIX.size, line.size).dropLeadingSpace()
                        val eventSize = dataLines.sumOf { it.size } + value.size
                        if (eventSize > MAX_STREAM_EVENT_BYTES) throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
                        dataLines += value
                    } else if (line.hasPrefix(COLON_PREFIX)) {
                        continue
                    } else {
                        throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
                    }
                }
            }
            if (!completed) {
                currentCoroutineContext().ensureActive()
                throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
            }
        } catch (error: CancellationException) {
            throw error
        } catch (error: GatewayClientException) {
            throw error
        } catch (_: Exception) {
            currentCoroutineContext().ensureActive()
            throw GatewayClientException(GatewayClientErrorCode.CONNECTION_FAILED)
        } finally {
            cancellationHandle.dispose()
            activeConnection.getAndSet(null)?.disconnect()
            if (!completed) {
                withContext(NonCancellable) {
                    runCatching { control.stop(this@CodexGatewayClient) }
                }
            }
        }
    }

    open fun interrupt(requestId: String) {
        opaquePath(requestId)
        val status = postJson("/internal/codex/turn/$requestId/interrupt", null).requiredString("status")
        if (status != "interrupt_requested") throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
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
            if (body != null) {
                if (body.size > MAX_REQUEST_BYTES) throw GatewayClientException(GatewayClientErrorCode.REQUEST_TOO_LARGE)
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(body.size)
                connection.setRequestProperty("Content-Type", "application/json")
                requestSigner.authorize(connection, "POST", path, body)
                connection.outputStream.use { it.write(body) }
            } else {
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(0)
                connection.setRequestProperty("Content-Type", "application/json")
                requestSigner.authorize(connection, "POST", path, EMPTY_BODY)
                connection.outputStream.close()
            }
            readJson(connection)
        } finally {
            connection.disconnect()
        }
    }

    private fun readJson(connection: HttpURLConnection): Map<String, JsonValue> {
        val status = connection.responseCode
        if (status !in 200..299) throw httpFailure(connection, status)
        if (!connection.contentType.orEmpty().lowercase().startsWith("application/json")) {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
        }
        val bytes = connection.inputStream.use { readLimited(it, MAX_RESPONSE_BYTES) }
        return BoundedJson.parse(bytes, MAX_RESPONSE_BYTES).asObject()
    }

    private fun httpFailure(connection: HttpURLConnection, status: Int): GatewayClientException {
        val code = runCatching {
            connection.errorStream?.use { stream ->
                BoundedJson.parse(readLimited(stream, MAX_RESPONSE_BYTES), MAX_RESPONSE_BYTES)
                    .asObject()["error"]
                    ?.asObject()
                    ?.requiredString("code")
            }
        }.getOrNull()
        return GatewayClientException(GatewayClientErrorCode.HTTP_ERROR, status, code)
    }

    private fun open(path: String): HttpURLConnection {
        if (path !in ALLOWED_PATHS && !path.matches(LOGIN_STATUS_PATH) && !path.matches(LOGIN_CANCEL_PATH) && !path.matches(INTERRUPT_PATH)) {
            throw GatewayClientException(GatewayClientErrorCode.INVALID_ENDPOINT)
        }
        return try {
            transport.open(path)
        } catch (_: Exception) {
            throw GatewayClientException(GatewayClientErrorCode.CONNECTION_FAILED)
        }
    }

    private fun parseStreamEvent(value: ByteArray): GatewayStreamEvent {
        val objectValue = BoundedJson.parse(value, MAX_STREAM_EVENT_BYTES).asObject()
        val type = objectValue.requiredString("type")
        if (type !in setOf("start", "delta", "done", "error")) {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
        }
        val event = GatewayStreamEvent(
            id = objectValue.requiredString("id"),
            type = type,
            text = objectValue.optionalString("text") ?: "",
            code = objectValue.optionalString("code"),
            conversationId = objectValue.optionalString("conversation_id"),
        )
        if (type == "delta" && event.text.isEmpty()) throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
        return event
    }

    private fun parseSseEvent(lines: List<ByteArray>): ByteArray? {
        if (lines.isEmpty()) return null
        val size = lines.sumOf { it.size } + (lines.size - 1)
        if (size > MAX_STREAM_EVENT_BYTES) throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
        val output = ByteArrayOutputStream(size)
        lines.forEachIndexed { index, value ->
            if (index > 0) output.write('\n'.code)
            output.write(value)
        }
        return output.toByteArray()
    }

    private fun validateChatRequest(request: GatewayChatRequest) {
        if (request.model.isBlank() || request.model.length > 256 || request.text.isBlank()) {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
        }
        if (request.text.toByteArray(Charsets.UTF_8).size > MAX_TEXT_BYTES) {
            throw GatewayClientException(GatewayClientErrorCode.REQUEST_TOO_LARGE)
        }
        request.conversationId?.let(::opaquePath)
        if (request.resumeExisting && request.conversationId == null) {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
        }
    }

    private fun opaquePath(value: String): String {
        if (value.isBlank() || value.length > 4096 || !OPAQUE_PATH.matches(value)) {
            throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
        }
        return value
    }

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
            if (output.size() > maxBytes) throw GatewayClientException(GatewayClientErrorCode.STREAM_TOO_LARGE)
        }
    }

    private fun readLimited(input: InputStream, maxBytes: Int): ByteArray {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(4096)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) return output.toByteArray()
            output.write(buffer, 0, count)
            if (output.size() > maxBytes) throw GatewayClientException(GatewayClientErrorCode.RESPONSE_TOO_LARGE)
        }
    }

    private fun ByteArray.hasPrefix(prefix: ByteArray): Boolean = size >= prefix.size && prefix.indices.all { this[it] == prefix[it] }

    private fun ByteArray.dropLeadingSpace(): ByteArray = if (firstOrNull() == ' '.code.toByte()) copyOfRange(1, size) else this

    private companion object {
        const val MAX_REQUEST_BYTES = 32 * 1024
        const val MAX_RESPONSE_BYTES = 128 * 1024
        const val MAX_STREAM_EVENT_BYTES = 32 * 1024
        const val MAX_STREAM_BYTES = 512 * 1024
        const val MAX_LOGIN_EXPIRY_SECONDS = 60 * 60
        const val MAX_LOGIN_POLL_SECONDS = 60
        const val MAX_TEXT_BYTES = 16 * 1024
        val DATA_PREFIX = "data:".toByteArray(Charsets.US_ASCII)
        val COLON_PREFIX = ":".toByteArray(Charsets.US_ASCII)
        val DONE_MARKER = "[DONE]".toByteArray(Charsets.US_ASCII)
        val EMPTY_BODY = byteArrayOf()
        val OPAQUE_PATH = Regex("[A-Za-z0-9_-]+")
        val ALLOWED_PATHS = setOf(
            "/healthz",
            "/internal/codex/account",
            "/internal/codex/login/device",
            "/internal/codex/logout",
            "/v1/models",
            "/v1/chat/completions",
        )
        val LOGIN_STATUS_PATH = Regex("/internal/codex/login/[A-Za-z0-9_-]+")
        val LOGIN_CANCEL_PATH = Regex("/internal/codex/login/[A-Za-z0-9_-]+/cancel")
        val INTERRUPT_PATH = Regex("/internal/codex/turn/[A-Za-z0-9_-]+/interrupt")
    }
}
