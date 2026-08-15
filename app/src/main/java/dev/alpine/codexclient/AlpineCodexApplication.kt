package dev.alpine.codexclient

import android.app.Application
import android.os.Process
import android.system.Os
import android.system.OsConstants
import android.util.Log
import dev.alpine.runtime.android.AndroidRuntimeConfiguration
import dev.alpine.runtime.android.DefaultAndroidAlpineRuntimeFactory
import dev.alpine.runtime.api.AlpineRuntimeManager
import dev.alpine.runtime.api.RuntimeStartRequest
import dev.alpine.runtime.api.RuntimeLifecycleState
import dev.alpine.runtime.api.RuntimeStopReason
import dev.alpine.runtime.api.RuntimeSubscription
import dev.alpine.runtime.background.android.RuntimeBackgroundHostRegistry
import dev.alpine.runtime.background.android.RuntimeForegroundProcessListener
import dev.alpine.runtime.background.android.RuntimeForegroundServiceController
import dev.alpine.runtime.host.RuntimeHostController
import dev.alpine.runtime.pack.bundled.Alpine321Arm64Pack
import dev.alpine.runtime.pack.bundled.BundledRuntimeArtifactProvider
import dev.alpine.pythonpack.bundled.BundledPythonPackageProvider
import dev.alpine.pythonpack.bundled.StagedPythonPackagePack
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
import dev.alpine.codexclient.bridge.AgentGatewayClient
import dev.alpine.codexclient.bridge.AgentGatewayChatBackend
import dev.alpine.codexclient.bridge.CodexRuntimeController
import dev.alpine.codexclient.bridge.GatewayArtifactStager
import dev.alpine.codexclient.bridge.GatewayLaunchSpec
import dev.alpine.codexclient.bridge.GatewayRequestSigner
import dev.alpine.codexclient.bridge.GatewaySessionLifecycle
import java.io.File
import java.util.concurrent.CompletionStage

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
    lateinit var gatewaySecurityDirectory: File
        private set
    lateinit var gatewayWrappedDirectory: File
        private set
    lateinit var conversationStateDirectory: File
        private set
    lateinit var gatewayTransportDirectory: File
        private set
    internal var sensitiveStateNoBackupCommitted: Boolean = false
        private set
    internal var sensitiveStateMigrationFailureCode: String? = null
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
    lateinit var pythonPackageProvider: BundledPythonPackageProvider
        private set
    lateinit var codexRuntimeController: CodexRuntimeController
        private set
    lateinit var codexGatewayClient: CodexGatewayClient
        private set
    lateinit var codexChatBackend: CodexGatewayChatBackend
        private set
    lateinit var agentGatewayClient: AgentGatewayClient
        private set
    lateinit var agentChatBackend: AgentGatewayChatBackend
        private set
    private lateinit var gatewayCapabilityManager: GatewayCapabilityManager
    private lateinit var gatewayPythonBootstrapper: GatewayPythonBootstrapper
    private lateinit var configuredRuntimeStarter: ConfiguredRuntimeStarter
    private lateinit var runtimeRestorePreference: RuntimeRestorePreference
    private lateinit var initialReconnect: CompletionStage<*>

    private lateinit var backgroundController: RuntimeForegroundServiceController
    private var backgroundBinding: RuntimeSubscription? = null

    override fun onCreate() {
        super.onCreate()
        val runtimeDirectoryName = "alpine-codex-runtime"
        backgroundController = RuntimeForegroundServiceController(this)
        backgroundController.normalizeAfterProcessStart()
        val workspaceDirectory = File(filesDir, "$runtimeDirectoryName/workspace")
        val sensitiveStateMigrator = SensitiveStateMigrator.forContext(
            context = this,
            workspaceDirectory = workspaceDirectory,
            runtimeActive = { hasActiveSiblingUidProcess(packageName) },
        )
        val sensitiveState = sensitiveStateMigrator.prepare()
        sensitiveStateNoBackupCommitted = sensitiveState.noBackupCommitted
        sensitiveStateMigrationFailureCode = sensitiveStateMigrator.lastFailureCode()
        Log.i(
            SENSITIVE_STATE_AUDIT_TAG,
            "committed=$sensitiveStateNoBackupCommitted failure=" +
                (sensitiveStateMigrationFailureCode ?: "none"),
        )

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
                privateDirectoryBinds = sensitiveState.privateDirectoryBinds,
            ),
        )
        runtimeController = RuntimeHostController(runtimeManager)
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
            if (sensitiveState.noBackupCommitted) noBackupFilesDir else workspaceDirectory,
            sensitiveState.codexHomeDirectory,
        )
        codexStagingDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(codexWorkspaceDirectory, CodexRuntimePaths.STAGING_DIRECTORY),
        )
        codexGatewayDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(codexWorkspaceDirectory, CodexRuntimePaths.GATEWAY_DIRECTORY),
        )
        gatewaySecurityDirectory = AppPrivatePathPolicy.ensureDirectory(
            if (sensitiveState.noBackupCommitted) noBackupFilesDir else workspaceDirectory,
            sensitiveState.gatewayHandoffDirectory,
        )
        gatewayWrappedDirectory = AppPrivatePathPolicy.ensureDirectory(
            if (sensitiveState.noBackupCommitted) noBackupFilesDir else filesDir,
            sensitiveState.gatewayWrappedDirectory,
        )
        conversationStateDirectory = if (sensitiveState.noBackupCommitted) {
            AppPrivatePathPolicy.ensureDirectory(noBackupFilesDir, sensitiveState.conversationDirectory)
        } else {
            filesDir
        }
        gatewayTransportDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(workspaceDirectory, CodexRuntimePaths.TRANSPORT_DIRECTORY),
        )
        grokWorkspaceDirectory = AppPrivatePathPolicy.ensureDirectory(
            workspaceDirectory,
            File(workspaceDirectory, GrokRuntimePaths.PRIVATE_WORKSPACE_DIRECTORY),
        )
        grokHomeDirectory = AppPrivatePathPolicy.ensureDirectory(
            if (sensitiveState.noBackupCommitted) noBackupFilesDir else workspaceDirectory,
            sensitiveState.grokHomeDirectory,
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
        pythonPackageProvider = BundledPythonPackageProvider(this)
        gatewayCapabilityManager = GatewayCapabilityManager(
            context = this,
            capabilityDirectory = gatewaySecurityDirectory,
            wrappedDirectory = gatewayWrappedDirectory,
        )
        gatewayPythonBootstrapper = GatewayPythonBootstrapper(
            runtimeController = runtimeController,
            stagePackagePack = ::stagePythonPackagePack,
            stageGateway = ::stageCodexGateway,
        )
        val gatewayTransport = UnixDomainSocketGatewayTransport(
            socketFile = File(gatewayTransportDirectory, CodexRuntimePaths.GATEWAY_SOCKET_FILE),
            expectedPeerUid = Process.myUid(),
        )
        codexGatewayClient = CodexGatewayClient(
            GatewayRequestSigner(gatewayCapabilityManager),
            gatewayTransport,
        )
        codexChatBackend = CodexGatewayChatBackend(codexGatewayClient)
        agentGatewayClient = AgentGatewayClient(
            GatewayRequestSigner(gatewayCapabilityManager),
            gatewayTransport,
        )
        agentChatBackend = AgentGatewayChatBackend(agentGatewayClient)
        codexRuntimeController = CodexRuntimeController(
            runtimeHost = AndroidGatewayRuntimeHost(runtimeManager, runtimeController),
            stager = GatewayArtifactStager(::stageGatewayLaunch),
            gatewayClient = agentGatewayClient,
            homeDirectory = CodexRuntimePaths.GUEST_HOME,
            sessionLifecycle = object : GatewaySessionLifecycle {
                override fun onGatewayStartFailed() {
                    gatewayCapabilityManager.cleanupTransientStart()
                    cleanupGatewaySocket()
                }

                override fun onRuntimeStopped() {
                    gatewayCapabilityManager.clearAfterRuntimeStop()
                    cleanupGatewaySocket()
                }
            },
        )
        configuredRuntimeStarter = ConfiguredRuntimeStarter(
            gatewayLifecycle = { codexRuntimeController.currentState().lifecycle },
            gatewayHealthy = agentGatewayClient::isRuntimeHealthy,
            stopStaleGateway = ::stopRuntime,
            startAlpine = ::startAlpineRuntime,
            preparePython = ::prepareGatewayPython,
            startGateway = ::startRuntime,
        )
        runtimeRestorePreference = RuntimeRestorePreference(this)
        // This only probes an already-running app-private UDS Gateway after process recovery.
        initialReconnect = codexRuntimeController.reconnectIfRuntimeActive()
        backgroundBinding = RuntimeBackgroundHostRegistry.bind {
            runtimeRestorePreference.setRestoreRequested(false)
            codexRuntimeController.stop()
        }
    }

    /** Starts only Alpine so the user can explicitly prepare the APK-contained Python package set. */
    fun startAlpineRuntime() = runtimeController.start(
        RuntimeStartRequest(environment = mapOf("HOME" to CodexRuntimePaths.GUEST_HOME)),
    )

    /** Runs only the fixed local-package availability/simulate/install/verify sequence. */
    fun prepareGatewayPython() = gatewayPythonBootstrapper.prepare()

    /** Stages the locked Python package set from APK assets without repository or network access. */
    fun stagePythonPackagePack(): StagedPythonPackagePack = pythonPackageProvider.stage(
        hostStagingDirectory = codexStagingDirectory,
        guestStagingDirectory = CodexRuntimePaths.GUEST_STAGING,
    )

    /** Stages verified assets and launches the private UDS Gateway in the active Runtime. */
    fun startRuntime() = codexRuntimeController.start()

    /** Restores the complete installed Runtime → Python → Gateway chain without redoing OAuth. */
    fun restoreConfiguredRuntime(): CompletionStage<Unit> = initialReconnect
        .handle { _, _ -> Unit }
        .thenCompose { ensureRuntimeInstalled() }
        .thenCompose { configuredRuntimeStarter.start() }

    private fun ensureRuntimeInstalled(): CompletionStage<Unit> = when (
        runtimeController.currentState().runtimeState.lifecycle
    ) {
        RuntimeLifecycleState.NOT_INSTALLED -> runtimeController.install().thenApply { Unit }
        RuntimeLifecycleState.REPAIR_REQUIRED -> runtimeController.repair().thenApply { Unit }
        else -> java.util.concurrent.CompletableFuture.completedFuture(Unit)
    }

    fun shouldRestoreConfiguredRuntime(): Boolean =
        runtimeRestorePreference.shouldRestore(runtimeController.currentState().runtimeState.lifecycle)

    fun setRuntimeRestoreRequested(requested: Boolean) =
        runtimeRestorePreference.setRestoreRequested(requested)

    /** Stops the gateway first, or the raw Alpine preflight session when no gateway was started. */
    fun stopRuntime() = if (
        codexRuntimeController.currentState().lifecycle == dev.alpine.codexclient.bridge.CodexRuntimeLifecycle.STOPPED
    ) {
        runtimeController.stop(RuntimeStopReason.USER_REQUEST)
    } else {
        codexRuntimeController.stop()
    }

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
        val grok = stageGrokCli()
        stageGrokProfile()
        stageCodexGateway()
        OfficialCliHomeProvisioner.provisionCodex(codexHomeDirectory)
        OfficialCliHomeProvisioner.validateGrok(grokHomeDirectory)
        val capabilityFile = gatewayCapabilityManager.rotateAndStage()
        return GatewayLaunchSpec(
            codexExecutable = cli.guestExecutablePath,
            grokExecutable = grok.guestExecutablePath,
            gatewayRootDirectory = CodexRuntimePaths.GUEST_GATEWAY,
            homeDirectory = CodexRuntimePaths.GUEST_HOME,
            workspaceDirectory = "/workspace",
            grokHomeDirectory = GrokRuntimePaths.GUEST_HOME,
            grokWorkDirectory = GrokRuntimePaths.GUEST_WORK,
            capabilityFile = capabilityFile,
            socketPath = File(
                gatewayTransportDirectory,
                CodexRuntimePaths.GATEWAY_SOCKET_FILE,
            ).canonicalPath,
            expectedPeerUid = Process.myUid(),
        )
    }

    /** Removes only the app-owned socket inode left by a force-terminated PRoot child. */
    private fun cleanupGatewaySocket() {
        val socket = File(gatewayTransportDirectory, CodexRuntimePaths.GATEWAY_SOCKET_FILE)
        val metadata = try {
            Os.lstat(socket.absolutePath)
        } catch (_: Exception) {
            return
        }
        if (
            metadata.st_uid == Process.myUid() &&
            metadata.st_mode and OsConstants.S_IFMT == OsConstants.S_IFSOCK
        ) {
            runCatching { socket.delete() }
        }
    }

    override fun onTerminate() {
        backgroundBinding?.close()
        backgroundController.stop()
        gatewayCapabilityManager.close()
        codexRuntimeController.close()
        runtimeController.close()
        runtimeManager.close()
        super.onTerminate()
    }

    private companion object {
        const val SENSITIVE_STATE_AUDIT_TAG = "SensitiveStateAudit"
    }
}
