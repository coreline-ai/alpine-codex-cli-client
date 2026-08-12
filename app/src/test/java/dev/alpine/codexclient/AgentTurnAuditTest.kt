package dev.alpine.codexclient

import dev.alpine.codexclient.bridge.AgentTurnDiagnostics
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class AgentTurnAuditTest {
    @Test
    fun `redacted audit line contains only fixed labels enums and counts`() {
        val value = AgentTurnDiagnostics(
            promptDispatchCount = 1,
            visibleDeltaCount = 2,
            terminalCount = 1,
            cancelDispatchCount = 0,
            retryClassification = "none",
            retryAttempts = 0,
            retryMax = 0,
            toolEventCount = 0,
            subagentEventCount = 0,
            mcpEventCount = 0,
            filesystemEventCount = 0,
            terminalEventCount = 0,
        )

        val line = AgentTurnAudit.format("done", value)

        assertEquals(
            "agent=grok outcome=done prompt_dispatch=1 visible_delta=2 terminal=1 " +
                "cancel=0 retry=none retry_attempts=0 retry_max=0 profile_tool=0 " +
                "profile_subagent=0 profile_mcp=0 profile_filesystem=0 profile_terminal=0",
            line,
        )
        for (forbidden in listOf("https://", "private@example", "secret-input", "secret-output", "token=")) {
            assertFalse(line.contains(forbidden))
        }
    }
}
