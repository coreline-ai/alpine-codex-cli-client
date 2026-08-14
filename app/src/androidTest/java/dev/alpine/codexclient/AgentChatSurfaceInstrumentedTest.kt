package dev.alpine.codexclient

import android.content.pm.ActivityInfo
import android.view.WindowManager
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertTextEquals
import androidx.compose.ui.test.isDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performScrollToIndex
import androidx.compose.ui.test.performTextInput
import dev.alpine.codexclient.bridge.AgentCapabilities
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.AgentModel
import dev.alpine.codexclient.bridge.GatewayAgent
import dev.alpine.codexclient.ui.theme.AlpineAgentTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** Fake UI state only: never starts Runtime, browser, OAuth, clipboard, or network. */
class AgentChatSurfaceInstrumentedTest {
    @get:Rule val compose = createAndroidComposeRule<Phase6TestActivity>()

    @Test
    fun grokLoginClickImmediatelyShowsChallengePreparationProgress() {
        val refreshing = mutableStateOf(false)
        var loginRequests = 0
        compose.setContent {
            AlpineAgentTheme {
                AgentChatViewport(
                    state = AgentChatUiState(
                        connection = AgentConnectionState.LOGIN_REQUIRED,
                        selectedAgentId = AgentId.GROK,
                        refreshing = refreshing.value,
                    ),
                    onLogin = {
                        loginRequests++
                        refreshing.value = true
                    },
                    onCancelRecoveredLogin = {},
                    onRefresh = {},
                )
            }
        }

        compose.onNodeWithTag("agent-login-action")
            .assertTextEquals("Grok 로그인")
            .assertIsEnabled()
            .performClick()
        assertEquals(1, loginRequests)
        compose.onNodeWithText("LOGIN STARTING").assertIsDisplayed()
        compose.onNodeWithText("로그인 주소 요청 중").assertIsDisplayed()
        compose.onNodeWithTag("agent-login-action")
            .assertTextEquals("로그인 준비 중…")
            .assertIsNotEnabled()
    }

    @Test
    fun agentSelectorRendersOneAndTwoAgentsAndLocksDuringSwitching() {
        val scenario = mutableStateOf(0)
        var selected: AgentId? = null
        compose.setContent {
            AlpineAgentTheme {
                val agents = if (scenario.value == 0) listOf(agent(AgentId.CODEX, true)) else {
                    listOf(agent(AgentId.CODEX, true), agent(AgentId.GROK, false))
                }
                AgentSelectorSheet(
                    state = AgentChatUiState(
                        connection = if (scenario.value == 2) {
                            AgentConnectionState.SWITCHING
                        } else {
                            AgentConnectionState.READY
                        },
                        selectedAgentId = AgentId.CODEX,
                        agents = agents,
                    ),
                    onDismiss = {},
                    onSelect = { selected = it },
                )
            }
        }

        compose.onNodeWithTag("agent-option-codex").assertIsDisplayed().assertIsNotEnabled()
        compose.onAllNodesWithTag("agent-option-grok").assertCountEquals(0)

        compose.runOnUiThread { scenario.value = 1 }
        val grokOption = compose.onNodeWithTag("agent-option-grok")
        grokOption.performScrollTo()
        compose.waitUntil(5_000) { grokOption.isDisplayed() }
        grokOption.assertIsDisplayed()
            .assertIsEnabled()
            .performClick()
        assertEquals(AgentId.GROK, selected)

        selected = null
        compose.runOnUiThread { scenario.value = 2 }
        compose.onNodeWithTag("agent-option-grok").assertIsNotEnabled()
        assertEquals(null, selected)
    }

    @Test
    fun modelSelectorRendersZeroOneAndManyModelsAndLocksDuringLogin() {
        val scenario = mutableStateOf(0)
        var selected: String? = null
        compose.setContent {
            AlpineAgentTheme {
                val models = when (scenario.value) {
                    0 -> emptyList()
                    1 -> listOf(model("one", true))
                    else -> listOf(model("one", true)) +
                        (2..12).map { index -> model("model-$index", false) }
                }
                AgentModelSelectorSheet(
                    state = AgentChatUiState(
                        connection = if (scenario.value == 3) {
                            AgentConnectionState.LOGIN_PENDING
                        } else {
                            AgentConnectionState.READY
                        },
                        selectedAgentId = AgentId.GROK,
                        models = models,
                        selectedModelId = "one".takeIf { models.isNotEmpty() },
                    ),
                    onDismiss = {},
                    onSelect = { selected = it },
                )
            }
        }

        compose.onNodeWithText("Grok 모델").assertIsDisplayed()
        compose.onAllNodesWithTag("agent-model-one").assertCountEquals(0)

        compose.runOnUiThread { scenario.value = 1 }
        compose.onNodeWithTag("agent-model-one").assertIsDisplayed().assertIsNotEnabled()
        compose.onAllNodesWithTag("agent-model-two").assertCountEquals(0)

        compose.runOnUiThread { scenario.value = 2 }
        compose.onNodeWithTag("agent-model-model-2").assertIsEnabled().performClick()
        compose.onNodeWithTag("agent-model-list").performScrollToIndex(13)
        val lastModel = compose.onNodeWithTag("agent-model-model-12")
        compose.waitUntil(5_000) { lastModel.isDisplayed() }
        lastModel.assertIsDisplayed()
        assertEquals("model-2", selected)

        selected = null
        compose.runOnUiThread { scenario.value = 3 }
        compose.onNodeWithTag("agent-model-list").performScrollToIndex(2)
        compose.onNodeWithTag("agent-model-model-2").assertIsNotEnabled()
        assertEquals(null, selected)
    }

    @Test
    fun grokLoginDoesNotExposeUrlOrCodeAndCodexCodeHasNoTextSemantics() {
        val showCodex = mutableStateOf(false)
        compose.setContent {
            AlpineAgentTheme {
                AgentDeviceLoginSheet(
                    challenge = if (showCodex.value) {
                        AgentLoginChallenge(
                            AgentId.CODEX,
                            "login-codex",
                            "SENSITIVE-CODE",
                            60,
                            1,
                            "https://auth.openai.com/device",
                        )
                    } else {
                        AgentLoginChallenge(AgentId.GROK, "login-grok", null, 60, 2, null)
                    },
                    onOpenBrowser = {},
                    onCheck = {},
                    onCancel = {},
                )
            }
        }

        compose.onNodeWithText("Grok Device 로그인").assertIsDisplayed()
        compose.onAllNodesWithText("auth.x.ai", substring = true).assertCountEquals(0)
        compose.onAllNodesWithText("SENSITIVE-CODE", substring = true, useUnmergedTree = true)
            .assertCountEquals(0)
        compose.onAllNodesWithTag("agent-copy-device-code").assertCountEquals(0)
        compose.onAllNodesWithTag("agent-open-browser").assertCountEquals(0)

        compose.runOnUiThread { showCodex.value = true }
        compose.onNodeWithText("Codex Device 로그인").assertIsDisplayed()
        compose.onAllNodesWithText("SENSITIVE-CODE", substring = true, useUnmergedTree = true)
            .assertCountEquals(0)
        val copyCode = compose.onNodeWithTag("agent-copy-device-code")
        copyCode.performScrollTo()
        compose.waitUntil(5_000) { copyCode.isDisplayed() }
        copyCode.assertIsDisplayed()
        val openBrowser = compose.onNodeWithTag("agent-open-browser")
        openBrowser.performScrollTo()
        compose.waitUntil(5_000) { openBrowser.isDisplayed() }
        openBrowser.assertIsDisplayed()
    }

    @Test
    fun landscapeComposerSendsOnceAndThenExposesOneStopAction() {
        val generating = mutableStateOf(false)
        val draft = mutableStateOf("")
        var actions = 0
        try {
            compose.setContent {
                AlpineAgentTheme {
                    AgentComposer(
                        state = AgentChatUiState(
                            connection = if (generating.value) {
                                AgentConnectionState.GENERATING
                            } else {
                                AgentConnectionState.READY
                            },
                            selectedAgentId = AgentId.GROK,
                            models = listOf(model("one", true)),
                            selectedModelId = "one",
                            draft = draft.value,
                        ),
                        onDraftChange = { draft.value = it },
                        onSendOrStop = { actions++ },
                    )
                }
            }
            compose.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
            compose.waitForIdle()
            compose.onNodeWithTag("agent-composer").assertIsDisplayed().performTextInput("fixture")
            compose.onNodeWithTag("agent-send-stop").assertTextEquals("전송").performClick()
            assertEquals(1, actions)

            compose.runOnUiThread { generating.value = true }
            compose.onNodeWithTag("agent-send-stop").assertTextEquals("Stop").performClick()
            assertEquals(2, actions)
        } finally {
            compose.activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    @Test
    fun sensitiveLoginStateSetsAndRestoresSecureWindowFlag() {
        val secure = mutableStateOf(false)
        val secureFlag = WindowManager.LayoutParams.FLAG_SECURE
        val originalFlags = compose.activity.window.attributes.flags
        compose.setContent {
            AlpineAgentTheme { SecureLoginWindow(secure.value) }
        }

        compose.runOnUiThread { secure.value = true }
        compose.waitForIdle()
        compose.runOnIdle {
            assertEquals(secureFlag, compose.activity.window.attributes.flags and secureFlag)
        }

        compose.runOnUiThread { secure.value = false }
        compose.waitForIdle()
        compose.runOnIdle {
            assertEquals(originalFlags and secureFlag, compose.activity.window.attributes.flags and secureFlag)
        }
    }

    private fun agent(id: AgentId, selected: Boolean) = GatewayAgent(
        agentId = id,
        selected = selected,
        ready = selected,
        capabilities = AgentCapabilities(true, true, true, true, true),
    )

    private fun model(id: String, isDefault: Boolean) =
        AgentModel(AgentId.GROK, id, "Model $id", isDefault)
}
