package dev.alpine.codexclient

import android.app.Application
import androidx.test.platform.app.InstrumentationRegistry
import dev.alpine.codexclient.bridge.AgentAccount
import dev.alpine.codexclient.bridge.AgentCapabilities
import dev.alpine.codexclient.bridge.AgentGatewayChatBackend
import dev.alpine.codexclient.bridge.AgentGatewayChatRequest
import dev.alpine.codexclient.bridge.AgentGatewayClient
import dev.alpine.codexclient.bridge.AgentGatewayHealth
import dev.alpine.codexclient.bridge.AgentGatewayStreamControl
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.AgentLogin
import dev.alpine.codexclient.bridge.AgentModel
import dev.alpine.codexclient.bridge.AgentSelection
import dev.alpine.codexclient.bridge.AgentTurnEvent
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.codexclient.bridge.CodexRuntimeState
import dev.alpine.codexclient.bridge.CodexRuntimeStateListener
import dev.alpine.codexclient.bridge.CodexRuntimeStateSource
import dev.alpine.codexclient.bridge.GatewayAgent
import dev.alpine.runtime.api.RuntimeSubscription
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.After
import org.junit.Before
import org.junit.Test

/** Credential-free selected-Agent workflow. No Runtime process, browser, OAuth, or network starts. */
class AgentChatWorkflowInstrumentedTest {
    private lateinit var application: Application
    private lateinit var originalStates: List<File>
    private lateinit var stateBackups: List<File>

    @Before
    fun preserveExistingConversationState() {
        val instrumentation = InstrumentationRegistry.getInstrumentation()
        application = instrumentation.targetContext.applicationContext as Application
        originalStates = STORE_FILES.map { File(application.filesDir, it) }
        stateBackups = STORE_FILES.map { File(application.cacheDir, "$it.instrumentation-backup") }
        originalStates.zip(stateBackups).forEach { (originalState, stateBackup) ->
            check(!stateBackup.exists()) { "stale instrumentation state backup" }
            if (originalState.exists()) {
                check(originalState.renameTo(stateBackup)) { "failed to preserve existing app state" }
            }
        }
    }

    @After
    fun restoreExistingConversationState() {
        originalStates.zip(stateBackups).forEach { (originalState, stateBackup) ->
            originalState.delete()
            if (stateBackup.exists()) {
                check(stateBackup.renameTo(originalState)) { "failed to restore existing app state" }
            }
        }
    }

    @Test
    fun fakeCodexAndGrokLifecycleKeepsConversationModelAndLoginStateSeparate() {
        val gateway = FakeAgentGatewayClient()
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
                true,
            )
        }

        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_REQUIRED }
        onMain {
            viewModel.startDeviceLogin()
            viewModel.startDeviceLogin()
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_PENDING }
        assertEquals(1, gateway.loginStarts[AgentId.CODEX])
        assertEquals("CODEX-FIXTURE", viewModel.state.value.login?.userCode)
        gateway.approve(AgentId.CODEX)
        awaitState(viewModel) { it.connection == AgentConnectionState.READY }
        assertEquals("codex-default", viewModel.state.value.selectedModelId)

        onMain {
            viewModel.updateDraft("codex fixture input")
            viewModel.send()
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.READY && it.messages.size == 2 }
        val codexConversation = checkNotNull(viewModel.state.value.conversationId)

        onMain { viewModel.switchAgent(AgentId.GROK) }
        awaitState(viewModel) {
            it.selectedAgentId == AgentId.GROK && it.connection == AgentConnectionState.LOGIN_REQUIRED
        }
        assertTrue(viewModel.state.value.messages.isEmpty())
        assertNull(viewModel.state.value.conversationId)

        onMain {
            viewModel.startDeviceLogin()
            viewModel.startDeviceLogin()
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_PENDING }
        assertEquals(1, gateway.loginStarts[AgentId.GROK])
        assertNull(viewModel.state.value.login?.userCode)
        assertNull(viewModel.state.value.login?.verificationUrl)
        gateway.approve(AgentId.GROK)
        awaitState(viewModel) { it.connection == AgentConnectionState.READY }
        assertEquals("grok-default", viewModel.state.value.selectedModelId)

        onMain {
            viewModel.updateDraft("grok fixture input")
            viewModel.send()
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.READY && it.messages.size == 2 }
        val grokConversation = checkNotNull(viewModel.state.value.conversationId)
        assertTrue(grokConversation != codexConversation)
        assertEquals(listOf(AgentId.CODEX, AgentId.GROK), gateway.sentRequests.map { it.agentId })

        val recreatedGateway = FakeAgentGatewayClient().apply {
            selectedAgent = AgentId.GROK
            approve(AgentId.CODEX)
            approve(AgentId.GROK)
        }
        val recreated = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                recreatedGateway,
                AgentGatewayChatBackend(recreatedGateway),
            )
        }
        awaitState(recreated) {
            it.selectedAgentId == AgentId.GROK && it.connection == AgentConnectionState.READY
        }
        assertEquals(grokConversation, recreated.state.value.conversationId)
        assertEquals("grok-default", recreated.state.value.selectedModelId)
        assertEquals(2, recreated.state.value.messages.size)
        assertTrue(recreatedGateway.sentRequests.isEmpty())

        onMain { viewModel.switchAgent(AgentId.CODEX) }
        awaitState(viewModel) {
            it.selectedAgentId == AgentId.CODEX && it.connection == AgentConnectionState.READY
        }
        assertEquals(codexConversation, viewModel.state.value.conversationId)
        assertEquals("codex-default", viewModel.state.value.selectedModelId)
        assertEquals(2, viewModel.state.value.messages.size)

        onMain { viewModel.switchAgent(AgentId.GROK) }
        awaitState(viewModel) {
            it.selectedAgentId == AgentId.GROK && it.connection == AgentConnectionState.READY
        }
        gateway.blockNextTurn()
        onMain {
            viewModel.newConversation()
            viewModel.updateDraft("grok stop fixture")
            viewModel.send()
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.GENERATING && it.activeRequestId != null }
        onMain { viewModel.stopGeneration() }
        awaitState(viewModel) { it.connection == AgentConnectionState.STABLE_ERROR }
        assertEquals(1, gateway.interrupts[AgentId.GROK])

        onMain { viewModel.refreshConnection() }
        awaitState(viewModel) { it.connection == AgentConnectionState.READY }
        onMain { viewModel.logout() }
        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_REQUIRED }
        assertEquals(1, gateway.logouts[AgentId.GROK])
        onMain { viewModel.switchAgent(AgentId.CODEX) }
        awaitState(viewModel) {
            it.selectedAgentId == AgentId.CODEX && it.connection == AgentConnectionState.READY
        }
        assertEquals(codexConversation, viewModel.state.value.conversationId)
        assertEquals(2, viewModel.state.value.messages.size)

        val storedBytes = File(application.filesDir, STORE_FILE_V2).readBytes().toString(Charsets.UTF_8)
        assertFalse(storedBytes.contains("CODEX-FIXTURE"))
        assertFalse(storedBytes.contains("auth.x.ai"))
    }

    @Test
    fun invalidCodexChallengeFailsClosedAndCancelsTheOfficialAttempt() {
        val gateway = FakeAgentGatewayClient().apply { omitNextCodexCode = true }
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
                true,
            )
        }

        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_REQUIRED }
        onMain { viewModel.startDeviceLogin() }
        awaitState(viewModel) {
            it.connection == AgentConnectionState.STABLE_ERROR &&
                it.stableErrorCode == "login_challenge_invalid"
        }
        val deadline = System.currentTimeMillis() + 2_000L
        while (gateway.cancelledLogins[AgentId.CODEX] != 1 && System.currentTimeMillis() < deadline) {
            Thread.sleep(25L)
        }
        assertEquals(1, gateway.cancelledLogins[AgentId.CODEX])
        assertNull(viewModel.state.value.login)
    }

    @Test
    fun labBuildBlocksRealOAuthBeforeGatewayDispatch() {
        val gateway = FakeAgentGatewayClient()
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
                false,
            )
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_REQUIRED }
        onMain { viewModel.startDeviceLogin() }
        awaitState(viewModel) {
            it.connection == AgentConnectionState.STABLE_ERROR &&
                it.stableErrorCode == "oauth_disabled_in_lab_build"
        }
        assertEquals(null, gateway.loginStarts[AgentId.CODEX])
        assertNull(viewModel.state.value.login)
    }

    @Test
    fun encryptedStoreKeepsSameOpaqueConversationIdSeparateByAgent() {
        val sameId = "same-opaque-id"
        val state = StoredConversationState(
            activeConversationId = sameId,
            conversations = listOf(
                StoredConversation(
                    sameId,
                    "codex-default",
                    listOf(StoredChatMessage(ChatRole.USER, "codex fixture")),
                    AgentId.CODEX,
                ),
                StoredConversation(
                    sameId,
                    "grok-default",
                    listOf(StoredChatMessage(ChatRole.USER, "grok fixture")),
                    AgentId.GROK,
                ),
            ),
            selectedAgentId = AgentId.GROK,
            activeConversationIds = mapOf(AgentId.CODEX to sameId, AgentId.GROK to sameId),
            selectedModelIds = mapOf(
                AgentId.CODEX to "codex-default",
                AgentId.GROK to "grok-default",
            ),
        )
        val store = EncryptedConversationStore(application)
        assertTrue(store.save(state))

        val restored = checkNotNull(store.load())
        assertEquals(2, restored.conversations.size)
        assertEquals(setOf(AgentId.CODEX, AgentId.GROK), restored.conversations.map { it.agentId }.toSet())
        assertEquals(sameId, restored.activeConversationIds[AgentId.CODEX])
        assertEquals(sameId, restored.activeConversationIds[AgentId.GROK])
    }

    @Test
    fun browserLaunchFailureCancelsPendingLoginWithoutRetainingChallenge() {
        val gateway = FakeAgentGatewayClient()
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
                true,
            )
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_REQUIRED }
        onMain { viewModel.startDeviceLogin() }
        awaitState(viewModel) { it.connection == AgentConnectionState.LOGIN_PENDING }

        onMain {
            viewModel.onBrowserLaunchFailed(
                BrowserLaunchRequest(AgentId.CODEX, "https://auth.openai.com/device"),
            )
        }
        awaitState(viewModel) {
            it.connection == AgentConnectionState.STABLE_ERROR &&
                it.stableErrorCode == "browser_launch_failed"
        }
        val deadline = System.currentTimeMillis() + 2_000L
        while (gateway.cancelledLogins[AgentId.CODEX] != 1 && System.currentTimeMillis() < deadline) {
            Thread.sleep(25L)
        }
        assertEquals(1, gateway.cancelledLogins[AgentId.CODEX])
        assertNull(viewModel.state.value.login)
    }

    @Test
    fun partialFailureKeepsOneUserOneAssistantAndRecreationNeverReplays() {
        val gateway = FakeAgentGatewayClient().apply { approve(AgentId.CODEX) }
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
            )
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.READY }
        gateway.failAfterPartialNextTurn()
        onMain {
            viewModel.updateDraft("one fixture request")
            viewModel.send()
        }
        awaitState(viewModel) {
            it.connection == AgentConnectionState.STABLE_ERROR && it.stableErrorCode == "grok_retry_after_output"
        }
        assertEquals(2, viewModel.state.value.messages.size)
        assertEquals(ChatRole.USER, viewModel.state.value.messages[0].role)
        assertEquals(ChatRole.ASSISTANT, viewModel.state.value.messages[1].role)
        assertEquals("partial fixture", viewModel.state.value.messages[1].text)
        assertEquals(1, gateway.sentRequests.size)
        Thread.sleep(100L)
        assertEquals(1, gateway.sentRequests.size)

        val recreatedGateway = FakeAgentGatewayClient().apply { approve(AgentId.CODEX) }
        val recreated = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                recreatedGateway,
                AgentGatewayChatBackend(recreatedGateway),
            )
        }
        awaitState(recreated) { it.connection == AgentConnectionState.READY }
        assertEquals(2, recreated.state.value.messages.size)
        assertTrue(recreatedGateway.sentRequests.isEmpty())
    }

    @Test
    fun mismatchedTurnRequestIdFailsClosedAndCannotBeOverwrittenByLateCompletion() {
        val gateway = FakeAgentGatewayClient().apply { approve(AgentId.CODEX) }
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
            )
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.READY }
        gateway.mismatchNextTurnRequestId()
        onMain {
            viewModel.updateDraft("one malformed fixture")
            viewModel.send()
        }
        awaitState(viewModel) {
            it.connection == AgentConnectionState.STABLE_ERROR &&
                it.stableErrorCode == "MALFORMED_RESPONSE"
        }
        Thread.sleep(100L)
        assertEquals(AgentConnectionState.STABLE_ERROR, viewModel.state.value.connection)
        assertEquals(2, viewModel.state.value.messages.size)
        assertEquals(1, gateway.sentRequests.size)
    }

    @Test
    fun stopBeforeStartAckIsQueuedThenSentExactlyOnceForTheBoundRequest() {
        val gateway = FakeAgentGatewayClient().apply { approve(AgentId.CODEX) }
        val viewModel = onMain {
            AgentChatViewModel(
                application,
                FakeRuntimeStateSource(),
                gateway,
                AgentGatewayChatBackend(gateway),
            )
        }
        awaitState(viewModel) { it.connection == AgentConnectionState.READY }
        gateway.delayAndBlockNextTurn()
        onMain {
            viewModel.updateDraft("one queued stop fixture")
            viewModel.send()
            viewModel.stopGeneration()
        }
        awaitState(viewModel) {
            it.connection == AgentConnectionState.GENERATING &&
                it.stopRequested && it.activeRequestId == null
        }
        assertNull(gateway.interrupts[AgentId.CODEX])
        gateway.releaseDelayedStart()
        awaitState(viewModel) {
            it.connection == AgentConnectionState.STABLE_ERROR && it.stableErrorCode == "turn_interrupted"
        }
        assertEquals(1, gateway.interrupts[AgentId.CODEX])
        assertEquals(1, gateway.sentRequests.size)
    }

    private fun <T> onMain(block: () -> T): T {
        var value: T? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync { value = block() }
        @Suppress("UNCHECKED_CAST")
        return value as T
    }

    private fun awaitState(viewModel: AgentChatViewModel, predicate: (AgentChatUiState) -> Boolean) {
        val deadline = System.currentTimeMillis() + 6_000L
        while (System.currentTimeMillis() < deadline) {
            if (predicate(viewModel.state.value)) return
            Thread.sleep(25L)
        }
        throw AssertionError("timed out waiting for Agent chat fixture")
    }

    private class FakeRuntimeStateSource : CodexRuntimeStateSource {
        private val listeners = CopyOnWriteArrayList<CodexRuntimeStateListener>()
        private val current = CodexRuntimeState(CodexRuntimeLifecycle.RUNNING, generation = 1)

        override fun currentState(): CodexRuntimeState = current

        override fun addStateListener(listener: CodexRuntimeStateListener): RuntimeSubscription {
            listeners += listener
            listener.onStateChanged(current)
            return RuntimeSubscription { listeners -= listener }
        }
    }

    private class FakeAgentGatewayClient : AgentGatewayClient() {
        private enum class TurnMode { NORMAL, FAIL_AFTER_PARTIAL, MISMATCH_REQUEST_ID }

        @Volatile var selectedAgent: AgentId = AgentId.CODEX
        val loginStarts = mutableMapOf<AgentId, Int>()
        val sentRequests = mutableListOf<AgentGatewayChatRequest>()
        val interrupts = mutableMapOf<AgentId, Int>()
        val logouts = mutableMapOf<AgentId, Int>()
        val cancelledLogins = mutableMapOf<AgentId, Int>()
        var omitNextCodexCode = false
        private val authenticated = mutableMapOf(AgentId.CODEX to false, AgentId.GROK to false)
        @Volatile private var blockNext = false
        @Volatile private var delayStartNext = false
        @Volatile private var startRelease = CompletableDeferred<Unit>().apply { complete(Unit) }
        @Volatile private var nextTurnMode = TurnMode.NORMAL
        @Volatile private var stopSignal: CompletableDeferred<Unit>? = null

        private val capabilities = AgentCapabilities(true, true, true, true, true)

        override fun health() = AgentGatewayHealth("ready", "ready", selectedAgent, true)

        override fun agents(): List<GatewayAgent> = AgentId.entries.map { agentId ->
            GatewayAgent(agentId, agentId == selectedAgent, ready = agentId == selectedAgent, capabilities)
        }

        override fun selectAgent(agentId: AgentId): AgentSelection {
            selectedAgent = agentId
            return AgentSelection(agentId, true)
        }

        override fun account(agentId: AgentId) = AgentAccount(
            agentId,
            authenticated = authenticated[agentId] == true,
            requiresAuth = authenticated[agentId] != true,
        )

        override fun startDeviceLogin(agentId: AgentId): AgentLogin {
            loginStarts[agentId] = (loginStarts[agentId] ?: 0) + 1
            return AgentLogin(
                agentId = agentId,
                requestId = "${agentId.wireValue}-login",
                state = "pending",
                verificationUrl = if (agentId == AgentId.CODEX) {
                    "https://auth.openai.com/device"
                } else {
                    "https://auth.x.ai/device?challenge=fixture"
                },
                userCode = "CODEX-FIXTURE".takeIf {
                    agentId == AgentId.CODEX && !omitNextCodexCode
                },
                expiresInSeconds = 60,
                pollIntervalSeconds = 1.takeIf { agentId == AgentId.CODEX },
            )
        }

        override fun loginStatus(agentId: AgentId, requestId: String) = AgentLogin(
            agentId,
            requestId,
            if (authenticated[agentId] == true) {
                if (agentId == AgentId.CODEX) "completed" else "authenticated"
            } else {
                "pending"
            },
        )

        override fun cancelLogin(agentId: AgentId, requestId: String): AgentLogin {
            cancelledLogins[agentId] = (cancelledLogins[agentId] ?: 0) + 1
            return AgentLogin(agentId, requestId, "cancelled")
        }

        override fun cancelActiveDeviceLogin(agentId: AgentId) = Unit

        override fun logout(agentId: AgentId) {
            logouts[agentId] = (logouts[agentId] ?: 0) + 1
            authenticated[agentId] = false
        }

        override fun models(agentId: AgentId): List<AgentModel> = listOf(
            AgentModel(agentId, "${agentId.wireValue}-default", "Default", true),
            AgentModel(agentId, "${agentId.wireValue}-other", "Other", false),
        )

        override fun stream(
            request: AgentGatewayChatRequest,
            control: AgentGatewayStreamControl,
        ): Flow<AgentTurnEvent> = flow {
            sentRequests += request
            val requestId = "${request.agentId.wireValue}-turn-${sentRequests.size}"
            val conversationId = "${request.agentId.wireValue}-conversation-${sentRequests.size}"
            val mode = nextTurnMode.also { nextTurnMode = TurnMode.NORMAL }
            val shouldBlock = blockNext.also { blockNext = false }
            val signal = if (shouldBlock) {
                CompletableDeferred<Unit>().also { stopSignal = it }
            } else {
                null
            }
            if (delayStartNext) {
                delayStartNext = false
                startRelease.await()
            }
            emit(AgentTurnEvent.Started(request.agentId, requestId, conversationId))
            if (signal != null) {
                signal.await()
                emit(AgentTurnEvent.Failed(request.agentId, requestId, "turn_interrupted"))
            } else {
                when (mode) {
                    TurnMode.NORMAL -> {
                        emit(AgentTurnEvent.Delta(request.agentId, requestId, "fixture response"))
                        emit(AgentTurnEvent.Completed(request.agentId, requestId))
                    }
                    TurnMode.FAIL_AFTER_PARTIAL -> {
                        emit(AgentTurnEvent.Delta(request.agentId, requestId, "partial fixture"))
                        emit(AgentTurnEvent.Failed(request.agentId, requestId, "grok_retry_after_output"))
                    }
                    TurnMode.MISMATCH_REQUEST_ID -> {
                        emit(AgentTurnEvent.Delta(request.agentId, "$requestId-wrong", "discard"))
                        emit(AgentTurnEvent.Completed(request.agentId, requestId))
                    }
                }
            }
        }

        override fun interrupt(agentId: AgentId, requestId: String) {
            interrupts[agentId] = (interrupts[agentId] ?: 0) + 1
            stopSignal?.complete(Unit)
        }

        fun approve(agentId: AgentId) {
            authenticated[agentId] = true
        }

        fun blockNextTurn() {
            blockNext = true
        }

        fun delayAndBlockNextTurn() {
            startRelease = CompletableDeferred()
            delayStartNext = true
            blockNext = true
        }

        fun releaseDelayedStart() {
            startRelease.complete(Unit)
        }

        fun failAfterPartialNextTurn() {
            nextTurnMode = TurnMode.FAIL_AFTER_PARTIAL
        }

        fun mismatchNextTurnRequestId() {
            nextTurnMode = TurnMode.MISMATCH_REQUEST_ID
        }
    }

    private companion object {
        const val STORE_FILE_V1 = "codex-chat-state.v1"
        const val STORE_FILE_V2 = "codex-chat-state.v2"
        val STORE_FILES = listOf(STORE_FILE_V1, STORE_FILE_V2)
    }
}
