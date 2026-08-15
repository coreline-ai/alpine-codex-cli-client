package dev.alpine.codexclient.bridge

import java.net.HttpURLConnection
import java.net.URI

/** Test-only Phase 1 wire fixture. No loopback implementation is packaged in the APK. */
object LoopbackTestGatewayTransport : GatewayTransport {
    override fun open(path: String): HttpURLConnection =
        (URI("http://127.0.0.1:8787$path").toURL().openConnection() as HttpURLConnection).apply {
            connectTimeout = 5_000
            readTimeout = 30_000
            useCaches = false
            instanceFollowRedirects = false
        }
}
