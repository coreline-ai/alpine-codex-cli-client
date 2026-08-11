package dev.alpine.codexclient

import android.app.Application
import dev.alpine.runtime.android.AndroidRuntimeConfiguration
import dev.alpine.runtime.android.DefaultAndroidAlpineRuntimeFactory
import dev.alpine.runtime.api.AlpineRuntimeManager
import dev.alpine.runtime.api.RuntimeStartRequest
import dev.alpine.runtime.api.RuntimeStopReason
import dev.alpine.runtime.api.RuntimeSubscription
import dev.alpine.runtime.background.android.RuntimeBackgroundHostRegistry
import dev.alpine.runtime.background.android.RuntimeForegroundProcessListener
import dev.alpine.runtime.background.android.RuntimeForegroundServiceController
import dev.alpine.runtime.host.RuntimeHostController
import dev.alpine.runtime.pack.bundled.Alpine321Arm64Pack
import dev.alpine.runtime.pack.bundled.BundledRuntimeArtifactProvider
import dev.alpine.workspace.android.AppPrivateWorkspaceStore
import dev.alpine.workspace.api.WorkspaceStore
import dev.alpine.codexclient.cli.CodexCliArtifactProvider
import dev.alpine.codexclient.cli.StagedCodexCli
import java.io.File

class AlpineCodexApplication : Application() {
    lateinit var runtimeManager: AlpineRuntimeManager
        private set
    lateinit var runtimeController: RuntimeHostController
        private set
    lateinit var workspaceStore: WorkspaceStore
        private set
    lateinit var codexWorkspaceDirectory: File
        private set
    lateinit var codexHomeDirectory: File
        private set
    lateinit var codexStagingDirectory: File
        private set
    lateinit var codexGatewayDirectory: File
        private set
    lateinit var codexCliArtifactProvider: CodexCliArtifactProvider
        private set

    private lateinit var backgroundController: RuntimeForegroundServiceController
    private var backgroundBinding: RuntimeSubscription? = null

    override fun onCreate() {
        super.onCreate()
        val runtimeDirectoryName = "alpine-codex-runtime"
        backgroundController = RuntimeForegroundServiceController(this)
        backgroundController.normalizeAfterProcessStart()

        runtimeManager = DefaultAndroidAlpineRuntimeFactory().create(
            context = this,
            configuration = AndroidRuntimeConfiguration(
                artifactProvider = BundledRuntimeArtifactProvider(this, Alpine321Arm64Pack.create()),
                processListener = RuntimeForegroundProcessListener(
                    controller = backgroundController,
                    onStartRejected = Runnable {
                        runtimeManager.stop(RuntimeStopReason.HOST_BACKGROUND_POLICY)
                    },
                ),
                runtimeDirectoryName = runtimeDirectoryName,
                workspaceDirectoryName = "workspace",
            ),
        )
        runtimeController = RuntimeHostController(runtimeManager)
        val workspaceDirectory = File(filesDir, "$runtimeDirectoryName/workspace")
        workspaceStore = AppPrivateWorkspaceStore.forDirectory(
            context = this,
            directory = workspaceDirectory,
        )
        codexWorkspaceDirectory = ensurePrivateDirectory(
            File(
            workspaceDirectory,
            CodexRuntimePaths.PRIVATE_WORKSPACE_DIRECTORY,
            ),
        )
        codexHomeDirectory = ensurePrivateDirectory(
            File(codexWorkspaceDirectory, CodexRuntimePaths.HOME_DIRECTORY),
        )
        codexStagingDirectory = ensurePrivateDirectory(
            File(codexWorkspaceDirectory, CodexRuntimePaths.STAGING_DIRECTORY),
        )
        codexGatewayDirectory = ensurePrivateDirectory(
            File(codexWorkspaceDirectory, CodexRuntimePaths.GATEWAY_DIRECTORY),
        )
        codexCliArtifactProvider = CodexCliArtifactProvider(this)
        backgroundBinding = RuntimeBackgroundHostRegistry.bind {
            runtimeManager.stop(RuntimeStopReason.USER_REQUEST)
        }
    }

    /** Starts every runtime session with a CLI-owned home inside this app-private workspace. */
    fun startRuntime() = runtimeController.start(
        RuntimeStartRequest(environment = mapOf("HOME" to CodexRuntimePaths.GUEST_HOME)),
    )

    /** Stages the debug-only official CLI before any app-server process can start. */
    fun stageCodexCli(): StagedCodexCli = codexCliArtifactProvider.stage(
        hostStagingDirectory = codexStagingDirectory,
        guestStagingDirectory = CodexRuntimePaths.GUEST_STAGING,
    )

    private fun ensurePrivateDirectory(directory: File): File {
        check(directory.exists() || directory.mkdirs()) {
            "cannot create app-private Codex workspace"
        }
        return directory
    }

    override fun onTerminate() {
        backgroundBinding?.close()
        backgroundController.stop()
        runtimeController.close()
        runtimeManager.close()
        super.onTerminate()
    }
}
