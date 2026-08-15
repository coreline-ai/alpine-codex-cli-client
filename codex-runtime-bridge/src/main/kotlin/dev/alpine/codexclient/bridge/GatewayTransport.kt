package dev.alpine.codexclient.bridge

import java.net.HttpURLConnection

/**
 * Compile-time transport seam for the app-owned Gateway.
 *
 * Callers still validate every exact path and own all request/response parsing. Implementations
 * only create one connection and must not retry, redirect, or fall back to another endpoint.
 */
fun interface GatewayTransport {
    fun open(path: String): HttpURLConnection
}

/** Fail-closed default used only by test subclasses that override all transport operations. */
object DisabledGatewayTransport : GatewayTransport {
    override fun open(path: String): HttpURLConnection =
        throw GatewayClientException(GatewayClientErrorCode.CONNECTION_FAILED)
}
