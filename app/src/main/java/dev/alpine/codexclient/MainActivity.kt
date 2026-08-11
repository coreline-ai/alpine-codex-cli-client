package dev.alpine.codexclient

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

class MainActivity : ComponentActivity() {
    private val runtimeViewModel: RuntimeViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AlpineCodexClientApp(runtimeViewModel)
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun AlpineCodexClientApp(runtimeViewModel: RuntimeViewModel) {
    val state = runtimeViewModel.state.collectAsStateWithLifecycle().value
    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Scaffold(
                topBar = {
                    TopAppBar(title = { Text("Alpine Codex Client") })
                },
            ) { contentPadding ->
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        .padding(contentPadding)
                        .padding(24.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text(
                        text = "Alpine Runtime: ${state.lifecycle}",
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        text = "상태: ${state.status}",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    state.errorCode?.let { errorCode ->
                        Text(
                            text = "오류: ${errorCode.name}",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    state.gatewayPythonBootstrap?.let { outcome ->
                        Text(
                            text = "Gateway Python 준비: ${outcome.name}",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                    Button(
                        enabled = !state.busy && state.lifecycle == dev.alpine.runtime.api.RuntimeLifecycleState.NOT_INSTALLED,
                        onClick = runtimeViewModel::install,
                    ) {
                        Text("Runtime 설치")
                    }
                    Button(
                        enabled = !state.busy && state.lifecycle == dev.alpine.runtime.api.RuntimeLifecycleState.READY,
                        onClick = runtimeViewModel::start,
                    ) {
                        Text("Runtime 시작")
                    }
                    Button(
                        enabled = !state.busy && state.sessionActive,
                        onClick = runtimeViewModel::runSmoke,
                    ) {
                        Text("Gateway Python 준비·smoke")
                    }
                    OutlinedButton(
                        enabled = !state.busy,
                        onClick = runtimeViewModel::refresh,
                    ) {
                        Text("상태 확인")
                    }
                    OutlinedButton(
                        enabled = !state.busy && state.sessionActive,
                        onClick = runtimeViewModel::stop,
                    ) {
                        Text("Runtime 종료")
                    }
                }
            }
        }
    }
}
