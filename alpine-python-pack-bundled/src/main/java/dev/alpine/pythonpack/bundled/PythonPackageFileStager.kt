package dev.alpine.pythonpack.bundled

import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.InputStream
import java.nio.file.FileVisitResult
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.SimpleFileVisitor
import java.nio.file.StandardCopyOption
import java.nio.file.attribute.BasicFileAttributes
import java.security.MessageDigest

internal data class VerifiedPythonPackageEntry(
    val fileName: String,
    val size: Long,
    val sha256: String,
) {
    init {
        require(fileName.endsWith(".apk") && SAFE_FILE_NAME.matches(fileName))
        require(size in 1..MAX_PACKAGE_BYTES)
        require(SHA_256.matches(sha256))
    }

    private companion object {
        const val MAX_PACKAGE_BYTES = 512L * 1024 * 1024
        val SAFE_FILE_NAME = Regex("[A-Za-z0-9][A-Za-z0-9._+~-]*")
        val SHA_256 = Regex("[0-9a-f]{64}")
    }
}

/** Pure file stager kept independent of Android so atomicity and repair behavior are unit tested. */
internal class PythonPackageFileStager(
    private val openPackage: (String) -> InputStream,
) {
    fun stage(
        hostStagingDirectory: File,
        guestStagingDirectory: String,
        packId: String,
        entries: List<VerifiedPythonPackageEntry>,
    ): List<String> {
        require(SAFE_PACK_ID.matches(packId))
        require(SAFE_GUEST_DIRECTORY.matches(guestStagingDirectory))
        require(
            guestStagingDirectory.startsWith("/workspace/") &&
                guestStagingDirectory.split('/').drop(1).all { it.isNotBlank() && it != "." && it != ".." },
        )
        require(entries.size in 1..MAX_PACKAGES)
        require(entries.map { it.fileName }.distinct().size == entries.size)
        val root = ensureDirectory(File(hostStagingDirectory, ROOT_DIRECTORY))
        cleanupPartialDirectories(root)
        val target = File(root, packId)
        if (!isValidPack(target, entries)) {
            deleteTree(target)
            stageNewPack(root, target, packId, entries)
        }
        check(isValidPack(target, entries))
        cleanupPreviousPacks(root, packId)
        return entries.map { entry ->
            "$guestStagingDirectory/$ROOT_DIRECTORY/$packId/packages/${entry.fileName}"
        }
    }

    private fun stageNewPack(
        root: File,
        target: File,
        packId: String,
        entries: List<VerifiedPythonPackageEntry>,
    ) {
        check(!target.exists())
        val partial = File(root, ".$packId.partial-${System.nanoTime()}")
        check(partial.mkdirs())
        try {
            for (entry in entries) {
                val destination = safePackageFile(partial, entry.fileName)
                check(destination.parentFile?.exists() == true || destination.parentFile?.mkdirs() == true)
                openPackage(entry.fileName).use { input ->
                    FileOutputStream(destination).use { output ->
                        input.copyTo(output)
                        output.fd.sync()
                    }
                }
                check(isValidFile(destination, entry))
                check(destination.setReadable(true, true))
                check(destination.setWritable(true, true))
            }
            Files.move(partial.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
        } catch (error: Exception) {
            deleteTree(partial)
            throw error
        }
    }

    private fun isValidPack(directory: File, entries: List<VerifiedPythonPackageEntry>): Boolean {
        if (!directory.isDirectory || Files.isSymbolicLink(directory.toPath())) return false
        val packageDirectory = File(directory, "packages")
        if (!packageDirectory.isDirectory || Files.isSymbolicLink(packageDirectory.toPath())) return false
        val expectedNames = entries.map { it.fileName }.toSet()
        val actualNames = packageDirectory.listFiles()?.map { it.name }?.toSet() ?: return false
        return actualNames == expectedNames && entries.all { entry ->
            isValidFile(safePackageFile(directory, entry.fileName), entry)
        }
    }

    private fun isValidFile(file: File, entry: VerifiedPythonPackageEntry): Boolean =
        file.isFile &&
            !Files.isSymbolicLink(file.toPath()) &&
            file.length() == entry.size &&
            sha256(file) == entry.sha256

    private fun safePackageFile(root: File, fileName: String): File {
        check(fileName.endsWith(".apk") && SAFE_FILE_NAME.matches(fileName))
        val packages = File(root, "packages")
        val file = File(packages, fileName)
        check(file.parentFile == packages)
        return file
    }

    private fun ensureDirectory(directory: File): File {
        check(directory.exists() || directory.mkdirs())
        check(directory.isDirectory && !Files.isSymbolicLink(directory.toPath()))
        return directory
    }

    private fun cleanupPartialDirectories(root: File) {
        root.listFiles()
            ?.filter { it.name.startsWith(".") && it.name.contains(".partial-") }
            ?.forEach(::deleteTree)
    }

    private fun cleanupPreviousPacks(root: File, activePackId: String) {
        root.listFiles()
            ?.filter { it.name != activePackId && !it.name.startsWith(".") }
            ?.forEach(::deleteTree)
    }

    /** Deletes links as links and never follows them outside the app-private staging tree. */
    private fun deleteTree(file: File) {
        if (!file.exists() && !Files.isSymbolicLink(file.toPath())) return
        Files.walkFileTree(file.toPath(), object : SimpleFileVisitor<Path>() {
            override fun visitFile(path: Path, attributes: BasicFileAttributes): FileVisitResult {
                Files.deleteIfExists(path)
                return FileVisitResult.CONTINUE
            }

            override fun postVisitDirectory(path: Path, error: java.io.IOException?): FileVisitResult {
                if (error != null) throw error
                Files.deleteIfExists(path)
                return FileVisitResult.CONTINUE
            }
        })
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

    private companion object {
        const val ROOT_DIRECTORY = "python-pack"
        const val MAX_PACKAGES = 128
        val SAFE_PACK_ID = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        val SAFE_GUEST_DIRECTORY = Regex("/workspace/[A-Za-z0-9._/-]+")
        val SAFE_FILE_NAME = Regex("[A-Za-z0-9][A-Za-z0-9._+~-]*")
    }
}
