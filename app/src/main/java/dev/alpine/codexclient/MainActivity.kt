package dev.alpine.codexclient

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.alpine.codexclient.bridge.CodexRuntimeLifecycle

class MainActivity : ComponentActivity() {
    private val runtimeViewModel: RuntimeViewModel by viewModels()
    private val chatViewModel: AgentChatViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
        setContent { AlpineAgentClientApp(runtimeViewModel, chatViewModel) }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun AlpineCodexClientApp(
    runtimeViewModel: RuntimeViewModel,
    chatViewModel: CodexChatViewModel,
) {
    val runtimeState by runtimeViewModel.state.collectAsStateWithLifecycle()
    val chatState by chatViewModel.state.collectAsStateWithLifecycle()
    var showRuntimeSheet by rememberSaveable { mutableStateOf(false) }
    var showOverflow by remember { mutableStateOf(false) }
    var showConversations by rememberSaveable { mutableStateOf(false) }
    var showModels by rememberSaveable { mutableStateOf(false) }
    var showLogoutConfirmation by rememberSaveable { mutableStateOf(false) }

    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Scaffold(
                topBar = {
                    TopAppBar(
                        title = {
                            Column {
                                Text("Alpine Codex")
                                Text(
                                    text = selectedModelLabel(chatState),
                                    style = MaterialTheme.typography.labelSmall,
                                )
                            }
                        },
                        actions = {
                            TextButton(
                                modifier = Modifier.testTag("codex-status-action"),
                                onClick = { showRuntimeSheet = true },
                            ) { Text(connectionLabel(chatState.connection)) }
                            Box {
                                TextButton(
                                    modifier = Modifier.testTag("codex-overflow-action"),
                                    onClick = { showOverflow = true },
                                ) { Text("⋮") }
                                DropdownMenu(expanded = showOverflow, onDismissRequest = { showOverflow = false }) {
                                    DropdownMenuItem(
                                        text = { Text("연결 새로고침") },
                                        onClick = {
                                            showOverflow = false
                                            chatViewModel.refreshConnection()
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("새 대화") },
                                        enabled = !chatState.isGenerating,
                                        onClick = {
                                            showOverflow = false
                                            chatViewModel.newConversation()
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("대화 목록") },
                                        enabled = !chatState.isGenerating && chatState.conversations.isNotEmpty(),
                                        onClick = {
                                            showOverflow = false
                                            showConversations = true
                                        },
                                    )
                                    DropdownMenuItem(
                                        text = { Text("로그아웃") },
                                        enabled = !chatState.isGenerating && chatState.connection == CodexConnectionState.READY,
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
                    ChatViewport(
                        state = chatState,
                        onLogin = chatViewModel::startDeviceLogin,
                        onCancelRecoveredLogin = chatViewModel::cancelRecoveredDeviceLogin,
                        onRefresh = chatViewModel::refreshConnection,
                        modifier = Modifier.weight(1f),
                    )
                    Composer(
                        state = chatState,
                        onModelClick = { showModels = true },
                        onDraftChange = chatViewModel::updateDraft,
                        onSendOrStop = {
                            if (chatState.isGenerating) chatViewModel.stopGeneration() else chatViewModel.send()
                        },
                    )
                }
            }
        }
    }

    if (showRuntimeSheet) {
        RuntimeStatusSheet(
            runtimeState = runtimeState,
            generationActive = chatState.isGenerating,
            onDismiss = { showRuntimeSheet = false },
            onInstall = runtimeViewModel::install,
            onStartAlpine = runtimeViewModel::startAlpine,
            onStartGateway = runtimeViewModel::startGateway,
            onStop = runtimeViewModel::stop,
            onRefresh = runtimeViewModel::refresh,
            onPreparePython = runtimeViewModel::runSmoke,
            onPrepareCli = runtimeViewModel::prepareCodexCli,
            onAppServerSmoke = runtimeViewModel::runAppServerSmoke,
        )
    }
    if (showModels) {
        ModelSelectorSheet(
            state = chatState,
            onDismiss = { showModels = false },
            onSelect = {
                chatViewModel.selectModel(it)
                showModels = false
            },
        )
    }
    if (showConversations) {
        ConversationListSheet(
            conversations = chatState.conversations,
            activeConversationId = chatState.conversationId,
            onDismiss = { showConversations = false },
            onSelect = {
                chatViewModel.selectConversation(it)
                showConversations = false
            },
        )
    }
    chatState.login?.let { challenge ->
        DeviceLoginSheet(
            challenge = challenge,
            onCheck = chatViewModel::checkDeviceLoginStatus,
            onCancel = chatViewModel::cancelDeviceLogin,
        )
    }
    if (showLogoutConfirmation) {
        AlertDialog(
            onDismissRequest = { showLogoutConfirmation = false },
            title = { Text("Codex 로그아웃") },
            text = { Text("현재 Alpine Codex CLI 계정 연결을 해제합니다. 대화 상태도 이 기기에서 지웁니다.") },
            confirmButton = {
                TextButton(onClick = {
                    showLogoutConfirmation = false
                    chatViewModel.logout()
                }) { Text("로그아웃") }
            },
            dismissButton = { TextButton(onClick = { showLogoutConfirmation = false }) { Text("취소") } },
        )
    }
}

@Composable
fun ChatViewport(
    state: CodexChatUiState,
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
            items(state.messages, key = { it.id }) { message -> MessageCard(message) }
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
                CodexConnectionState.RUNTIME_STOPPED -> {
                    Text("Codex Runtime을 시작하면 로그인과 대화를 사용할 수 있습니다.")
                }
                CodexConnectionState.GATEWAY_STARTING -> {
                    CircularProgressIndicator(modifier = Modifier.size(28.dp))
                    Spacer(Modifier.size(12.dp))
                    Text("Codex gateway 연결을 확인하는 중입니다.")
                }
                CodexConnectionState.LOGIN_REQUIRED -> {
                    if (state.recoveredPendingLogin) {
                        Text("이전에 시작한 Device Code 로그인이 아직 활성입니다. 취소한 뒤 새 코드를 시작하세요.")
                        Spacer(Modifier.size(12.dp))
                        OutlinedButton(
                            modifier = Modifier.testTag("codex-cancel-recovered-login"),
                            enabled = !state.refreshing,
                            onClick = onCancelRecoveredLogin,
                        ) { Text("현재 로그인 취소") }
                    } else {
                        Text("공식 Codex Device Code 로그인으로 계정을 연결하세요.")
                        Spacer(Modifier.size(12.dp))
                        Button(
                            modifier = Modifier.testTag("codex-login-action"),
                            enabled = !state.refreshing,
                            onClick = onLogin,
                        ) { Text("Codex 로그인") }
                    }
                }
                CodexConnectionState.LOGIN_PENDING -> Text("브라우저에서 승인을 완료한 뒤 상태를 확인합니다.")
                CodexConnectionState.READY -> Text("모델을 선택하고 첫 메시지를 보내세요.")
                CodexConnectionState.GENERATING -> Text("응답을 생성하는 중입니다.")
                CodexConnectionState.STABLE_ERROR -> {
                    Text("연결 상태: ${state.stableErrorCode ?: "unknown"}")
                    Spacer(Modifier.size(12.dp))
                    OutlinedButton(onClick = onRefresh) { Text("연결 다시 확인") }
                }
            }
        }
    }
}

@Composable
private fun MessageCard(message: ChatMessage) {
    val label = if (message.role == ChatRole.USER) "나" else "Codex"
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(label, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold)
            if (message.text.isNotEmpty()) {
                Text(message.text, modifier = Modifier.padding(top = 4.dp))
            } else if (message.role == ChatRole.ASSISTANT) {
                Text("…", modifier = Modifier.padding(top = 4.dp))
            }
        }
    }
}

@Composable
fun Composer(
    state: CodexChatUiState,
    onModelClick: () -> Unit,
    onDraftChange: (String) -> Unit,
    onSendOrStop: () -> Unit,
) {
    Surface(modifier = Modifier.fillMaxWidth(), tonalElevation = 2.dp) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedButton(
                modifier = Modifier.testTag("codex-model-selector"),
                enabled = state.models.isNotEmpty() && !state.isGenerating,
                onClick = onModelClick,
            ) { Text(selectedModelLabel(state) + " ▾") }
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    modifier = Modifier
                        .weight(1f)
                        .testTag("codex-composer"),
                    value = state.draft,
                    enabled = state.connection == CodexConnectionState.READY,
                    onValueChange = onDraftChange,
                    label = { Text("메시지") },
                    maxLines = 4,
                )
                Button(
                    modifier = Modifier.testTag("codex-send-stop"),
                    enabled = state.isGenerating || (state.connection == CodexConnectionState.READY && state.draft.isNotBlank()),
                    onClick = onSendOrStop,
                ) { Text(if (state.isGenerating) "Stop" else "전송") }
            }
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun RuntimeStatusSheet(
    runtimeState: RuntimeUiState,
    generationActive: Boolean,
    onDismiss: () -> Unit,
    onInstall: () -> Unit,
    onStartAlpine: () -> Unit,
    onStartGateway: () -> Unit,
    onStop: () -> Unit,
    onRefresh: () -> Unit,
    onPreparePython: () -> Unit,
    onPrepareCli: () -> Unit,
    onAppServerSmoke: () -> Unit,
) {
    var showDiagnostics by remember { mutableStateOf(false) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text("Runtime 상태", style = MaterialTheme.typography.titleLarge)
            Text("Alpine: ${runtimeState.lifecycle}")
            Text("Gateway: ${runtimeState.gatewayLifecycle}")
            Text("작업: ${runtimeState.status}")
            runtimeState.errorCode?.let { Text("오류: ${it.name}") }
            if (runtimeState.lifecycle == dev.alpine.runtime.api.RuntimeLifecycleState.NOT_INSTALLED) {
                Button(enabled = !runtimeState.busy && !generationActive, onClick = onInstall) { Text("Runtime 설치") }
            }
            if (runtimeState.lifecycle == dev.alpine.runtime.api.RuntimeLifecycleState.READY) {
                Button(enabled = !runtimeState.busy && !generationActive, onClick = onStartAlpine) { Text("Alpine 시작") }
            }
            if (runtimeState.sessionActive) {
                if (
                    runtimeState.gatewayLifecycle == CodexRuntimeLifecycle.STOPPED ||
                    runtimeState.gatewayLifecycle == CodexRuntimeLifecycle.FAILED
                ) {
                    val pythonReady = runtimeState.gatewayPythonBootstrap == GatewayPythonBootstrapOutcome.ALREADY_AVAILABLE ||
                        runtimeState.gatewayPythonBootstrap == GatewayPythonBootstrapOutcome.INSTALLED
                    Button(
                        enabled = !runtimeState.busy && !generationActive && pythonReady,
                        onClick = onStartGateway,
                    ) { Text("Gateway 시작") }
                    if (!pythonReady) Text("진단에서 Gateway Python 준비를 먼저 실행하세요.")
                }
                OutlinedButton(enabled = !runtimeState.busy && !generationActive, onClick = onStop) { Text("Runtime 종료") }
                Box {
                    OutlinedButton(enabled = !runtimeState.busy && !generationActive, onClick = { showDiagnostics = true }) {
                        Text("진단 ▾")
                    }
                    DropdownMenu(expanded = showDiagnostics, onDismissRequest = { showDiagnostics = false }) {
                        DropdownMenuItem(
                            text = { Text("Gateway Python 준비") },
                            onClick = {
                                showDiagnostics = false
                                onPreparePython()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("Codex CLI 점검") },
                            onClick = {
                                showDiagnostics = false
                                onPrepareCli()
                            },
                        )
                        DropdownMenuItem(
                            text = { Text("app-server 점검") },
                            onClick = {
                                showDiagnostics = false
                                onAppServerSmoke()
                            },
                        )
                    }
                }
            }
            OutlinedButton(enabled = !runtimeState.busy && !generationActive, onClick = onRefresh) { Text("Runtime 상태 확인") }
            Spacer(Modifier.size(16.dp))
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
fun ModelSelectorSheet(
    state: CodexChatUiState,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("사용 가능한 Codex 모델", style = MaterialTheme.typography.titleLarge)
            state.models.forEach { model ->
                TextButton(
                    modifier = Modifier.fillMaxWidth(),
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
fun ConversationListSheet(
    conversations: List<ConversationSummary>,
    activeConversationId: String?,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("최근 대화", style = MaterialTheme.typography.titleLarge)
            conversations.forEach { conversation ->
                TextButton(
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("codex-conversation-${conversation.conversationId}"),
                    enabled = conversation.conversationId != activeConversationId,
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
fun DeviceLoginSheet(
    challenge: DeviceCodeChallenge,
    onCheck: () -> Unit,
    onCancel: () -> Unit,
) {
    val context = LocalContext.current
    ModalBottomSheet(onDismissRequest = { /* login must be cancelled explicitly */ }) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Codex Device Code 로그인", style = MaterialTheme.typography.titleLarge)
            Text("1. 아래 Device Code를 복사합니다. 2. 브라우저에서 로그인한 뒤 Device Code 입력란에 붙여넣고 승인합니다.")
            TextButton(
                modifier = Modifier.testTag("codex-login-cancel"),
                onClick = onCancel,
            ) { Text("로그인 취소") }
            Text(challenge.userCode, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.testTag("codex-device-code"))
            Text("만료: 약 ${challenge.expiresInSeconds / 60}분 · 상태 확인 간격: ${challenge.pollIntervalSeconds}초")
            OutlinedButton(
                modifier = Modifier.testTag("codex-copy-device-code"),
                onClick = {
                    val clipboard = context.getSystemService(ClipboardManager::class.java)
                    clipboard?.setPrimaryClip(ClipData.newPlainText("Codex Device Code", challenge.userCode))
                },
            ) { Text("코드 복사") }
            Button(
                modifier = Modifier.testTag("codex-open-browser"),
                onClick = {
                    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(challenge.verificationUrl)))
                },
            ) { Text("브라우저 열기") }
            OutlinedButton(onClick = onCheck) { Text("승인 상태 확인") }
            Spacer(Modifier.size(16.dp))
        }
    }
}

private fun selectedModelLabel(state: CodexChatUiState): String =
    state.models.firstOrNull { it.id == state.selectedModelId }?.displayName ?: "모델 선택"

private fun connectionLabel(connection: CodexConnectionState): String = when (connection) {
    CodexConnectionState.RUNTIME_STOPPED -> "Runtime"
    CodexConnectionState.GATEWAY_STARTING -> "연결 중"
    CodexConnectionState.LOGIN_REQUIRED -> "로그인 필요"
    CodexConnectionState.LOGIN_PENDING -> "승인 대기"
    CodexConnectionState.READY -> "준비됨"
    CodexConnectionState.GENERATING -> "생성 중"
    CodexConnectionState.STABLE_ERROR -> "오류"
}
