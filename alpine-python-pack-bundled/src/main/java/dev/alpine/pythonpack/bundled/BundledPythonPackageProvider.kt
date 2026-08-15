package dev.alpine.pythonpack.bundled

import android.content.Context
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest

data class StagedPythonPackagePack(
    val packId: String,
    val alpineVersion: String,
    val guestPackagePaths: List<String>,
)

enum class PythonPackagePackFailure {
    UNAVAILABLE,
    INVALID,
}

class PythonPackagePackException(val failure: PythonPackagePackFailure) :
    RuntimeException("PYTHON_PACKAGE_PACK_${failure.name}")

/**
 * Copies only hash-locked Alpine package files from APK assets into the app-private workspace.
 * It never opens a network connection, resolves a repository, or starts `apk` itself.
 */
class BundledPythonPackageProvider(private val context: Context) {
    private val fileStager = PythonPackageFileStager { fileName ->
        context.assets.open("$ASSET_DIRECTORY/packages/$fileName")
    }

    fun stage(
        hostStagingDirectory: File,
        guestStagingDirectory: String,
    ): StagedPythonPackagePack = try {
        val status = readStatus()
        if (!status.available) {
            throw PythonPackagePackException(PythonPackagePackFailure.UNAVAILABLE)
        }
        val lockBytes = readAssetBytes("$ASSET_DIRECTORY/$LOCK_NAME", MAX_LOCK_BYTES)
        check(sha256(lockBytes) == status.lockSha256)
        val lock = parseLock(lockBytes)
        check(status.packId == lock.packId)
        check(status.packageCount == lock.packages.size)
        verifyAssetCoverage(lock)

        StagedPythonPackagePack(
            packId = lock.packId,
            alpineVersion = lock.alpineVersion,
            guestPackagePaths = fileStager.stage(
                hostStagingDirectory = hostStagingDirectory,
                guestStagingDirectory = guestStagingDirectory,
                packId = lock.packId,
                entries = lock.packages.map { entry ->
                    VerifiedPythonPackageEntry(entry.fileName, entry.size, entry.sha256)
                },
            ),
        )
    } catch (error: PythonPackagePackException) {
        throw error
    } catch (_: Exception) {
        throw PythonPackagePackException(PythonPackagePackFailure.INVALID)
    }

    private fun readStatus(): PackStatus {
        val value = JSONObject(
            readAssetBytes("$ASSET_DIRECTORY/$STATUS_NAME", MAX_STATUS_BYTES).toString(Charsets.UTF_8),
        )
        check(value.getInt("schema") == 1)
        val available = value.getBoolean("available")
        if (!available) return PackStatus(false, null, null, null)
        check(value.getBoolean("production"))
        return PackStatus(
            available = true,
            packId = value.getString("pack_id").also { check(SAFE_PACK_ID.matches(it)) },
            lockSha256 = value.getString("lock_sha256").also { check(SHA_256.matches(it)) },
            packageCount = value.getInt("package_count").also { check(it in 1..MAX_PACKAGES) },
        )
    }

    private fun parseLock(bytes: ByteArray): PackLock {
        val value = JSONObject(bytes.toString(Charsets.UTF_8))
        check(value.getInt("schema") == 1)
        check(value.getBoolean("production"))
        check(value.getString("architecture") == "aarch64")
        val packId = value.getString("pack_id").also { check(SAFE_PACK_ID.matches(it)) }
        val alpineVersion = value.getString("alpine_version").also { check(SAFE_VERSION.matches(it)) }
        val array = value.getJSONArray("packages")
        check(array.length() in 1..MAX_PACKAGES)
        val entries = (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            val relativePath = item.getString("file")
            val fileName = relativePath.removePrefix("packages/")
            check(relativePath == "packages/$fileName")
            check(fileName.endsWith(".apk") && SAFE_FILE_NAME.matches(fileName))
            PackageEntry(
                relativePath = relativePath,
                fileName = fileName,
                name = item.getString("name").also { check(SAFE_COMPONENT.matches(it)) },
                version = item.getString("version").also { check(SAFE_VERSION.matches(it)) },
                size = item.getLong("size").also { check(it in 1..MAX_PACKAGE_BYTES) },
                sha256 = item.getString("sha256").also { check(SHA_256.matches(it)) },
            )
        }
        check(entries.map { it.fileName }.distinct().size == entries.size)
        check(entries.map { it.name }.distinct().size == entries.size)
        check(entries.any { it.name == "python3" })
        check(entries.sumOf { it.size } <= MAX_TOTAL_BYTES)
        return PackLock(packId, alpineVersion, entries)
    }

    private fun verifyAssetCoverage(lock: PackLock) {
        val actualRoot = context.assets.list(ASSET_DIRECTORY)?.toSet().orEmpty()
        check(actualRoot == setOf(STATUS_NAME, LOCK_NAME, SBOM_NAME, "packages"))
        val actualPackages = context.assets.list("$ASSET_DIRECTORY/packages")?.toSet().orEmpty()
        check(actualPackages == lock.packages.map { it.fileName }.toSet())
    }

    private fun readAssetBytes(path: String, limit: Int): ByteArray =
        context.assets.open(path).use { input ->
            val output = ByteArrayOutputStream(minOf(limit, 16 * 1024))
            val buffer = ByteArray(8 * 1024)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                total += count
                check(total <= limit)
                output.write(buffer, 0, count)
            }
            output.toByteArray()
        }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).toHex()

    private fun ByteArray.toHex(): String = joinToString("") { byte ->
        (byte.toInt() and 0xff).toString(16).padStart(2, '0')
    }

    private data class PackStatus(
        val available: Boolean,
        val packId: String?,
        val lockSha256: String?,
        val packageCount: Int?,
    )

    private data class PackLock(
        val packId: String,
        val alpineVersion: String,
        val packages: List<PackageEntry>,
    )

    private data class PackageEntry(
        val relativePath: String,
        val fileName: String,
        val name: String,
        val version: String,
        val size: Long,
        val sha256: String,
    )

    private companion object {
        const val ASSET_DIRECTORY = "alpine-python-pack"
        const val STATUS_NAME = "pack-status.json"
        const val LOCK_NAME = "python-pack.lock.json"
        const val SBOM_NAME = "sbom.spdx.json"
        const val MAX_PACKAGES = 128
        const val MAX_STATUS_BYTES = 16 * 1024
        const val MAX_LOCK_BYTES = 512 * 1024
        const val MAX_PACKAGE_BYTES = 512L * 1024 * 1024
        const val MAX_TOTAL_BYTES = 1024L * 1024 * 1024
        val SAFE_PACK_ID = Regex("[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        val SAFE_VERSION = Regex("[A-Za-z0-9][A-Za-z0-9._+~-]{0,127}")
        val SAFE_COMPONENT = Regex("[A-Za-z0-9][A-Za-z0-9._+~-]*")
        val SAFE_FILE_NAME = Regex("[A-Za-z0-9][A-Za-z0-9._+~-]*")
        val SHA_256 = Regex("[0-9a-f]{64}")
    }
}
