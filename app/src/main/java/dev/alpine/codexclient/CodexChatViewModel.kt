package dev.alpine.codexclient

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.alpine.codexclient.bridge.CodexGatewayChatEvent
import dev.alpine.codexclient.bridge.CodexGatewayChatTurn
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.codexclient.bridge.CodexRuntimeStateSource
import dev.alpine.codexclient.bridge.GatewayChatRequest
import dev.alpine.codexclient.bridge.GatewayClientException
import dev.alpine.codexclient.bridge.GatewayLoginStart
import dev.alpine.codexclient.bridge.GatewayModel
import dev.alpine.runtime.api.RuntimeSubscription
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class CodexConnectionState {
    RUNTIME_STOPPED,
    GATEWAY_STARTING,
    LOGIN_REQUIRED,
    LOGIN_PENDING,
    READY,
    GENERATING,
    STABLE_ERROR,
}

enum class ChatRole { USER, ASSISTANT }

data class ChatMessage(
    val id: String,
    val role: ChatRole,
    val text: String,
)

data class ConversationSummary(
    val conversationId: String,
    val label: String,
    val selectedModelId: String?,
)

data class DeviceCodeChallenge(
    val loginId: String,
    val verificationUrl: String,
    val userCode: String,
    val expiresInSeconds: Int,
    val pollIntervalSeconds: Int,
)

data class CodexChatUiState(
    val connection: CodexConnectionState = CodexConnectionState.RUNTIME_STOPPED,
    val models: List<GatewayModel> = emptyList(),
    val selectedModelId: String? = null,
    val conversationId: String? = null,
    val conversations: List<ConversationSummary> = emptyList(),
    val messages: List<ChatMessage> = emptyList(),
    val draft: String = "",
    val login: DeviceCodeChallenge? = null,
    val activeRequestId: String? = null,
    val stopRequested: Boolean = false,
    val stableErrorCode: String? = null,
    val refreshing: Boolean = false,
) {
    val isGenerating: Boolean get() = connection == CodexConnectionState.GENERATING
}

/** UI state owner for one Codex-only chat session. No alternate backend or prompt replay exists. */
class CodexChatViewModel(
    application: Application,
    private val runtimeStateSource: CodexRuntimeStateSource = (application as AlpineCodexApplication).codexRuntimeController,
    private val gatewayClient: dev.alpine.codexclient.bridge.CodexGatewayClient =
        (application as AlpineCodexApplication).codexGatewayClient,
    private val chatBackend: dev.alpine.codexclient.bridge.CodexGatewayChatBackend =
        (application as AlpineCodexApplication).codexChatBackend,
) : AndroidViewModel(application) {
    private val conversationStore = EncryptedConversationStore(application)
    private val restoredConversationState = conversationStore.load()
    private val archivedConversations = LinkedHashMap<String, StoredConversation>().apply {
        restoredConversationState?.conversations?.forEach { put(it.conversationId, it) }
    }
    private val _state = MutableStateFlow(restoreState(restoredConversationState))
    val state: StateFlow<CodexChatUiState> = _state.asStateFlow()

    private var loginPollJob: Job? = null
    private var streamJob: Job? = null
    private var activeTurn: CodexGatewayChatTurn? = null
    private var loginStartInFlight = false
    private var nextLoginPollAtMillis = 0L
    private val runtimeSubscription: RuntimeSubscription = runtimeStateSource.addStateListener { runtime ->
        viewModelScope.launch { onRuntimeStateChanged(runtime.lifecycle) }
    }

    fun refreshConnection() {
        if (_state.value.isGenerating || _state.value.connection == CodexConnectionState.LOGIN_PENDING) return
        viewModelScope.launch {
            if (runtimeStateSource.currentState().lifecycle != CodexRuntimeLifecycle.RUNNING) {
                onRuntimeStateChanged(runtimeStateSource.currentState().lifecycle)
                return@launch
            }
            _state.update {
                it.copy(
                    connection = CodexConnectionState.GATEWAY_STARTING,
                    refreshing = true,
                    stableErrorCode = null,
                )
            }
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    gatewayClient.health()
                    gatewayClient.account()
                }
            }
            result.fold(
                onSuccess = { account ->
                    if (!account.authenticated) {
                        _state.update {
                            it.copy(
                                connection = CodexConnectionState.LOGIN_REQUIRED,
                                models = emptyList(),
                                selectedModelId = null,
                                refreshing = false,
                                stableErrorCode = null,
                            )
                        }
                    } else {
                        loadModelsAfterAuthentication()
                    }
                },
                onFailure = { setStableError(it) },
            )
        }
    }

    fun startDeviceLogin() {
        if (
            _state.value.connection != CodexConnectionState.LOGIN_REQUIRED ||
            _state.value.login != null ||
            loginStartInFlight
        ) return
        loginStartInFlight = true
        _state.update { it.copy(refreshing = true, stableErrorCode = null) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.startDeviceLogin() } }
            loginStartInFlight = false
            result.fold(
                onSuccess = { login ->
                    val challenge = login.toChallenge()
                    nextLoginPollAtMillis = System.currentTimeMillis() + challenge.pollIntervalSeconds * 1_000L
                    _state.update {
                        it.copy(
                            connection = CodexConnectionState.LOGIN_PENDING,
                            login = challenge,
                            refreshing = false,
                            stableErrorCode = null,
                        )
                    }
                    scheduleLoginPoll(challenge)
                },
                onFailure = { setStableError(it) },
            )
        }
    }

    /** Explicit status checks are rate-limited by the gateway-provided minimum interval. */
    fun checkDeviceLoginStatus() {
        val challenge = _state.value.login ?: return
        if (System.currentTimeMillis() < nextLoginPollAtMillis) return
        viewModelScope.launch { checkDeviceLoginStatus(challenge) }
    }

    fun cancelDeviceLogin() {
        val challenge = _state.value.login ?: return
        loginPollJob?.cancel()
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.cancelLogin(challenge.loginId) } }
            result.fold(
                onSuccess = {
                    _state.update {
                        it.copy(
                            connection = CodexConnectionState.LOGIN_REQUIRED,
                            login = null,
                            stableErrorCode = null,
                        )
                    }
                },
                onFailure = { setStableError(it) },
            )
        }
    }

    fun selectModel(modelId: String) {
        if (_state.value.isGenerating || _state.value.models.none { it.id == modelId }) return
        _state.update { it.copy(selectedModelId = modelId, stableErrorCode = null) }
        persistConversation()
    }

    fun updateDraft(value: String) {
        if (!_state.value.isGenerating && value.toByteArray(Charsets.UTF_8).size <= MAX_DRAFT_BYTES) {
            _state.update { it.copy(draft = value) }
        }
    }

    fun newConversation() {
        if (_state.value.isGenerating) return
        persistConversation()
        _state.update { it.copy(conversationId = null, messages = emptyList(), stableErrorCode = null) }
        persistConversation()
    }

    fun selectConversation(conversationId: String) {
        if (_state.value.isGenerating) return
        val selected = archivedConversations[conversationId] ?: return
        _state.update {
            it.copy(
                conversationId = selected.conversationId,
                selectedModelId = selected.selectedModelId ?: it.selectedModelId,
                messages = selected.messages.map { message ->
                    ChatMessage(UUID.randomUUID().toString(), message.role, message.text)
                },
                stableErrorCode = null,
            )
        }
        persistConversation()
    }

    fun send() {
        val before = _state.value
        val model = before.selectedModelId ?: return
        val text = before.draft.trim()
        if (before.connection != CodexConnectionState.READY || text.isEmpty()) return
        val assistantId = UUID.randomUUID().toString()
        val request = GatewayChatRequest(
            conversationId = before.conversationId,
            model = model,
            text = text,
            resumeExisting = before.conversationId != null,
        )
        val turn = chatBackend.startTurn(request)
        activeTurn = turn
        _state.update {
            it.copy(
                connection = CodexConnectionState.GENERATING,
                draft = "",
                activeRequestId = null,
                stopRequested = false,
                stableErrorCode = null,
                messages = it.messages +
                    ChatMessage(UUID.randomUUID().toString(), ChatRole.USER, text) +
                    ChatMessage(assistantId, ChatRole.ASSISTANT, ""),
            )
        }
        persistConversation()
        streamJob = viewModelScope.launch(Dispatchers.IO) {
            try {
                turn.events.collect { event -> handleChatEvent(event, assistantId, turn) }
            } catch (_: CancellationException) {
                throw CancellationException()
            } catch (error: Throwable) {
                setStableError(error)
            } finally {
                activeTurn = null
                _state.update { current ->
                    if (current.connection == CodexConnectionState.GENERATING) {
                        current.copy(
                            connection = CodexConnectionState.STABLE_ERROR,
                            activeRequestId = null,
                            stableErrorCode = "stream_terminated",
                        )
                    } else {
                        current
                    }
                }
                persistConversation()
            }
        }
    }

    fun stopGeneration() {
        val turn = activeTurn ?: return
        if (!_state.value.isGenerating) return
        _state.update { it.copy(stopRequested = true) }
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { turn.stop() }.onFailure(::setStableError)
        }
    }

    fun logout() {
        if (_state.value.isGenerating) return
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.logout() } }
            result.fold(
            onSuccess = {
                loginPollJob?.cancel()
                conversationStore.clear()
                archivedConversations.clear()
                _state.update {
                        it.copy(
                            connection = CodexConnectionState.LOGIN_REQUIRED,
                            models = emptyList(),
                            selectedModelId = null,
                            conversationId = null,
                            messages = emptyList(),
                            login = null,
                            stableErrorCode = null,
                        )
                    }
                },
                onFailure = { setStableError(it) },
            )
        }
    }

    private fun onRuntimeStateChanged(lifecycle: CodexRuntimeLifecycle) {
        when (lifecycle) {
            CodexRuntimeLifecycle.RUNNING -> refreshConnection()
            CodexRuntimeLifecycle.STARTING -> _state.update {
                it.copy(connection = CodexConnectionState.GATEWAY_STARTING, stableErrorCode = null)
            }
            CodexRuntimeLifecycle.STOPPED,
            CodexRuntimeLifecycle.STOPPING,
            CodexRuntimeLifecycle.FAILED,
            -> {
                loginPollJob?.cancel()
                loginStartInFlight = false
                _state.update {
                    it.copy(
                        connection = CodexConnectionState.RUNTIME_STOPPED,
                        models = emptyList(),
                        login = null,
                        activeRequestId = null,
                        stopRequested = false,
                    )
                }
            }
        }
    }

    private suspend fun loadModelsAfterAuthentication() {
        val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.models() } }
        result.fold(
            onSuccess = { models ->
                if (models.isEmpty()) {
                    _state.update {
                        it.copy(
                            connection = CodexConnectionState.STABLE_ERROR,
                            models = emptyList(),
                            selectedModelId = null,
                            refreshing = false,
                            stableErrorCode = "model_list_empty",
                        )
                    }
                } else {
                    _state.update { current ->
                        val selected = current.selectedModelId?.takeIf { selectedId -> models.any { it.id == selectedId } }
                            ?: models.firstOrNull { it.isDefault }?.id
                            ?: models.first().id
                        current.copy(
                            connection = CodexConnectionState.READY,
                            models = models,
                            selectedModelId = selected,
                            refreshing = false,
                            stableErrorCode = null,
                        )
                    }
                    persistConversation()
                }
            },
            onFailure = { setStableError(it) },
        )
    }

    private fun scheduleLoginPoll(challenge: DeviceCodeChallenge) {
        loginPollJob?.cancel()
        loginPollJob = viewModelScope.launch {
            delay(challenge.pollIntervalSeconds * 1_000L)
            while (isActive && _state.value.login?.loginId == challenge.loginId) {
                checkDeviceLoginStatus(challenge)
                if (_state.value.connection != CodexConnectionState.LOGIN_PENDING) return@launch
                delay(challenge.pollIntervalSeconds * 1_000L)
            }
        }
    }

    private suspend fun checkDeviceLoginStatus(challenge: DeviceCodeChallenge) {
        nextLoginPollAtMillis = System.currentTimeMillis() + challenge.pollIntervalSeconds * 1_000L
        val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.loginStatus(challenge.loginId) } }
        result.fold(
            onSuccess = { status ->
                when (status.status) {
                    "pending" -> Unit
                    "completed" -> {
                        loginPollJob?.cancel()
                        _state.update { it.copy(connection = CodexConnectionState.GATEWAY_STARTING, login = null) }
                        refreshConnection()
                    }
                    "cancelled", "expired", "failed" -> {
                        loginPollJob?.cancel()
                        _state.update {
                            it.copy(
                                connection = CodexConnectionState.LOGIN_REQUIRED,
                                login = null,
                                stableErrorCode = if (status.status == "cancelled") null else "login_${status.status}",
                            )
                        }
                    }
                    else -> _state.update {
                        it.copy(connection = CodexConnectionState.STABLE_ERROR, stableErrorCode = "login_status_invalid")
                    }
                }
            },
            onFailure = { setStableError(it) },
        )
    }

    private fun handleChatEvent(event: CodexGatewayChatEvent, assistantId: String, turn: CodexGatewayChatTurn) {
        when (event) {
            is CodexGatewayChatEvent.Started -> {
                _state.update {
                    it.copy(activeRequestId = event.requestId, conversationId = event.conversationId ?: it.conversationId)
                }
                persistConversation()
                if (_state.value.stopRequested) {
                    viewModelScope.launch(Dispatchers.IO) { runCatching { turn.stop() }.onFailure(::setStableError) }
                }
            }
            is CodexGatewayChatEvent.Delta -> {
                _state.update { current ->
                    current.copy(messages = current.messages.map { message ->
                        if (message.id == assistantId) message.copy(text = (message.text + event.text).take(MAX_ASSISTANT_CHARS)) else message
                    })
                }
            }
            is CodexGatewayChatEvent.Completed -> {
                _state.update {
                    it.copy(
                        connection = CodexConnectionState.READY,
                        activeRequestId = null,
                        stopRequested = false,
                        stableErrorCode = null,
                    )
                }
                persistConversation()
            }
            is CodexGatewayChatEvent.Failed -> {
                _state.update {
                    it.copy(
                        connection = CodexConnectionState.STABLE_ERROR,
                        activeRequestId = null,
                        stopRequested = false,
                        stableErrorCode = event.code,
                    )
                }
                persistConversation()
            }
        }
    }

    private fun setStableError(error: Throwable) {
        val code = (error as? GatewayClientException)?.gatewayCode ?: (error as? GatewayClientException)?.errorCode?.name ?: "gateway_unavailable"
        _state.update {
            it.copy(
                connection = CodexConnectionState.STABLE_ERROR,
                activeRequestId = null,
                refreshing = false,
                stableErrorCode = code,
            )
        }
    }

    private fun restoreState(stored: StoredConversationState?): CodexChatUiState {
        val active = stored?.activeConversationId?.let { activeId ->
            stored.conversations.firstOrNull { it.conversationId == activeId }
        }
        return CodexChatUiState(
            selectedModelId = active?.selectedModelId,
            conversationId = active?.conversationId,
            conversations = stored?.conversations?.asReversed()?.map(::toSummary).orEmpty(),
            messages = active?.messages?.map { ChatMessage(UUID.randomUUID().toString(), it.role, it.text) }.orEmpty(),
        )
    }

    private fun persistConversation() {
        val current = _state.value
        current.conversationId?.takeIf { it.isNotBlank() }?.let { conversationId ->
            archivedConversations.remove(conversationId)
            archivedConversations[conversationId] = StoredConversation(
                conversationId = conversationId,
                selectedModelId = current.selectedModelId,
                messages = current.messages.takeLast(MAX_STORED_MESSAGES).map { message ->
                    StoredChatMessage(message.role, message.text.take(MAX_STORED_MESSAGE_CHARS))
                },
            )
            while (archivedConversations.size > MAX_ARCHIVED_CONVERSATIONS) {
                archivedConversations.entries.iterator().next().also { archivedConversations.remove(it.key) }
            }
        }
        val summaries = archivedConversations.values.toList().asReversed().map(::toSummary)
        val saved = conversationStore.save(
            StoredConversationState(
                activeConversationId = current.conversationId,
                conversations = archivedConversations.values.toList(),
            ),
        )
        if (saved && current.conversations != summaries) {
            _state.update { it.copy(conversations = summaries) }
        }
    }

    private fun toSummary(conversation: StoredConversation): ConversationSummary {
        val firstUserText = conversation.messages.firstOrNull { it.role == ChatRole.USER }
            ?.text
            ?.replace(Regex("\\s+"), " ")
            ?.trim()
            .orEmpty()
        return ConversationSummary(
            conversationId = conversation.conversationId,
            label = firstUserText.take(MAX_CONVERSATION_LABEL_CHARS).ifBlank { "대화" },
            selectedModelId = conversation.selectedModelId,
        )
    }

    private fun GatewayLoginStart.toChallenge() = DeviceCodeChallenge(
        loginId = loginId,
        verificationUrl = verificationUrl,
        userCode = userCode,
        expiresInSeconds = expiresInSeconds,
        pollIntervalSeconds = pollIntervalSeconds,
    )

    override fun onCleared() {
        loginPollJob?.cancel()
        streamJob?.cancel()
        runtimeSubscription.close()
        super.onCleared()
    }

    private companion object {
        const val MAX_DRAFT_BYTES = 16 * 1024
        const val MAX_ASSISTANT_CHARS = 128 * 1024
        const val MAX_ARCHIVED_CONVERSATIONS = 4
        const val MAX_STORED_MESSAGES = 8
        const val MAX_STORED_MESSAGE_CHARS = 1_024
        const val MAX_CONVERSATION_LABEL_CHARS = 60
    }
}
