package dev.alpine.codexclient.grokcli

import android.content.Context
import android.system.Os
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import org.json.JSONObject

data class StagedGrokCli(
    val version: String,
    val versionOutput: String,
    val guestExecutablePath: String,
)

/** Stable, redacted artifact error; raw filesystem and asset details are intentionally omitted. */
class GrokCliArtifactException : RuntimeException("GROK_CLI_ARTIFACT_INVALID")

/**
 * Verifies and atomically stages only the pinned debug asset. Process launch, OAuth, account state,
 * and backend selection remain outside this artifact boundary.
 */
class GrokCliArtifactProvider(private val context: Context) {
    fun stage(
        hostStagingDirectory: File,
        guestStagingDirectory: String,
    ): StagedGrokCli = try {
        val lock = readLock()
        requireSafeLock(lock)
        val stagingRoot = ensurePrivateDirectory(hostStagingDirectory.canonicalFile)
        val root = ensurePrivateDirectory(File(stagingRoot, ROOT_DIRECTORY))
        cleanupPartialDirectories(root)
        val versionDirectory = File(root, lock.version)
        val executable = File(versionDirectory, lock.binaryName)
        if (versionDirectory.exists() && !hasExpectedBinary(executable, lock)) {
            quarantine(root, versionDirectory)
        }
        val stagedExecutable = if (hasExpectedBinary(executable, lock)) {
            setExecutableMode(executable)
            executable
        } else {
            stageNewVersion(root, versionDirectory, lock)
        }
        check(hasExpectedBinary(stagedExecutable, lock))
        cleanupPreviousVersions(root, lock.version)
        StagedGrokCli(
            version = lock.version,
            versionOutput = lock.versionOutput,
            guestExecutablePath = "$guestStagingDirectory/$ROOT_DIRECTORY/${lock.version}/${lock.binaryName}",
        )
    } catch (_: Exception) {
        throw GrokCliArtifactException()
    }

    private fun stageNewVersion(
        root: File,
        versionDirectory: File,
        lock: GrokCliAssetLock,
    ): File {
        check(!versionDirectory.exists())
        val partialDirectory = File(root, ".${lock.version}.partial-${System.nanoTime()}")
        ensurePrivateDirectory(partialDirectory)
        return try {
            val partialExecutable = File(partialDirectory, lock.binaryName)
            context.assets.open("$ASSET_DIRECTORY/${lock.binaryName}").use { input ->
                FileOutputStream(partialExecutable).use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var copied = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        copied += count
                        check(copied <= lock.binarySize)
                        output.write(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            check(hasExpectedBinary(partialExecutable, lock))
            setExecutableMode(partialExecutable)
            Files.move(
                partialDirectory.toPath(),
                versionDirectory.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
            )
            setDirectoryMode(versionDirectory)
            File(versionDirectory, lock.binaryName).also {
                check(hasExpectedBinary(it, lock))
                setExecutableMode(it)
            }
        } catch (error: Exception) {
            partialDirectory.deleteRecursively()
            throw error
        }
    }

    private fun quarantine(root: File, invalidVersionDirectory: File) {
        val quarantineDirectory = ensurePrivateDirectory(File(root, QUARANTINE_DIRECTORY))
        val destination = File(
            quarantineDirectory,
            "${invalidVersionDirectory.name}-${System.currentTimeMillis()}",
        )
        Files.move(
            invalidVersionDirectory.toPath(),
            destination.toPath(),
            StandardCopyOption.ATOMIC_MOVE,
        )
        setDirectoryMode(destination)
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

    /** Keeps executable bytes for the active pinned version only; no credential directory is touched. */
    private fun cleanupPreviousVersions(root: File, activeVersion: String) {
        root.listFiles()
            ?.filter {
                it.isDirectory &&
                    it.name != activeVersion &&
                    it.name != QUARANTINE_DIRECTORY &&
                    !it.name.startsWith(".")
            }
            ?.forEach(File::deleteRecursively)
    }

    private fun readLock(): GrokCliAssetLock {
        val json = context.assets.open("$ASSET_DIRECTORY/$LOCK_FILE").bufferedReader().use { reader ->
            JSONObject(reader.readText())
        }
        return GrokCliAssetLock(
            version = json.getString("version"),
            target = json.getString("target"),
            sourceUrl = json.getString("source_url"),
            artifactName = json.getString("artifact_name"),
            binaryName = json.getString("binary_name"),
            binarySize = json.getLong("binary_size"),
            binarySha256 = json.getString("binary_sha256"),
            versionOutput = json.getString("version_output"),
        )
    }

    private fun requireSafeLock(lock: GrokCliAssetLock) {
        check(SEMVER.matches(lock.version))
        check(lock.target == "linux-aarch64-static")
        check(lock.artifactName == "grok-${lock.version}-linux-aarch64")
        check(lock.sourceUrl == "https://x.ai/cli/${lock.artifactName}")
        check(lock.binaryName == "grok")
        check(lock.binarySize > 0)
        check(SHA_256.matches(lock.binarySha256))
        check(Regex("grok ${Regex.escape(lock.version)} \\([0-9a-f]{10}\\)").matches(lock.versionOutput))
    }

    private fun ensurePrivateDirectory(directory: File): File {
        check(directory.exists() || directory.mkdirs())
        check(directory.isDirectory)
        setDirectoryMode(directory)
        return directory
    }

    private fun setDirectoryMode(directory: File) {
        Os.chmod(directory.absolutePath, MODE_OWNER_RWX)
    }

    private fun setExecutableMode(file: File) {
        Os.chmod(file.absolutePath, MODE_OWNER_RWX)
    }

    private fun hasExpectedBinary(file: File, lock: GrokCliAssetLock): Boolean = runCatching {
        file.isFile &&
            file.length() == lock.binarySize &&
            sha256(file) == lock.binarySha256 &&
            isStaticAarch64Elf(file)
    }.getOrDefault(false)

    private fun isStaticAarch64Elf(file: File): Boolean = RandomAccessFile(file, "r").use { input ->
        val header = ByteArray(64)
        input.readFully(header)
        if (!header.copyOfRange(0, 4).contentEquals(ELF_MAGIC)) return@use false
        if (header[4].toInt() != 2 || header[5].toInt() != 1) return@use false
        val elf = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
        if ((elf.getShort(18).toInt() and 0xffff) != AARCH64_MACHINE) return@use false
        val programHeaderOffset = elf.getLong(32)
        val programHeaderSize = elf.getShort(54).toInt() and 0xffff
        val programHeaderCount = elf.getShort(56).toInt() and 0xffff
        if (programHeaderOffset < 64 || programHeaderSize < 56 || programHeaderCount !in 1..256) return@use false
        if (programHeaderOffset + programHeaderSize.toLong() * programHeaderCount > file.length()) return@use false
        repeat(programHeaderCount) { index ->
            input.seek(programHeaderOffset + programHeaderSize.toLong() * index)
            if (Integer.reverseBytes(input.readInt()) == PROGRAM_HEADER_INTERPRETER) return@use false
        }
        true
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

    private data class GrokCliAssetLock(
        val version: String,
        val target: String,
        val sourceUrl: String,
        val artifactName: String,
        val binaryName: String,
        val binarySize: Long,
        val binarySha256: String,
        val versionOutput: String,
    )

    private companion object {
        const val ROOT_DIRECTORY = "grok-cli"
        const val QUARANTINE_DIRECTORY = "quarantine"
        const val ASSET_DIRECTORY = "grok-cli"
        const val LOCK_FILE = "grok-cli.lock.json"
        const val MAX_QUARANTINE_DIRECTORIES = 3
        const val MODE_OWNER_RWX = 448
        const val AARCH64_MACHINE = 183
        const val PROGRAM_HEADER_INTERPRETER = 3
        val ELF_MAGIC = byteArrayOf(0x7f, 0x45, 0x4c, 0x46)
        val SEMVER = Regex("[0-9]+\\.[0-9]+\\.[0-9]+")
        val SHA_256 = Regex("[0-9a-f]{64}")
    }
}
