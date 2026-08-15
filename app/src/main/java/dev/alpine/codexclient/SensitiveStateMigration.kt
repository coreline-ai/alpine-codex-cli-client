package dev.alpine.codexclient

import android.content.Context
import android.os.Process
import android.system.Os
import android.system.OsConstants
import dev.alpine.runtime.android.AndroidPrivateDirectoryBind
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

private const val PRIVATE_MODE_MASK = 0x1ff

internal data class SensitiveStateLayout(
    val noBackupCommitted: Boolean,
    val codexHomeDirectory: File,
    val grokHomeDirectory: File,
    val gatewayHandoffDirectory: File,
    val gatewayWrappedDirectory: File,
    val conversationDirectory: File,
    val privateDirectoryBinds: List<AndroidPrivateDirectoryBind>,
)

internal data class SensitiveEntryMetadata(
    val uid: Int,
    val gid: Int,
    val mode: Int,
    val directory: Boolean,
    val regularFile: Boolean,
)

internal interface SensitiveFilePolicy {
    fun metadata(file: File): SensitiveEntryMetadata
    fun chmod(file: File, mode: Int)
}

/**
 * Bounded process-presence proof used before the first migration copy.
 *
 * Android instrumentation and explicit `android:process` components may create another managed
 * process with the application UID. Those processes cannot hold a PRoot/CLI file descriptor and
 * are ignored by exact package-process naming. Any other same-UID process is treated as a Runtime
 * child, and unreadable metadata fails closed.
 */
internal fun hasActiveSiblingUidProcess(applicationId: String): Boolean = runCatching {
    val currentPid = Process.myPid()
    val currentUid = Process.myUid()
    val entries = File("/proc").listFiles() ?: return@runCatching true
    var inspected = 0
    for (entry in entries) {
        val pid = entry.name.toIntOrNull() ?: continue
        if (pid == currentPid) continue
        inspected += 1
        if (inspected > 4096) return@runCatching true
        val owner = runCatching { Os.stat(entry.absolutePath).st_uid }.getOrNull() ?: continue
        if (owner != currentUid) continue
        val command = readBoundedProcessCommand(File(entry, "cmdline")) ?: return@runCatching true
        if (!isAndroidManagedSiblingProcess(command, applicationId)) return@runCatching true
    }
    false
}.getOrDefault(true)

internal fun isAndroidManagedSiblingProcess(command: String, applicationId: String): Boolean {
    if (applicationId.isBlank() || command.isBlank()) return false
    return command == applicationId ||
        command.startsWith("$applicationId:") ||
        command == "$applicationId.test" ||
        command.startsWith("$applicationId.test:")
}

private fun readBoundedProcessCommand(file: File): String? = runCatching {
    FileInputStream(file).use { input ->
        val buffer = ByteArray(512)
        val count = input.read(buffer)
        if (count <= 0) return@runCatching null
        val end = buffer.indexOfFirst { it == 0.toByte() }.let { if (it < 0 || it > count) count else it }
        buffer.copyOfRange(0, end).toString(Charsets.UTF_8)
    }
}.getOrNull()

private object AndroidSensitiveFilePolicy : SensitiveFilePolicy {
    override fun metadata(file: File): SensitiveEntryMetadata {
        val value = Os.lstat(file.absolutePath)
        val type = value.st_mode and OsConstants.S_IFMT
        return SensitiveEntryMetadata(
            uid = value.st_uid,
            gid = value.st_gid,
            mode = value.st_mode and PRIVATE_MODE_MASK,
            directory = type == OsConstants.S_IFDIR,
            regularFile = type == OsConstants.S_IFREG,
        )
    }

    override fun chmod(file: File, mode: Int) = Os.chmod(file.absolutePath, mode)
}

/**
 * Crash-safe opaque migration from backup-eligible legacy locations into no-backup directories.
 *
 * Sources are never parsed or changed before every destination is copied, hashed, validated, and
 * committed. After commit, legacy sources are atomically relocated to a no-backup rollback tree;
 * they are not deleted. Any pre-commit failure returns the untouched legacy layout.
 */
internal class SensitiveStateMigrator(
    private val filesDirectory: File,
    private val noBackupDirectory: File,
    private val workspaceDirectory: File,
    private val runtimeActive: () -> Boolean,
    private val expectedUid: Int = Process.myUid(),
    private val expectedGid: Int = Process.myUid(),
    private val filePolicy: SensitiveFilePolicy = AndroidSensitiveFilePolicy,
    private val availableBytes: () -> Long = { noBackupDirectory.usableSpace },
) {
    @Volatile private var failureCode: String? = null
    private var migrationStep: String = "migration_prepare"

    fun prepare(): SensitiveStateLayout {
        val legacy = legacyLayout()
        var committed = isCommitted()
        val marker = commitMarker()
        if (!committed && (marker.exists() || Files.isSymbolicLink(marker.toPath()))) {
            failureCode = "migration_marker_invalid"
            runCatching { restoreRollbackSources() }
            return legacy
        }
        if (!committed && runtimeActive()) {
            failureCode = "migration_runtime_active"
            return legacy
        }
        try {
            migrationStep = "migration_root_validation"
            validateRoot(filesDirectory)
            validateRoot(noBackupDirectory)
            if (!committed) {
                migrateAndCommit()
                committed = true
            } else {
                migrationStep = "migration_committed_validation"
                validateCommittedDestinations()
            }
            migrationStep = "migration_legacy_relocation"
            runCatching { relocateLegacySources() }
            return privateLayout()
        } catch (error: Exception) {
            failureCode = migrationStep
            if (committed) runCatching { restoreRollbackSources() }
            return legacy
        }
    }

    /** Fixed category only; never includes a path, credential, or file content. */
    internal fun lastFailureCode(): String? = failureCode

    private fun migrateAndCommit() {
        val targets = targets()
        migrationStep = "migration_source_preflight"
        var sourceBytes = 0L
        targets.forEach { target ->
            migrationStep = "migration_source_preflight_${target.auditLabel}"
            target.sources.forEach { source ->
                val size = treeSize(source.file, requirePrivateMode = false)
                requireMigration(size <= MAX_TOTAL_BYTES - sourceBytes, "total_size")
                sourceBytes += size
            }
        }
        requireMigration(availableBytes() >= sourceBytes + MIN_FREE_SPACE_HEADROOM, "free_space")

        targets.forEach { target ->
            migrationStep = "migration_stage_prepare"
            val final = File(noBackupDirectory, target.destinationName)
            val stage = File(noBackupDirectory, ".${target.destinationName}.stage")
            if (stage.exists() || Files.isSymbolicLink(stage.toPath())) {
                deleteTree(stage, requirePrivateMode = false)
            }
            if (final.exists() || Files.isSymbolicLink(final.toPath())) {
                deleteTree(final, requirePrivateMode = false)
            }
            createPrivateDirectory(stage)
            migrationStep = "migration_opaque_copy"
            target.sources.forEach { source ->
                if (!source.file.exists() && !Files.isSymbolicLink(source.file.toPath())) return@forEach
                if (source.contentsOnly) {
                    validateSourceDirectory(source.file)
                    source.file.listFiles()?.sortedBy { it.name }?.forEach { child ->
                        copyEntry(child, File(stage, child.name))
                    } ?: error("source listing failed")
                } else {
                    copyEntry(source.file, File(stage, source.destinationName ?: source.file.name))
                }
            }
            migrationStep = "migration_copy_validation"
            validatePrivateTree(stage)
            target.sources.forEach { source -> verifyCopiedSource(source, stage) }
            migrationStep = "migration_atomic_rename"
            Files.move(stage.toPath(), final.toPath(), StandardCopyOption.ATOMIC_MOVE)
            validatePrivateTree(final)
        }
        migrationStep = "migration_marker_commit"
        writeCommitMarker()
        migrationStep = "migration_destination_validation"
        validateCommittedDestinations()
    }

    private fun verifyCopiedSource(source: MigrationSource, destinationRoot: File) {
        if (!source.file.exists() && !Files.isSymbolicLink(source.file.toPath())) return
        if (source.contentsOnly) {
            val sourceManifest = treeManifest(
                source.file,
                requirePrivateMode = false,
                normalizeMode = true,
            )
            val destinationManifest = treeManifest(
                destinationRoot,
                requirePrivateMode = true,
                normalizeMode = false,
            )
            check(sourceManifest == destinationManifest)
        } else {
            val destination = File(destinationRoot, source.destinationName ?: source.file.name)
            check(
                fingerprint(source.file, requirePrivateMode = false, normalizeMode = true) ==
                    fingerprint(destination, requirePrivateMode = true, normalizeMode = false),
            )
        }
    }

    private fun validateCommittedDestinations() {
        check(isCommitted())
        targets().forEach { target ->
            val root = File(noBackupDirectory, target.destinationName)
            validateDirectory(root)
            // Official CLIs own their live children and may safely create 0755/0644 entries. The
            // no-backup root remains 0700; every child remains same UID/GID and non-world-writable.
            treeSize(root, requirePrivateMode = false)
        }
    }

    private fun relocateLegacySources() {
        val rollback = File(noBackupDirectory, ROLLBACK_DIRECTORY)
        if (!rollback.exists()) createPrivateDirectory(rollback) else validateDirectory(rollback)
        targets().forEach { target ->
            val category = File(rollback, target.destinationName)
            if (!category.exists()) createPrivateDirectory(category) else validateDirectory(category)
            target.sources.forEach { source ->
                if (!source.file.exists() && !Files.isSymbolicLink(source.file.toPath())) return@forEach
                validateSourceTreeOrFile(source.file)
                val destination = File(category, source.destinationName ?: source.file.name)
                if (destination.exists() || Files.isSymbolicLink(destination.toPath())) {
                    validateSourceTreeOrFile(destination)
                    when {
                        isEmptyDirectory(source.file) ->
                            deleteTree(source.file, requirePrivateMode = false)
                        fingerprint(source.file, false, false) ==
                            fingerprint(destination, false, false) ->
                            deleteTree(source.file, requirePrivateMode = false)
                        else -> quarantinePostCommitConflict(source.file, category, destination.name)
                    }
                } else {
                    Files.move(source.file.toPath(), destination.toPath(), StandardCopyOption.ATOMIC_MOVE)
                }
            }
        }
    }

    private fun restoreRollbackSources() {
        val rollback = File(noBackupDirectory, ROLLBACK_DIRECTORY)
        if (!rollback.exists() && !Files.isSymbolicLink(rollback.toPath())) return
        validateDirectory(rollback)
        targets().forEach { target ->
            val category = File(rollback, target.destinationName)
            if (!category.exists() && !Files.isSymbolicLink(category.toPath())) return@forEach
            validateDirectory(category)
            target.sources.forEach { source ->
                val backup = File(category, source.destinationName ?: source.file.name)
                if (!backup.exists() && !Files.isSymbolicLink(backup.toPath())) return@forEach
                validateSourceTreeOrFile(backup)
                if (source.file.exists() || Files.isSymbolicLink(source.file.toPath())) {
                    validateSourceTreeOrFile(source.file)
                    if (isEmptyDirectory(source.file)) {
                        deleteTree(source.file, requirePrivateMode = false)
                        check(source.file.parentFile?.isDirectory == true)
                        Files.move(backup.toPath(), source.file.toPath(), StandardCopyOption.ATOMIC_MOVE)
                    } else {
                        check(
                            fingerprint(source.file, false, false) ==
                                fingerprint(backup, false, false),
                        )
                    }
                } else {
                    check(source.file.parentFile?.isDirectory == true)
                    Files.move(backup.toPath(), source.file.toPath(), StandardCopyOption.ATOMIC_MOVE)
                }
            }
        }
    }

    private fun isEmptyDirectory(value: File): Boolean {
        if (Files.isSymbolicLink(value.toPath())) return false
        val metadata = checkedMetadata(value, requirePrivateMode = false)
        return metadata.directory && value.listFiles()?.isEmpty() == true
    }

    /** Preserves an ambiguous post-commit legacy tree without hiding it below a private bind. */
    private fun quarantinePostCommitConflict(source: File, category: File, destinationName: String) {
        val conflict = File(category, "$destinationName.postcommit-conflict")
        if (conflict.exists() || Files.isSymbolicLink(conflict.toPath())) {
            validateSourceTreeOrFile(conflict)
            check(fingerprint(source, false, false) == fingerprint(conflict, false, false))
            deleteTree(source, requirePrivateMode = false)
        } else {
            Files.move(source.toPath(), conflict.toPath(), StandardCopyOption.ATOMIC_MOVE)
        }
    }

    private fun targets(): List<MigrationTarget> = listOf(
        MigrationTarget(
            CODEX_HOME_DIRECTORY,
            "codex_home",
            listOf(MigrationSource(File(workspaceDirectory, ".alpine-codex/home"), contentsOnly = true)),
        ),
        MigrationTarget(
            GROK_HOME_DIRECTORY,
            "grok_home",
            listOf(MigrationSource(File(workspaceDirectory, ".alpine-grok/home"), contentsOnly = true)),
        ),
        MigrationTarget(
            GATEWAY_HANDOFF_DIRECTORY,
            "gateway_handoff",
            listOf(MigrationSource(File(workspaceDirectory, ".alpine-codex/security"), contentsOnly = true)),
        ),
        MigrationTarget(
            GATEWAY_WRAPPED_DIRECTORY,
            "gateway_wrapped",
            listOf(MigrationSource(File(filesDirectory, "gateway-security"), contentsOnly = true)),
        ),
        MigrationTarget(
            CONVERSATION_DIRECTORY,
            "conversation",
            listOf(
                MigrationSource(File(filesDirectory, "codex-chat-state.v1")),
                MigrationSource(File(filesDirectory, "codex-chat-state.v2")),
            ),
        ),
    )

    private fun privateLayout(): SensitiveStateLayout = SensitiveStateLayout(
        noBackupCommitted = true,
        codexHomeDirectory = File(noBackupDirectory, CODEX_HOME_DIRECTORY),
        grokHomeDirectory = File(noBackupDirectory, GROK_HOME_DIRECTORY),
        gatewayHandoffDirectory = File(noBackupDirectory, GATEWAY_HANDOFF_DIRECTORY),
        gatewayWrappedDirectory = File(noBackupDirectory, GATEWAY_WRAPPED_DIRECTORY),
        conversationDirectory = File(noBackupDirectory, CONVERSATION_DIRECTORY),
        privateDirectoryBinds = listOf(
            AndroidPrivateDirectoryBind(CODEX_HOME_DIRECTORY, CodexRuntimePaths.GUEST_HOME),
            AndroidPrivateDirectoryBind(GROK_HOME_DIRECTORY, GrokRuntimePaths.GUEST_HOME),
            AndroidPrivateDirectoryBind(GATEWAY_HANDOFF_DIRECTORY, CodexRuntimePaths.GUEST_SECURITY),
        ),
    )

    private fun legacyLayout(): SensitiveStateLayout = SensitiveStateLayout(
        noBackupCommitted = false,
        codexHomeDirectory = File(workspaceDirectory, ".alpine-codex/home"),
        grokHomeDirectory = File(workspaceDirectory, ".alpine-grok/home"),
        gatewayHandoffDirectory = File(workspaceDirectory, ".alpine-codex/security"),
        gatewayWrappedDirectory = File(filesDirectory, "gateway-security"),
        conversationDirectory = filesDirectory,
        privateDirectoryBinds = emptyList(),
    )

    private fun writeCommitMarker() {
        val marker = commitMarker()
        check(!marker.exists() && !Files.isSymbolicLink(marker.toPath()))
        FileOutputStream(marker).use { output ->
            output.write(COMMIT_CONTENT)
            output.fd.sync()
        }
        filePolicy.chmod(marker, MODE_OWNER_RW)
        check(isCommitted())
    }

    private fun isCommitted(): Boolean = runCatching {
        val marker = commitMarker()
        val metadata = filePolicy.metadata(marker)
        !Files.isSymbolicLink(marker.toPath()) &&
            metadata.uid == expectedUid &&
            metadata.gid == expectedGid &&
            metadata.regularFile &&
            metadata.mode == MODE_OWNER_RW &&
            marker.length() == COMMIT_CONTENT.size.toLong() &&
            FileInputStream(marker).use { it.readBytes().contentEquals(COMMIT_CONTENT) }
    }.getOrDefault(false)

    private fun commitMarker(): File = File(noBackupDirectory, COMMIT_MARKER)

    private fun copyEntry(source: File, destination: File) {
        check(!destination.exists() && !Files.isSymbolicLink(destination.toPath()))
        approvedSymbolicLink(source)?.let { target ->
            Files.createSymbolicLink(destination.toPath(), java.nio.file.Paths.get(target))
            check(approvedSymbolicLink(destination) == target)
            return
        }
        val metadata = checkedMetadata(source, requirePrivateMode = false)
        when {
            metadata.directory -> {
                createPrivateDirectory(destination)
                source.listFiles()?.sortedBy { it.name }?.forEach { child ->
                    copyEntry(child, File(destination, child.name))
                } ?: error("source listing failed")
            }
            metadata.regularFile -> {
                check(source.length() in 0..MAX_FILE_BYTES)
                FileInputStream(source).use { input ->
                    FileOutputStream(destination).use { output ->
                        val buffer = ByteArray(COPY_BUFFER_BYTES)
                        var copied = 0L
                        while (true) {
                            val count = input.read(buffer)
                            if (count < 0) break
                            copied += count
                            check(copied <= MAX_FILE_BYTES)
                            output.write(buffer, 0, count)
                        }
                        output.fd.sync()
                    }
                }
                filePolicy.chmod(destination, normalizedPrivateMode(metadata))
            }
            else -> error("special migration entry")
        }
    }

    private fun treeSize(file: File, requirePrivateMode: Boolean): Long {
        if (!file.exists() && !Files.isSymbolicLink(file.toPath())) return 0
        var total = 0L
        var entries = 0
        fun visit(value: File) {
            entries += 1
            requireMigration(entries <= MAX_ENTRIES, "entry_limit")
            approvedSymbolicLink(value)?.let { target ->
                total += target.toByteArray(Charsets.UTF_8).size
                requireMigration(total <= MAX_TOTAL_BYTES, "tree_size")
                return
            }
            val metadata = checkedMetadata(value, requirePrivateMode)
            if (metadata.directory) {
                value.listFiles()?.forEach(::visit) ?: failMigration("directory_listing")
            } else {
                requireMigration(metadata.regularFile, "special_entry")
                requireMigration(value.length() in 0..MAX_FILE_BYTES, "file_size")
                total += value.length()
                requireMigration(total <= MAX_TOTAL_BYTES, "tree_size")
            }
        }
        visit(file)
        return total
    }

    private fun validatePrivateTree(root: File) {
        validateDirectory(root)
        treeSize(root, requirePrivateMode = true)
    }

    private fun validateSourceTreeOrFile(value: File) {
        if (approvedSymbolicLink(value) != null) return
        val metadata = checkedMetadata(value, requirePrivateMode = false)
        if (metadata.directory) {
            validateSourceDirectory(value)
            treeSize(value, requirePrivateMode = false)
        } else {
            check(metadata.regularFile)
        }
    }

    private fun validateRoot(root: File) {
        check(!Files.isSymbolicLink(root.toPath()))
        val metadata = filePolicy.metadata(root)
        check(
            metadata.uid == expectedUid &&
                metadata.gid == expectedGid &&
                metadata.directory &&
                metadata.mode and MODE_OWNER_RWX == MODE_OWNER_RWX &&
                metadata.mode and MODE_OTHER_WRITE == 0
        )
    }

    private fun validateDirectory(directory: File) {
        val metadata = checkedMetadata(directory, requirePrivateMode = true)
        check(metadata.directory && metadata.mode == MODE_OWNER_RWX)
    }

    private fun validateSourceDirectory(directory: File) {
        val metadata = checkedMetadata(directory, requirePrivateMode = false)
        check(metadata.directory)
    }

    private fun checkedMetadata(file: File, requirePrivateMode: Boolean): SensitiveEntryMetadata {
        requireMigration(!Files.isSymbolicLink(file.toPath()), "unexpected_symlink")
        val metadata = runCatching { filePolicy.metadata(file) }
            .getOrElse { failMigration("metadata") }
        requireMigration(metadata.uid == expectedUid && metadata.gid == expectedGid, "ownership")
        if (requirePrivateMode) {
            if (metadata.directory) {
                requireMigration(metadata.mode == MODE_OWNER_RWX, "private_directory_mode")
            } else if (metadata.regularFile) {
                requireMigration(
                    metadata.mode == MODE_OWNER_RW || metadata.mode == MODE_OWNER_RWX,
                    "private_file_mode",
                )
            }
        } else {
            if (metadata.directory) {
                requireMigration(
                    metadata.mode and MODE_OWNER_RWX == MODE_OWNER_RWX,
                    "source_directory_mode",
                )
            } else if (metadata.regularFile) {
                requireMigration(
                    metadata.mode and MODE_OWNER_READ == MODE_OWNER_READ,
                    "source_file_mode",
                )
            }
            requireMigration(metadata.mode and MODE_OTHER_WRITE == 0, "world_writable")
        }
        return metadata
    }

    /**
     * Keeps only the two symlink forms required by an already-working CLI HOME. `/dev/null` is a
     * sink, while absolute `/workspace` targets stay inside the app-private guest workspace. The
     * migration never follows either form; every other link is rejected.
     */
    private fun approvedSymbolicLink(file: File): String? {
        if (!Files.isSymbolicLink(file.toPath())) return null
        val metadata = runCatching { filePolicy.metadata(file) }
            .getOrElse { failMigration("symlink_metadata") }
        requireMigration(metadata.uid == expectedUid && metadata.gid == expectedGid, "symlink_ownership")
        val target = runCatching { Files.readSymbolicLink(file.toPath()) }
            .getOrElse { failMigration("symlink_unreadable") }
        val targetValue = target.toString()
        requireMigration(targetValue.toByteArray(Charsets.UTF_8).size <= MAX_SYMLINK_BYTES, "symlink_size")
        val approved = targetValue == "/dev/null" || isFixedGuestWorkspacePath(targetValue)
        requireMigration(approved, "symlink_target")
        return targetValue
    }

    private fun isFixedGuestWorkspacePath(value: String): Boolean {
        if (value == "/workspace") return true
        if (!value.startsWith("/workspace/")) return false
        return value.removePrefix("/workspace/").split('/').all { segment ->
            segment.isNotEmpty() && segment != "." && segment != ".." &&
                segment.matches(SAFE_SYMLINK_SEGMENT)
        }
    }

    private fun requireMigration(condition: Boolean, reason: String) {
        if (!condition) failMigration(reason)
    }

    private fun failMigration(reason: String): Nothing {
        migrationStep = "${migrationStep.take(48)}_$reason"
        error("sensitive migration rejected")
    }

    private fun normalizedPrivateMode(metadata: SensitiveEntryMetadata): Int = when {
        metadata.directory -> MODE_OWNER_RWX
        metadata.regularFile && metadata.mode and MODE_OWNER_EXECUTE != 0 -> MODE_OWNER_RWX
        metadata.regularFile -> MODE_OWNER_RW
        else -> error("special migration entry")
    }

    private fun createPrivateDirectory(directory: File) {
        check(!directory.exists() && !Files.isSymbolicLink(directory.toPath()))
        check(directory.mkdir())
        filePolicy.chmod(directory, MODE_OWNER_RWX)
        validateDirectory(directory)
    }

    private fun deleteTree(value: File, requirePrivateMode: Boolean) {
        if (approvedSymbolicLink(value) != null) {
            check(value.delete())
            return
        }
        val metadata = checkedMetadata(value, requirePrivateMode)
        if (metadata.directory) {
            value.listFiles()?.forEach { child -> deleteTree(child, requirePrivateMode) }
                ?: error("delete listing failed")
        } else {
            check(metadata.regularFile)
        }
        check(value.delete())
    }

    private fun fingerprint(
        value: File,
        requirePrivateMode: Boolean,
        normalizeMode: Boolean,
    ): String {
        approvedSymbolicLink(value)?.let { target ->
            return "l:${target.toByteArray(Charsets.UTF_8).size}:${sha256(target)}"
        }
        val metadata = checkedMetadata(value, requirePrivateMode)
        if (metadata.directory) {
            return treeManifest(value, requirePrivateMode, normalizeMode)
                .entries
                .joinToString("|") { (path, entry) ->
                    "$path=${entry.type},${entry.mode},${entry.size},${entry.digest}"
                }
        }
        val mode = if (normalizeMode) normalizedPrivateMode(metadata) else metadata.mode
        return "f:$mode:${value.length()}:${sha256(value)}"
    }

    private fun treeManifest(
        root: File,
        requirePrivateMode: Boolean,
        normalizeMode: Boolean,
    ): Map<String, Fingerprint> {
        val rootMetadata = checkedMetadata(root, requirePrivateMode)
        check(rootMetadata.directory)
        val values = linkedMapOf<String, Fingerprint>()
        fun visit(directory: File, prefix: String) {
            directory.listFiles()?.sortedBy { it.name }?.forEach { child ->
                val relative = if (prefix.isEmpty()) child.name else "$prefix/${child.name}"
                approvedSymbolicLink(child)?.let { target ->
                    values[relative] = Fingerprint(
                        "l",
                        0,
                        target.toByteArray(Charsets.UTF_8).size.toLong(),
                        sha256(target),
                    )
                    check(values.size <= MAX_ENTRIES)
                    return@forEach
                }
                val metadata = checkedMetadata(child, requirePrivateMode)
                val mode = if (normalizeMode) normalizedPrivateMode(metadata) else metadata.mode
                if (metadata.directory) {
                    values[relative] = Fingerprint("d", mode, 0, "")
                    visit(child, relative)
                } else {
                    check(metadata.regularFile && child.length() <= MAX_FILE_BYTES)
                    values[relative] = Fingerprint("f", mode, child.length(), sha256(child))
                }
                check(values.size <= MAX_ENTRIES)
            } ?: error("manifest listing failed")
        }
        visit(root, "")
        return values
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).use { input ->
            val buffer = ByteArray(COPY_BUFFER_BYTES)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    private fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
        .digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    private data class MigrationTarget(
        val destinationName: String,
        val auditLabel: String,
        val sources: List<MigrationSource>,
    )

    private data class MigrationSource(
        val file: File,
        val destinationName: String? = null,
        val contentsOnly: Boolean = false,
    )

    private data class Fingerprint(
        val type: String,
        val mode: Int,
        val size: Long,
        val digest: String,
    )

    companion object {
        const val CODEX_HOME_DIRECTORY = "alpine-codex-home-v1"
        const val GROK_HOME_DIRECTORY = "alpine-grok-home-v1"
        const val GATEWAY_HANDOFF_DIRECTORY = "alpine-gateway-handoff-v1"
        const val GATEWAY_WRAPPED_DIRECTORY = "alpine-gateway-wrapped-v1"
        const val CONVERSATION_DIRECTORY = "alpine-conversation-state-v1"
        private const val ROLLBACK_DIRECTORY = "alpine-sensitive-rollback-v1"
        private const val COMMIT_MARKER = "alpine-sensitive-state-v1.commit"
        private val COMMIT_CONTENT = "alpine-sensitive-state-v1\n".toByteArray(Charsets.US_ASCII)
        private const val MODE_OWNER_RWX = 0x1c0
        private const val MODE_OWNER_RW = 0x180
        private const val MODE_OWNER_READ = 0x100
        private const val MODE_OWNER_EXECUTE = 0x40
        private const val MODE_OTHER_WRITE = 0x2
        private const val MAX_ENTRIES = 8192
        private const val MAX_FILE_BYTES = 32L * 1024 * 1024
        private const val MAX_TOTAL_BYTES = 128L * 1024 * 1024
        private const val MIN_FREE_SPACE_HEADROOM = 1024L * 1024
        private const val COPY_BUFFER_BYTES = 64 * 1024
        private const val MAX_SYMLINK_BYTES = 512
        private val SAFE_SYMLINK_SEGMENT = Regex("[A-Za-z0-9._-]+")

        fun forContext(
            context: Context,
            workspaceDirectory: File,
            runtimeActive: () -> Boolean,
        ): SensitiveStateMigrator = SensitiveStateMigrator(
            filesDirectory = context.filesDir,
            noBackupDirectory = context.noBackupFilesDir,
            workspaceDirectory = workspaceDirectory,
            runtimeActive = runtimeActive,
        )
    }
}
