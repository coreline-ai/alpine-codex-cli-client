package dev.alpine.codexclient

import dev.alpine.codexclient.bridge.AgentCapabilities
import dev.alpine.codexclient.bridge.AgentGatewayHealth
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.GatewayAgent
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentCatalogRecoveryTest {
    @Test
    fun preservesDiscoveredCatalogBeforeBackendReadinessFailure() {
        val discovered = listOf(
            GatewayAgent(AgentId.CODEX, selected = false, ready = false, capabilities = capabilities),
            GatewayAgent(AgentId.GROK, selected = true, ready = false, capabilities = capabilities),
        )

        val recovered = AgentChatUiState(selectedAgentId = AgentId.GROK)
            .withDiscoveredAgents(AgentId.GROK, discovered)

        assertEquals(discovered, recovered.agents)
    }

    @Test
    fun ignoresEmptyOrStaleDiscovery() {
        val original = AgentChatUiState(
            selectedAgentId = AgentId.GROK,
            agents = listOf(
                GatewayAgent(AgentId.GROK, selected = true, ready = false, capabilities = capabilities),
            ),
        )

        assertTrue(original === original.withDiscoveredAgents(AgentId.GROK, emptyList()))
        assertTrue(
            original === original.withDiscoveredAgents(
                AgentId.CODEX,
                listOf(GatewayAgent(AgentId.CODEX, selected = true, ready = true, capabilities = capabilities)),
            ),
        )
    }

    @Test
    fun requestsSelectionForMismatchOrFailedSelectedBackend() {
        assertTrue(health(AgentId.CODEX, ready = true).requiresSelection(AgentId.GROK))
        assertTrue(health(AgentId.GROK, ready = false).requiresSelection(AgentId.GROK))
        assertTrue(!health(AgentId.GROK, ready = true).requiresSelection(AgentId.GROK))
    }

    private fun health(selected: AgentId, ready: Boolean) = AgentGatewayHealth(
        runtime = "running",
        gateway = "running",
        selectedAgent = selected,
        backendReady = ready,
    )

    private val capabilities = AgentCapabilities(
        deviceOAuth = true,
        dynamicModels = true,
        streaming = true,
        stop = true,
        resume = true,
    )
}
