package dev.alpine.codexclient.gatewaypack

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

data class StagedCodexGateway(val guestPackageDirectory: String)

class CodexGatewayArtifactException : RuntimeException("CODEX_GATEWAY_ARTIFACT_INVALID")

/** Stages only the manifest-verified Python supervisor source in an app-private directory. */
class CodexGatewayArtifactProvider(private val context: Context) {
    fun stage(hostGatewayDirectory: File, guestGatewayDirectory: String): StagedCodexGateway = try {
        val entries = readManifest()
        val target = File(hostGatewayDirectory, PACKAGE_DIRECTORY)
        if (!isValidPackage(target, entries)) {
            val partial = File(hostGatewayDirectory, ".${PACKAGE_DIRECTORY}.partial-${System.nanoTime()}")
            partial.deleteRecursively()
            check(partial.mkdirs())
            try {
                for (entry in entries) {
                    val destination = safeChild(partial, packageRelativePath(entry.path))
                    check(destination.parentFile?.exists() == true || destination.parentFile?.mkdirs() == true)
                    context.assets.open("$ASSET_DIRECTORY/${entry.path}").use { input ->
                        FileOutputStream(destination).use { output ->
                            input.copyTo(output)
                            output.fd.sync()
                        }
                    }
                    check(isValidFile(destination, entry))
                }
                target.deleteRecursively()
                Files.move(partial.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE)
            } catch (error: Exception) {
                partial.deleteRecursively()
                throw error
            }
        }
        check(isValidPackage(target, entries))
        StagedCodexGateway("$guestGatewayDirectory/$PACKAGE_DIRECTORY")
    } catch (_: Exception) {
        throw CodexGatewayArtifactException()
    }

    private fun readManifest(): List<ManifestEntry> {
        val json = context.assets.open("$ASSET_DIRECTORY/$MANIFEST_NAME").bufferedReader().use { reader ->
            JSONObject(reader.readText())
        }
        val files = json.getJSONArray("files")
        check(files.length() in 1..64)
        return (0 until files.length()).map { index ->
            val item = files.getJSONObject(index)
            ManifestEntry(
                path = item.getString("path"),
                size = item.getLong("size"),
                sha256 = item.getString("sha256"),
            ).also { entry ->
                check(SAFE_PATH.matches(entry.path) && entry.path.startsWith("codex_gateway/"))
                check(entry.size in 1..(1024 * 1024))
                check(SHA_256.matches(entry.sha256))
            }
        }
    }

    private fun isValidPackage(directory: File, entries: List<ManifestEntry>): Boolean =
        directory.isDirectory && entries.all { entry ->
            isValidFile(safeChild(directory, packageRelativePath(entry.path)), entry)
        }

    private fun isValidFile(file: File, entry: ManifestEntry): Boolean =
        file.isFile && file.length() == entry.size && sha256(file) == entry.sha256

    private fun safeChild(root: File, path: String): File {
        check(SAFE_PATH.matches(path))
        check(path.split('/').none { it == "." || it == ".." || it.isBlank() })
        return File(root, path)
    }

    private fun packageRelativePath(path: String): String = path.removePrefix("$PACKAGE_DIRECTORY/").also {
        check(it.isNotBlank())
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

    private data class ManifestEntry(val path: String, val size: Long, val sha256: String)

    private companion object {
        const val ASSET_DIRECTORY = "codex-gateway"
        const val MANIFEST_NAME = "gateway-manifest.json"
        const val PACKAGE_DIRECTORY = "codex_gateway"
        val SAFE_PATH = Regex("[A-Za-z0-9._/-]+")
        val SHA_256 = Regex("[0-9a-f]{64}")
    }
}
