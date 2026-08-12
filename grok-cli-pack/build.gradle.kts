import java.io.File
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

plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.alpine.codexclient.grokcli"
    compileSdk = 36

    defaultConfig {
        minSdk = 26
        consumerProguardFiles("consumer-rules.pro")
    }

    // The locked executable exists only in generated debug assets. No release source set,
    // publication, or signing configuration is created by this module.
    sourceSets {
        getByName("debug").assets.srcDir(layout.buildDirectory.dir("generated/debug/assets"))
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

data class GrokCliLock(
    val version: String,
    val target: String,
    val sourceUrl: String,
    val artifactName: String,
    val binaryName: String,
    val binarySize: Long,
    val binarySha256: String,
    val versionOutput: String,
    val sourceRepository: String,
    val sourceRepositoryCommit: String,
    val sourceRevision: String,
    val license: String,
)

fun readGrokLock(file: File): GrokCliLock {
    val text = file.readText(StandardCharsets.UTF_8)
    fun requiredString(name: String): String = Regex("\\\"$name\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"")
        .find(text)
        ?.groupValues
        ?.get(1)
        ?.also { check(it.isNotBlank()) { "Missing Grok CLI lock field: $name" } }
        ?: error("Missing Grok CLI lock field: $name")
    fun requiredLong(name: String): Long = Regex("\\\"$name\\\"\\s*:\\s*([0-9]+)")
        .find(text)
        ?.groupValues
        ?.get(1)
        ?.toLongOrNull()
        ?.also { check(it > 0) { "Invalid Grok CLI size: $name" } }
        ?: error("Missing Grok CLI size: $name")
    fun safeFileName(name: String) = requiredString(name).also {
        check(Regex("[A-Za-z0-9._-]+").matches(it)) { "Invalid Grok CLI filename: $name" }
    }
    fun sha(name: String) = requiredString(name).also {
        check(Regex("[0-9a-f]{64}").matches(it)) { "Invalid Grok CLI SHA-256: $name" }
    }
    fun revision(name: String) = requiredString(name).also {
        check(Regex("[0-9a-f]{40}").matches(it)) { "Invalid Grok source revision: $name" }
    }
    return GrokCliLock(
        version = requiredString("version").also {
            check(Regex("[0-9]+\\.[0-9]+\\.[0-9]+").matches(it)) { "Invalid Grok CLI version" }
        },
        target = requiredString("target").also {
            check(it == "linux-aarch64-static") { "Unexpected Grok CLI target" }
        },
        sourceUrl = requiredString("source_url"),
        artifactName = safeFileName("artifact_name"),
        binaryName = safeFileName("binary_name").also {
            check(it == "grok") { "Unexpected Grok CLI binary name" }
        },
        binarySize = requiredLong("binary_size"),
        binarySha256 = sha("binary_sha256"),
        versionOutput = requiredString("version_output"),
        sourceRepository = requiredString("source_repository"),
        sourceRepositoryCommit = revision("source_repository_commit"),
        sourceRevision = revision("source_revision"),
        license = requiredString("license").also {
            check(it == "Apache-2.0") { "Unexpected Grok CLI license" }
        },
    ).also { lock ->
        check(lock.artifactName == "grok-${lock.version}-linux-aarch64") { "Grok artifact filename mismatch" }
        check(lock.sourceUrl == "https://x.ai/cli/${lock.artifactName}") { "Unexpected Grok CLI source URL" }
        check(lock.sourceRepository == "https://github.com/xai-org/grok-build") { "Unexpected Grok source repository" }
        check(Regex("grok ${Regex.escape(lock.version)} \\([0-9a-f]{10}\\)").matches(lock.versionOutput)) {
            "Grok CLI version output is not locked"
        }
    }
}

fun grokSha256(file: File): String {
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

fun verifyGrokElf(file: File, lock: GrokCliLock) {
    check(file.isFile && file.length() == lock.binarySize) { "Grok CLI binary size mismatch" }
    check(grokSha256(file) == lock.binarySha256) { "Grok CLI binary checksum mismatch" }
    RandomAccessFile(file, "r").use { input ->
        val header = ByteArray(64)
        input.readFully(header)
        check(header.copyOfRange(0, 4).contentEquals(byteArrayOf(0x7f, 0x45, 0x4c, 0x46))) {
            "Grok CLI binary is not ELF"
        }
        check(header[4].toInt() == 2 && header[5].toInt() == 1) {
            "Grok CLI binary is not little-endian ELF64"
        }
        val elf = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)
        val machine = elf.getShort(18).toInt() and 0xffff
        check(machine == 183) { "Grok CLI binary is not AArch64" }
        val programHeaderOffset = elf.getLong(32)
        val programHeaderSize = elf.getShort(54).toInt() and 0xffff
        val programHeaderCount = elf.getShort(56).toInt() and 0xffff
        check(programHeaderOffset >= 64 && programHeaderSize >= 56 && programHeaderCount in 1..256) {
            "Grok CLI ELF program headers are invalid"
        }
        check(programHeaderOffset + programHeaderSize.toLong() * programHeaderCount <= file.length()) {
            "Grok CLI ELF program headers are truncated"
        }
        repeat(programHeaderCount) { index ->
            input.seek(programHeaderOffset + programHeaderSize.toLong() * index)
            val type = Integer.reverseBytes(input.readInt())
            check(type != 3) { "Grok CLI binary contains a dynamic interpreter" }
        }
    }
}

fun copyGrokAtomically(source: File, target: File, expectedSize: Long) {
    val temporary = File(target.parentFile, ".${target.name}.${System.nanoTime()}.partial")
    try {
        source.inputStream().buffered().use { input ->
            FileOutputStream(temporary).use { output ->
                val buffer = ByteArray(64 * 1024)
                var copied = 0L
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    copied += count
                    check(copied <= expectedSize) { "Grok CLI artifact exceeds locked size" }
                    output.write(buffer, 0, count)
                }
                output.fd.sync()
            }
        }
        check(temporary.length() == expectedSize) { "Grok CLI artifact size mismatch" }
        Files.move(
            temporary.toPath(),
            target.toPath(),
            StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING,
        )
    } finally {
        temporary.delete()
    }
}

val generatedAssetsDirectory = layout.buildDirectory.dir("generated/debug/assets/grok-cli")
val lockFile = layout.projectDirectory.file("grok-cli.lock.json")
val configuredBinary = providers.environmentVariable("GROK_CLI_BINARY_PATH")

val prepareGrokCliDebug by tasks.registering {
    group = "build setup"
    description = "Prepares the checksum-pinned official Grok CLI as a debug-only Android asset."
    inputs.file(lockFile)
    inputs.property("configuredGrokCliBinary", configuredBinary.orNull ?: "")
    outputs.dir(generatedAssetsDirectory)

    doLast {
        val lock = readGrokLock(lockFile.asFile)
        val cacheDirectory = File(project.gradle.gradleUserHomeDir, "grok-cli-cache").also { it.mkdirs() }
        val cacheBinary = File(cacheDirectory, lock.artifactName)
        val explicitBinary = configuredBinary.orNull?.takeIf { it.isNotBlank() }?.let(::File)

        val sourceBinary = when {
            explicitBinary != null -> {
                verifyGrokElf(explicitBinary, lock)
                if (explicitBinary.canonicalFile != cacheBinary.canonicalFile) {
                    copyGrokAtomically(explicitBinary, cacheBinary, lock.binarySize)
                    verifyGrokElf(cacheBinary, lock)
                }
                explicitBinary
            }
            cacheBinary.isFile -> {
                verifyGrokElf(cacheBinary, lock)
                cacheBinary
            }
            else -> {
                val connection = (URL(lock.sourceUrl).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 30_000
                    readTimeout = 120_000
                    instanceFollowRedirects = false
                }
                try {
                    check(connection.responseCode in 200..299) { "Official Grok CLI download failed" }
                    val contentLength = connection.contentLengthLong
                    check(contentLength == -1L || contentLength == lock.binarySize) {
                        "Official Grok CLI content length mismatch"
                    }
                    val temporary = File(cacheDirectory, ".${lock.artifactName}.${System.nanoTime()}.download")
                    try {
                        connection.inputStream.use { input ->
                            FileOutputStream(temporary).use { output ->
                                val buffer = ByteArray(64 * 1024)
                                var copied = 0L
                                while (true) {
                                    val count = input.read(buffer)
                                    if (count < 0) break
                                    copied += count
                                    check(copied <= lock.binarySize) { "Grok CLI download exceeds locked size" }
                                    output.write(buffer, 0, count)
                                }
                                output.fd.sync()
                            }
                        }
                        verifyGrokElf(temporary, lock)
                        Files.move(temporary.toPath(), cacheBinary.toPath(), StandardCopyOption.ATOMIC_MOVE)
                    } finally {
                        temporary.delete()
                    }
                } finally {
                    connection.disconnect()
                }
                cacheBinary
            }
        }

        val assetDirectory = generatedAssetsDirectory.get().asFile
        assetDirectory.parentFile.deleteRecursively()
        check(assetDirectory.mkdirs()) { "Cannot create generated Grok CLI asset directory" }
        val binary = File(assetDirectory, lock.binaryName)
        copyGrokAtomically(sourceBinary, binary, lock.binarySize)
        verifyGrokElf(binary, lock)
        lockFile.asFile.copyTo(File(assetDirectory, "grok-cli.lock.json"), overwrite = true)
    }
}

tasks.configureEach {
    if (name == "mergeDebugAssets" || (name.contains("Debug") && name.lowercase().contains("lint"))) {
        dependsOn(prepareGrokCliDebug)
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        jvmDefault.set(org.jetbrains.kotlin.gradle.dsl.JvmDefaultMode.DISABLE)
    }
}
