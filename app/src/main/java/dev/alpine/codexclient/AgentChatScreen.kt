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
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
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
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberModalBottomSheetState
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.ui.components.AlpinePanel
import dev.alpine.codexclient.ui.components.AlpineSectionHeader
import dev.alpine.codexclient.ui.components.AlpineStatusBadge
import dev.alpine.codexclient.ui.theme.AlpineAcid
import dev.alpine.codexclient.ui.theme.AlpineCodex
import dev.alpine.codexclient.ui.theme.AlpineError
import dev.alpine.codexclient.ui.theme.AlpineErrorInk
import dev.alpine.codexclient.ui.theme.AlpineGrok
import dev.alpine.codexclient.ui.theme.AlpineHighSurface
import dev.alpine.codexclient.ui.theme.AlpineInfo
import dev.alpine.codexclient.ui.theme.AlpineInk
import dev.alpine.codexclient.ui.theme.AlpineLocal
import dev.alpine.codexclient.ui.theme.AlpineOutline
import dev.alpine.codexclient.ui.theme.AlpinePaper
import dev.alpine.codexclient.ui.theme.AlpineRaisedSurface
import dev.alpine.codexclient.ui.theme.AlpineSlate
import dev.alpine.codexclient.ui.theme.AlpineStrongOutline
import dev.alpine.codexclient.ui.theme.AlpineWarning

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

    Surface(modifier = Modifier.fillMaxSize()) {
        Scaffold(
            containerColor = AlpinePaper,
            topBar = {
                TopAppBar(
                    title = { AlpineBrandLockup() },
                    actions = {
                        Box {
                            TextButton(
                                modifier = Modifier.testTag("agent-overflow-action"),
                                onClick = { showOverflow = true },
                            ) {
                                Text(
                                    text = "⋮",
                                    style = MaterialTheme.typography.titleLarge,
                                )
                            }
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
                    colors = TopAppBarDefaults.topAppBarColors(
                        containerColor = AlpinePaper,
                        scrolledContainerColor = AlpinePaper,
                        titleContentColor = AlpineInk,
                        actionIconContentColor = AlpineInk,
                    ),
                )
            },
        ) { padding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
            ) {
                AgentControlStrip(
                    state = chatState,
                    onAgentClick = { showAgentSheet = true },
                    onModelClick = { showModelSheet = true },
                )
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
            onStartAlpine = runtimeViewModel::startAlpine,
            onStartGateway = runtimeViewModel::startGateway,
            onStop = runtimeViewModel::stop,
            onRefresh = runtimeViewModel::refresh,
            onPreparePython = runtimeViewModel::runSmoke,
            onPrepareCli = runtimeViewModel::prepareCodexCli,
            onAppServerSmoke = runtimeViewModel::runAppServerSmoke,
            onGrokAcpSmoke = runtimeViewModel::runGrokAcpSmoke,
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
private fun AlpineBrandLockup() {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Surface(
            modifier = Modifier.size(38.dp),
            color = AlpineInk,
            contentColor = AlpineAcid,
            shape = MaterialTheme.shapes.small,
        ) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    text = "A>",
                    style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Black),
                )
            }
        }
        Column {
            Text(
                text = "ALPINE AGENT",
                style = MaterialTheme.typography.labelLarge.copy(
                    fontWeight = FontWeight.Black,
                    letterSpacing = 0.9.sp,
                ),
            )
            Text(
                text = "PRIVATE CLI RUNTIME",
                color = AlpineInk.copy(alpha = 0.58f),
                style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 0.45.sp),
            )
        }
    }
}

@Composable
private fun AgentControlStrip(
    state: AgentChatUiState,
    onAgentClick: () -> Unit,
    onModelClick: () -> Unit,
) {
    Surface(color = AlpinePaper, tonalElevation = 0.dp, shadowElevation = 0.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedButton(
                modifier = Modifier
                    .weight(1f)
                    .heightIn(min = 52.dp)
                    .testTag("agent-selector"),
                enabled = !state.operationLocked && state.agents.size > 1,
                onClick = onAgentClick,
                shape = MaterialTheme.shapes.medium,
                border = BorderStroke(1.dp, AlpineStrongOutline),
                colors = ButtonDefaults.outlinedButtonColors(
                    contentColor = AlpineInk,
                    disabledContentColor = AlpineInk.copy(alpha = 0.72f),
                ),
                contentPadding = PaddingValues(horizontal = 10.dp, vertical = 7.dp),
            ) {
                AgentMark(state.selectedAgentId, Modifier.size(32.dp))
                Spacer(Modifier.size(8.dp))
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = "AGENT",
                        style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 0.8.sp),
                    )
                    Text(
                        text = agentLabel(state.selectedAgentId) + "  ▾",
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.titleMedium,
                    )
                }
            }
            OutlinedButton(
                modifier = Modifier
                    .weight(1.35f)
                    .heightIn(min = 52.dp)
                    .testTag("agent-model-selector"),
                enabled = !state.operationLocked && state.models.isNotEmpty(),
                onClick = onModelClick,
                shape = MaterialTheme.shapes.medium,
                border = BorderStroke(1.dp, AlpineStrongOutline),
                colors = ButtonDefaults.outlinedButtonColors(
                    contentColor = AlpineInk,
                    disabledContentColor = AlpineInk.copy(alpha = 0.56f),
                ),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 7.dp),
            ) {
                Column(modifier = Modifier.fillMaxWidth()) {
                    Text(
                        text = "MODEL",
                        style = MaterialTheme.typography.labelSmall.copy(letterSpacing = 0.8.sp),
                    )
                    Text(
                        text = agentSelectedModelLabel(state) + "  ▾",
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        style = MaterialTheme.typography.titleMedium,
                    )
                }
            }
        }
    }
}

@Composable
private fun AgentMark(agentId: AgentId, modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        color = if (agentId == AgentId.GROK) AlpineGrok else AlpineInk,
        contentColor = AlpinePaper,
        shape = CircleShape,
    ) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(
                text = if (agentId == AgentId.GROK) "GR" else "CX",
                style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Black),
            )
        }
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
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            items(state.messages, key = { it.id }) { message ->
                AgentMessageCard(message, state.selectedAgentId)
            }
            if (state.isGenerating) {
                item { LiveRunIndicator(stopRequested = state.stopRequested) }
            }
        }
    } else {
        Box(
            modifier = modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 16.dp),
            contentAlignment = Alignment.Center,
        ) {
            AgentConnectionHero(
                state = state,
                onLogin = onLogin,
                onCancelRecoveredLogin = onCancelRecoveredLogin,
                onRefresh = onRefresh,
            )
        }
    }
}

private data class AgentHeroContent(
    val badge: String,
    val headline: String,
    val body: String,
    val badgeColor: androidx.compose.ui.graphics.Color = AlpineAcid,
    val badgeContentColor: androidx.compose.ui.graphics.Color = AlpineInk,
    val showProgress: Boolean = false,
)

@Composable
private fun AgentConnectionHero(
    state: AgentChatUiState,
    onLogin: () -> Unit,
    onCancelRecoveredLogin: () -> Unit,
    onRefresh: () -> Unit,
) {
    val agent = agentLabel(state.selectedAgentId)
    val content = when (state.connection) {
        AgentConnectionState.RUNTIME_STOPPED -> AgentHeroContent(
            badge = "RUNTIME OFFLINE",
            headline = "런타임을 준비하면\nAgent를 사용할 수 있습니다.",
            body = "상태 메뉴에서 app-private Alpine Runtime을 시작하세요.",
            badgeColor = AlpineWarning,
        )
        AgentConnectionState.GATEWAY_STARTING -> AgentHeroContent(
            badge = "GATEWAY CHECK",
            headline = "안전한 로컬 연결을\n확인하고 있습니다.",
            body = "인증된 Gateway와 선택 Agent의 준비 상태를 확인합니다.",
            showProgress = true,
        )
        AgentConnectionState.SWITCHING -> AgentHeroContent(
            badge = "AGENT SWITCH",
            headline = "${agent}로\n전환하고 있습니다.",
            body = "활성 작업을 만들지 않고 선택한 CLI의 준비 상태만 확인합니다.",
            showProgress = true,
        )
        AgentConnectionState.LOGIN_REQUIRED -> if (state.recoveredPendingLogin) {
            AgentHeroContent(
                badge = "LOGIN RECOVERY",
                headline = "진행 중인 로그인을\n먼저 정리하세요.",
                body = "이전에 시작한 $agent 로그인이 활성입니다. 새 로그인 전에 취소해야 합니다.",
                badgeColor = AlpineWarning,
            )
        } else if (state.refreshing) {
            AgentHeroContent(
                badge = "LOGIN STARTING",
                headline = "$agent 로그인 페이지를\n준비하고 있습니다.",
                body = "공식 CLI에서 일회용 Device 인증 주소를 요청하고 있습니다.",
                showProgress = true,
            )
        } else {
            AgentHeroContent(
                badge = "DEVICE OAUTH",
                headline = "$agent 계정을\n안전하게 연결하세요.",
                body = "비밀번호는 앱에 입력하지 않습니다. 공식 CLI Device 로그인을 사용합니다.",
            )
        }
        AgentConnectionState.LOGIN_PENDING -> AgentHeroContent(
            badge = "APPROVAL PENDING",
            headline = "브라우저 승인을\n기다리고 있습니다.",
            body = "공식 인증 페이지에서 승인을 마친 뒤 이 앱으로 돌아오세요.",
            showProgress = true,
        )
        AgentConnectionState.READY -> AgentHeroContent(
            badge = "READY",
            headline = "모델을 선택하고\n첫 메시지를 보내세요.",
            body = "$agent CLI와의 로컬 연결이 준비되었습니다.",
        )
        AgentConnectionState.GENERATING -> AgentHeroContent(
            badge = "LIVE RUN",
            headline = "응답을 생성하고\n있습니다.",
            body = "Stop을 누르면 현재 요청에만 중단을 전달합니다.",
            showProgress = true,
        )
        AgentConnectionState.STABLE_ERROR -> AgentHeroContent(
            badge = "STABLE ERROR",
            headline = "연결을 다시\n확인해야 합니다.",
            body = "상태 코드: ${state.stableErrorCode ?: "unknown"}",
            badgeColor = AlpineError,
            badgeContentColor = AlpineErrorInk,
        )
    }

    AlpinePanel(
        modifier = Modifier.fillMaxWidth(),
        containerColor = AlpineInk,
        contentColor = AlpinePaper,
        borderColor = AlpineInk,
        padding = PaddingValues(24.dp),
    ) {
        AlpineStatusBadge(
            label = content.badge,
            containerColor = content.badgeColor,
            contentColor = content.badgeContentColor,
        )
        Text(
            text = content.headline,
            modifier = Modifier.padding(top = 18.dp),
            color = AlpinePaper,
            style = MaterialTheme.typography.headlineSmall,
        )
        Text(
            text = content.body,
            modifier = Modifier.padding(top = 14.dp),
            color = AlpinePaper.copy(alpha = 0.72f),
            style = MaterialTheme.typography.bodyMedium,
        )
        if (content.showProgress) {
            Row(
                modifier = Modifier.padding(top = 22.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                CircularProgressIndicator(
                    modifier = Modifier.size(24.dp),
                    color = AlpineAcid,
                    strokeWidth = 3.dp,
                )
                Text(
                    text = when {
                        state.stopRequested -> "중단 요청 처리 중"
                        state.connection == AgentConnectionState.LOGIN_REQUIRED -> "로그인 주소 요청 중"
                        else -> "상태 변경 대기 중"
                    },
                    color = AlpinePaper.copy(alpha = 0.78f),
                    style = MaterialTheme.typography.labelLarge,
                )
            }
        }
        if (state.connection == AgentConnectionState.LOGIN_REQUIRED) {
            Spacer(Modifier.size(22.dp))
            if (state.recoveredPendingLogin) {
                OutlinedButton(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 52.dp)
                        .testTag("agent-cancel-recovered-login"),
                    enabled = !state.refreshing,
                    onClick = onCancelRecoveredLogin,
                    border = BorderStroke(1.dp, AlpinePaper.copy(alpha = 0.35f)),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = AlpinePaper,
                        disabledContentColor = AlpinePaper.copy(alpha = 0.42f),
                    ),
                ) { Text("현재 로그인 취소") }
            } else {
                Button(
                    modifier = Modifier
                        .fillMaxWidth()
                        .heightIn(min = 52.dp)
                        .testTag("agent-login-action"),
                    enabled = !state.refreshing,
                    onClick = onLogin,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = AlpineAcid,
                        contentColor = AlpineInk,
                        disabledContainerColor = AlpineSlate,
                        disabledContentColor = AlpinePaper.copy(alpha = 0.42f),
                    ),
                ) { Text(if (state.refreshing) "로그인 준비 중…" else "$agent 로그인") }
            }
        }
        if (state.connection == AgentConnectionState.STABLE_ERROR) {
            Spacer(Modifier.size(22.dp))
            OutlinedButton(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 52.dp),
                onClick = onRefresh,
                border = BorderStroke(1.dp, AlpinePaper.copy(alpha = 0.35f)),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = AlpinePaper),
            ) { Text("상태 다시 확인") }
        }
    }
}

@Composable
private fun AgentMessageCard(message: ChatMessage, agentId: AgentId) {
    val user = message.role == ChatRole.USER
    val label = if (message.role == ChatRole.USER) "나" else agentLabel(agentId)
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = if (user) AlpineInk else AlpineHighSurface,
        contentColor = if (user) AlpinePaper else AlpineInk,
        shape = MaterialTheme.shapes.medium,
        border = if (user) null else BorderStroke(1.dp, AlpineOutline),
        tonalElevation = 0.dp,
        shadowElevation = 0.dp,
    ) {
        Column(modifier = Modifier.padding(14.dp)) {
            Text(
                text = label.uppercase(),
                color = if (user) AlpineAcid else agentAccent(agentId),
                style = MaterialTheme.typography.labelSmall.copy(
                    fontWeight = FontWeight.Black,
                    letterSpacing = 0.8.sp,
                ),
            )
            Text(
                text = message.text.ifEmpty { "…" },
                modifier = Modifier.padding(top = 6.dp),
                style = MaterialTheme.typography.bodyLarge,
            )
        }
    }
}

@Composable
private fun LiveRunIndicator(stopRequested: Boolean) {
    AlpinePanel(
        modifier = Modifier.fillMaxWidth(),
        containerColor = AlpineLocal,
        borderColor = AlpineOutline,
        padding = PaddingValues(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Surface(modifier = Modifier.size(8.dp), color = AlpineAcid, shape = CircleShape) { }
            Column {
                Text(
                    text = "LIVE RUN",
                    style = MaterialTheme.typography.labelSmall.copy(
                        fontWeight = FontWeight.Black,
                        letterSpacing = 1.sp,
                    ),
                )
                Text(
                    text = if (stopRequested) "중단 요청을 처리하고 있습니다." else "응답을 생성하고 있습니다.",
                    modifier = Modifier.padding(top = 2.dp),
                    color = AlpineInk.copy(alpha = 0.72f),
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
    }
}

@Composable
internal fun AgentComposer(
    state: AgentChatUiState,
    onDraftChange: (String) -> Unit,
    onSendOrStop: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        color = AlpinePaper,
        tonalElevation = 0.dp,
        shadowElevation = 0.dp,
    ) {
        BoxWithConstraints(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .imePadding()
                .padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            val compactLayout = maxWidth < 520.dp
            AlpinePanel(
                modifier = Modifier.fillMaxWidth(),
                containerColor = AlpineHighSurface,
                borderColor = AlpineStrongOutline,
                padding = PaddingValues(12.dp),
            ) {
                if (compactLayout) {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        AgentPromptField(
                            state = state,
                            onDraftChange = onDraftChange,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        AgentSendOrStopButton(
                            state = state,
                            onClick = onSendOrStop,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                } else {
                    Row(
                        verticalAlignment = Alignment.Bottom,
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        AgentPromptField(
                            state = state,
                            onDraftChange = onDraftChange,
                            modifier = Modifier.weight(1f),
                        )
                        AgentSendOrStopButton(
                            state = state,
                            onClick = onSendOrStop,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun AgentPromptField(
    state: AgentChatUiState,
    onDraftChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    OutlinedTextField(
        modifier = modifier.testTag("agent-composer"),
        value = state.draft,
        enabled = state.connection == AgentConnectionState.READY,
        onValueChange = onDraftChange,
        label = { Text("메시지") },
        minLines = 2,
        maxLines = 4,
        shape = MaterialTheme.shapes.medium,
    )
}

@Composable
private fun AgentSendOrStopButton(
    state: AgentChatUiState,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val enabled = state.isGenerating ||
        (state.connection == AgentConnectionState.READY && state.draft.isNotBlank())
    if (state.isGenerating) {
        OutlinedButton(
            modifier = modifier
                .heightIn(min = 52.dp)
                .testTag("agent-send-stop"),
            enabled = enabled,
            onClick = onClick,
            border = BorderStroke(1.dp, AlpineErrorInk),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = AlpineErrorInk),
        ) { Text("Stop") }
    } else {
        Button(
            modifier = modifier
                .heightIn(min = 52.dp)
                .testTag("agent-send-stop"),
            enabled = enabled,
            onClick = onClick,
            colors = ButtonDefaults.buttonColors(
                containerColor = AlpineAcid,
                contentColor = AlpineInk,
                disabledContainerColor = AlpineSlate,
                disabledContentColor = AlpinePaper.copy(alpha = 0.42f),
            ),
        ) { Text("전송") }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
internal fun AgentSelectorSheet(
    state: AgentChatUiState,
    onDismiss: () -> Unit,
    onSelect: (AgentId) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = AlpinePaper,
        contentColor = AlpineInk,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            AlpineSectionHeader(
                eyebrow = "OFFICIAL CLI",
                title = "Agent 선택",
                subtitle = "한 시점에는 선택한 Agent 하나만 활성화됩니다.",
            )
            Spacer(Modifier.size(2.dp))
            state.agents.forEach { agent ->
                AgentSelectionRow(
                    modifier = Modifier.fillMaxWidth().testTag("agent-option-${agent.agentId.wireValue}"),
                    selected = agent.agentId == state.selectedAgentId,
                    enabled = agent.agentId != state.selectedAgentId && !state.operationLocked,
                    onClick = { onSelect(agent.agentId) },
                    title = agentLabel(agent.agentId),
                    subtitle = if (agent.ready) "준비됨" else "선택 시 시작",
                    agentId = agent.agentId,
                )
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
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = AlpinePaper,
        contentColor = AlpineInk,
    ) {
        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = 560.dp)
                .testTag("agent-model-list")
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                AlpineSectionHeader(
                    eyebrow = "DYNAMIC CATALOG",
                    title = "${agentLabel(state.selectedAgentId)} 모델",
                    subtitle = "공식 CLI가 제공한 현재 모델 목록입니다.",
                )
                Spacer(Modifier.size(2.dp))
            }
            items(state.models, key = { model -> model.id }) { model ->
                AgentSelectionRow(
                    modifier = Modifier.fillMaxWidth().testTag("agent-model-${model.id}"),
                    selected = model.id == state.selectedModelId,
                    enabled = model.id != state.selectedModelId && !state.operationLocked,
                    onClick = { onSelect(model.id) },
                    title = model.displayName,
                    subtitle = model.id,
                )
            }
            item { Spacer(Modifier.size(16.dp)) }
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
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        containerColor = AlpinePaper,
        contentColor = AlpineInk,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            AlpineSectionHeader(
                eyebrow = "LOCAL CONVERSATIONS",
                title = "${agentLabel(state.selectedAgentId)} 최근 대화",
                subtitle = "선택한 Agent의 이 기기 대화만 표시합니다.",
            )
            Spacer(Modifier.size(2.dp))
            state.conversations.forEach { conversation ->
                AgentSelectionRow(
                    modifier = Modifier.fillMaxWidth().testTag("agent-conversation-${conversation.conversationId}"),
                    selected = conversation.conversationId == state.conversationId,
                    enabled = conversation.conversationId != state.conversationId,
                    onClick = { onSelect(conversation.conversationId) },
                    title = conversation.label,
                    subtitle = conversation.selectedModelId ?: "모델 정보 없음",
                )
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
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet(
        onDismissRequest = { /* explicit cancellation owns the pending login */ },
        sheetState = sheetState,
        containerColor = AlpinePaper,
        contentColor = AlpineInk,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 24.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            AlpineSectionHeader(
                eyebrow = "SECURE DEVICE OAUTH",
                title = "${agentLabel(challenge.agentId)} Device 로그인",
                subtitle = "공식 CLI가 소유한 인증 흐름만 사용합니다.",
            )
            if (challenge.agentId == AgentId.GROK) {
                AlpinePanel(
                    containerColor = AlpineInfo,
                    borderColor = AlpineOutline,
                    padding = PaddingValues(14.dp),
                ) {
                    Text(
                        text = "공식 xAI 인증 페이지를 열었습니다. 브라우저에서 승인한 뒤 이 화면으로 돌아오세요.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            } else {
                Text("브라우저에서 로그인한 뒤 아래 Device Code를 입력하고 승인하세요.")
                challenge.userCode?.let { code ->
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        color = AlpineInk,
                        contentColor = AlpinePaper,
                        shape = MaterialTheme.shapes.medium,
                    ) {
                        Text(
                            text = code,
                            style = MaterialTheme.typography.headlineSmall,
                            modifier = Modifier
                                .padding(16.dp)
                                .clearAndSetSemantics { },
                        )
                    }
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

@Composable
private fun AgentSelectionRow(
    title: String,
    subtitle: String,
    selected: Boolean,
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    agentId: AgentId? = null,
) {
    val containerColor = if (selected) AlpineInk else AlpineRaisedSurface
    val contentColor = if (selected) AlpinePaper else AlpineInk
    Surface(
        onClick = onClick,
        modifier = modifier,
        enabled = enabled,
        color = containerColor,
        contentColor = contentColor,
        shape = MaterialTheme.shapes.medium,
        border = BorderStroke(1.dp, if (selected) AlpineInk else AlpineOutline),
        tonalElevation = 0.dp,
        shadowElevation = 0.dp,
    ) {
        Row(
            modifier = Modifier.padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            agentId?.let { AgentMark(it, Modifier.size(42.dp)) }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    text = subtitle,
                    modifier = Modifier.padding(top = 2.dp),
                    color = contentColor.copy(alpha = 0.66f),
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    style = MaterialTheme.typography.labelMedium,
                )
            }
            if (selected) {
                AlpineStatusBadge(label = "ACTIVE")
            }
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

private fun agentAccent(agentId: AgentId) = when (agentId) {
    AgentId.CODEX -> AlpineCodex
    AgentId.GROK -> AlpineGrok
}

private fun agentSelectedModelLabel(state: AgentChatUiState): String =
    state.models.firstOrNull { it.id == state.selectedModelId }?.displayName
        ?.take(18)
        ?: "모델"

private const val DEVICE_CODE_CLIPBOARD_TTL_MILLIS = 60_000L
private const val SENSITIVE_CLIP_EXTRA = "android.content.extra.IS_SENSITIVE"
