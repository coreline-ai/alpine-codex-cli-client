package dev.alpine.codexclient.bridge

import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class GatewayRequestSignerTest {
    private val secret = ByteArray(32) { it.toByte() }
    private val nonce = ByteArray(16) { (it + 16).toByte() }

    @Test
    fun canonicalV1MatchesIndependentGoldenCalculation() {
        val signer = GatewayRequestSigner(
            GatewaySecretProvider { secret.copyOf() },
            epochSeconds = { 1_700_000_000L },
            nonceSource = { nonce.copyOf() },
        )
        val body = "{\"agent_id\":\"grok\"}".toByteArray()
        val headers = signer.signedHeaders("POST", "/internal/agents/select", body)
        val bodyHash = GatewayRequestSigner.sha256Hex(body)
        val encodedNonce = Base64.getUrlEncoder().withoutPadding().encodeToString(nonce)
        val canonical = "v1\nPOST\n/internal/agents/select\n1700000000\n$encodedNonce\n$bodyHash"
        val mac = Mac.getInstance("HmacSHA256").apply {
            init(SecretKeySpec(secret, "HmacSHA256"))
        }
        val expected = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(mac.doFinal(canonical.toByteArray()))

        assertEquals("1", headers[GatewayRequestSigner.HEADER_VERSION])
        assertEquals("1700000000", headers[GatewayRequestSigner.HEADER_TIMESTAMP])
        assertEquals(encodedNonce, headers[GatewayRequestSigner.HEADER_NONCE])
        assertEquals(bodyHash, headers[GatewayRequestSigner.HEADER_BODY_HASH])
        assertEquals(expected, headers[GatewayRequestSigner.HEADER_SIGNATURE])
    }

    @Test
    fun methodPathAndBodyAreBoundIndependently() {
        fun sign(method: String, path: String, body: ByteArray) = GatewayRequestSigner(
            GatewaySecretProvider { secret.copyOf() },
            epochSeconds = { 9L },
            nonceSource = { nonce.copyOf() },
        ).signedHeaders(method, path, body).getValue(GatewayRequestSigner.HEADER_SIGNATURE)

        val baseline = sign("POST", "/v1/chat/completions", "a".toByteArray())
        assertNotEquals(baseline, sign("GET", "/v1/chat/completions", "a".toByteArray()))
        assertNotEquals(baseline, sign("POST", "/healthz", "a".toByteArray()))
        assertNotEquals(baseline, sign("POST", "/v1/chat/completions", "b".toByteArray()))
    }

    @Test
    fun queryAndMalformedSecretFailClosed() {
        val signer = GatewayRequestSigner(
            GatewaySecretProvider { ByteArray(31) },
            epochSeconds = { 1L },
            nonceSource = { nonce.copyOf() },
        )
        assertThrows(IllegalArgumentException::class.java) {
            signer.signedHeaders("GET", "/v1/models?agent_id=grok", byteArrayOf())
        }
        assertThrows(IllegalArgumentException::class.java) {
            signer.signedHeaders("GET", "/healthz", byteArrayOf())
        }
    }
}
