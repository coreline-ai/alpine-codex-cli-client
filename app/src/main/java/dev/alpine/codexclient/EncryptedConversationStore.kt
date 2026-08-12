package dev.alpine.codexclient

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.system.Os
import dev.alpine.codexclient.bridge.AgentId
import dev.alpine.codexclient.bridge.AgentStorageSchema
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
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
    val selectedAgentId: AgentId = AgentId.CODEX,
    val activeConversationIds: Map<AgentId, String> = activeConversationId
        ?.let { mapOf(AgentId.CODEX to it) }
        .orEmpty(),
    val selectedModelIds: Map<AgentId, String> = emptyMap(),
)

/**
 * Stores only locally rendered messages and the opaque conversation selector. OAuth data, login
 * challenges, account fields, and gateway thread IDs deliberately never enter this store.
 */
internal class EncryptedConversationStore(context: Context) {
    private val applicationId = context.packageName
    private val file = File(context.filesDir, FILE_NAME_V2)
    private val legacyFile = File(context.filesDir, FILE_NAME_V1)
    @Volatile private var lastWriteFailureCode: String? = null

    fun load(): StoredConversationState? {
        loadV2()?.let { return it }
        val migrated = loadLegacy() ?: return null
        if (save(migrated) && loadV2() != null) return migrated
        return migrated
    }

    private fun loadV2(): StoredConversationState? = runCatching {
        if (!file.isFile || Files.isSymbolicLink(file.toPath()) || file.length() !in MIN_V2_BYTES..MAX_FILE_BYTES) return null
        val bytes = FileInputStream(file).use { input -> input.readBytes() }
        if (bytes.size.toLong() !in MIN_V2_BYTES..MAX_FILE_BYTES) return null
        if (!bytes.copyOfRange(0, MAGIC.size).contentEquals(MAGIC) || bytes[MAGIC.size] != FORMAT_VERSION) return null
        val ivOffset = MAGIC.size + 1
        val iv = bytes.copyOfRange(ivOffset, ivOffset + IV_BYTES)
        val decryptor = cipher(Cipher.DECRYPT_MODE, iv, KEY_ALIAS_V2).apply { updateAAD(aad()) }
        val plaintext = decryptor.doFinal(bytes.copyOfRange(ivOffset + IV_BYTES, bytes.size))
        parse(JSONObject(plaintext.toString(Charsets.UTF_8)))
    }.getOrNull()

    private fun loadLegacy(): StoredConversationState? = runCatching {
        if (!legacyFile.isFile || Files.isSymbolicLink(legacyFile.toPath()) || legacyFile.length() !in (IV_BYTES + 1L)..MAX_FILE_BYTES) return null
        val bytes = FileInputStream(legacyFile).use { it.readBytes() }
        if (bytes.size.toLong() !in (IV_BYTES + 1L)..MAX_FILE_BYTES) return null
        val iv = bytes.copyOfRange(0, IV_BYTES)
        val plaintext = cipher(Cipher.DECRYPT_MODE, iv, KEY_ALIAS_V1)
            .doFinal(bytes.copyOfRange(IV_BYTES, bytes.size))
        parse(JSONObject(plaintext.toString(Charsets.UTF_8)))
    }.getOrNull()

    fun save(value: StoredConversationState): Boolean {
        val outcome = runCatching {
            val plaintext = encode(value).toString().toByteArray(Charsets.UTF_8)
            require(plaintext.size <= MAX_PLAINTEXT_BYTES)
            val encryptor = cipher(Cipher.ENCRYPT_MODE, alias = KEY_ALIAS_V2).apply { updateAAD(aad()) }
            val encrypted = encryptor.doFinal(plaintext)
            val iv = encryptor.iv
            require(iv.size == IV_BYTES)
            val temporary = File(file.parentFile, ".$FILE_NAME_V2.${System.nanoTime()}.partial")
            try {
                FileOutputStream(temporary).use { output ->
                    output.write(MAGIC)
                    output.write(byteArrayOf(FORMAT_VERSION))
                    output.write(iv)
                    output.write(encrypted)
                    output.fd.sync()
                }
                Os.chmod(temporary.absolutePath, MODE_OWNER_RW)
                Files.move(
                    temporary.toPath(),
                    file.toPath(),
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING,
                )
                Os.chmod(file.absolutePath, MODE_OWNER_RW)
            } finally {
                temporary.delete()
            }
        }
        lastWriteFailureCode = outcome.exceptionOrNull()?.javaClass?.simpleName
        return outcome.isSuccess
    }

    /** Test-only error category; it contains no plaintext, account, OAuth, or key material. */
    internal fun lastWriteFailureCode(): String? = lastWriteFailureCode

    fun clear() {
        runCatching { if (file.exists()) file.delete() }
        runCatching { if (legacyFile.exists()) legacyFile.delete() }
    }

    private fun cipher(mode: Int, iv: ByteArray? = null, alias: String): Cipher = Cipher.getInstance(TRANSFORMATION).apply {
        if (mode == Cipher.ENCRYPT_MODE) {
            init(mode, getOrCreateKey(alias))
        } else {
            require(iv?.size == IV_BYTES)
            init(mode, getOrCreateKey(alias), GCMParameterSpec(TAG_BITS, iv))
        }
    }

    private fun getOrCreateKey(alias: String): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(alias, null) as? SecretKey)?.let { return it }
        if (alias == KEY_ALIAS_V2 && Build.VERSION.SDK_INT >= 28) {
            try {
                return generateKey(alias, true)
            } catch (_: Exception) {
                runCatching { keyStore.deleteEntry(alias) }
            }
        }
        return generateKey(alias, false)
    }

    private fun generateKey(alias: String, strongBox: Boolean): SecretKey {
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        val builder = KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setKeySize(256)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true)
        if (Build.VERSION.SDK_INT >= 28) builder.setIsStrongBoxBacked(strongBox)
        generator.init(builder.build())
        return generator.generateKey()
    }

    private fun aad(): ByteArray = "$applicationId|conversation-state|2".toByteArray(Charsets.UTF_8)

    private fun encode(value: StoredConversationState): JSONObject {
        val conversations = JSONArray()
        value.conversations.take(MAX_CONVERSATIONS).forEach { conversation ->
            conversations.put(encodeConversation(conversation))
        }
        return JSONObject()
            .put("schema_version", AgentStorageSchema.CURRENT_VERSION)
            .put("selected_agent_id", value.selectedAgentId.wireValue)
            .put("active_conversation_ids", encodeAgentMap(value.activeConversationIds, MAX_CONVERSATION_ID_LENGTH))
            .put("selected_model_ids", encodeAgentMap(value.selectedModelIds, MAX_MODEL_ID_LENGTH))
            .put("conversations", conversations)
    }

    private fun encodeAgentMap(values: Map<AgentId, String>, maxLength: Int): JSONObject = JSONObject().also { output ->
        values.forEach { (agentId, storedValue) ->
            require(storedValue.isNotBlank() && storedValue.length <= maxLength)
            output.put(agentId.wireValue, storedValue)
        }
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
            if (conversations.none {
                    it.agentId == conversation.agentId && it.conversationId == conversation.conversationId
                }
            ) {
                conversations += conversation
            }
        }
        if (schemaVersion < AgentStorageSchema.CURRENT_VERSION) {
            val activeConversationId = value.optString("active_conversation_id", "")
                .takeIf { activeId -> conversations.any { it.conversationId == activeId } }
            val selectedAgent = conversations.firstOrNull { it.conversationId == activeConversationId }
                ?.agentId
                ?: AgentId.CODEX
            val activeIds = activeConversationId?.let { mapOf(selectedAgent to it) }.orEmpty()
            val selectedModels = conversations
                .filter { it.selectedModelId != null }
                .associate { it.agentId to checkNotNull(it.selectedModelId) }
            return StoredConversationState(
                activeConversationId = activeConversationId,
                conversations = conversations,
                selectedAgentId = selectedAgent,
                activeConversationIds = activeIds,
                selectedModelIds = selectedModels,
            )
        }
        val selectedAgent = AgentId.fromWire(value.optString("selected_agent_id", "")) ?: return null
        val activeIds = parseAgentMap(
            value.optJSONObject("active_conversation_ids") ?: return null,
            MAX_CONVERSATION_ID_LENGTH,
        )
            ?: return null
        if (activeIds.any { (agentId, activeId) ->
                conversations.none { it.agentId == agentId && it.conversationId == activeId }
            }
        ) return null
        val selectedModels = parseAgentMap(
            value.optJSONObject("selected_model_ids") ?: return null,
            MAX_MODEL_ID_LENGTH,
        )
            ?: return null
        return StoredConversationState(
            activeConversationId = activeIds[selectedAgent],
            conversations = conversations,
            selectedAgentId = selectedAgent,
            activeConversationIds = activeIds,
            selectedModelIds = selectedModels,
        )
    }

    private fun parseAgentMap(value: JSONObject, maxLength: Int): Map<AgentId, String>? {
        if (value.length() > AgentId.entries.size) return null
        val parsed = linkedMapOf<AgentId, String>()
        val keys = value.keys()
        while (keys.hasNext()) {
            val rawAgent = keys.next()
            val agentId = AgentId.fromWire(rawAgent) ?: return null
            val storedValue = value.optString(rawAgent, "")
                .takeIf { it.isNotEmpty() && it.length <= maxLength }
                ?: return null
            parsed[agentId] = storedValue
        }
        return parsed
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
        const val FILE_NAME_V1 = "codex-chat-state.v1"
        const val FILE_NAME_V2 = "codex-chat-state.v2"
        const val KEY_ALIAS_V1 = "alpine_codex_chat_state_v1"
        const val KEY_ALIAS_V2 = "alpine_agent_chat_state_v2"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val TAG_BITS = 128
        const val IV_BYTES = 12
        const val FORMAT_VERSION: Byte = 2
        val MAGIC = byteArrayOf(0x41, 0x4c, 0x50, 0x4e)
        val MIN_V2_BYTES = (MAGIC.size + 1 + IV_BYTES + 16 + 1).toLong()
        const val MODE_OWNER_RW = 384
        const val MAX_FILE_BYTES = 256 * 1024L
        const val MAX_PLAINTEXT_BYTES = 192 * 1024
        const val MAX_CONVERSATIONS = 8
        const val MAX_CONVERSATION_ID_LENGTH = 128
        const val MAX_MODEL_ID_LENGTH = 256
        const val MAX_MESSAGES = 8
        const val MAX_MESSAGE_BYTES = 4 * 1024
    }
}
