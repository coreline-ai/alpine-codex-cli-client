package dev.alpine.codexclient.bridge

import java.net.HttpURLConnection
import java.security.MessageDigest
import java.security.SecureRandom
import java.time.Instant
import java.util.Base64
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

fun interface GatewaySecretProvider {
    /** Returns a fresh 32-byte copy. The signer clears it after each request. */
    fun currentSecret(): ByteArray
}

/** Pure canonical-v1 HMAC signer shared by every private-carrier management and SSE request. */
class GatewayRequestSigner(
    private val secretProvider: GatewaySecretProvider,
    private val epochSeconds: () -> Long = { Instant.now().epochSecond },
    private val nonceSource: () -> ByteArray = {
        ByteArray(NONCE_BYTES).also(SecureRandom()::nextBytes)
    },
) {
    fun signedHeaders(method: String, exactPath: String, body: ByteArray): Map<String, String> {
        require(method in METHODS)
        require(EXACT_PATH.matches(exactPath) && '?' !in exactPath && '#' !in exactPath)
        val nonce = nonceSource()
        require(nonce.size == NONCE_BYTES)
        val timestamp = epochSeconds()
        val bodyHash = sha256Hex(body)
        val encodedNonce = BASE64_URL.encodeToString(nonce)
        val canonical = "v1\n$method\n$exactPath\n$timestamp\n$encodedNonce\n$bodyHash"
            .toByteArray(Charsets.UTF_8)
        val secret = secretProvider.currentSecret()
        require(secret.size == SECRET_BYTES)
        val signature = try {
            val mac = Mac.getInstance(HMAC_SHA_256)
            mac.init(SecretKeySpec(secret, HMAC_SHA_256))
            BASE64_URL.encodeToString(mac.doFinal(canonical))
        } finally {
            secret.fill(0)
            nonce.fill(0)
        }
        return linkedMapOf(
            HEADER_VERSION to "1",
            HEADER_TIMESTAMP to timestamp.toString(),
            HEADER_NONCE to encodedNonce,
            HEADER_BODY_HASH to bodyHash,
            HEADER_SIGNATURE to signature,
        )
    }

    fun authorize(connection: HttpURLConnection, method: String, exactPath: String, body: ByteArray) {
        signedHeaders(method, exactPath, body).forEach(connection::setRequestProperty)
    }

    companion object {
        const val HEADER_VERSION = "X-Alpine-Auth-Version"
        const val HEADER_TIMESTAMP = "X-Alpine-Timestamp"
        const val HEADER_NONCE = "X-Alpine-Nonce"
        const val HEADER_BODY_HASH = "X-Alpine-Content-SHA256"
        const val HEADER_SIGNATURE = "X-Alpine-Signature"
        const val SECRET_BYTES = 32
        const val NONCE_BYTES = 16
        private const val HMAC_SHA_256 = "HmacSHA256"
        private val METHODS = setOf("GET", "POST")
        private val EXACT_PATH = Regex("/[A-Za-z0-9_./-]+")
        private val BASE64_URL = Base64.getUrlEncoder().withoutPadding()

        fun ephemeral(): GatewayRequestSigner {
            val secret = ByteArray(SECRET_BYTES).also(SecureRandom()::nextBytes)
            return GatewayRequestSigner(GatewaySecretProvider { secret.copyOf() })
        }

        fun sha256Hex(value: ByteArray): String = MessageDigest.getInstance("SHA-256")
            .digest(value)
            .joinToString("") { byte ->
                (byte.toInt() and 0xff).toString(16).padStart(2, '0')
            }
    }
}
