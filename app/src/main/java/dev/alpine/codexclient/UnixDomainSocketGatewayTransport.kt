package dev.alpine.codexclient

import android.net.LocalSocket
import android.net.LocalSocketAddress
import dev.alpine.codexclient.bridge.GatewayClientErrorCode
import dev.alpine.codexclient.bridge.GatewayClientException
import dev.alpine.codexclient.bridge.GatewayRequestSigner
import dev.alpine.codexclient.bridge.GatewayTransport
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileNotFoundException
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.util.Collections

/** App-private HTTP/1.1 carrier over one filesystem Unix domain socket. */
internal class UnixDomainSocketGatewayTransport(
    socketFile: File,
    private val expectedPeerUid: Int,
) : GatewayTransport {
    private val canonicalSocketPath = socketFile.canonicalPath

    init {
        require(expectedPeerUid >= 0)
        require(socketFile.name == CodexRuntimePaths.GATEWAY_SOCKET_FILE)
        require(socketFile.parentFile?.isDirectory == true)
        require(socketFile.canonicalFile.parentFile == socketFile.parentFile?.canonicalFile)
        require(canonicalSocketPath.toByteArray(Charsets.UTF_8).size <= MAX_SOCKET_PATH_BYTES)
    }

    override fun open(path: String): HttpURLConnection {
        if (!SAFE_PATH.matches(path) || '?' in path || '#' in path) {
            throw GatewayClientException(GatewayClientErrorCode.INVALID_ENDPOINT)
        }
        return UnixHttpURLConnection(path, canonicalSocketPath, expectedPeerUid).apply {
            connectTimeout = CONNECT_TIMEOUT_MILLIS
            readTimeout = READ_TIMEOUT_MILLIS
            useCaches = false
            instanceFollowRedirects = false
        }
    }

    private class UnixHttpURLConnection(
        private val exactPath: String,
        private val socketPath: String,
        private val expectedPeerUid: Int,
    ) : HttpURLConnection(URI("http://localhost$exactPath").toURL()) {
        private val requestHeaders = linkedMapOf<String, String>()
        private val requestBody = ByteArrayOutputStream()
        private var localSocket: LocalSocket? = null
        private var responseInput: InputStream? = null
        private var responseHeaders: Map<String, List<String>> = emptyMap()
        private var parsedStatus: Int? = null
        private var executed = false
        private var closed = false
        private var declaredLength: Long? = null

        @Synchronized
        override fun connect() {
            ensureExecuted()
        }

        @Synchronized
        override fun disconnect() {
            if (closed) return
            closed = true
            runCatching { responseInput?.close() }
            runCatching { localSocket?.close() }
            responseInput = null
            localSocket = null
        }

        override fun usingProxy(): Boolean = false

        @Synchronized
        override fun setRequestProperty(key: String, value: String) {
            checkMutableRequest()
            if (key !in ALLOWED_REQUEST_HEADERS || !safeHeaderValue(value)) {
                throw IllegalArgumentException("invalid gateway request header")
            }
            requestHeaders[key] = value
        }

        @Synchronized
        override fun addRequestProperty(key: String, value: String) {
            if (requestHeaders.containsKey(key)) {
                throw IllegalArgumentException("duplicate gateway request header")
            }
            setRequestProperty(key, value)
        }

        @Synchronized
        override fun getRequestProperty(key: String): String? = requestHeaders[key]

        @Synchronized
        override fun getRequestProperties(): Map<String, List<String>> =
            Collections.unmodifiableMap(requestHeaders.mapValues { listOf(it.value) })

        @Synchronized
        override fun setFixedLengthStreamingMode(contentLength: Int) {
            if (contentLength < 0) throw IllegalArgumentException("negative request length")
            checkMutableRequest()
            declaredLength = contentLength.toLong()
        }

        @Synchronized
        override fun setFixedLengthStreamingMode(contentLength: Long) {
            if (contentLength < 0 || contentLength > Int.MAX_VALUE) {
                throw IllegalArgumentException("invalid request length")
            }
            checkMutableRequest()
            declaredLength = contentLength
        }

        @Synchronized
        override fun getOutputStream(): OutputStream {
            checkMutableRequest()
            if (!doOutput || requestMethod != "POST") {
                throw IOException("gateway request body is not allowed")
            }
            return requestBody
        }

        @Synchronized
        override fun getResponseCode(): Int {
            ensureExecuted()
            return checkNotNull(parsedStatus)
        }

        @Synchronized
        override fun getContentType(): String? {
            ensureExecuted()
            return responseHeaders["content-type"]?.singleOrNull()
        }

        @Synchronized
        override fun getHeaderField(name: String?): String? {
            ensureExecuted()
            return name?.lowercase()?.let { responseHeaders[it]?.singleOrNull() }
        }

        @Synchronized
        override fun getHeaderFields(): Map<String, List<String>> {
            ensureExecuted()
            return responseHeaders
        }

        @Synchronized
        override fun getInputStream(): InputStream {
            ensureExecuted()
            if (checkNotNull(parsedStatus) !in 200..299) throw FileNotFoundException()
            return checkNotNull(responseInput)
        }

        @Synchronized
        override fun getErrorStream(): InputStream? {
            if (!executed) return null
            return responseInput.takeIf { checkNotNull(parsedStatus) !in 200..299 }
        }

        private fun checkMutableRequest() {
            if (executed || connected || closed) throw IllegalStateException("gateway request already committed")
        }

        private fun ensureExecuted() {
            if (executed) return
            if (closed) throw GatewayClientException(GatewayClientErrorCode.CONNECTION_FAILED)
            executed = true
            try {
                val method = requestMethod
                if (method !in setOf("GET", "POST")) throw IOException("gateway method rejected")
                val body = requestBody.toByteArray()
                if (method == "GET" && (doOutput || body.isNotEmpty())) {
                    throw IOException("GET body rejected")
                }
                if (method == "POST" && (!doOutput || declaredLength != body.size.toLong())) {
                    throw IOException("POST length mismatch")
                }
                if (body.size > MAX_REQUEST_BYTES) throw IOException("request too large")

                val socket = LocalSocket().also { localSocket = it }
                socket.connect(
                    LocalSocketAddress(socketPath, LocalSocketAddress.Namespace.FILESYSTEM),
                )
                socket.soTimeout = readTimeout.coerceAtLeast(MIN_READ_TIMEOUT_MILLIS)
                val peer = socket.peerCredentials
                if (peer.uid != expectedPeerUid) throw IOException("gateway peer rejected")
                connected = true

                val output = socket.outputStream
                output.write("$method $exactPath HTTP/1.1\r\n".toByteArray(Charsets.US_ASCII))
                output.write("Host: $FIXED_HOST\r\n".toByteArray(Charsets.US_ASCII))
                output.write("Connection: close\r\n".toByteArray(Charsets.US_ASCII))
                requestHeaders.forEach { (key, value) ->
                    output.write("$key: $value\r\n".toByteArray(Charsets.US_ASCII))
                }
                if (method == "POST") {
                    output.write("Content-Length: ${body.size}\r\n".toByteArray(Charsets.US_ASCII))
                }
                output.write("\r\n".toByteArray(Charsets.US_ASCII))
                if (body.isNotEmpty()) output.write(body)
                output.flush()

                parseResponse(BufferedInputStream(socket.inputStream))
            } catch (error: GatewayClientException) {
                disconnect()
                throw error
            } catch (_: Exception) {
                disconnect()
                throw GatewayClientException(GatewayClientErrorCode.CONNECTION_FAILED)
            }
        }

        private fun parseResponse(input: BufferedInputStream) {
            val statusLine = readAsciiLine(input, MAX_HEADER_LINE_BYTES)
                ?: throw IOException("missing response status")
            val match = STATUS_LINE.matchEntire(statusLine) ?: throw IOException("invalid response status")
            parsedStatus = match.groupValues[1].toInt()

            val values = linkedMapOf<String, MutableList<String>>()
            var headerBytes = statusLine.length + 2
            var headerCount = 0
            while (true) {
                val line = readAsciiLine(input, MAX_HEADER_LINE_BYTES)
                    ?: throw IOException("truncated response headers")
                headerBytes += line.length + 2
                if (headerBytes > MAX_RESPONSE_HEADER_BYTES) throw IOException("response headers too large")
                if (line.isEmpty()) break
                if (++headerCount > MAX_RESPONSE_HEADERS || line.firstOrNull()?.isWhitespace() == true) {
                    throw IOException("invalid response headers")
                }
                val separator = line.indexOf(':')
                if (separator <= 0) throw IOException("invalid response header")
                val name = line.substring(0, separator).lowercase()
                val value = line.substring(separator + 1).trim()
                if (!HEADER_NAME.matches(name) || !safeHeaderValue(value)) {
                    throw IOException("invalid response header")
                }
                values.getOrPut(name) { mutableListOf() }.add(value)
            }
            if (values.containsKey("transfer-encoding")) throw IOException("transfer encoding rejected")
            if ((values["content-type"]?.size ?: 0) > 1 || (values["content-length"]?.size ?: 0) > 1) {
                throw IOException("duplicate response header")
            }
            val contentLength = values["content-length"]?.singleOrNull()?.toLongOrNull()
            if (contentLength != null && contentLength !in 0..MAX_WIRE_RESPONSE_BYTES.toLong()) {
                throw IOException("invalid response length")
            }
            responseHeaders = Collections.unmodifiableMap(values.mapValues { it.value.toList() })
            responseInput = contentLength?.let { ExactLengthInputStream(input, it) } ?: input
        }

        private class ExactLengthInputStream(
            private val delegate: InputStream,
            length: Long,
        ) : InputStream() {
            private var remaining = length

            override fun read(): Int {
                if (remaining == 0L) return -1
                val value = delegate.read()
                if (value < 0) throw IOException("truncated response body")
                remaining -= 1
                return value
            }

            override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
                if (remaining == 0L) return -1
                val requested = minOf(length.toLong(), remaining).toInt()
                val count = delegate.read(buffer, offset, requested)
                if (count < 0) throw IOException("truncated response body")
                remaining -= count
                return count
            }

            override fun close() = delegate.close()
        }

        private companion object {
            val STATUS_LINE = Regex("HTTP/1\\.[01] ([1-5][0-9]{2})(?: [\\x20-\\x7e]*)?")
            val HEADER_NAME = Regex("[a-z0-9-]{1,64}")
        }
    }

    private companion object {
        const val FIXED_HOST = "127.0.0.1:8787"
        const val CONNECT_TIMEOUT_MILLIS = 5_000
        const val READ_TIMEOUT_MILLIS = 30_000
        const val MIN_READ_TIMEOUT_MILLIS = 1_000
        // Linux sockaddr_un.sun_path is 108 bytes including the trailing NUL.
        const val MAX_SOCKET_PATH_BYTES = 107
        const val MAX_REQUEST_BYTES = 32 * 1024
        const val MAX_WIRE_RESPONSE_BYTES = 512 * 1024
        const val MAX_HEADER_LINE_BYTES = 8 * 1024
        const val MAX_RESPONSE_HEADER_BYTES = 16 * 1024
        const val MAX_RESPONSE_HEADERS = 32
        val SAFE_PATH = Regex("/[A-Za-z0-9_./-]+")
        val ALLOWED_REQUEST_HEADERS = setOf(
            "Content-Type",
            "Accept",
            GatewayRequestSigner.HEADER_VERSION,
            GatewayRequestSigner.HEADER_TIMESTAMP,
            GatewayRequestSigner.HEADER_NONCE,
            GatewayRequestSigner.HEADER_BODY_HASH,
            GatewayRequestSigner.HEADER_SIGNATURE,
        )

        fun safeHeaderValue(value: String): Boolean =
            value.length <= 4096 && value.all { it == '\t' || it.code in 0x20..0x7e }

        fun readAsciiLine(input: InputStream, maximum: Int): String? {
            val output = ByteArrayOutputStream()
            while (true) {
                val next = input.read()
                if (next < 0) return output.toByteArray().takeIf { it.isNotEmpty() }?.toString(Charsets.US_ASCII)
                if (next == '\n'.code) {
                    val value = output.toByteArray()
                    val length = if (value.lastOrNull() == '\r'.code.toByte()) value.size - 1 else value.size
                    if (value.copyOf(length).any { byte -> byte.toInt() and 0xff !in 0x20..0x7e }) {
                        throw IOException("non-ASCII response header")
                    }
                    return value.copyOf(length).toString(Charsets.US_ASCII)
                }
                output.write(next)
                if (output.size() > maximum) throw IOException("response header line too large")
            }
        }
    }
}
