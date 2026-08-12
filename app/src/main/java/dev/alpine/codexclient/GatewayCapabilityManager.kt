package dev.alpine.codexclient

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.system.Os
import dev.alpine.codexclient.bridge.GatewaySecretProvider
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.KeyStore
import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** Keystore-wrapped Runtime capability with a single owner-only launch handoff file. */
internal class GatewayCapabilityManager(
    context: Context,
    private val capabilityDirectory: File,
) : GatewaySecretProvider, AutoCloseable {
    private val applicationId = context.packageName
    private val wrappedDirectory = AppPrivatePathPolicy.ensureDirectory(
        context.filesDir,
        File(context.filesDir, "gateway-security"),
    )
    private val wrappedFile = File(wrappedDirectory, WRAPPED_FILE)
    private val capabilityFile = File(capabilityDirectory, CAPABILITY_FILE)
    private var activeSecret: ByteArray? = loadWrapped()

    @Synchronized
    override fun currentSecret(): ByteArray = activeSecret?.copyOf()
        ?: throw IllegalStateException("gateway_session_restart_required")

    @Synchronized
    fun rotateAndStage(): String {
        clearMemory()
        deleteOwnedCapabilityFile()
        val secret = ByteArray(SECRET_BYTES).also(SecureRandom()::nextBytes)
        return try {
            writeWrapped(secret)
            writeCapability(secret)
            activeSecret = secret
            CodexRuntimePaths.GUEST_CAPABILITY_FILE
        } catch (error: Exception) {
            secret.fill(0)
            wrappedFile.delete()
            deleteOwnedCapabilityFile()
            throw error
        }
    }

    @Synchronized
    fun cleanupTransientStart() {
        deleteOwnedCapabilityFile()
        wrappedFile.delete()
        clearMemory()
    }

    @Synchronized
    fun clearAfterRuntimeStop() = cleanupTransientStart()

    @Synchronized
    override fun close() {
        clearMemory()
    }

    private fun writeCapability(secret: ByteArray) {
        check(capabilityDirectory.isDirectory && !Files.isSymbolicLink(capabilityDirectory.toPath()))
        check(Os.stat(capabilityDirectory.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RWX)
        check(!capabilityFile.exists() && !Files.isSymbolicLink(capabilityFile.toPath()))
        FileOutputStream(capabilityFile).use { output ->
            output.write(secret)
            output.fd.sync()
        }
        Os.chmod(capabilityFile.absolutePath, MODE_OWNER_RW)
        check(capabilityFile.length() == SECRET_BYTES.toLong())
        check(Os.stat(capabilityFile.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RW)
    }

    private fun writeWrapped(secret: ByteArray) {
        val encryptor = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.ENCRYPT_MODE, getOrCreateKey())
            updateAAD(aad())
        }
        val encrypted = encryptor.doFinal(secret)
        val iv = encryptor.iv
        check(iv.size == IV_BYTES)
        val temporary = File(wrappedDirectory, ".$WRAPPED_FILE.${System.nanoTime()}.partial")
        try {
            FileOutputStream(temporary).use { output ->
                output.write(byteArrayOf(FORMAT_VERSION))
                output.write(iv)
                output.write(encrypted)
                output.fd.sync()
            }
            Os.chmod(temporary.absolutePath, MODE_OWNER_RW)
            Files.move(
                temporary.toPath(),
                wrappedFile.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
                StandardCopyOption.REPLACE_EXISTING,
            )
            Os.chmod(wrappedFile.absolutePath, MODE_OWNER_RW)
        } finally {
            temporary.delete()
        }
    }

    private fun loadWrapped(): ByteArray? = runCatching {
        if (!wrappedFile.exists()) return null
        check(wrappedFile.isFile && !Files.isSymbolicLink(wrappedFile.toPath()))
        check(Os.stat(wrappedFile.absolutePath).st_mode and MODE_MASK == MODE_OWNER_RW)
        val bytes = FileInputStream(wrappedFile).use { it.readBytes() }
        check(bytes.size in MIN_WRAPPED_BYTES..MAX_WRAPPED_BYTES && bytes[0] == FORMAT_VERSION)
        val iv = bytes.copyOfRange(1, 1 + IV_BYTES)
        val decryptor = Cipher.getInstance(TRANSFORMATION).apply {
            init(Cipher.DECRYPT_MODE, getOrCreateKey(), GCMParameterSpec(TAG_BITS, iv))
            updateAAD(aad())
        }
        decryptor.doFinal(bytes.copyOfRange(1 + IV_BYTES, bytes.size)).also {
            check(it.size == SECRET_BYTES)
        }
    }.getOrNull()

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        if (Build.VERSION.SDK_INT >= 28) {
            try {
                return generateKey(strongBox = true)
            } catch (_: Exception) {
                runCatching { keyStore.deleteEntry(KEY_ALIAS) }
            }
        }
        return generateKey(strongBox = false)
    }

    private fun generateKey(strongBox: Boolean): SecretKey {
        val builder = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
        )
            .setKeySize(256)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
        if (Build.VERSION.SDK_INT >= 28) builder.setIsStrongBoxBacked(strongBox)
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE).run {
            init(builder.build())
            generateKey()
        }
    }

    private fun deleteOwnedCapabilityFile() {
        if (!capabilityFile.exists() && !Files.isSymbolicLink(capabilityFile.toPath())) return
        check(capabilityFile.isFile && !Files.isSymbolicLink(capabilityFile.toPath()))
        check(capabilityFile.canonicalFile.parentFile == capabilityDirectory.canonicalFile)
        check(capabilityFile.delete())
    }

    private fun clearMemory() {
        activeSecret?.fill(0)
        activeSecret = null
    }

    private fun aad(): ByteArray = "$applicationId|gateway-session|1".toByteArray(Charsets.UTF_8)

    private companion object {
        const val WRAPPED_FILE = "gateway-session.v1"
        const val CAPABILITY_FILE = "gateway-capability.v1"
        const val KEY_ALIAS = "alpine_gateway_session_v1"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val SECRET_BYTES = 32
        const val IV_BYTES = 12
        const val TAG_BITS = 128
        const val FORMAT_VERSION: Byte = 1
        const val MIN_WRAPPED_BYTES = 1 + IV_BYTES + SECRET_BYTES + 16
        const val MAX_WRAPPED_BYTES = 128
        const val MODE_OWNER_RWX = 448
        const val MODE_OWNER_RW = 384
        const val MODE_MASK = 511
    }
}
