package dev.alpine.codexclient.bridge

import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction

/** Small strict JSON reader/writer for the closed gateway contract. */
internal sealed interface JsonValue {
    data class ObjectValue(val values: Map<String, JsonValue>) : JsonValue
    data class ArrayValue(val values: List<JsonValue>) : JsonValue
    data class StringValue(val value: String) : JsonValue
    data class BooleanValue(val value: Boolean) : JsonValue
    data class NumberValue(val value: String) : JsonValue
    data object NullValue : JsonValue
}

internal object BoundedJson {
    fun decodeUtf8(bytes: ByteArray): String = try {
        Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(bytes))
            .toString()
    } catch (_: CharacterCodingException) {
        throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
    }

    fun parse(bytes: ByteArray, maxBytes: Int): JsonValue {
        if (bytes.isEmpty() || bytes.size > maxBytes) {
            throw GatewayClientException(GatewayClientErrorCode.RESPONSE_TOO_LARGE)
        }
        return Parser(decodeUtf8(bytes)).parse()
    }

    fun encode(value: JsonValue): ByteArray = buildString { appendValue(value) }.toByteArray(Charsets.UTF_8)

    private fun StringBuilder.appendValue(value: JsonValue) {
        when (value) {
            is JsonValue.ObjectValue -> {
                append('{')
                value.values.entries.forEachIndexed { index, (key, nested) ->
                    if (index > 0) append(',')
                    appendString(key)
                    append(':')
                    appendValue(nested)
                }
                append('}')
            }
            is JsonValue.ArrayValue -> {
                append('[')
                value.values.forEachIndexed { index, nested ->
                    if (index > 0) append(',')
                    appendValue(nested)
                }
                append(']')
            }
            is JsonValue.StringValue -> appendString(value.value)
            is JsonValue.BooleanValue -> append(if (value.value) "true" else "false")
            is JsonValue.NumberValue -> append(value.value)
            JsonValue.NullValue -> append("null")
        }
    }

    private fun StringBuilder.appendString(value: String) {
        append('"')
        value.forEach { character ->
            when (character) {
                '"' -> append("\\\"")
                '\\' -> append("\\\\")
                '\b' -> append("\\b")
                '\u000c' -> append("\\f")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> if (character.code < 0x20) {
                    append("\\u")
                    append(character.code.toString(16).padStart(4, '0'))
                } else {
                    append(character)
                }
            }
        }
        append('"')
    }

    private class Parser(private val source: String) {
        private var index = 0

        fun parse(): JsonValue {
            val value = readValue(depth = 0)
            skipWhitespace()
            if (index != source.length) fail()
            return value
        }

        private fun readValue(depth: Int): JsonValue {
            if (depth > MAX_DEPTH) fail()
            skipWhitespace()
            return when (peek()) {
                '{' -> readObject(depth + 1)
                '[' -> readArray(depth + 1)
                '"' -> JsonValue.StringValue(readString())
                't' -> readLiteral("true", JsonValue.BooleanValue(true))
                'f' -> readLiteral("false", JsonValue.BooleanValue(false))
                'n' -> readLiteral("null", JsonValue.NullValue)
                '-', in '0'..'9' -> JsonValue.NumberValue(readNumber())
                else -> fail()
            }
        }

        private fun readObject(depth: Int): JsonValue.ObjectValue {
            expect('{')
            skipWhitespace()
            val values = linkedMapOf<String, JsonValue>()
            if (consume('}')) return JsonValue.ObjectValue(values)
            while (true) {
                skipWhitespace()
                if (peek() != '"') fail()
                val key = readString()
                if (values.containsKey(key)) fail()
                skipWhitespace()
                expect(':')
                values[key] = readValue(depth)
                if (values.size > MAX_OBJECT_ENTRIES) fail()
                skipWhitespace()
                if (consume('}')) return JsonValue.ObjectValue(values)
                expect(',')
            }
        }

        private fun readArray(depth: Int): JsonValue.ArrayValue {
            expect('[')
            skipWhitespace()
            val values = mutableListOf<JsonValue>()
            if (consume(']')) return JsonValue.ArrayValue(values)
            while (true) {
                values += readValue(depth)
                if (values.size > MAX_ARRAY_ENTRIES) fail()
                skipWhitespace()
                if (consume(']')) return JsonValue.ArrayValue(values)
                expect(',')
            }
        }

        private fun readString(): String {
            expect('"')
            val result = StringBuilder()
            while (index < source.length) {
                val character = source[index++]
                when (character) {
                    '"' -> return result.toString()
                    '\\' -> result.append(readEscape())
                    in '\u0000'..'\u001f' -> fail()
                    else -> result.append(character)
                }
                if (result.length > MAX_STRING_CHARS) fail()
            }
            fail()
        }

        private fun readEscape(): Char = when (val escape = take()) {
            '"', '\\', '/' -> escape
            'b' -> '\b'
            'f' -> '\u000c'
            'n' -> '\n'
            'r' -> '\r'
            't' -> '\t'
            'u' -> {
                val end = index + 4
                if (end > source.length) fail()
                val hex = source.substring(index, end)
                if (!hex.all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }) fail()
                index = end
                hex.toInt(16).toChar()
            }
            else -> fail()
        }

        private fun readNumber(): String {
            val start = index
            consume('-')
            if (consume('0')) {
                // A leading zero is valid only when immediately followed by a fraction/exponent/end.
            } else {
                if (peek() !in '1'..'9') fail()
                while (peek() in '0'..'9') index++
            }
            if (consume('.')) {
                if (peek() !in '0'..'9') fail()
                while (peek() in '0'..'9') index++
            }
            if (peek() == 'e' || peek() == 'E') {
                index++
                if (peek() == '+' || peek() == '-') index++
                if (peek() !in '0'..'9') fail()
                while (peek() in '0'..'9') index++
            }
            return source.substring(start, index)
        }

        private fun <T : JsonValue> readLiteral(expected: String, value: T): T {
            if (!source.regionMatches(index, expected, 0, expected.length)) fail()
            index += expected.length
            return value
        }

        private fun skipWhitespace() {
            while (peek() in WHITESPACE) index++
        }

        private fun expect(expected: Char) {
            if (!consume(expected)) fail()
        }

        private fun consume(expected: Char): Boolean {
            if (peek() != expected) return false
            index++
            return true
        }

        private fun take(): Char = if (index < source.length) source[index++] else fail()

        private fun peek(): Char = source.getOrNull(index) ?: '\u0000'

        private fun fail(): Nothing = throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
    }

    private const val MAX_DEPTH = 12
    private const val MAX_OBJECT_ENTRIES = 128
    private const val MAX_ARRAY_ENTRIES = 128
    private const val MAX_STRING_CHARS = 32 * 1024
    private val WHITESPACE = setOf(' ', '\t', '\n', '\r')
}

internal fun JsonValue.asObject(): Map<String, JsonValue> = (this as? JsonValue.ObjectValue)?.values
    ?: throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)

internal fun JsonValue.asArray(): List<JsonValue> = (this as? JsonValue.ArrayValue)?.values
    ?: throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)

internal fun Map<String, JsonValue>.requiredString(name: String): String =
    ((this[name] as? JsonValue.StringValue)?.value)?.takeIf { it.isNotEmpty() && it.length <= 4096 }
        ?: throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)

internal fun Map<String, JsonValue>.requiredBoolean(name: String): Boolean =
    (this[name] as? JsonValue.BooleanValue)?.value
        ?: throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)

internal fun Map<String, JsonValue>.requiredPositiveInt(name: String, maximum: Int): Int =
    (this[name] as? JsonValue.NumberValue)?.value?.toIntOrNull()?.takeIf { it in 1..maximum }
        ?: throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)

internal fun Map<String, JsonValue>.optionalString(name: String): String? = when (val value = this[name]) {
    null, JsonValue.NullValue -> null
    is JsonValue.StringValue -> value.value.takeIf { it.length <= 4096 }
    else -> throw GatewayClientException(GatewayClientErrorCode.MALFORMED_RESPONSE)
}
