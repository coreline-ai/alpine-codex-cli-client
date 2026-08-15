package dev.alpine.codexclient

import android.util.Log
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.AgentTurnDiagnostics

/** Emits one fixed, content-free terminal line for Samsung Phase 9 evidence. */
internal object AgentTurnAudit {
    private const val TAG = "AgentTurnAudit"

    fun record(agentId: AgentId, outcome: String, diagnostics: AgentTurnDiagnostics?) {
        if (agentId != AgentId.GROK || diagnostics == null) return
        Log.i(TAG, format(outcome, diagnostics))
    }

    internal fun format(outcome: String, value: AgentTurnDiagnostics): String {
        require(outcome in setOf("done", "error"))
        return buildString {
            append("agent=grok outcome=")
            append(outcome)
            append(" prompt_dispatch=")
            append(value.promptDispatchCount)
            append(" visible_delta=")
            append(value.visibleDeltaCount)
            append(" terminal=")
            append(value.terminalCount)
            append(" cancel=")
            append(value.cancelDispatchCount)
            append(" retry=")
            append(value.retryClassification)
            append(" retry_attempts=")
            append(value.retryAttempts)
            append(" retry_max=")
            append(value.retryMax)
            append(" profile_tool=")
            append(value.toolEventCount)
            append(" profile_subagent=")
            append(value.subagentEventCount)
            append(" profile_mcp=")
            append(value.mcpEventCount)
            append(" profile_filesystem=")
            append(value.filesystemEventCount)
            append(" profile_terminal=")
            append(value.terminalEventCount)
        }
    }
}

/** Emits content-free lifecycle checkpoints so a live Stop test never needs chat UI text. */
internal object AgentTurnStateAudit {
    private const val TAG = "AgentTurnStateAudit"

    fun recordStarted(agentId: AgentId) {
        if (agentId == AgentId.GROK) Log.i(TAG, startedLine())
    }

    fun recordStop(agentId: AgentId, dispatched: Boolean) {
        if (agentId == AgentId.GROK) Log.i(TAG, stopLine(dispatched))
    }

    internal fun startedLine(): String = "agent=grok state=started request_bound=1"

    internal fun stopLine(dispatched: Boolean): String =
        "agent=grok state=stop_requested dispatched=${if (dispatched) 1 else 0}"
}
