package dev.alpine.codexclient.cli

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

data class StagedCodexCli(
    val version: String,
    val guestExecutablePath: String,
)

/** Stable, redacted artifact error; raw filesystem and asset details are intentionally omitted. */
class CodexCliArtifactException : RuntimeException("CODEX_CLI_ARTIFACT_INVALID")

/**
 * Owns debug-asset validation and app-private staging only. It never starts a CLI process and
 * never handles OAuth or account state. Call it only before an app-server process is active.
 */
class CodexCliArtifactProvider(private val context: Context) {
    fun stage(
        hostStagingDirectory: File,
        guestStagingDirectory: String,
    ): StagedCodexCli = try {
        val lock = readLock()
        requireSafeLock(lock)
        val root = ensureDirectory(File(hostStagingDirectory, ROOT_DIRECTORY))
        cleanupPartialDirectories(root)
        val versionDirectory = File(root, lock.version)
        val executable = File(versionDirectory, lock.binaryName)
        if (versionDirectory.exists() && !hasExpectedBinary(executable, lock)) {
            quarantine(root, versionDirectory)
        }
        val stagedExecutable = if (hasExpectedBinary(executable, lock)) {
            ensureExecutable(executable)
            executable
        } else {
            stageNewVersion(root, versionDirectory, lock)
        }
        cleanupPreviousVersions(root, lock.version)
        StagedCodexCli(
            version = lock.version,
            guestExecutablePath = "$guestStagingDirectory/$ROOT_DIRECTORY/${lock.version}/${lock.binaryName}",
        ).also { check(stagedExecutable == File(versionDirectory, lock.binaryName)) }
    } catch (_: Exception) {
        throw CodexCliArtifactException()
    }

    private fun stageNewVersion(
        root: File,
        versionDirectory: File,
        lock: CodexCliAssetLock,
    ): File {
        check(!versionDirectory.exists())
        val partialDirectory = File(root, ".${lock.version}.partial-${System.nanoTime()}")
        check(partialDirectory.mkdirs())
        return try {
            val partialExecutable = File(partialDirectory, lock.binaryName)
            context.assets.open("$ASSET_DIRECTORY/${lock.binaryName}").use { input ->
                FileOutputStream(partialExecutable).use { output ->
                    input.copyTo(output)
                    output.fd.sync()
                }
            }
            check(hasExpectedBinary(partialExecutable, lock))
            ensureExecutable(partialExecutable)
            Files.move(
                partialDirectory.toPath(),
                versionDirectory.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
            )
            File(versionDirectory, lock.binaryName).also { check(hasExpectedBinary(it, lock)) }
        } catch (error: Exception) {
            partialDirectory.deleteRecursively()
            throw error
        }
    }

    private fun quarantine(root: File, invalidVersionDirectory: File) {
        val quarantineDirectory = ensureDirectory(File(root, QUARANTINE_DIRECTORY))
        val destination = File(
            quarantineDirectory,
            "${invalidVersionDirectory.name}-${System.currentTimeMillis()}",
        )
        Files.move(
            invalidVersionDirectory.toPath(),
            destination.toPath(),
            StandardCopyOption.ATOMIC_MOVE,
        )
        quarantineDirectory.listFiles()
            ?.filter { it.isDirectory }
            ?.sortedByDescending(File::lastModified)
            ?.drop(MAX_QUARANTINE_DIRECTORIES)
            ?.forEach(File::deleteRecursively)
    }

    private fun cleanupPartialDirectories(root: File) {
        root.listFiles()
            ?.filter { it.isDirectory && it.name.startsWith(".") && it.name.contains(".partial-") }
            ?.forEach(File::deleteRecursively)
    }

    /** Keeps the active pinned version only; this directory contains executable bytes, not OAuth state. */
    private fun cleanupPreviousVersions(root: File, activeVersion: String) {
        root.listFiles()
            ?.filter { it.isDirectory && it.name != activeVersion && it.name != QUARANTINE_DIRECTORY && !it.name.startsWith(".") }
            ?.forEach(File::deleteRecursively)
    }

    private fun readLock(): CodexCliAssetLock {
        val json = context.assets.open("$ASSET_DIRECTORY/$LOCK_FILE").bufferedReader().use { reader ->
            JSONObject(reader.readText())
        }
        return CodexCliAssetLock(
            version = json.getString("version"),
            binaryName = json.getString("binary_name"),
            binarySize = json.getLong("binary_size"),
            binarySha256 = json.getString("binary_sha256"),
        )
    }

    private fun requireSafeLock(lock: CodexCliAssetLock) {
        check(SAFE_PATH_COMPONENT.matches(lock.version))
        check(lock.binaryName == "codex")
        check(lock.binarySize > 0)
        check(SHA_256.matches(lock.binarySha256))
    }

    private fun ensureDirectory(directory: File): File {
        check(directory.exists() || directory.mkdirs())
        check(directory.isDirectory)
        return directory
    }

    private fun hasExpectedBinary(file: File, lock: CodexCliAssetLock): Boolean =
        file.isFile && file.length() == lock.binarySize && sha256(file) == lock.binarySha256

    private fun ensureExecutable(file: File) {
        check(file.setReadable(true, true))
        check(file.setExecutable(true, true))
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        FileInputStream(file).buffered().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { byte ->
            (byte.toInt() and 0xff).toString(16).padStart(2, '0')
        }
    }

    private data class CodexCliAssetLock(
        val version: String,
        val binaryName: String,
        val binarySize: Long,
        val binarySha256: String,
    )

    private companion object {
        const val ROOT_DIRECTORY = "codex-cli"
        const val QUARANTINE_DIRECTORY = "quarantine"
        const val ASSET_DIRECTORY = "codex-cli"
        const val LOCK_FILE = "codex-cli.lock.json"
        const val MAX_QUARANTINE_DIRECTORIES = 3
        val SAFE_PATH_COMPONENT = Regex("[A-Za-z0-9._-]+")
        val SHA_256 = Regex("[0-9a-f]{64}")
    }
}
