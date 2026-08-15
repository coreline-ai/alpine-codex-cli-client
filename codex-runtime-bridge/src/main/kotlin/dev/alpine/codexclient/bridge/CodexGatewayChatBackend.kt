package dev.alpine.codexclient.bridge

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/**
 * The only Android chat dispatch boundary. It delegates solely to the private UDS Codex
 * gateway and intentionally has no provider, endpoint, retry, or prompt-replay alternative.
 */
class CodexGatewayChatBackend(private val gatewayClient: CodexGatewayClient) {
    fun startTurn(request: GatewayChatRequest): CodexGatewayChatTurn {
        val control = gatewayClient.newStreamControl()
        return CodexGatewayChatTurn(
            events = gatewayClient.stream(request, control).map { event ->
                // The production client observes this while parsing SSE. Keeping it at the
                // dispatch boundary also preserves Stop for an in-process contract test double.
                control.observeRequestId(event.id)
                toChatEvent(event)
            },
            stopAction = { control.stop(gatewayClient) },
        )
    }

    private fun toChatEvent(event: GatewayStreamEvent): CodexGatewayChatEvent = when (event.type) {
        "start" -> CodexGatewayChatEvent.Started(event.id, event.conversationId)
        "delta" -> CodexGatewayChatEvent.Delta(event.id, event.text)
        "done" -> CodexGatewayChatEvent.Completed(event.id)
        "error" -> CodexGatewayChatEvent.Failed(event.id, event.code ?: "gateway_error")
        else -> throw GatewayClientException(GatewayClientErrorCode.MALFORMED_SSE)
    }
}

class CodexGatewayChatTurn internal constructor(
    val events: Flow<CodexGatewayChatEvent>,
    private val stopAction: () -> Boolean,
) {
    /** Explicit Stop is idempotent; cancellation follows the same one-shot action. */
    fun stop(): Boolean = stopAction()
}

sealed interface CodexGatewayChatEvent {
    data class Started(val requestId: String, val conversationId: String?) : CodexGatewayChatEvent
    data class Delta(val requestId: String, val text: String) : CodexGatewayChatEvent
    data class Completed(val requestId: String) : CodexGatewayChatEvent
    data class Failed(val requestId: String, val code: String) : CodexGatewayChatEvent
}
