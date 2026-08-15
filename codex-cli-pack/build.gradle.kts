import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.charset.StandardCharsets
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import java.util.zip.GZIPInputStream

plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.alpine.codexclient.cli"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    // Every app variant consumes the same checksum-pinned executable. The binary is generated
    // from a verified local cache or the exact official lock URL and is never tracked in Git.
    sourceSets {
        getByName("main").assets.srcDir(layout.buildDirectory.dir("generated/distribution/assets"))
    }

    androidResources {
        noCompress += "asset"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        disable += "AndroidGradlePluginVersion"
    }
}

data class CodexCliLock(
    val version: String,
    val target: String,
    val sourceUrl: String,
    val archiveName: String,
    val archiveSize: Long,
    val archiveSha256: String,
    val archiveBinaryName: String,
    val binaryName: String,
    val binarySize: Long,
    val binarySha256: String,
)

fun readLock(file: File): CodexCliLock {
    val text = file.readText(StandardCharsets.UTF_8)
    fun requiredString(name: String): String = Regex("\\\"$name\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"")
        .find(text)
        ?.groupValues
        ?.get(1)
        ?.also { check(it.isNotBlank()) { "Missing Codex CLI lock field: $name" } }
        ?: error("Missing Codex CLI lock field: $name")
    fun requiredLong(name: String): Long = Regex("\\\"$name\\\"\\s*:\\s*([0-9]+)")
        .find(text)
        ?.groupValues
        ?.get(1)
        ?.toLongOrNull()
        ?.also { check(it > 0) { "Invalid Codex CLI size: $name" } }
        ?: error("Missing Codex CLI size: $name")
    fun safeFileName(name: String) = requiredString(name).also {
        check(Regex("[A-Za-z0-9._-]+").matches(it)) { "Invalid Codex CLI filename: $name" }
    }
    fun sha(name: String) = requiredString(name).also {
        check(Regex("[0-9a-f]{64}").matches(it)) { "Invalid Codex CLI SHA-256: $name" }
    }
    return CodexCliLock(
        version = requiredString("version"),
        target = requiredString("target"),
        sourceUrl = requiredString("source_url").also {
            check(it.startsWith("https://github.com/openai/codex/releases/download/")) { "Unexpected Codex CLI source URL" }
        },
        archiveName = safeFileName("archive_name"),
        archiveSize = requiredLong("archive_size"),
        archiveSha256 = sha("archive_sha256"),
        archiveBinaryName = safeFileName("archive_binary_name"),
        binaryName = safeFileName("binary_name"),
        binarySize = requiredLong("binary_size"),
        binarySha256 = sha("binary_sha256"),
    )
}

fun sha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().buffered().use { input ->
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

fun verifyArchive(file: File, lock: CodexCliLock) {
    check(file.isFile) { "Codex CLI archive is missing" }
    check(file.length() == lock.archiveSize) { "Codex CLI archive size mismatch" }
    check(sha256(file) == lock.archiveSha256) { "Codex CLI archive checksum mismatch" }
}

fun verifyAarch64Elf(file: File, lock: CodexCliLock) {
    check(file.isFile && file.length() == lock.binarySize) { "Codex CLI binary size mismatch" }
    check(sha256(file) == lock.binarySha256) { "Codex CLI binary checksum mismatch" }
    RandomAccessFile(file, "r").use { input ->
        val header = ByteArray(64)
        input.readFully(header)
        check(header.copyOfRange(0, 4).contentEquals(byteArrayOf(0x7f, 0x45, 0x4c, 0x46))) {
            "Codex CLI binary is not ELF"
        }
        check(header[4].toInt() == 2 && header[5].toInt() == 1) {
            "Codex CLI binary is not little-endian ELF64"
        }
        val machine = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN).getShort(18).toInt() and 0xffff
        check(machine == 183) { "Codex CLI binary is not AArch64" }
    }
}

private fun readFully(input: GZIPInputStream, buffer: ByteArray): Boolean {
    var offset = 0
    while (offset < buffer.size) {
        val count = input.read(buffer, offset, buffer.size - offset)
        if (count < 0) {
            check(offset == 0) { "Truncated Codex CLI tar archive" }
            return false
        }
        offset += count
    }
    return true
}

private fun skipFully(input: GZIPInputStream, size: Long) {
    var remaining = size
    val buffer = ByteArray(64 * 1024)
    while (remaining > 0) {
        val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
        check(count > 0) { "Truncated Codex CLI tar archive" }
        remaining -= count
    }
}

private fun tarName(header: ByteArray): String {
    val end = header.indexOf(0).let { if (it < 0) header.size else it }
    return header.copyOfRange(0, end).toString(StandardCharsets.US_ASCII)
}

private fun tarSize(header: ByteArray): Long {
    val raw = header.copyOfRange(124, 136).toString(StandardCharsets.US_ASCII).trim('\u0000', ' ')
    check(raw.isNotEmpty() && raw.all { it in '0'..'7' }) { "Invalid Codex CLI tar entry size" }
    return raw.toLong(8)
}

fun extractLockedBinary(archive: File, destination: File, lock: CodexCliLock) {
    var entries = 0
    var matches = 0
    GZIPInputStream(FileInputStream(archive).buffered()).use { input ->
        while (true) {
            val header = ByteArray(512)
            if (!readFully(input, header)) break
            if (header.all { it == 0.toByte() }) {
                val finalHeader = ByteArray(512)
                check(readFully(input, finalHeader) && finalHeader.all { it == 0.toByte() }) {
                    "Codex CLI tar archive is missing the end marker"
                }
                break
            }
            entries += 1
            val name = tarName(header)
            val size = tarSize(header)
            val regularFile = header[156].toInt() == 0 || header[156].toInt() == '0'.code
            if (name == lock.archiveBinaryName && regularFile) {
                check(matches == 0) { "Codex CLI tar archive has duplicate executable entries" }
                FileOutputStream(destination).buffered().use { output ->
                    var remaining = size
                    val buffer = ByteArray(64 * 1024)
                    while (remaining > 0) {
                        val count = input.read(buffer, 0, minOf(buffer.size.toLong(), remaining).toInt())
                        check(count > 0) { "Truncated Codex CLI binary entry" }
                        output.write(buffer, 0, count)
                        remaining -= count
                    }
                }
                matches += 1
            } else {
                skipFully(input, size)
            }
            val padding = (512 - (size % 512)) % 512
            if (padding > 0) skipFully(input, padding)
        }
    }
    check(entries == 1 && matches == 1) { "Codex CLI archive must contain exactly one locked executable" }
}

fun copyAtomically(source: File, target: File) {
    val temporary = File(target.parentFile, ".${target.name}.${System.nanoTime()}.partial")
    source.inputStream().buffered().use { input ->
        FileOutputStream(temporary).use { output ->
            input.copyTo(output)
            output.fd.sync()
        }
    }
    Files.move(
        temporary.toPath(),
        target.toPath(),
        StandardCopyOption.ATOMIC_MOVE,
        StandardCopyOption.REPLACE_EXISTING,
    )
}

val generatedAssetsDirectory = layout.buildDirectory.dir("generated/distribution/assets/codex-cli")
val lockFile = layout.projectDirectory.file("codex-cli.lock.json")
val configuredArchive = providers.environmentVariable("CODEX_CLI_ARCHIVE_PATH")

val prepareCodexCliAssets by tasks.registering {
    group = "build setup"
    description = "Prepares the checksum-pinned official Codex CLI for Android app variants."
    inputs.file(lockFile)
    inputs.property("configuredCodexCliArchive", configuredArchive.orNull ?: "")
    outputs.dir(generatedAssetsDirectory)

    doLast {
        val lock = readLock(lockFile.asFile)
        val cacheDirectory = File(project.gradle.gradleUserHomeDir, "codex-cli-cache").also { it.mkdirs() }
        val cacheArchive = File(cacheDirectory, lock.archiveName)
        val explicitArchive = configuredArchive.orNull?.takeIf { it.isNotBlank() }?.let(::File)

        val archive = when {
            explicitArchive != null -> {
                verifyArchive(explicitArchive, lock)
                if (explicitArchive.canonicalFile != cacheArchive.canonicalFile) {
                    val cacheTemporary = File(cacheDirectory, ".${lock.archiveName}.${System.nanoTime()}.partial")
                    explicitArchive.copyTo(cacheTemporary, overwrite = true)
                    verifyArchive(cacheTemporary, lock)
                    Files.move(
                        cacheTemporary.toPath(),
                        cacheArchive.toPath(),
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING,
                    )
                }
                explicitArchive
            }
            cacheArchive.isFile -> {
                verifyArchive(cacheArchive, lock)
                cacheArchive
            }
            else -> {
                val temporary = File(cacheDirectory, ".${lock.archiveName}.${System.nanoTime()}.partial")
                try {
                    val connection = (URL(lock.sourceUrl).openConnection() as HttpURLConnection).apply {
                        connectTimeout = 30_000
                        readTimeout = 120_000
                        instanceFollowRedirects = true
                    }
                    check(connection.responseCode in 200..299) { "Official Codex CLI download failed" }
                    connection.inputStream.use { input -> temporary.outputStream().use(input::copyTo) }
                    verifyArchive(temporary, lock)
                    Files.move(
                        temporary.toPath(),
                        cacheArchive.toPath(),
                        StandardCopyOption.ATOMIC_MOVE,
                    )
                } finally {
                    temporary.delete()
                }
                cacheArchive
            }
        }

        val assetDirectory = generatedAssetsDirectory.get().asFile
        assetDirectory.parentFile.deleteRecursively()
        check(assetDirectory.mkdirs()) { "Cannot create generated Codex CLI asset directory" }
        val binary = File(assetDirectory, lock.binaryName)
        extractLockedBinary(archive, binary, lock)
        verifyAarch64Elf(binary, lock)
        lockFile.asFile.copyTo(File(assetDirectory, "codex-cli.lock.json"), overwrite = true)
    }
}

// AGP reads the generated main asset source set from every variant. Asset merge and lint tasks
// therefore share one explicit producer dependency instead of variant-specific copies.
tasks.configureEach {
    if (
        (name.startsWith("merge") && name.endsWith("Assets")) ||
        name.lowercase().contains("lint")
    ) {
        dependsOn(prepareCodexCliAssets)
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}
