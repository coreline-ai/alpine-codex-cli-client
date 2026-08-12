package dev.alpine.codexclient.bridge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AgentContractsTest {
    @Test
    fun `wire agent id parsing is exact and closed`() {
        assertEquals(AgentId.CODEX, AgentId.fromWire("codex"))
        assertEquals(AgentId.GROK, AgentId.fromWire("grok"))
        assertNull(AgentId.fromWire("GROK"))
        assertNull(AgentId.fromWire("other"))
        assertNull(AgentId.fromWire(null))
    }

    @Test
    fun `legacy Codex-only storage migrates without an agent field`() {
        val version = AgentStorageSchema.parseVersion(null)
        assertEquals(1, version)
        assertEquals(AgentId.CODEX, AgentStorageSchema.resolveAgentId(checkNotNull(version), null))
        assertEquals(AgentId.CODEX, AgentStorageSchema.resolveAgentId(version, ""))
    }

    @Test
    fun `current storage requires a known explicit agent`() {
        assertEquals(2, AgentStorageSchema.parseVersion(2))
        assertEquals(AgentId.GROK, AgentStorageSchema.resolveAgentId(2, "grok"))
        assertNull(AgentStorageSchema.resolveAgentId(2, null))
        assertNull(AgentStorageSchema.resolveAgentId(2, "future-agent"))
        assertEquals(3, AgentStorageSchema.parseVersion(3))
        assertEquals(AgentId.GROK, AgentStorageSchema.resolveAgentId(3, "grok"))
        assertNull(AgentStorageSchema.parseVersion(4))
    }
}
