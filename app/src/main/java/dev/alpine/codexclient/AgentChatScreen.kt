package dev.alpine.codexclient

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.PersistableBundle
import android.view.WindowManager
import androidx.activity.compose.LocalActivity
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.alpine.codexclient.bridge.AgentId

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun AlpineAgentClientApp(
    runtimeViewModel: RuntimeViewModel,
    chatViewModel: AgentChatViewModel,
) {
    val runtimeState by runtimeViewModel.state.collectAsStateWithLifecycle()
    val chatState by chatViewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var showAgentSheet by rememberSaveable { mutableStateOf(false) }
    var showModelSheet by rememberSaveable { mutableStateOf(false) }
    var showRuntimeSheet by rememberSaveable { mutableStateOf(false) }
    var showConversations by rememberSaveable { mutableStateOf(false) }
    var showOverflow by remember { mutableStateOf(false) }
    var showLogoutConfirmation by rememberSaveable { mutableStateOf(false) }
    var pendingAgentSwitch by remember { mutableStateOf<AgentId?>(null) }

    LaunchedEffect(chatViewModel) {
        chatViewModel.browserLaunches.collect { request ->
            val launched = runCatching {
                context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(request.url)))
            }.isSuccess
            if (!launched) chatViewModel.onBrowserLaunchFailed(request)
        }
    }
    SecureLoginWindow(enabled = chatState.login != null)

    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Scaffold(
                topBar = {
                    TopAppBar(
                        title = {
                            TextButton(
                                modifier = Modifier.testTag("agent-selector"),
                                enabled = !chatState.operationLocked && chatState.agents.size > 1,
                                onClick = { showAgentSheet = true },
                            ) {
                                Text(agentLabel(chatState.selectedAgentId) + " ▾")
                            }
                        },
                        actions = {
                            TextButton(
                                modifier = Modifier.testTag("agent-model-selector"),
                                enabled = !chatState.operationLocked && chatState.models.isNotEmpty(),
                                onClick = { showModelSheet = true },
                            ) { Text(agentSelectedModelLabel(chatState) + " ▾") }
                            Box {
                                TextButton(
                                    modifier = Modifier.testTag("agent-overflow-action"),
                                    onClick = { showOverflow = true },
                                ) { Text("⋮") }
                                DropdownMenu(
                                    expanded = showOverflow,
                                    onDismissRequest = { showOverflow = false },
                                ) {
                                    DropdownMenuItem(
                                        text = { Text("상태 · Runtime") },
                                        onClick = {
                                            showOverflow = false
                                            showRuntimeSheet = true
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("연결 다시 확인") },
                                        enabled = !chatState.operationLocked,
                                        onClick = {
                                            showOverflow = false
                                            chatViewModel.refreshConnection()
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("새 대화") },
                                        enabled = !chatState.operationLocked,
                                        onClick = {
                                            showOverflow = false
                                            chatViewModel.newConversation()
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("대화 목록") },
                                        enabled = !chatState.operationLocked && chatState.conversations.isNotEmpty(),
                                        onClick = {
                                            showOverflow = false
                                            showConversations = true
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("${agentLabel(chatState.selectedAgentId)} 로그아웃") },
                                        enabled = !chatState.operationLocked && chatState.connection == AgentConnectionState.READY,
                                        onClick = {
                                            showOverflow = false
                                            showLogoutConfirmation = true
                                        },
                                    )
                                }
                            }
                        },
                    )
                },
            ) { padding ->
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(padding),
                ) {
                    AgentChatViewport(
                        state = chatState,
                        onLogin = chatViewModel::startDeviceLogin,
                        onCancelRecoveredLogin = chatViewModel::cancelRecoveredDeviceLogin,
                        onRefresh = chatViewModel::refreshConnection,
                        modifier = Modifier.weight(1f),
                    )
                    AgentComposer(
                        state = chatState,
                        onDraftChange = chatViewModel::updateDraft,
                        onSendOrStop = {
                            if (chatState.isGenerating) chatViewModel.stopGeneration() else chatViewModel.send()
                        },
                    )
                }
            }
        }
    }

    if (showAgentSheet) {
        AgentSelectorSheet(
            state = chatState,
            onDismiss = { showAgentSheet = false },
            onSelect = { target ->
                showAgentSheet = false
                if (chatState.draft.isNotBlank() || chatState.conversationId != null) {
                    pendingAgentSwitch = target
                } else {
                    chatViewModel.switchAgent(target)
                }
            },
        )
    }
    pendingAgentSwitch?.let { target ->
        AlertDialog(
            onDismissRequest = { pendingAgentSwitch = null },
            title = { Text("${agentLabel(target)}로 전환") },
            text = { Text("현재 작성 중인 입력은 지워지고, 대화 목록과 모델은 Agent별로 분리됩니다. 로그인은 자동 해제하지 않습니다.") },
            confirmButton = {
                TextButton(onClick = {
                    pendingAgentSwitch = null
                    chatViewModel.switchAgent(target)
                }) { Text("전환") }
            },
            dismissButton = {
                TextButton(onClick = { pendingAgentSwitch = null }) { Text("취소") }
            },
        )
    }
    if (showModelSheet) {
        AgentModelSelectorSheet(
            state = chatState,
            onDismiss = { showModelSheet = false },
            onSelect = {
                chatViewModel.selectModel(it)
                showModelSheet = false
            },
        )
    }
    if (showRuntimeSheet) {
        RuntimeStatusSheet(
            runtimeState = runtimeState,
            generationActive = chatState.isGenerating,
            onDismiss = { showRuntimeSheet = false },
            onInstall = runtimeViewModel::install,
            onStart = runtimeViewModel::start,
            onStop = runtimeViewModel::stop,
            onRefresh = runtimeViewModel::refresh,
            onPreparePython = runtimeViewModel::runSmoke,
            onPrepareCli = runtimeViewModel::prepareCodexCli,
            onAppServerSmoke = runtimeViewModel::runAppServerSmoke,
        )
    }
    if (showConversations) {
        AgentConversationListSheet(
            state = chatState,
            onDismiss = { showConversations = false },
            onSelect = {
                chatViewModel.selectConversation(it)
                showConversations = false
            },
        )
    }
    chatState.login?.let { challenge ->
        AgentDeviceLoginSheet(
            challenge = challenge,
            onOpenBrowser = chatViewModel::openLoginBrowser,
            onCheck = chatViewModel::checkDeviceLoginStatus,
            onCancel = chatViewModel::cancelDeviceLogin,
        )
    }
    if (showLogoutConfirmation) {
        AlertDialog(
            onDismissRequest = { showLogoutConfirmation = false },
            title = { Text("${agentLabel(chatState.selectedAgentId)} 로그아웃") },
            text = { Text("선택한 Agent의 공식 CLI 계정 연결과 이 기기의 해당 Agent 대화 상태만 해제합니다.") },
            confirmButton = {
                TextButton(onClick = {
                    showLogoutConfirmation = false
                    chatViewModel.logout()
                }) { Text("로그아웃") }
            },
            dismissButton = {
                TextButton(onClick = { showLogoutConfirmation = false }) { Text("취소") }
            },
        )
    }
}

@Composable
internal fun SecureLoginWindow(enabled: Boolean) {
    val activity = LocalActivity.current ?: return
    DisposableEffect(activity, enabled) {
        if (!enabled) return@DisposableEffect onDispose { }
        val wasSecure = activity.window.attributes.flags and WindowManager.LayoutParams.FLAG_SECURE != 0
        activity.window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        onDispose {
            if (!wasSecure) activity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
        }
    }
}

@Composable
internal fun AgentChatViewport(
    state: AgentChatUiState,
    onLogin: () -> Unit,
    onCancelRecoveredLogin: () -> Unit,
    onRefresh: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state.messages.isNotEmpty()) {
        LazyColumn(
            modifier = modifier.fillMaxWidth(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(state.messages, key = { it.id }) { message ->
                AgentMessageCard(message, state.selectedAgentId)
            }
            if (state.isGenerating) {
                item { Text(if (state.stopRequested) "중단 요청 중…" else "응답 생성 중…") }
            }
        }
    } else {
        Column(
            modifier = modifier
                .fillMaxWidth()
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            when (state.connection) {
                AgentConnectionState.RUNTIME_STOPPED -> Text("Runtime을 시작하면 Agent 로그인과 대화를 사용할 수 있습니다.")
                AgentConnectionState.GATEWAY_STARTING -> StatusProgress("Gateway 연결을 확인하는 중입니다.")
                AgentConnectionState.SWITCHING -> StatusProgress("${agentLabel(state.selectedAgentId)}로 전환하는 중입니다.")
                AgentConnectionState.LOGIN_REQUIRED -> if (state.recoveredPendingLogin) {
                    Text("이전에 시작한 ${agentLabel(state.selectedAgentId)} 로그인이 활성입니다. 새 로그인 전에 취소하세요.")
                    Spacer(Modifier.size(12.dp))
                    OutlinedButton(
                        modifier = Modifier.testTag("agent-cancel-recovered-login"),
                        enabled = !state.refreshing,
                        onClick = onCancelRecoveredLogin,
                    ) { Text("현재 로그인 취소") }
                } else {
                    Text("공식 ${agentLabel(state.selectedAgentId)} Device 로그인을 시작하세요.")
                    Spacer(Modifier.size(12.dp))
                    Button(
                        modifier = Modifier.testTag("agent-login-action"),
                        enabled = !state.refreshing,
                        onClick = onLogin,
                    ) { Text("${agentLabel(state.selectedAgentId)} 로그인") }
                }
                AgentConnectionState.LOGIN_PENDING -> Text("브라우저 승인을 기다리는 중입니다.")
                AgentConnectionState.READY -> Text("모델을 선택하고 메시지를 보내세요.")
                AgentConnectionState.GENERATING -> Text("응답을 생성하는 중입니다.")
                AgentConnectionState.STABLE_ERROR -> {
                    Text("상태: ${state.stableErrorCode ?: "unknown"}")
                    Spacer(Modifier.size(12.dp))
                    OutlinedButton(onClick = onRefresh) { Text("상태 다시 확인") }
                }
            }
        }
    }
}

@Composable
private fun StatusProgress(label: String) {
    CircularProgressIndicator(modifier = Modifier.size(28.dp))
    Spacer(Modifier.size(12.dp))
    Text(label)
}

@Composable
private fun AgentMessageCard(message: ChatMessage, agentId: AgentId) {
    val label = if (message.role == ChatRole.USER) "나" else agentLabel(agentId)
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
            Text(
                text = message.text.ifEmpty { "…" },
                modifier = Modifier.padding(top = 4.dp),
            )
        }
    }
}

@Composable
internal fun AgentComposer(
    state: AgentChatUiState,
    onDraftChange: (String) -> Unit,
    onSendOrStop: () -> Unit,
) {
    Surface(modifier = Modifier.fillMaxWidth(), tonalElevation = 2.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                modifier = Modifier
                    .weight(1f)
                    .testTag("agent-composer"),
                value = state.draft,
                enabled = state.connection == AgentConnectionState.READY,
                onValueChange = onDraftChange,
                label = { Text("메시지") },
                maxLines = 4,
            )
            Button(
                modifier = Modifier.testTag("agent-send-stop"),
                enabled = state.isGenerating ||
                    (state.connection == AgentConnectionState.READY && state.draft.isNotBlank()),
                onClick = onSendOrStop,
            ) { Text(if (state.isGenerating) "Stop" else "전송") }
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun AgentSelectorSheet(
    state: AgentChatUiState,
    onDismiss: () -> Unit,
    onSelect: (AgentId) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("Agent 선택", style = MaterialTheme.typography.titleLarge)
            state.agents.forEach { agent ->
                TextButton(
                    modifier = Modifier.fillMaxWidth().testTag("agent-option-${agent.agentId.wireValue}"),
                    enabled = agent.agentId != state.selectedAgentId && !state.operationLocked,
                    onClick = { onSelect(agent.agentId) },
                ) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        Text(agentLabel(agent.agentId))
                        Text(
                            if (agent.ready) "준비됨" else "선택 시 시작",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                }
            }
            Spacer(Modifier.size(16.dp))
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun AgentModelSelectorSheet(
    state: AgentChatUiState,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("${agentLabel(state.selectedAgentId)} 모델", style = MaterialTheme.typography.titleLarge)
            state.models.forEach { model ->
                TextButton(
                    modifier = Modifier.fillMaxWidth().testTag("agent-model-${model.id}"),
                    enabled = model.id != state.selectedModelId && !state.operationLocked,
                    onClick = { onSelect(model.id) },
                ) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        Text(model.displayName)
                        Text(model.id, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
            Spacer(Modifier.size(16.dp))
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun AgentConversationListSheet(
    state: AgentChatUiState,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("${agentLabel(state.selectedAgentId)} 최근 대화", style = MaterialTheme.typography.titleLarge)
            state.conversations.forEach { conversation ->
                TextButton(
                    modifier = Modifier.fillMaxWidth().testTag("agent-conversation-${conversation.conversationId}"),
                    enabled = conversation.conversationId != state.conversationId,
                    onClick = { onSelect(conversation.conversationId) },
                ) {
                    Column(modifier = Modifier.fillMaxWidth()) {
                        Text(conversation.label)
                        conversation.selectedModelId?.let { Text(it, style = MaterialTheme.typography.labelSmall) }
                    }
                }
            }
            Spacer(Modifier.size(16.dp))
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun AgentDeviceLoginSheet(
    challenge: AgentLoginChallenge,
    onOpenBrowser: () -> Unit,
    onCheck: () -> Unit,
    onCancel: () -> Unit,
) {
    val context = LocalContext.current
    ModalBottomSheet(onDismissRequest = { /* explicit cancellation owns the pending login */ }) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("${agentLabel(challenge.agentId)} Device 로그인", style = MaterialTheme.typography.titleLarge)
            if (challenge.agentId == AgentId.GROK) {
                Text("공식 xAI 인증 페이지를 열었습니다. 브라우저에서 승인한 뒤 이 화면으로 돌아오세요.")
            } else {
                Text("브라우저에서 로그인한 뒤 아래 Device Code를 입력하고 승인하세요.")
                challenge.userCode?.let { code ->
                    Text(
                        code,
                        style = MaterialTheme.typography.headlineSmall,
                        modifier = Modifier.clearAndSetSemantics { },
                    )
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Button(
                            modifier = Modifier.weight(1f).testTag("agent-open-browser"),
                            onClick = onOpenBrowser,
                        ) { Text("브라우저 열기") }
                        OutlinedButton(
                            modifier = Modifier.weight(1f).testTag("agent-copy-device-code"),
                            onClick = { copySensitiveDeviceCode(context, code) },
                        ) { Text("코드 복사") }
                    }
                }
            }
            Text("만료: 약 ${challenge.expiresInSeconds / 60}분")
            OutlinedButton(onClick = onCheck) { Text("승인 상태 확인") }
            TextButton(
                modifier = Modifier.testTag("agent-login-cancel"),
                onClick = onCancel,
            ) { Text("로그인 취소") }
            Spacer(Modifier.size(16.dp))
        }
    }
}

private fun copySensitiveDeviceCode(context: Context, code: String) {
    val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return
    val clip = ClipData.newPlainText("Device Code", code)
    clip.description.extras = PersistableBundle().apply {
        // The documented key is a String extra and is safe to attach on pre-33 devices.
        putBoolean(SENSITIVE_CLIP_EXTRA, true)
    }
    clipboard.setPrimaryClip(clip)
    Handler(Looper.getMainLooper()).postDelayed({
        val current = clipboard.primaryClip
        val unchanged = current?.itemCount == 1 && current.getItemAt(0).coerceToText(context).toString() == code
        if (unchanged) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) clipboard.clearPrimaryClip()
            else clipboard.setPrimaryClip(ClipData.newPlainText("", ""))
        }
    }, DEVICE_CODE_CLIPBOARD_TTL_MILLIS)
}

private fun agentLabel(agentId: AgentId): String = when (agentId) {
    AgentId.CODEX -> "Codex"
    AgentId.GROK -> "Grok"
}

private fun agentSelectedModelLabel(state: AgentChatUiState): String =
    state.models.firstOrNull { it.id == state.selectedModelId }?.displayName
        ?.take(18)
        ?: "모델"

private const val DEVICE_CODE_CLIPBOARD_TTL_MILLIS = 60_000L
private const val SENSITIVE_CLIP_EXTRA = "android.content.extra.IS_SENSITIVE"
