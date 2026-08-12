package dev.alpine.codexclient

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.AgentStorageSchema
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONArray
import org.json.JSONObject

internal data class StoredChatMessage(val role: ChatRole, val text: String)

internal data class StoredConversation(
    val conversationId: String,
    val selectedModelId: String?,
    val messages: List<StoredChatMessage>,
    val agentId: AgentId = AgentId.CODEX,
)

internal data class StoredConversationState(
    val activeConversationId: String?,
    val conversations: List<StoredConversation>,
)

/**
 * Stores only locally rendered messages and the opaque conversation selector. OAuth data, login
 * challenges, account fields, and gateway thread IDs deliberately never enter this store.
 */
internal class EncryptedConversationStore(context: Context) {
    private val file = File(context.filesDir, FILE_NAME)
    @Volatile private var lastWriteFailureCode: String? = null

    fun load(): StoredConversationState? = runCatching {
        if (!file.isFile || file.length() !in (IV_BYTES + 1L)..MAX_FILE_BYTES) return null
        val bytes = FileInputStream(file).use { input -> input.readBytes() }
        if (bytes.size.toLong() !in (IV_BYTES + 1L)..MAX_FILE_BYTES) return null
        val iv = bytes.copyOfRange(0, IV_BYTES)
        val plaintext = cipher(Cipher.DECRYPT_MODE, iv).doFinal(bytes.copyOfRange(IV_BYTES, bytes.size))
        parse(JSONObject(plaintext.toString(Charsets.UTF_8)))
    }.getOrNull()

    fun save(value: StoredConversationState): Boolean {
        val outcome = runCatching {
            val plaintext = encode(value).toString().toByteArray(Charsets.UTF_8)
            require(plaintext.size <= MAX_PLAINTEXT_BYTES)
            val encryptor = cipher(Cipher.ENCRYPT_MODE)
            val encrypted = encryptor.doFinal(plaintext)
            val iv = encryptor.iv
            require(iv.size == IV_BYTES)
            val temporary = File(file.parentFile, "$FILE_NAME.tmp")
            FileOutputStream(temporary).use { output ->
                output.write(iv)
                output.write(encrypted)
                output.fd.sync()
            }
            if (!temporary.renameTo(file)) {
                temporary.delete()
                error("conversation store replace failed")
            }
        }
        lastWriteFailureCode = outcome.exceptionOrNull()?.javaClass?.simpleName
        return outcome.isSuccess
    }

    /** Test-only error category; it contains no plaintext, account, OAuth, or key material. */
    internal fun lastWriteFailureCode(): String? = lastWriteFailureCode

    fun clear() {
        runCatching { if (file.exists()) file.delete() }
    }

    private fun cipher(mode: Int, iv: ByteArray? = null): Cipher = Cipher.getInstance(TRANSFORMATION).apply {
        if (mode == Cipher.ENCRYPT_MODE) {
            init(mode, getOrCreateKey())
        } else {
            require(iv?.size == IV_BYTES)
            init(mode, getOrCreateKey(), GCMParameterSpec(TAG_BITS, iv))
        }
    }

    private fun getOrCreateKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
                .build(),
        )
        return generator.generateKey()
    }

    private fun encode(value: StoredConversationState): JSONObject {
        val conversations = JSONArray()
        value.conversations.take(MAX_CONVERSATIONS).forEach { conversation ->
            conversations.put(encodeConversation(conversation))
        }
        return JSONObject()
            .put("schema_version", AgentStorageSchema.CURRENT_VERSION)
            .put("active_conversation_id", value.activeConversationId)
            .put("conversations", conversations)
    }

    private fun encodeConversation(value: StoredConversation): JSONObject {
        require(value.conversationId.isNotBlank() && value.conversationId.length <= MAX_CONVERSATION_ID_LENGTH)
        val messages = JSONArray()
        value.messages.take(MAX_MESSAGES).forEach { message ->
            require(message.text.toByteArray(Charsets.UTF_8).size <= MAX_MESSAGE_BYTES)
            messages.put(JSONObject().put("role", message.role.name).put("text", message.text))
        }
        return JSONObject()
            .put("agent_id", value.agentId.wireValue)
            .put("conversation_id", value.conversationId)
            .put("selected_model_id", value.selectedModelId)
            .put("messages", messages)
    }

    private fun parse(value: JSONObject): StoredConversationState? {
        val schemaVersion = AgentStorageSchema.parseVersion(
            value.optInt("schema_version").takeIf { value.has("schema_version") },
        ) ?: return null
        val rawConversations = value.optJSONArray("conversations") ?: return null
        if (rawConversations.length() > MAX_CONVERSATIONS) return null
        val conversations = mutableListOf<StoredConversation>()
        for (index in 0 until rawConversations.length()) {
            val conversation = parseConversation(
                rawConversations.optJSONObject(index) ?: return null,
                schemaVersion,
            ) ?: return null
            if (conversations.none { it.conversationId == conversation.conversationId }) {
                conversations += conversation
            }
        }
        val activeConversationId = value.optString("active_conversation_id", "")
            .takeIf { activeId -> conversations.any { it.conversationId == activeId } }
        return StoredConversationState(activeConversationId, conversations)
    }

    private fun parseConversation(value: JSONObject, schemaVersion: Int): StoredConversation? {
        val agentId = AgentStorageSchema.resolveAgentId(
            schemaVersion,
            value.optString("agent_id", "").takeIf { value.has("agent_id") },
        ) ?: return null
        val conversationId = value.optString("conversation_id", "")
            .takeIf { it.isNotEmpty() && it.length <= MAX_CONVERSATION_ID_LENGTH }
            ?: return null
        val selectedModelId = value.optString("selected_model_id", "").takeIf { it.isNotEmpty() && it.length <= 256 }
        val rawMessages = value.optJSONArray("messages") ?: return null
        if (rawMessages.length() > MAX_MESSAGES) return null
        val messages = buildList {
            for (index in 0 until rawMessages.length()) {
                val item = rawMessages.optJSONObject(index) ?: return null
                val role = runCatching { ChatRole.valueOf(item.getString("role")) }.getOrNull() ?: return null
                val text = item.optString("text", "")
                if (text.toByteArray(Charsets.UTF_8).size > MAX_MESSAGE_BYTES) return null
                add(StoredChatMessage(role, text))
            }
        }
        return StoredConversation(conversationId, selectedModelId, messages, agentId)
    }

    private companion object {
        const val FILE_NAME = "codex-chat-state.v1"
        const val KEY_ALIAS = "alpine_codex_chat_state_v1"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val TAG_BITS = 128
        const val IV_BYTES = 12
        const val MAX_FILE_BYTES = 256 * 1024L
        const val MAX_PLAINTEXT_BYTES = 192 * 1024
        const val MAX_CONVERSATIONS = 4
        const val MAX_CONVERSATION_ID_LENGTH = 128
        const val MAX_MESSAGES = 8
        const val MAX_MESSAGE_BYTES = 4 * 1024
    }
}
