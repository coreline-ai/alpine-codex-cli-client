package dev.alpine.codexclient.bridge

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.net.HttpURLConnection
import java.net.URI
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class GatewayTransportTest {
    @Test
    fun `Agent client keeps exact signed health contract through injected transport`() {
        val fixture = FixtureTransport(
            """{"runtime":"ready","gateway":"ready","selected_agent":"grok","backend_ready":true}""",
        )
        val signer = GatewayRequestSigner(
            secretProvider = GatewaySecretProvider { ByteArray(32) { 7 } },
            epochSeconds = { 1234L },
            nonceSource = { ByteArray(16) { 9 } },
        )

        val health = AgentGatewayClient(signer, fixture).health()

        assertEquals(AgentId.GROK, health.selectedAgent)
        assertEquals("GET", fixture.connection.requestMethod)
        assertEquals("/healthz", fixture.path)
        assertEquals("1", fixture.connection.headers[GatewayRequestSigner.HEADER_VERSION])
        assertEquals("1234", fixture.connection.headers[GatewayRequestSigner.HEADER_TIMESTAMP])
        assertEquals(0, fixture.connection.output.size())
        assertFalse(fixture.connection.redirectsEnabled)
        assertTrue(fixture.connection.disconnected)
    }

    @Test
    fun `Codex client keeps zero-length POST and never asks transport for another path`() {
        val fixture = FixtureTransport("""{"status":"logged_out"}""")
        val signer = GatewayRequestSigner(
            secretProvider = GatewaySecretProvider { ByteArray(32) { 3 } },
            epochSeconds = { 5678L },
            nonceSource = { ByteArray(16) { 4 } },
        )

        CodexGatewayClient(signer, fixture).logout()

        assertEquals("POST", fixture.connection.requestMethod)
        assertEquals("/internal/codex/logout", fixture.path)
        assertEquals("application/json", fixture.connection.headers["Content-Type"])
        assertEquals(0, fixture.connection.output.size())
        assertEquals(1, fixture.openCount)
        assertTrue(fixture.connection.disconnected)
    }

    private class FixtureTransport(private val response: String) : GatewayTransport {
        var openCount = 0
        lateinit var path: String
        lateinit var connection: FixtureConnection

        override fun open(path: String): HttpURLConnection {
            openCount += 1
            this.path = path
            return FixtureConnection(response.toByteArray()).also { connection = it }
        }
    }

    private class FixtureConnection(private val response: ByteArray) :
        HttpURLConnection(URI("http://127.0.0.1/fixture").toURL()) {
        val headers = linkedMapOf<String, String>()
        val output = ByteArrayOutputStream()
        var disconnected = false
        var redirectsEnabled = false

        override fun connect() {
            connected = true
        }

        override fun disconnect() {
            disconnected = true
        }

        override fun usingProxy(): Boolean = false

        override fun setInstanceFollowRedirects(followRedirects: Boolean) {
            redirectsEnabled = followRedirects
        }

        override fun setRequestProperty(key: String, value: String) {
            headers[key] = value
        }

        override fun getRequestProperty(key: String): String? = headers[key]

        override fun getOutputStream(): OutputStream = output

        override fun getResponseCode(): Int = HTTP_OK

        override fun getContentType(): String = "application/json"

        override fun getInputStream(): InputStream = ByteArrayInputStream(response)
    }
}
