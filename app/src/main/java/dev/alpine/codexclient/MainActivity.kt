package dev.alpine.codexclient

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            AlpineCodexClientApp()
        }
    }
}

@Composable
@OptIn(ExperimentalMaterial3Api::class)
private fun AlpineCodexClientApp() {
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
                        text = "Phase 0: 프로젝트 골격 준비 완료",
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        text = "다음 단계에서 Alpine Runtime, 공식 Codex CLI, Device Code OAuth를 연결합니다.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        }
    }
}
