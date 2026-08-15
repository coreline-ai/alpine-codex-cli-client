package dev.alpine.codexclient

import android.app.Application
import android.net.Uri
import android.util.Log
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import dev.alpine.codexclient.bridge.AgentGatewayChatBackend
import dev.alpine.codexclient.bridge.AgentGatewayChatRequest
import dev.alpine.codexclient.bridge.AgentGatewayChatTurn
import dev.alpine.codexclient.bridge.AgentGatewayClient
import dev.alpine.codexclient.bridge.AgentGatewayHealth
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.AgentLogin
import dev.alpine.codexclient.bridge.AgentModel
import dev.alpine.codexclient.bridge.AgentTurnEvent
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle
import dev.alpine.codexclient.bridge.CodexRuntimeStateSource
import dev.alpine.codexclient.bridge.GatewayAgent
import dev.alpine.codexclient.bridge.GatewayClientException
import dev.alpine.runtime.api.RuntimeSubscription
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

enum class AgentConnectionState {
    RUNTIME_STOPPED,
    GATEWAY_STARTING,
    SWITCHING,
    LOGIN_REQUIRED,
    LOGIN_PENDING,
    READY,
    GENERATING,
    STABLE_ERROR,
}

data class AgentLoginChallenge(
    val agentId: AgentId,
    val requestId: String,
    val userCode: String? = null,
    val expiresInSeconds: Int,
    val pollIntervalSeconds: Int,
    internal val verificationUrl: String? = null,
)

data class BrowserLaunchRequest(val agentId: AgentId, val url: String)

data class AgentChatUiState(
    val connection: AgentConnectionState = AgentConnectionState.RUNTIME_STOPPED,
    val selectedAgentId: AgentId = AgentId.CODEX,
    val agents: List<GatewayAgent> = emptyList(),
    val models: List<AgentModel> = emptyList(),
    val selectedModelId: String? = null,
    val conversationId: String? = null,
    val conversations: List<ConversationSummary> = emptyList(),
    val messages: List<ChatMessage> = emptyList(),
    val draft: String = "",
    val login: AgentLoginChallenge? = null,
    val recoveredPendingLogin: Boolean = false,
    val activeRequestId: String? = null,
    val stopRequested: Boolean = false,
    val stableErrorCode: String? = null,
    val refreshing: Boolean = false,
) {
    val isGenerating: Boolean get() = connection == AgentConnectionState.GENERATING
    val operationLocked: Boolean get() = isGenerating || connection in setOf(
        AgentConnectionState.LOGIN_PENDING,
        AgentConnectionState.SWITCHING,
    )
}

internal fun AgentChatUiState.withDiscoveredAgents(
    target: AgentId,
    discoveredAgents: List<GatewayAgent>,
): AgentChatUiState = if (selectedAgentId == target && discoveredAgents.isNotEmpty()) {
    copy(agents = discoveredAgents)
} else {
    this
}

internal fun AgentGatewayHealth.requiresSelection(target: AgentId): Boolean =
    selectedAgent != target || !backendReady

/** Selected-Agent UI owner. It never starts a prompt while restoring or switching Agent state. */
class AgentChatViewModel @JvmOverloads constructor(
    application: Application,
    private val runtimeStateSource: CodexRuntimeStateSource =
        (application as AlpineCodexApplication).codexRuntimeController,
    private val gatewayClient: AgentGatewayClient =
        (application as AlpineCodexApplication).agentGatewayClient,
    private val chatBackend: AgentGatewayChatBackend =
        (application as AlpineCodexApplication).agentChatBackend,
    private val allowDeviceOAuth: Boolean = BuildConfig.ALLOW_REAL_OAUTH,
) : AndroidViewModel(application) {
    private val conversationStore = EncryptedConversationStore(application)
    private val restored = conversationStore.load()
    private val archived = LinkedHashMap<String, StoredConversation>().apply {
        restored?.conversations?.forEach { put(bindingKey(it.agentId, it.conversationId), it) }
    }
    private val activeConversationIds = restored?.activeConversationIds?.toMutableMap() ?: mutableMapOf()
    private val selectedModelIds = restored?.selectedModelIds?.toMutableMap() ?: mutableMapOf()
    private val _state = MutableStateFlow(restoreAgent(restored?.selectedAgentId ?: AgentId.CODEX))
    val state: StateFlow<AgentChatUiState> = _state.asStateFlow()
    private val _browserLaunches = MutableSharedFlow<BrowserLaunchRequest>(extraBufferCapacity = 1)
    val browserLaunches: SharedFlow<BrowserLaunchRequest> = _browserLaunches.asSharedFlow()

    private var loginPollJob: Job? = null
    private var loginStatusInFlight = false
    private var streamJob: Job? = null
    private var activeTurn: AgentGatewayChatTurn? = null
    private var loginStartInFlight = false
    private var switchInFlight = false
    private var nextLoginPollAtMillis = 0L
    private val runtimeSubscription: RuntimeSubscription = runtimeStateSource.addStateListener { runtime ->
        viewModelScope.launch { onRuntimeStateChanged(runtime.lifecycle) }
    }

    fun refreshConnection() {
        val before = _state.value
        if (before.operationLocked || before.refreshing) return
        viewModelScope.launch {
            if (runtimeStateSource.currentState().lifecycle != CodexRuntimeLifecycle.RUNNING) {
                onRuntimeStateChanged(runtimeStateSource.currentState().lifecycle)
                return@launch
            }
            _state.update {
                it.copy(
                    connection = AgentConnectionState.GATEWAY_STARTING,
                    refreshing = true,
                    stableErrorCode = null,
                )
            }
            val target = _state.value.selectedAgentId
            val (discoveredAgents, result) = withContext(Dispatchers.IO) {
                var visibleAgents = emptyList<GatewayAgent>()
                runCatching {
                    var health = gatewayClient.health()
                    var agents = gatewayClient.agents()
                    visibleAgents = agents
                    if (health.requiresSelection(target)) {
                        gatewayClient.selectAgent(target)
                        health = gatewayClient.health()
                        agents = agents.map { it.copy(selected = it.agentId == target) }
                        visibleAgents = agents
                    }
                    if (health.selectedAgent != target || !health.backendReady) {
                        throw GatewayClientException(
                            dev.alpine.codexclient.bridge.GatewayClientErrorCode.MALFORMED_RESPONSE,
                        )
                    }
                    Triple(health, agents, gatewayClient.account(target))
                }.let { visibleAgents to it }
            }
            _state.update { it.withDiscoveredAgents(target, discoveredAgents) }
            result.fold(
                onSuccess = { (_, agents, account) ->
                    _state.update { it.copy(agents = agents) }
                    if (!account.authenticated) {
                        _state.update {
                            it.copy(
                                connection = AgentConnectionState.LOGIN_REQUIRED,
                                models = emptyList(),
                                selectedModelId = selectedModelIds[target],
                                refreshing = false,
                                stableErrorCode = null,
                            )
                        }
                    } else {
                        loadModelsAfterAuthentication(target)
                    }
                },
                onFailure =(::setStableError),
            )
        }
    }

    fun switchAgent(target: AgentId) {
        val before = _state.value
        if (
            target == before.selectedAgentId || before.operationLocked || before.refreshing || switchInFlight ||
            before.agents.none { it.agentId == target }
        ) return
        persistAgentState()
        loginPollJob?.cancel()
        switchInFlight = true
        _state.update {
            it.copy(
                connection = AgentConnectionState.SWITCHING,
                login = null,
                recoveredPendingLogin = false,
                refreshing = true,
                stableErrorCode = null,
            )
        }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.selectAgent(target) } }
            switchInFlight = false
            result.fold(
                onSuccess = {
                    _state.value = restoreAgent(target).copy(
                        connection = AgentConnectionState.GATEWAY_STARTING,
                        agents = before.agents.map { agent -> agent.copy(selected = agent.agentId == target) },
                        refreshing = false,
                    )
                    persistAgentState()
                    refreshConnection()
                },
                onFailure =(::setStableError),
            )
        }
    }

    fun startDeviceLogin() {
        val before = _state.value
        if (
            before.connection != AgentConnectionState.LOGIN_REQUIRED || before.login != null ||
            before.recoveredPendingLogin || loginStartInFlight
        ) return
        if (!allowDeviceOAuth) {
            _state.update {
                it.copy(
                    connection = AgentConnectionState.STABLE_ERROR,
                    refreshing = false,
                    stableErrorCode = "oauth_disabled_in_lab_build",
                )
            }
            return
        }
        loginStartInFlight = true
        _state.update { it.copy(refreshing = true, stableErrorCode = null) }
        viewModelScope.launch {
            val agentId = _state.value.selectedAgentId
            val result = withContext(Dispatchers.IO) {
                runCatching { gatewayClient.startDeviceLogin(agentId) }
            }
            loginStartInFlight = false
            result.fold(
                onSuccess = { login -> handleLoginStart(login) },
                onFailure = { error ->
                    if (error.isActiveLoginConflict()) {
                        _state.update {
                            it.copy(
                                connection = AgentConnectionState.LOGIN_REQUIRED,
                                recoveredPendingLogin = true,
                                refreshing = false,
                                stableErrorCode = null,
                            )
                        }
                    } else setStableError(error)
                },
            )
        }
    }

    fun openLoginBrowser() {
        val challenge = _state.value.login ?: return
        val url = challenge.verificationUrl ?: return
        if (validBrowserUrl(challenge.agentId, url)) {
            _browserLaunches.tryEmit(BrowserLaunchRequest(challenge.agentId, url))
        }
    }

    fun onBrowserLaunchFailed(request: BrowserLaunchRequest) {
        val challenge = _state.value.login ?: return
        if (challenge.agentId != request.agentId || !validBrowserUrl(request.agentId, request.url)) return
        loginPollJob?.cancel()
        _state.update {
            it.copy(
                connection = AgentConnectionState.STABLE_ERROR,
                login = null,
                refreshing = false,
                stableErrorCode = "browser_launch_failed",
            )
        }
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { gatewayClient.cancelLogin(challenge.agentId, challenge.requestId) }
        }
    }

    fun checkDeviceLoginStatus() {
        val challenge = _state.value.login ?: return
        if (loginStatusInFlight || System.currentTimeMillis() < nextLoginPollAtMillis) return
        viewModelScope.launch { checkDeviceLoginStatus(challenge) }
    }

    /** Reconciles the existing OAuth attempt immediately when the browser returns to the app. */
    fun onHostResumed() {
        if (runtimeStateSource.currentState().lifecycle != CodexRuntimeLifecycle.RUNNING) return
        val before = _state.value
        val challenge = before.login
        if (challenge != null) {
            if (!loginStatusInFlight) viewModelScope.launch { checkDeviceLoginStatus(challenge) }
        } else if (
            before.connection in setOf(AgentConnectionState.LOGIN_REQUIRED, AgentConnectionState.STABLE_ERROR) &&
            !before.operationLocked && !before.refreshing
        ) {
            // Also recovers a completed official OAuth session when Android recreated the app and
            // intentionally did not persist the opaque pending-login handle.
            refreshConnection()
        }
    }

    fun cancelDeviceLogin() {
        val challenge = _state.value.login ?: return
        loginPollJob?.cancel()
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { gatewayClient.cancelLogin(challenge.agentId, challenge.requestId) }
            }
            result.fold(
                onSuccess = {
                    _state.update {
                        it.copy(
                            connection = AgentConnectionState.LOGIN_REQUIRED,
                            login = null,
                            recoveredPendingLogin = false,
                            stableErrorCode = null,
                        )
                    }
                },
                onFailure =(::setStableError),
            )
        }
    }

    fun cancelRecoveredDeviceLogin() {
        val before = _state.value
        if (!before.recoveredPendingLogin || before.refreshing) return
        _state.update { it.copy(refreshing = true, stableErrorCode = null) }
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { gatewayClient.cancelActiveDeviceLogin(before.selectedAgentId) }
            }
            result.fold(
                onSuccess = {
                    _state.update {
                        it.copy(
                            connection = AgentConnectionState.LOGIN_REQUIRED,
                            recoveredPendingLogin = false,
                            refreshing = false,
                            stableErrorCode = null,
                        )
                    }
                },
                onFailure =(::setStableError),
            )
        }
    }

    fun selectModel(modelId: String) {
        val before = _state.value
        if (before.operationLocked || before.models.none { it.id == modelId }) return
        selectedModelIds[before.selectedAgentId] = modelId
        _state.update { it.copy(selectedModelId = modelId, stableErrorCode = null) }
        persistAgentState()
    }

    fun updateDraft(value: String) {
        if (!_state.value.operationLocked && value.toByteArray(Charsets.UTF_8).size <= MAX_DRAFT_BYTES) {
            _state.update { it.copy(draft = value) }
        }
    }

    fun newConversation() {
        if (_state.value.operationLocked) return
        persistAgentState()
        activeConversationIds.remove(_state.value.selectedAgentId)
        _state.update { it.copy(conversationId = null, messages = emptyList(), stableErrorCode = null) }
        persistAgentState()
    }

    fun selectConversation(conversationId: String) {
        val before = _state.value
        if (before.operationLocked) return
        val selected = archived[bindingKey(before.selectedAgentId, conversationId)] ?: return
        activeConversationIds[before.selectedAgentId] = selected.conversationId
        selected.selectedModelId?.let { selectedModelIds[before.selectedAgentId] = it }
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
        persistAgentState()
    }

    fun send() {
        val before = _state.value
        val model = before.selectedModelId ?: return
        val text = before.draft.trim()
        if (before.connection != AgentConnectionState.READY || text.isEmpty()) return
        val request = AgentGatewayChatRequest(
            agentId = before.selectedAgentId,
            conversationId = before.conversationId,
            model = model,
            text = text,
            resumeExisting = before.conversationId != null,
        )
        val assistantId = UUID.randomUUID().toString()
        val turn = chatBackend.startTurn(request)
        activeTurn = turn
        _state.update {
            it.copy(
                connection = AgentConnectionState.GENERATING,
                draft = "",
                activeRequestId = null,
                stopRequested = false,
                stableErrorCode = null,
                messages = it.messages +
                    ChatMessage(UUID.randomUUID().toString(), ChatRole.USER, text) +
                    ChatMessage(assistantId, ChatRole.ASSISTANT, ""),
            )
        }
        persistAgentState()
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
                    if (current.connection == AgentConnectionState.GENERATING) {
                        current.copy(
                            connection = AgentConnectionState.STABLE_ERROR,
                            activeRequestId = null,
                            stableErrorCode = "stream_terminated",
                        )
                    } else current
                }
                persistAgentState()
            }
        }
    }

    fun stopGeneration() {
        val turn = activeTurn ?: return
        if (!_state.value.isGenerating || turn.agentId != _state.value.selectedAgentId) return
        _state.update { it.copy(stopRequested = true) }
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { turn.stop() }
                .onSuccess { dispatched ->
                    AgentTurnStateAudit.recordStop(turn.agentId, dispatched)
                }
                .onFailure(::setStableError)
        }
    }

    fun logout() {
        val before = _state.value
        if (before.operationLocked) return
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { gatewayClient.logout(before.selectedAgentId) }
            }
            result.fold(
                onSuccess = {
                    loginPollJob?.cancel()
                    archived.entries.removeAll { it.value.agentId == before.selectedAgentId }
                    activeConversationIds.remove(before.selectedAgentId)
                    selectedModelIds.remove(before.selectedAgentId)
                    _state.update {
                        it.copy(
                            connection = AgentConnectionState.LOGIN_REQUIRED,
                            models = emptyList(),
                            selectedModelId = null,
                            conversationId = null,
                            conversations = emptyList(),
                            messages = emptyList(),
                            login = null,
                            stableErrorCode = null,
                        )
                    }
                    persistAgentState()
                },
                onFailure =(::setStableError),
            )
        }
    }

    private fun handleLoginStart(login: AgentLogin) {
        val selected = _state.value.selectedAgentId
        val url = login.verificationUrl
        val validRequestId = login.requestId.isNotBlank() && login.requestId.length <= MAX_LOGIN_REQUEST_ID_CHARS
        val validCodexCode = selected != AgentId.CODEX || login.userCode?.let { code ->
            code.isNotBlank() && code.length <= MAX_DEVICE_CODE_CHARS && code.none(Char::isISOControl)
        } == true
        if (
            login.agentId != selected || !validRequestId || !validCodexCode ||
            url == null || !validBrowserUrl(selected, url)
        ) {
            setStableError(IllegalStateException("login_challenge_invalid"))
            if (validRequestId) {
                viewModelScope.launch(Dispatchers.IO) {
                    runCatching { gatewayClient.cancelLogin(selected, login.requestId) }
                }
            }
            return
        }
        val pollSeconds = login.pollIntervalSeconds ?: DEFAULT_LOGIN_POLL_SECONDS
        val challenge = AgentLoginChallenge(
            agentId = selected,
            requestId = login.requestId,
            userCode = login.userCode.takeIf { selected == AgentId.CODEX },
            expiresInSeconds = login.expiresInSeconds ?: DEFAULT_LOGIN_EXPIRY_SECONDS,
            pollIntervalSeconds = pollSeconds,
            verificationUrl = url.takeIf { selected == AgentId.CODEX },
        )
        nextLoginPollAtMillis = System.currentTimeMillis() + pollSeconds * 1_000L
        _state.update {
            it.copy(
                connection = AgentConnectionState.LOGIN_PENDING,
                login = challenge,
                recoveredPendingLogin = false,
                refreshing = false,
                stableErrorCode = null,
            )
        }
        if (selected == AgentId.GROK) {
            _browserLaunches.tryEmit(BrowserLaunchRequest(selected, url))
        }
        scheduleLoginPoll(challenge)
    }

    private fun onRuntimeStateChanged(lifecycle: CodexRuntimeLifecycle) {
        when (lifecycle) {
            CodexRuntimeLifecycle.RUNNING -> refreshConnection()
            CodexRuntimeLifecycle.STARTING -> _state.update {
                it.copy(connection = AgentConnectionState.GATEWAY_STARTING, stableErrorCode = null)
            }
            CodexRuntimeLifecycle.STOPPED,
            CodexRuntimeLifecycle.STOPPING,
            CodexRuntimeLifecycle.FAILED,
            -> {
                loginPollJob?.cancel()
                loginStartInFlight = false
                loginStatusInFlight = false
                switchInFlight = false
                _state.update {
                    it.copy(
                        connection = AgentConnectionState.RUNTIME_STOPPED,
                        models = emptyList(),
                        login = null,
                        recoveredPendingLogin = false,
                        activeRequestId = null,
                        stopRequested = false,
                        refreshing = false,
                    )
                }
            }
        }
    }

    private suspend fun loadModelsAfterAuthentication(agentId: AgentId) {
        val result = withContext(Dispatchers.IO) { runCatching { gatewayClient.models(agentId) } }
        result.fold(
            onSuccess = { models ->
                if (models.isEmpty()) {
                    _state.update {
                        it.copy(
                            connection = AgentConnectionState.STABLE_ERROR,
                            models = emptyList(),
                            selectedModelId = null,
                            refreshing = false,
                            stableErrorCode = "model_list_empty",
                        )
                    }
                } else {
                    _state.update { current ->
                        if (current.selectedAgentId != agentId) return@update current
                        val selected = selectedModelIds[agentId]
                            ?.takeIf { id -> models.any { it.id == id } }
                            ?: models.firstOrNull { it.isDefault }?.id
                            ?: models.first().id
                        selectedModelIds[agentId] = selected
                        current.copy(
                            connection = AgentConnectionState.READY,
                            models = models,
                            selectedModelId = selected,
                            refreshing = false,
                            stableErrorCode = null,
                        )
                    }
                    persistAgentState()
                }
            },
            onFailure =(::setStableError),
        )
    }

    private fun scheduleLoginPoll(challenge: AgentLoginChallenge) {
        loginPollJob?.cancel()
        loginPollJob = viewModelScope.launch {
            delay(challenge.pollIntervalSeconds * 1_000L)
            while (isActive && _state.value.login?.requestId == challenge.requestId) {
                checkDeviceLoginStatus(challenge)
                if (_state.value.connection != AgentConnectionState.LOGIN_PENDING) return@launch
                delay(challenge.pollIntervalSeconds * 1_000L)
            }
        }
    }

    private suspend fun checkDeviceLoginStatus(challenge: AgentLoginChallenge) {
        if (loginStatusInFlight) return
        loginStatusInFlight = true
        nextLoginPollAtMillis = System.currentTimeMillis() + challenge.pollIntervalSeconds * 1_000L
        try {
            val result = withContext(Dispatchers.IO) {
                runCatching { gatewayClient.loginStatus(challenge.agentId, challenge.requestId) }
            }
            result.fold(
                onSuccess = { status ->
                    if (_state.value.login?.requestId != challenge.requestId) return@fold
                    if (status.agentId != challenge.agentId || status.requestId != challenge.requestId) {
                        setStableError(IllegalStateException("login_status_invalid"))
                        return@fold
                    }
                    when (status.state) {
                        "pending" -> Unit
                        "authenticated", "completed" -> {
                            loginPollJob?.cancel()
                            _state.update {
                                it.copy(connection = AgentConnectionState.GATEWAY_STARTING, login = null)
                            }
                            refreshConnection()
                        }
                        "cancelled", "expired", "failed" -> {
                            loginPollJob?.cancel()
                            _state.update {
                                it.copy(
                                    connection = AgentConnectionState.LOGIN_REQUIRED,
                                    login = null,
                                    recoveredPendingLogin = false,
                                    stableErrorCode = if (status.state == "cancelled") null else "login_${status.state}",
                                )
                            }
                        }
                        else -> setStableError(IllegalStateException("login_status_invalid"))
                    }
                },
                onFailure = { error -> reconcilePendingLogin(challenge, error) },
            )
        } finally {
            loginStatusInFlight = false
        }
    }

    private suspend fun reconcilePendingLogin(challenge: AgentLoginChallenge, statusError: Throwable) {
        val account = withContext(Dispatchers.IO) {
            runCatching { gatewayClient.account(challenge.agentId) }
        }
        if (account.getOrNull()?.authenticated == true) {
            loginPollJob?.cancel()
            _state.update { current ->
                if (current.login?.requestId == challenge.requestId) {
                    current.copy(
                        connection = AgentConnectionState.GATEWAY_STARTING,
                        login = null,
                        recoveredPendingLogin = false,
                        refreshing = false,
                        stableErrorCode = null,
                    )
                } else current
            }
            refreshConnection()
            return
        }
        _state.update { current ->
            if (current.login?.requestId != challenge.requestId) return@update current
            if (statusError.isMissingLogin() && account.isSuccess) {
                current.copy(
                    connection = AgentConnectionState.LOGIN_REQUIRED,
                    login = null,
                    recoveredPendingLogin = false,
                    refreshing = false,
                    stableErrorCode = "login_session_lost",
                )
            } else {
                // A transient status/account failure must not discard a browser-completed OAuth
                // attempt. The existing bounded poll resumes without issuing authenticate again.
                current.copy(
                    connection = AgentConnectionState.LOGIN_PENDING,
                    refreshing = false,
                    stableErrorCode = null,
                )
            }
        }
    }

    private fun handleChatEvent(
        event: AgentTurnEvent,
        assistantId: String,
        turn: AgentGatewayChatTurn,
    ) {
        if (event.agentId != _state.value.selectedAgentId || event.agentId != turn.agentId) {
            throw IllegalStateException("agent_event_mismatch")
        }
        val before = _state.value
        when (event) {
            is AgentTurnEvent.Started -> if (
                before.connection != AgentConnectionState.GENERATING || before.activeRequestId != null
            ) {
                throw IllegalStateException("agent_event_order_invalid")
            }
            else -> if (
                before.connection != AgentConnectionState.GENERATING || before.activeRequestId != event.requestId
            ) {
                throw IllegalStateException("agent_event_order_invalid")
            }
        }
        when (event) {
            is AgentTurnEvent.Started -> {
                AgentTurnStateAudit.recordStarted(event.agentId)
                _state.update {
                    it.copy(
                        activeRequestId = event.requestId,
                        conversationId = event.conversationId ?: it.conversationId,
                    )
                }
                _state.value.conversationId?.let { activeConversationIds[event.agentId] = it }
                persistAgentState()
                if (_state.value.stopRequested) {
                    viewModelScope.launch(Dispatchers.IO) {
                        runCatching { turn.stop() }
                            .onSuccess { dispatched ->
                                AgentTurnStateAudit.recordStop(event.agentId, dispatched)
                            }
                            .onFailure(::setStableError)
                    }
                }
            }
            is AgentTurnEvent.Delta -> _state.update { current ->
                current.copy(messages = current.messages.map { message ->
                    if (message.id == assistantId) {
                        message.copy(text = (message.text + event.text).take(MAX_ASSISTANT_CHARS))
                    } else message
                })
            }
            is AgentTurnEvent.Completed -> {
                AgentTurnAudit.record(event.agentId, "done", event.diagnostics)
                _state.update {
                    it.copy(
                        connection = AgentConnectionState.READY,
                        activeRequestId = null,
                        stopRequested = false,
                        stableErrorCode = null,
                    )
                }
                persistAgentState()
            }
            is AgentTurnEvent.Failed -> {
                AgentTurnAudit.record(event.agentId, "error", event.diagnostics)
                _state.update {
                    it.copy(
                        connection = AgentConnectionState.STABLE_ERROR,
                        activeRequestId = null,
                        stopRequested = false,
                        stableErrorCode = event.code,
                    )
                }
                persistAgentState()
            }
        }
    }

    private fun restoreAgent(agentId: AgentId): AgentChatUiState {
        val activeId = activeConversationIds[agentId]
        val active = activeId?.let { archived[bindingKey(agentId, it)] }
        val conversations = archived.values.filter { it.agentId == agentId }
        return AgentChatUiState(
            selectedAgentId = agentId,
            selectedModelId = active?.selectedModelId ?: selectedModelIds[agentId],
            conversationId = active?.conversationId,
            conversations = conversations.asReversed().map(::toSummary),
            messages = active?.messages?.map { message ->
                ChatMessage(UUID.randomUUID().toString(), message.role, message.text)
            }.orEmpty(),
        )
    }

    private fun persistAgentState() {
        val current = _state.value
        current.conversationId?.takeIf { it.isNotBlank() }?.let { conversationId ->
            val key = bindingKey(current.selectedAgentId, conversationId)
            archived.remove(key)
            archived[key] = StoredConversation(
                conversationId = conversationId,
                selectedModelId = current.selectedModelId,
                agentId = current.selectedAgentId,
                messages = current.messages.takeLast(MAX_STORED_MESSAGES).map { message ->
                    StoredChatMessage(message.role, message.text.take(MAX_STORED_MESSAGE_CHARS))
                },
            )
            activeConversationIds[current.selectedAgentId] = conversationId
            while (archived.values.count { it.agentId == current.selectedAgentId } > MAX_ARCHIVED_PER_AGENT) {
                val oldest = archived.entries.first { it.value.agentId == current.selectedAgentId }
                archived.remove(oldest.key)
            }
        }
        current.selectedModelId?.let { selectedModelIds[current.selectedAgentId] = it }
        val summaries = archived.values
            .filter { it.agentId == current.selectedAgentId }
            .asReversed()
            .map(::toSummary)
        val saved = conversationStore.save(
            StoredConversationState(
                activeConversationId = activeConversationIds[current.selectedAgentId],
                conversations = archived.values.toList(),
                selectedAgentId = current.selectedAgentId,
                activeConversationIds = activeConversationIds.toMap(),
                selectedModelIds = selectedModelIds.toMap(),
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
            agentId = conversation.agentId,
        )
    }

    private fun setStableError(error: Throwable) {
        val gateway = error as? GatewayClientException
        val code = gateway?.gatewayCode ?: gateway?.errorCode?.name ?: error.message
            ?.takeIf { it.matches(Regex("[a-z0-9_]{1,64}")) }
            ?: "gateway_unavailable"
        // The code is already a closed/bounded UI-safe value. Never log the exception, response
        // body, prompt, URL, account metadata, or credential material from this boundary.
        Log.i(STABLE_ERROR_AUDIT_TAG, "stable_error=$code")
        _state.update {
            it.copy(
                connection = AgentConnectionState.STABLE_ERROR,
                activeRequestId = null,
                refreshing = false,
                stableErrorCode = code,
            )
        }
    }

    private fun Throwable.isActiveLoginConflict(): Boolean =
        (this as? GatewayClientException)?.gatewayCode in setOf("agent_login_active", "login_already_active")

    private fun Throwable.isMissingLogin(): Boolean =
        (this as? GatewayClientException)?.gatewayCode in setOf(
            "login_not_found",
            "agent_login_not_found",
            "login_not_active",
        )

    override fun onCleared() {
        loginPollJob?.cancel()
        streamJob?.cancel()
        runtimeSubscription.close()
        super.onCleared()
    }

    private companion object {
        const val DEFAULT_LOGIN_POLL_SECONDS = 2
        const val DEFAULT_LOGIN_EXPIRY_SECONDS = 10 * 60
        const val MAX_LOGIN_REQUEST_ID_CHARS = 256
        const val MAX_DEVICE_CODE_CHARS = 128
        const val MAX_DRAFT_BYTES = 16 * 1024
        const val MAX_ASSISTANT_CHARS = 128 * 1024
        const val MAX_ARCHIVED_PER_AGENT = 4
        const val MAX_STORED_MESSAGES = 8
        const val MAX_STORED_MESSAGE_CHARS = 1_024
        const val MAX_CONVERSATION_LABEL_CHARS = 60
        const val STABLE_ERROR_AUDIT_TAG = "AgentStateAudit"

        fun bindingKey(agentId: AgentId, conversationId: String): String =
            agentId.wireValue + "\u0000" + conversationId

        fun validBrowserUrl(agentId: AgentId, value: String): Boolean {
            if (value.length !in 1..2048) return false
            val uri = runCatching { Uri.parse(value) }.getOrNull() ?: return false
            val allowedHosts = when (agentId) {
                AgentId.CODEX -> setOf("auth.openai.com", "chatgpt.com")
                AgentId.GROK -> setOf("auth.x.ai", "accounts.x.ai")
            }
            return uri.scheme == "https" && uri.host in allowedHosts && uri.userInfo == null &&
                uri.fragment == null && uri.port in setOf(-1, 443) && !uri.path.isNullOrEmpty()
        }
    }
}
