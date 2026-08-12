package dev.alpine.codexclient

import android.app.Application
import androidx.test.platform.app.InstrumentationRegistry
import dev.alpine.codexclient.bridge.CodexGatewayClient
import dev.alpine.codexclient.bridge.CodexGatewayChatBackend
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.codexclient.bridge.CodexRuntimeState
import dev.alpine.codexclient.bridge.CodexRuntimeStateListener
import dev.alpine.codexclient.bridge.CodexRuntimeStateSource
import dev.alpine.codexclient.bridge.GatewayAccount
import dev.alpine.codexclient.bridge.GatewayClientErrorCode
import dev.alpine.codexclient.bridge.GatewayClientException
import dev.alpine.codexclient.bridge.GatewayChatRequest
import dev.alpine.codexclient.bridge.GatewayHealth
import dev.alpine.codexclient.bridge.GatewayLoginStart
import dev.alpine.codexclient.bridge.GatewayLoginStatus
import dev.alpine.codexclient.bridge.GatewayModel
import dev.alpine.codexclient.bridge.GatewayStreamControl
import dev.alpine.codexclient.bridge.GatewayStreamEvent
import dev.alpine.runtime.api.RuntimeSubscription
import java.util.concurrent.CopyOnWriteArrayList
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Credential-free app workflow test. It drives the production ViewModel through only the
 * fixed-gateway contract; no Runtime process, OAuth challenge, browser, or network is started.
 */
class CodexChatWorkflowInstrumentedTest {
    @Test
    fun encryptedConversationStoreWritesAndReloadsLocalSyntheticState() {
        val application = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        application.deleteFile("codex-chat-state.v1")
        application.deleteFile("codex-chat-state.v2")
        val store = EncryptedConversationStore(application)
        val state = StoredConversationState(
            activeConversationId = "conversation-store-test",
            conversations = listOf(
                StoredConversation(
                    conversationId = "conversation-store-test",
                    selectedModelId = "model-test",
                    messages = listOf(StoredChatMessage(ChatRole.USER, "synthetic local state")),
                ),
            ),
        )
        val saved = store.save(state)
        assertTrue("encrypted store failure: ${store.lastWriteFailureCode()}", saved)
        assertEquals("conversation-store-test", store.load()?.activeConversationId)
        store.clear()
    }

    @Test
    fun fakeGatewayCompletesLoginModelTurnStopAndLogoutWithoutReplay() {
        val gateway = FakeGatewayClient()
        val runtime = FakeRuntimeStateSource()
        val application = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        application.deleteFile("codex-chat-state.v1")
        application.deleteFile("codex-chat-state.v2")
        val viewModel = onMain {
            CodexChatViewModel(
                application = application,
                runtimeStateSource = runtime,
                gatewayClient = gateway,
                chatBackend = CodexGatewayChatBackend(gateway),
            )
        }

        awaitState(viewModel) { it.connection == CodexConnectionState.LOGIN_REQUIRED }
        onMain {
            viewModel.startDeviceLogin()
            viewModel.startDeviceLogin()
        }
        awaitState(viewModel) { it.connection == CodexConnectionState.LOGIN_PENDING }
        assertEquals(1, gateway.loginStartCalls)

        gateway.approveLogin()
        awaitState(viewModel) {
            it.connection == CodexConnectionState.READY &&
                it.models.size == 2 &&
                it.selectedModelId == "model-default"
        }

        onMain {
            viewModel.updateDraft("synthetic test input")
            viewModel.send()
        }
        awaitState(viewModel) {
            it.connection == CodexConnectionState.READY && it.messages.size == 2
        }
        assertEquals(ChatRole.USER, viewModel.state.value.messages.first().role)
        assertEquals(ChatRole.ASSISTANT, viewModel.state.value.messages.last().role)
        val firstConversationId = viewModel.state.value.conversationId ?: throw AssertionError("missing conversation id")
        assertEquals(1, viewModel.state.value.conversations.size)

        val recreated = onMain {
            CodexChatViewModel(
                application = application,
                runtimeStateSource = FakeRuntimeStateSource(),
                gatewayClient = FakeGatewayClient(),
                chatBackend = CodexGatewayChatBackend(FakeGatewayClient()),
            )
        }
        awaitState(recreated) { it.connection == CodexConnectionState.LOGIN_REQUIRED }
        assertEquals(firstConversationId, recreated.state.value.conversationId)
        assertEquals(2, recreated.state.value.messages.size)

        onMain {
            viewModel.newConversation()
            viewModel.selectConversation(firstConversationId)
        }
        awaitState(viewModel) {
            it.conversationId == firstConversationId && it.messages.size == 2
        }

        gateway.setModels(listOf(GatewayModel("model-other", "Other", true)))
        onMain { viewModel.refreshConnection() }
        awaitState(viewModel) {
            it.connection == CodexConnectionState.READY &&
                it.models.size == 1 &&
                it.selectedModelId == "model-other"
        }

        gateway.blockNextTurn()
        onMain {
            viewModel.newConversation()
            viewModel.updateDraft("synthetic stop input")
            viewModel.send()
        }
        awaitState(viewModel) {
            it.connection == CodexConnectionState.GENERATING && it.activeRequestId != null
        }
        onMain { viewModel.stopGeneration() }
        awaitCondition("fake interrupt dispatch") { gateway.interruptCalls == 1 }
        awaitState(viewModel) { it.connection == CodexConnectionState.STABLE_ERROR }
        assertEquals(1, gateway.interruptCalls)

        gateway.setModels(emptyList())
        onMain { viewModel.refreshConnection() }
        awaitState(viewModel) { it.stableErrorCode == "model_list_empty" }

        onMain { viewModel.logout() }
        awaitState(viewModel) {
            it.connection == CodexConnectionState.LOGIN_REQUIRED &&
                it.messages.isEmpty() &&
                it.conversationId == null
        }
        assertEquals(1, gateway.logoutCalls)
        assertEquals(2, gateway.sentRequests.size)
        assertTrue(gateway.blockedRequestStopped)
    }

    @Test
    fun recoveredPendingLoginNeedsExplicitCancelAndNeverAutoRestarts() {
        val gateway = FakeGatewayClient().apply { rejectNextLoginAsAlreadyActive() }
        val application = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as Application
        application.deleteFile("codex-chat-state.v1")
        application.deleteFile("codex-chat-state.v2")
        val viewModel = onMain {
            CodexChatViewModel(
                application = application,
                runtimeStateSource = FakeRuntimeStateSource(),
                gatewayClient = gateway,
                chatBackend = CodexGatewayChatBackend(gateway),
            )
        }

        awaitState(viewModel) { it.connection == CodexConnectionState.LOGIN_REQUIRED }
        onMain { viewModel.startDeviceLogin() }
        awaitState(viewModel) { it.recoveredPendingLogin }
        assertEquals(1, gateway.loginStartCalls)

        onMain { viewModel.startDeviceLogin() }
        assertEquals(1, gateway.loginStartCalls)

        onMain { viewModel.cancelRecoveredDeviceLogin() }
        awaitState(viewModel) { !it.recoveredPendingLogin && !it.refreshing }
        assertEquals(1, gateway.recoveredLoginCancelCalls)
        assertEquals(1, gateway.loginStartCalls)
    }

    private fun <T> onMain(block: () -> T): T {
        var value: T? = null
        InstrumentationRegistry.getInstrumentation().runOnMainSync { value = block() }
        @Suppress("UNCHECKED_CAST")
        return value as T
    }

    private fun awaitState(viewModel: CodexChatViewModel, predicate: (CodexChatUiState) -> Boolean) {
        awaitCondition("fake chat state") { predicate(viewModel.state.value) }
    }

    private fun awaitCondition(label: String, predicate: () -> Boolean) {
        val deadline = System.currentTimeMillis() + 5_000L
        while (System.currentTimeMillis() < deadline) {
            if (predicate()) return
            Thread.sleep(25L)
        }
        throw AssertionError("timed out waiting for $label")
    }

    private class FakeRuntimeStateSource : CodexRuntimeStateSource {
        private val listeners = CopyOnWriteArrayList<CodexRuntimeStateListener>()
        private val state = CodexRuntimeState(CodexRuntimeLifecycle.RUNNING, generation = 1)

        override fun currentState(): CodexRuntimeState = state

        override fun addStateListener(listener: CodexRuntimeStateListener): RuntimeSubscription {
            listeners += listener
            listener.onStateChanged(state)
            return RuntimeSubscription { listeners -= listener }
        }
    }

    private class FakeGatewayClient : CodexGatewayClient() {
        var loginStartCalls = 0
            private set
        var interruptCalls = 0
            private set
        var logoutCalls = 0
            private set
        var recoveredLoginCancelCalls = 0
            private set
        val sentRequests = mutableListOf<GatewayChatRequest>()
        var blockedRequestStopped = false
            private set

        @Volatile private var authenticated = false
        @Volatile private var blockNext = false
        @Volatile private var rejectLoginAsAlreadyActive = false
        @Volatile private var stopSignal: CompletableDeferred<Unit>? = null

        fun approveLogin() {
            authenticated = true
        }

        fun blockNextTurn() {
            blockNext = true
        }

        fun rejectNextLoginAsAlreadyActive() {
            rejectLoginAsAlreadyActive = true
        }

        override fun health() = GatewayHealth("ready", "ready", "ready")

        override fun account() = GatewayAccount(
            authenticated = authenticated,
            requiresOpenaiAuth = !authenticated,
        )

        override fun startDeviceLogin(): GatewayLoginStart {
            loginStartCalls += 1
            if (rejectLoginAsAlreadyActive) {
                rejectLoginAsAlreadyActive = false
                throw GatewayClientException(
                    GatewayClientErrorCode.HTTP_ERROR,
                    statusCode = 409,
                    gatewayCode = "login_already_active",
                )
            }
            return GatewayLoginStart(
                loginId = "test-login",
                verificationUrl = "https://auth.openai.com/device",
                userCode = "TEST-CODE",
                expiresInSeconds = 60,
                pollIntervalSeconds = 1,
            )
        }

        override fun loginStatus(loginId: String): GatewayLoginStatus = GatewayLoginStatus(
            loginId = loginId,
            status = if (authenticated) "completed" else "pending",
        )

        override fun cancelLogin(loginId: String) = GatewayLoginStatus(loginId, "cancelled")

        override fun cancelActiveDeviceLogin() {
            recoveredLoginCancelCalls += 1
        }

        override fun logout() {
            logoutCalls += 1
            authenticated = false
        }

        @Volatile private var catalog: List<GatewayModel> = listOf(
            GatewayModel("model-default", "Default", true),
            GatewayModel("model-other", "Other", false),
        )

        fun setModels(models: List<GatewayModel>) {
            catalog = models
        }

        override fun models(): List<GatewayModel> = catalog

        override fun stream(request: GatewayChatRequest, control: GatewayStreamControl): Flow<GatewayStreamEvent> = flow {
            sentRequests += request
            val requestId = "test-turn-${sentRequests.size}"
            val signal = if (blockNext) {
                CompletableDeferred<Unit>().also { stopSignal = it }
            } else {
                null
            }
            emit(GatewayStreamEvent(requestId, "start", conversationId = "conversation-test"))
            if (signal != null) {
                signal.await()
                emit(GatewayStreamEvent(requestId, "error", code = "turn_interrupted"))
            } else {
                emit(GatewayStreamEvent(requestId, "delta", text = "synthetic response"))
                emit(GatewayStreamEvent(requestId, "done"))
            }
        }

        override fun interrupt(requestId: String) {
            interruptCalls += 1
            blockedRequestStopped = true
            stopSignal?.complete(Unit)
        }
    }
}
