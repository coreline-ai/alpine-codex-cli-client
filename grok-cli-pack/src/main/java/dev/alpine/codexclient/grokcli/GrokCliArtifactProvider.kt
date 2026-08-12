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

data class StagedGrokProfile(
    val profileName: String,
    val sha256: String,
    val guestProfilePath: String,
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

    fun stageProfile(
        hostProfileDirectory: File,
        guestProfileDirectory: String,
    ): StagedGrokProfile = try {
        check(SAFE_GUEST_PATH.matches(guestProfileDirectory))
        val lock = readProfileLock()
        requireSafeProfileLock(lock)
        val directory = ensurePrivateDirectory(hostProfileDirectory.canonicalFile)
        val destination = File(directory, lock.fileName)
        check(!Files.isSymbolicLink(destination.toPath()))
        val temporary = File(directory, ".${lock.fileName}.${System.nanoTime()}.partial")
        try {
            context.assets.open("$PROFILE_ASSET_DIRECTORY/${lock.fileName}").use { input ->
                FileOutputStream(temporary).use { output ->
                    val buffer = ByteArray(16 * 1024)
                    var copied = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        copied += count
                        check(copied <= lock.size)
                        output.write(buffer, 0, count)
                    }
                    output.fd.sync()
                }
            }
            check(hasExpectedProfile(temporary, lock))
            setProfileMode(temporary)
            Files.move(
                temporary.toPath(),
                destination.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
        } finally {
            temporary.delete()
        }
        check(!Files.isSymbolicLink(destination.toPath()))
        check(hasExpectedProfile(destination, lock))
        setProfileMode(destination)
        StagedGrokProfile(
            profileName = lock.profileName,
            sha256 = lock.sha256,
            guestProfilePath = "$guestProfileDirectory/${lock.fileName}",
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

    private fun readProfileLock(): GrokProfileAssetLock {
        val json = context.assets.open("$PROFILE_ASSET_DIRECTORY/$PROFILE_LOCK_FILE")
            .bufferedReader()
            .use { reader -> JSONObject(reader.readText()) }
        return GrokProfileAssetLock(
            schemaVersion = json.getInt("schema_version"),
            profileName = json.getString("profile_name"),
            fileName = json.getString("file_name"),
            size = json.getLong("size"),
            sha256 = json.getString("sha256"),
            grokCliVersion = json.getString("grok_cli_version"),
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

    private fun requireSafeProfileLock(lock: GrokProfileAssetLock) {
        check(lock.schemaVersion == 1)
        check(lock.profileName == "alpine-chat-only")
        check(lock.fileName == "chat-only.md")
        check(lock.size in 1..MAX_PROFILE_BYTES)
        check(SHA_256.matches(lock.sha256))
        check(lock.grokCliVersion == "1.0.0")
    }

    private fun ensurePrivateDirectory(directory: File): File {
        check(directory.exists() || directory.mkdirs())
        check(directory.isDirectory)
        setDirectoryMode(directory)
        return directory
    }

    private fun setDirectoryMode(directory: File) {
        Os.chmod(directory.absolutePath, MODE_OWNER_RWX)
        check(Os.stat(directory.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RWX)
    }

    private fun setExecutableMode(file: File) {
        Os.chmod(file.absolutePath, MODE_OWNER_RWX)
        check(Os.stat(file.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RWX)
    }

    private fun setProfileMode(file: File) {
        Os.chmod(file.absolutePath, MODE_OWNER_RW)
        check(Os.stat(file.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RW)
    }

    private fun hasExpectedBinary(file: File, lock: GrokCliAssetLock): Boolean = runCatching {
        file.isFile &&
            file.length() == lock.binarySize &&
            sha256(file) == lock.binarySha256 &&
            isStaticAarch64Elf(file)
    }.getOrDefault(false)

    private fun hasExpectedProfile(file: File, lock: GrokProfileAssetLock): Boolean =
        file.isFile && file.length() == lock.size && sha256(file) == lock.sha256

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

    private data class GrokProfileAssetLock(
        val schemaVersion: Int,
        val profileName: String,
        val fileName: String,
        val size: Long,
        val sha256: String,
        val grokCliVersion: String,
    )

    private companion object {
        const val ROOT_DIRECTORY = "grok-cli"
        const val QUARANTINE_DIRECTORY = "quarantine"
        const val ASSET_DIRECTORY = "grok-cli"
        const val PROFILE_ASSET_DIRECTORY = "grok-profile"
        const val LOCK_FILE = "grok-cli.lock.json"
        const val PROFILE_LOCK_FILE = "chat-only.lock.json"
        const val MAX_QUARANTINE_DIRECTORIES = 3
        const val MAX_PROFILE_BYTES = 16 * 1024L
        const val MODE_OWNER_RWX = 448
        const val MODE_OWNER_RW = 384
        const val MODE_MASK = 511
        const val AARCH64_MACHINE = 183
        const val PROGRAM_HEADER_INTERPRETER = 3
        val ELF_MAGIC = byteArrayOf(0x7f, 0x45, 0x4c, 0x46)
        val SEMVER = Regex("[0-9]+\\.[0-9]+\\.[0-9]+")
        val SHA_256 = Regex("[0-9a-f]{64}")
        val SAFE_GUEST_PATH = Regex("/[A-Za-z0-9_./-]+")
    }
}
