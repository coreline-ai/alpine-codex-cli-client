package dev.alpine.codexclient

import android.content.pm.ActivityInfo
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertHasClickAction
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTouchInput
import dev.alpine.codexclient.bridge.GatewayModel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/** Fake UI states only: no Runtime, OAuth challenge, account, or network request is created. */
class CodexChatSurfaceInstrumentedTest {
    @get:Rule val compose = createAndroidComposeRule<Phase6TestActivity>()

    @Test
    fun loginRequiredAndDynamicModelsExposeOnlyExpectedActions() {
        var loginRequests = 0
        val models = listOf(
            GatewayModel("model-default", "Default model", true),
            GatewayModel("model-other", "Other model", false),
        )
        val showModels = mutableStateOf(false)
        var selected: String? = null
        compose.setContent {
            MaterialTheme {
                if (!showModels.value) {
                    ChatViewport(
                        state = CodexChatUiState(connection = CodexConnectionState.LOGIN_REQUIRED, models = models),
                        onLogin = { loginRequests++ },
                        onRefresh = {},
                    )
                } else {
                    ModelSelectorSheet(
                        state = CodexChatUiState(models = models, selectedModelId = "model-default"),
                        onDismiss = {},
                        onSelect = { selected = it },
                    )
                }
            }
        }
        compose.onNodeWithTag("codex-login-action").performClick()
        assertEquals(1, loginRequests)
        compose.runOnUiThread { showModels.value = true }
        compose.onNodeWithText("Other model").performClick()
        assertEquals("model-other", selected)
    }

    @Test
    fun readyComposerSendsOnceAndGeneratingComposerExposesStop() {
        val model = GatewayModel("model-default", "Default model", true)
        var actions = 0
        val generating = mutableStateOf(false)
        val draft = mutableStateOf("")
        try {
            compose.setContent {
                MaterialTheme {
                    Composer(
                        state = if (generating.value) {
                            CodexChatUiState(connection = CodexConnectionState.GENERATING)
                        } else {
                            CodexChatUiState(
                                connection = CodexConnectionState.READY,
                                models = listOf(model),
                                selectedModelId = model.id,
                                draft = draft.value,
                            )
                        },
                        onModelClick = {},
                        onDraftChange = { draft.value = it },
                        onSendOrStop = { actions++ },
                    )
                }
            }
            compose.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
            compose.waitForIdle()
            compose.onNodeWithTag("codex-model-selector").assertIsDisplayed()
            compose.onNodeWithTag("codex-composer").performTextInput("synthetic test input")
            compose.onNodeWithTag("codex-send-stop").assertTextEquals("전송").performClick()
            assertEquals(1, actions)
            compose.runOnUiThread { generating.value = true }
            compose.onNodeWithTag("codex-send-stop").assertTextEquals("Stop").performClick()
            assertEquals(2, actions)
            assertTrue(actions == 2)
        } finally {
            compose.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    @Test
    fun recentConversationListSelectionHasStableSemantics() {
        var selectedConversation: String? = null
        val conversation = ConversationSummary("conversation-test", "Synthetic conversation", "model-default")
        compose.setContent {
            MaterialTheme {
                ConversationListSheet(
                    conversations = listOf(conversation),
                    activeConversationId = null,
                    onDismiss = {},
                    onSelect = { selectedConversation = it },
                )
            }
        }
        compose.onNodeWithTag("codex-conversation-conversation-test").performClick()
        assertEquals("conversation-test", selectedConversation)
    }

    @Test
    fun deviceLoginCancelHasStableSemantics() {
        val cancelled = mutableIntStateOf(0)
        compose.setContent {
            MaterialTheme {
                DeviceLoginSheet(
                    challenge = DeviceCodeChallenge(
                        loginId = "test-login",
                        verificationUrl = "https://auth.openai.com/device",
                        userCode = "TEST-CODE",
                        expiresInSeconds = 60,
                        pollIntervalSeconds = 1,
                    ),
                    onCheck = {},
                    onCancel = { cancelled.intValue++ },
                )
            }
        }
        compose.waitForIdle()
        compose.onNodeWithTag("codex-login-cancel", useUnmergedTree = true)
            .assertIsDisplayed()
            .assertHasClickAction()
            .performTouchInput { click() }
        compose.runOnIdle { assertEquals(1, cancelled.intValue) }
    }
}
