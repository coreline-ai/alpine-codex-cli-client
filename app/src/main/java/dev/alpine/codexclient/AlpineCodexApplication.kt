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
import dev.alpine.codexclient.grokcli.GrokCliArtifactProvider
import dev.alpine.codexclient.grokcli.StagedGrokCli
import dev.alpine.codexclient.grokcli.StagedGrokProfile
import dev.alpine.codexclient.gatewaypack.CodexGatewayArtifactProvider
import dev.alpine.codexclient.gatewaypack.StagedCodexGateway
import dev.alpine.codexclient.bridge.CodexGatewayClient
import dev.alpine.codexclient.bridge.CodexGatewayChatBackend
import dev.alpine.codexclient.bridge.CodexRuntimeController
import dev.alpine.codexclient.bridge.GatewayArtifactStager
import dev.alpine.codexclient.bridge.GatewayLaunchSpec
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
    lateinit var grokWorkspaceDirectory: File
        private set
    lateinit var grokHomeDirectory: File
        private set
    lateinit var grokStagingDirectory: File
        private set
    lateinit var grokProfileDirectory: File
        private set
    lateinit var grokWorkDirectory: File
        private set
    lateinit var grokGatewayDirectory: File
        private set
    lateinit var codexCliArtifactProvider: CodexCliArtifactProvider
        private set
    lateinit var grokCliArtifactProvider: GrokCliArtifactProvider
        private set
    lateinit var codexGatewayArtifactProvider: CodexGatewayArtifactProvider
        private set
    lateinit var codexRuntimeController: CodexRuntimeController
        private set
    lateinit var codexGatewayClient: CodexGatewayClient
        private set
    lateinit var codexChatBackend: CodexGatewayChatBackend
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
        codexWorkspaceDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(
                workspaceDirectory,
                CodexRuntimePaths.PRIVATE_WORKSPACE_DIRECTORY,
            ),
        )
        codexHomeDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(codexWorkspaceDirectory, CodexRuntimePaths.HOME_DIRECTORY),
        )
        codexStagingDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(codexWorkspaceDirectory, CodexRuntimePaths.STAGING_DIRECTORY),
        )
        codexGatewayDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(codexWorkspaceDirectory, CodexRuntimePaths.GATEWAY_DIRECTORY),
        )
        grokWorkspaceDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(workspaceDirectory, GrokRuntimePaths.PRIVATE_WORKSPACE_DIRECTORY),
        )
        grokHomeDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(grokWorkspaceDirectory, GrokRuntimePaths.HOME_DIRECTORY),
        )
        grokStagingDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(grokWorkspaceDirectory, GrokRuntimePaths.STAGING_DIRECTORY),
        )
        grokProfileDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(grokWorkspaceDirectory, GrokRuntimePaths.PROFILE_DIRECTORY),
        )
        grokWorkDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(grokWorkspaceDirectory, GrokRuntimePaths.WORK_DIRECTORY),
        )
        grokGatewayDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(grokWorkspaceDirectory, GrokRuntimePaths.GATEWAY_DIRECTORY),
        )
        codexCliArtifactProvider = CodexCliArtifactProvider(this)
        grokCliArtifactProvider = GrokCliArtifactProvider(this)
        codexGatewayArtifactProvider = CodexGatewayArtifactProvider(this)
        codexGatewayClient = CodexGatewayClient()
        codexChatBackend = CodexGatewayChatBackend(codexGatewayClient)
        codexRuntimeController = CodexRuntimeController(
            runtimeHost = AndroidGatewayRuntimeHost(runtimeManager, runtimeController),
            stager = GatewayArtifactStager(::stageGatewayLaunch),
            gatewayClient = codexGatewayClient,
            homeDirectory = CodexRuntimePaths.GUEST_HOME,
        )
        // This only probes an already-running app-private loopback gateway after process recovery.
        codexRuntimeController.reconnectIfRuntimeActive()
        backgroundBinding = RuntimeBackgroundHostRegistry.bind {
            codexRuntimeController.stop()
        }
    }

    /** Starts Runtime, stages verified assets, and launches the fixed loopback gateway in order. */
    fun startRuntime() = codexRuntimeController.start()

    /** Stops the gateway terminal before releasing the app-private Runtime session. */
    fun stopRuntime() = codexRuntimeController.stop()

    /** Stages the debug-only official CLI before any app-server process can start. */
    fun stageCodexCli(): StagedCodexCli = codexCliArtifactProvider.stage(
        hostStagingDirectory = codexStagingDirectory,
        guestStagingDirectory = CodexRuntimePaths.GUEST_STAGING,
    )

    /** Stages the pinned official Grok executable without starting a backend process. */
    fun stageGrokCli(): StagedGrokCli = grokCliArtifactProvider.stage(
        hostStagingDirectory = grokStagingDirectory,
        guestStagingDirectory = GrokRuntimePaths.GUEST_STAGING,
    )

    /** Stages only the hash-locked text-only Agent profile with owner-read/write mode. */
    fun stageGrokProfile(): StagedGrokProfile = grokCliArtifactProvider.stageProfile(
        hostProfileDirectory = grokProfileDirectory,
        guestProfileDirectory = GrokRuntimePaths.GUEST_PROFILE_DIRECTORY,
    )

    /** Stages only the manifest-verified local Python supervisor package. */
    fun stageCodexGateway(): StagedCodexGateway = codexGatewayArtifactProvider.stage(
        hostGatewayDirectory = codexGatewayDirectory,
        guestGatewayDirectory = CodexRuntimePaths.GUEST_GATEWAY,
    )

    /** Stages the same manifest-verified supervisor package into Grok's disjoint tree. */
    fun stageGrokGateway(): StagedCodexGateway = codexGatewayArtifactProvider.stage(
        hostGatewayDirectory = grokGatewayDirectory,
        guestGatewayDirectory = GrokRuntimePaths.GUEST_GATEWAY,
    )

    private fun stageGatewayLaunch(): GatewayLaunchSpec {
        val cli = stageCodexCli()
        stageCodexGateway()
        return GatewayLaunchSpec(
            codexExecutable = cli.guestExecutablePath,
            gatewayRootDirectory = CodexRuntimePaths.GUEST_GATEWAY,
            homeDirectory = CodexRuntimePaths.GUEST_HOME,
            workspaceDirectory = "/workspace",
        )
    }

    override fun onTerminate() {
        backgroundBinding?.close()
        backgroundController.stop()
        codexRuntimeController.close()
        runtimeController.close()
        runtimeManager.close()
        super.onTerminate()
    }
}
