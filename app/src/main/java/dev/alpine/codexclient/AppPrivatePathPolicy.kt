package dev.alpine.codexclient

import android.system.Os
import java.io.File
import java.nio.file.Files

/** Canonical, no-symlink, owner-only directory gate for backend state. */
internal object AppPrivatePathPolicy {
    fun ensureDirectory(allowedRoot: File, requested: File): File {
        val absoluteRoot = allowedRoot.absoluteFile.toPath().normalize()
        check(absoluteRoot.toFile().isDirectory)
        val root = absoluteRoot.toFile().canonicalFile
        check(root.isDirectory)
        val absoluteRequested = requested.absoluteFile.toPath().normalize()
        val canonicalRoot = root.toPath()
        val relative = when {
            absoluteRequested.startsWith(absoluteRoot) -> absoluteRoot.relativize(absoluteRequested)
            absoluteRequested.startsWith(canonicalRoot) -> canonicalRoot.relativize(absoluteRequested)
            else -> error("requested path is outside the app-private root")
        }
        check(relative.nameCount > 0)
        check(relative.none { it.toString() == "." || it.toString() == ".." })
        val normalized = root.toPath().resolve(relative).normalize().toFile()
        check(normalized.toPath().startsWith(root.toPath()))
        rejectExistingSymlinkComponents(root, normalized)
        check(normalized.exists() || normalized.mkdirs())
        rejectExistingSymlinkComponents(root, normalized)
        check(normalized.isDirectory)
        Os.chmod(normalized.absolutePath, MODE_OWNER_RWX)
        check(Os.stat(normalized.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RWX)
        check(normalized.canonicalFile.toPath().startsWith(root.toPath()))
        return normalized.canonicalFile
    }

    private fun rejectExistingSymlinkComponents(root: File, requested: File) {
        var current: File? = requested
        while (current != null && current != root) {
            if (current.exists()) check(!Files.isSymbolicLink(current.toPath()))
            current = current.parentFile
        }
        check(current == root)
        check(!Files.isSymbolicLink(root.toPath()))
    }

    private const val MODE_OWNER_RWX = 448
    private const val MODE_MASK = 511
}
