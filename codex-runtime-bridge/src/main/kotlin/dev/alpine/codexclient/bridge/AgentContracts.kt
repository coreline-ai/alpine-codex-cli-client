package dev.alpine.codexclient.bridge

/** Fixed Agent identifiers accepted on storage and Gateway wire boundaries. */
enum class AgentId(val wireValue: String) {
    CODEX("codex"),
    GROK("grok"),
    ;

    companion object {
        fun fromWire(value: String?): AgentId? = entries.firstOrNull { it.wireValue == value }
    }
}

/** Version rules for migrating the existing Codex-only encrypted conversation state. */
object AgentStorageSchema {
    const val CURRENT_VERSION = 3
    private const val CODEX_ONLY_VERSION = 1
    private const val AGENT_TAGGED_VERSION = 2

    fun parseVersion(value: Int?): Int? = when (value) {
        null -> CODEX_ONLY_VERSION
        CODEX_ONLY_VERSION, AGENT_TAGGED_VERSION, CURRENT_VERSION -> value
        else -> null
    }

    fun resolveAgentId(schemaVersion: Int, storedValue: String?): AgentId? = when (schemaVersion) {
        CODEX_ONLY_VERSION -> if (storedValue.isNullOrEmpty()) AgentId.CODEX else AgentId.fromWire(storedValue)
        AGENT_TAGGED_VERSION, CURRENT_VERSION -> AgentId.fromWire(storedValue)
        else -> null
    }
}

data class AgentCapabilities(
    val deviceOAuth: Boolean,
    val dynamicModels: Boolean,
    val streaming: Boolean,
    val stop: Boolean,
    val resume: Boolean,
)

data class AgentAccount(
    val agentId: AgentId,
    val authenticated: Boolean,
    val requiresAuth: Boolean,
)

data class AgentModel(
    val agentId: AgentId,
    val id: String,
    val displayName: String,
    val isDefault: Boolean,
)

data class AgentLogin(
    val agentId: AgentId,
    val requestId: String,
    val state: String,
    val verificationUrl: String? = null,
    val userCode: String? = null,
    val expiresInSeconds: Int? = null,
    val pollIntervalSeconds: Int? = null,
)

data class AgentConversationBinding(
    val agentId: AgentId,
    val conversationId: String,
    val backendSessionId: String,
    val modelId: String,
    val processGeneration: Long,
)

/** Content-free evidence emitted only with a Grok terminal stream event. */
data class AgentTurnDiagnostics(
    val promptDispatchCount: Int,
    val visibleDeltaCount: Int,
    val terminalCount: Int,
    val cancelDispatchCount: Int,
    val retryClassification: String,
    val retryAttempts: Int,
    val retryMax: Int,
    val toolEventCount: Int,
    val subagentEventCount: Int,
    val mcpEventCount: Int,
    val filesystemEventCount: Int,
    val terminalEventCount: Int,
)

sealed interface AgentTurnEvent {
    val agentId: AgentId
    val requestId: String

    data class Started(
        override val agentId: AgentId,
        override val requestId: String,
        val conversationId: String?,
    ) : AgentTurnEvent

    data class Delta(
        override val agentId: AgentId,
        override val requestId: String,
        val text: String,
    ) : AgentTurnEvent

    data class Completed(
        override val agentId: AgentId,
        override val requestId: String,
        val diagnostics: AgentTurnDiagnostics? = null,
    ) : AgentTurnEvent

    data class Failed(
        override val agentId: AgentId,
        override val requestId: String,
        val code: String,
        val diagnostics: AgentTurnDiagnostics? = null,
    ) : AgentTurnEvent
}
