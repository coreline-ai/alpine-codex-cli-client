package dev.alpine.codexclient

import java.io.File
import java.io.RandomAccessFile
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.BasicFileAttributes
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class SensitiveStateMigratorTest {
    @get:Rule
    val temporary = TemporaryFolder()

    private val policy = JvmSensitiveFilePolicy()

    @Test
    fun `only exact Android package processes are ignored by the Runtime activity gate`() {
        val applicationId = "dev.alpine.codexclient.labdebug"

        assertTrue(isAndroidManagedSiblingProcess(applicationId, applicationId))
        assertTrue(isAndroidManagedSiblingProcess("$applicationId:worker", applicationId))
        assertTrue(isAndroidManagedSiblingProcess("$applicationId.test", applicationId))
        assertTrue(isAndroidManagedSiblingProcess("$applicationId.test:runner", applicationId))
        assertFalse(isAndroidManagedSiblingProcess("$applicationId.attacker", applicationId))
        assertFalse(isAndroidManagedSiblingProcess("/data/app/libproot.so", applicationId))
        assertFalse(isAndroidManagedSiblingProcess("python3", applicationId))
        assertFalse(isAndroidManagedSiblingProcess("", applicationId))
    }

    @Test
    fun `new install commits empty no-backup layout and fixed binds`() {
        val fixture = fixture()

        val layout = fixture.migrator().prepare()

        assertTrue(layout.noBackupCommitted)
        assertTrue(layout.codexHomeDirectory.isDirectory)
        assertTrue(layout.grokHomeDirectory.isDirectory)
        assertTrue(layout.gatewayHandoffDirectory.isDirectory)
        assertTrue(layout.gatewayWrappedDirectory.isDirectory)
        assertTrue(layout.conversationDirectory.isDirectory)
        assertTrue(layout.privateDirectoryBinds.size == 3)
    }

    @Test
    fun `opaque credential session and conversation state migrate without content changes`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        val grok = fixture.legacyDirectory(".alpine-grok/home")
        val handoff = fixture.legacyDirectory(".alpine-codex/security")
        val wrapped = fixture.privateDirectory(fixture.files, "gateway-security")
        val codexBytes = byteArrayOf(0, 1, 2, 3, 0xff.toByte())
        val grokBytes = byteArrayOf(9, 8, 7)
        fixture.privateFile(codex, "auth.json", codexBytes)
        fixture.privateFile(grok, "session.bin", grokBytes)
        fixture.privateFile(handoff, "gateway-capability.v1", ByteArray(32) { it.toByte() })
        fixture.privateFile(wrapped, "gateway-session.v1", ByteArray(64) { (it * 3).toByte() })
        fixture.privateFile(fixture.files, "codex-chat-state.v2", byteArrayOf(4, 5, 6))

        val layout = fixture.migrator().prepare()

        assertTrue(layout.noBackupCommitted)
        assertArrayEquals(codexBytes, File(layout.codexHomeDirectory, "auth.json").readBytes())
        assertArrayEquals(grokBytes, File(layout.grokHomeDirectory, "session.bin").readBytes())
        assertArrayEquals(
            byteArrayOf(4, 5, 6),
            File(layout.conversationDirectory, "codex-chat-state.v2").readBytes(),
        )
        assertFalse(codex.exists())
        assertFalse(grok.exists())
        assertFalse(File(fixture.files, "codex-chat-state.v2").exists())
        assertTrue(File(fixture.noBackup, "alpine-sensitive-rollback-v1").isDirectory)
    }

    @Test
    fun `active runtime keeps untouched legacy layout`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        fixture.privateFile(codex, "auth.json", byteArrayOf(1, 2, 3))

        val layout = fixture.migrator(runtimeActive = true).prepare()

        assertFalse(layout.noBackupCommitted)
        assertTrue(File(codex, "auth.json").isFile)
        assertFalse(File(fixture.noBackup, "alpine-sensitive-state-v1.commit").exists())
        assertTrue(fixture.lastMigrator?.lastFailureCode() == "migration_runtime_active")
    }

    @Test
    fun `symlink oversized file and low space fail before source mutation`() {
        listOf("symlink", "oversized", "space").forEach { failure ->
            val fixture = fixture(failure)
            val codex = fixture.legacyDirectory(".alpine-codex/home")
            when (failure) {
                "symlink" -> {
                    val outside = fixture.privateFile(fixture.root, "outside", byteArrayOf(7))
                    Files.createSymbolicLink(File(codex, "auth.json").toPath(), outside.toPath())
                }
                "oversized" -> {
                    val file = File(codex, "oversized")
                    RandomAccessFile(file, "rw").use { it.setLength(32L * 1024 * 1024 + 1) }
                    policy.chmod(file, 0x180)
                }
                "space" -> fixture.privateFile(codex, "auth.json", byteArrayOf(1))
            }

            val layout = fixture.migrator(
                availableBytes = if (failure == "space") 0 else Long.MAX_VALUE,
            ).prepare()

            assertFalse(layout.noBackupCommitted)
            assertTrue(codex.exists())
            assertNotNull(fixture.lastMigrator?.lastFailureCode())
        }
    }

    @Test
    fun `special file metadata and world-writable mode fail before source mutation`() {
        listOf("special", "world-writable").forEach { failure ->
            val fixture = fixture(failure)
            val codex = fixture.legacyDirectory(".alpine-codex/home")
            val source = fixture.privateFile(codex, "auth.fixture", byteArrayOf(6, 2, 6))
            val selectedPolicy = if (failure == "special") {
                object : SensitiveFilePolicy by policy {
                    override fun metadata(file: File): SensitiveEntryMetadata =
                        policy.metadata(file).let { metadata ->
                            if (file == source) metadata.copy(regularFile = false) else metadata
                        }
                }
            } else {
                policy.chmod(source, 0x1b6)
                policy
            }

            val layout = fixture.migrator(filePolicy = selectedPolicy).prepare()

            assertFalse(layout.noBackupCommitted)
            assertTrue(source.isFile)
            assertNotNull(fixture.lastMigrator?.lastFailureCode())
        }
    }

    @Test
    fun `safe legacy CLI modes are normalized only in copied no-backup tree`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        val cache = fixture.privateDirectory(codex, "cache")
        val source = fixture.privateFile(cache, "state.fixture", byteArrayOf(9, 7, 9))
        policy.chmod(codex, 0x1ed)
        policy.chmod(cache, 0x1ed)
        policy.chmod(source, 0x1a4)

        val layout = fixture.migrator().prepare()

        assertTrue(layout.noBackupCommitted)
        val destinationCache = File(layout.codexHomeDirectory, "cache")
        val destination = File(destinationCache, "state.fixture")
        assertArrayEquals(byteArrayOf(9, 7, 9), destination.readBytes())
        assertTrue(policy.metadata(destinationCache).mode == 0x1c0)
        assertTrue(policy.metadata(destination).mode == 0x180)
    }

    @Test
    fun `fixed guest-workspace symlink is copied without following its target`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        val link = File(codex, "workspace-link")
        Files.createSymbolicLink(link.toPath(), java.nio.file.Path.of("/workspace/project"))

        val layout = fixture.migrator().prepare()

        val destination = File(layout.codexHomeDirectory, "workspace-link")
        assertTrue(layout.noBackupCommitted)
        assertTrue(Files.isSymbolicLink(destination.toPath()))
        assertTrue(Files.readSymbolicLink(destination.toPath()).toString() == "/workspace/project")
    }

    @Test
    fun `committed migration is idempotent across process restart`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        val opaque = byteArrayOf(8, 5, 3, 0)
        fixture.privateFile(codex, "session.fixture", opaque)

        val first = fixture.migrator().prepare()
        val second = fixture.migrator().prepare()

        assertTrue(first.noBackupCommitted)
        assertTrue(second.noBackupCommitted)
        assertArrayEquals(opaque, File(second.codexHomeDirectory, "session.fixture").readBytes())
        assertFalse(codex.exists())
    }

    @Test
    fun `committed CLI-owned safe modes remain valid across restart`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        fixture.privateFile(codex, "auth.fixture", byteArrayOf(4, 2))
        val first = fixture.migrator().prepare()
        val liveCache = fixture.privateDirectory(first.codexHomeDirectory, "live-cache")
        val liveFile = fixture.privateFile(liveCache, "models.fixture", byteArrayOf(1))
        policy.chmod(liveCache, 0x1ed)
        policy.chmod(liveFile, 0x1a4)
        File(fixture.workspace, ".alpine-codex/home").apply {
            mkdirs()
            policy.chmod(parentFile, 0x1c0)
            policy.chmod(this, 0x1c0)
        }

        val second = fixture.migrator().prepare()

        assertTrue(second.noBackupCommitted)
        assertArrayEquals(byteArrayOf(4, 2), File(second.codexHomeDirectory, "auth.fixture").readBytes())
        assertFalse(File(fixture.workspace, ".alpine-codex/home").exists())
    }

    @Test
    fun `committed validation failure restores rollback over an empty mount point`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        fixture.privateFile(codex, "auth.fixture", byteArrayOf(7, 3))
        val migrated = fixture.migrator().prepare()
        val destination = File(migrated.codexHomeDirectory, "auth.fixture")
        policy.chmod(destination, 0x1b6)
        File(fixture.workspace, ".alpine-codex/home").apply {
            mkdirs()
            policy.chmod(parentFile, 0x1c0)
            policy.chmod(this, 0x1c0)
        }

        val fallback = fixture.migrator().prepare()

        assertFalse(fallback.noBackupCommitted)
        assertArrayEquals(byteArrayOf(7, 3), File(codex, "auth.fixture").readBytes())
    }

    @Test
    fun `partial copy is discarded on retry while source remains authoritative`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        fixture.privateFile(codex, "auth.json", byteArrayOf(3, 1, 4))
        var chmodCalls = 0
        val crashAfterChmod = object : SensitiveFilePolicy by policy {
            override fun chmod(file: File, mode: Int) {
                policy.chmod(file, mode)
                chmodCalls += 1
                if (chmodCalls == 2) error("injected copy interruption")
            }
        }

        val failed = fixture.migrator(filePolicy = crashAfterChmod).prepare()
        assertFalse(failed.noBackupCommitted)
        assertTrue(File(codex, "auth.json").isFile)

        val recovered = fixture.migrator().prepare()
        assertTrue(recovered.noBackupCommitted)
        assertArrayEquals(byteArrayOf(3, 1, 4), File(recovered.codexHomeDirectory, "auth.json").readBytes())
    }

    @Test
    fun `retry removes an owned pre-commit stage with legacy-safe modes`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        fixture.privateFile(codex, "auth.fixture", byteArrayOf(1, 6, 1, 8))
        val staleStage = fixture.privateDirectory(fixture.noBackup, ".alpine-codex-home-v1.stage")
        val interrupted = fixture.privateFile(staleStage, "interrupted.fixture", byteArrayOf(9))
        policy.chmod(interrupted, 0x1a4)

        val recovered = fixture.migrator().prepare()

        assertTrue(recovered.noBackupCommitted)
        assertFalse(staleStage.exists())
        assertArrayEquals(
            byteArrayOf(1, 6, 1, 8),
            File(recovered.codexHomeDirectory, "auth.fixture").readBytes(),
        )
    }

    @Test
    fun `corrupt commit marker restores rollback to legacy without data loss`() {
        val fixture = fixture()
        val codex = fixture.legacyDirectory(".alpine-codex/home")
        fixture.privateFile(codex, "auth.json", byteArrayOf(2, 7, 1, 8))
        val migrated = fixture.migrator().prepare()
        assertTrue(migrated.noBackupCommitted)
        assertFalse(codex.exists())
        val marker = File(fixture.noBackup, "alpine-sensitive-state-v1.commit")
        marker.writeText("corrupt")
        policy.chmod(marker, 0x180)

        val fallback = fixture.migrator().prepare()

        assertFalse(fallback.noBackupCommitted)
        assertArrayEquals(byteArrayOf(2, 7, 1, 8), File(codex, "auth.json").readBytes())
        assertTrue(fixture.lastMigrator?.lastFailureCode() == "migration_marker_invalid")
    }

    private fun fixture(name: String = "fixture"): Fixture {
        val root = temporary.newFolder(name)
        policy.chmod(root, 0x1c0)
        val files = File(root, "files").also { it.mkdir() }
        val noBackup = File(root, "no_backup").also { it.mkdir() }
        policy.chmod(files, 0x1c0)
        policy.chmod(noBackup, 0x1c0)
        return Fixture(root, files, noBackup, File(files, "alpine-codex-runtime/workspace"))
    }

    private inner class Fixture(
        val root: File,
        val files: File,
        val noBackup: File,
        val workspace: File,
    ) {
        var lastMigrator: SensitiveStateMigrator? = null

        fun migrator(
            runtimeActive: Boolean = false,
            filePolicy: SensitiveFilePolicy = policy,
            availableBytes: Long = Long.MAX_VALUE,
        ): SensitiveStateMigrator = SensitiveStateMigrator(
            filesDirectory = files,
            noBackupDirectory = noBackup,
            workspaceDirectory = workspace,
            runtimeActive = { runtimeActive },
            expectedUid = TEST_UID,
            expectedGid = TEST_UID,
            filePolicy = filePolicy,
            availableBytes = { availableBytes },
        ).also { lastMigrator = it }

        fun legacyDirectory(relative: String): File {
            val value = File(workspace, relative)
            value.mkdirs()
            var current: File? = value
            while (current != null && current.toPath().startsWith(root.toPath())) {
                policy.chmod(current, 0x1c0)
                if (current == root) break
                current = current.parentFile
            }
            return value
        }

        fun privateDirectory(parent: File, name: String): File = File(parent, name).also {
            assertTrue(it.mkdir())
            policy.chmod(it, 0x1c0)
        }

        fun privateFile(parent: File, name: String, bytes: ByteArray): File = File(parent, name).also {
            it.writeBytes(bytes)
            policy.chmod(it, 0x180)
        }
    }

    private class JvmSensitiveFilePolicy : SensitiveFilePolicy {
        override fun metadata(file: File): SensitiveEntryMetadata {
            val permissions = Files.getPosixFilePermissions(file.toPath(), LinkOption.NOFOLLOW_LINKS)
            val attributes = Files.readAttributes(
                file.toPath(),
                BasicFileAttributes::class.java,
                LinkOption.NOFOLLOW_LINKS,
            )
            val mode = permissions.sumOf { permission ->
                when (permission) {
                    PosixFilePermission.OWNER_READ -> 0x100
                    PosixFilePermission.OWNER_WRITE -> 0x80
                    PosixFilePermission.OWNER_EXECUTE -> 0x40
                    PosixFilePermission.GROUP_READ -> 0x20
                    PosixFilePermission.GROUP_WRITE -> 0x10
                    PosixFilePermission.GROUP_EXECUTE -> 0x8
                    PosixFilePermission.OTHERS_READ -> 0x4
                    PosixFilePermission.OTHERS_WRITE -> 0x2
                    PosixFilePermission.OTHERS_EXECUTE -> 0x1
                }
            }
            return SensitiveEntryMetadata(
                uid = TEST_UID,
                gid = TEST_UID,
                mode = mode,
                directory = attributes.isDirectory,
                regularFile = attributes.isRegularFile,
            )
        }

        override fun chmod(file: File, mode: Int) {
            val values = mutableSetOf<PosixFilePermission>()
            if (mode and 0x100 != 0) values += PosixFilePermission.OWNER_READ
            if (mode and 0x80 != 0) values += PosixFilePermission.OWNER_WRITE
            if (mode and 0x40 != 0) values += PosixFilePermission.OWNER_EXECUTE
            if (mode and 0x20 != 0) values += PosixFilePermission.GROUP_READ
            if (mode and 0x10 != 0) values += PosixFilePermission.GROUP_WRITE
            if (mode and 0x8 != 0) values += PosixFilePermission.GROUP_EXECUTE
            if (mode and 0x4 != 0) values += PosixFilePermission.OTHERS_READ
            if (mode and 0x2 != 0) values += PosixFilePermission.OTHERS_WRITE
            if (mode and 0x1 != 0) values += PosixFilePermission.OTHERS_EXECUTE
            Files.setPosixFilePermissions(file.toPath(), values)
        }
    }

    private companion object {
        const val TEST_UID = 1000
    }
}
