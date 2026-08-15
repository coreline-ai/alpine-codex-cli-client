package dev.alpine.codexclient

import android.os.Process
import android.system.Os
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.nio.file.Files
import java.nio.file.Paths
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Exercises the real Android lstat/chmod/atomic-rename migration path with opaque fixture bytes. */
class SensitiveStateMigrationInstrumentedTest {
    @Test
    fun opaqueStateMigratesWithAndroidUidAndPrivateModes() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        assertEquals("dev.alpine.codexclient.labdebug", context.packageName)
        val root = File(context.cacheDir, "sensitive-migration-instrumented").apply {
            deleteRecursively()
            assertTrue(mkdir())
            Os.chmod(absolutePath, 448)
        }
        try {
            val files = File(root, "files").apply {
                assertTrue(mkdir())
                Os.chmod(absolutePath, 448)
            }
            val noBackup = File(root, "no_backup").apply {
                assertTrue(mkdir())
                Os.chmod(absolutePath, 448)
            }
            val workspace = File(files, "alpine-codex-runtime/workspace")
            val legacyHome = File(workspace, ".alpine-codex/home").apply {
                assertTrue(mkdirs())
            }
            generateSequence(legacyHome as File?) { value ->
                value.parentFile?.takeIf { it.toPath().startsWith(root.toPath()) }
            }.forEach { Os.chmod(it.absolutePath, 448) }
            val opaque = byteArrayOf(0, 3, 1, 4, 0xff.toByte())
            File(legacyHome, "opaque.fixture").apply {
                writeBytes(opaque)
                Os.chmod(absolutePath, 384)
            }
            Files.createSymbolicLink(
                File(legacyHome, "workspace-link").toPath(),
                Paths.get("/workspace/project"),
            )

            val migrator = SensitiveStateMigrator(
                filesDirectory = files,
                noBackupDirectory = noBackup,
                workspaceDirectory = workspace,
                runtimeActive = { false },
                expectedUid = Process.myUid(),
                expectedGid = Process.myUid(),
            )

            val first = migrator.prepare()
            assertTrue(first.noBackupCommitted)
            assertArrayEquals(opaque, File(first.codexHomeDirectory, "opaque.fixture").readBytes())
            assertTrue(Files.isSymbolicLink(File(first.codexHomeDirectory, "workspace-link").toPath()))
            assertFalse(legacyHome.exists())

            val second = migrator.prepare()
            assertTrue(second.noBackupCommitted)
            assertArrayEquals(opaque, File(second.codexHomeDirectory, "opaque.fixture").readBytes())
        } finally {
            root.deleteRecursively()
        }
    }
}
