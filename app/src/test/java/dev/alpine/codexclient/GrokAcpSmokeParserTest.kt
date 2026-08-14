package dev.alpine.codexclient

import org.junit.Assert.assertEquals
import org.junit.Test

class GrokAcpSmokeParserTest {
    @Test
    fun acceptsOnlyExactMarkerAndMatchingExitCode() {
        assertEquals(
            GrokAcpSmokeOutcome.READY,
            parse("GROK_SMOKE_READY\n", exitCode = 0),
        )
        assertEquals(
            GrokAcpSmokeOutcome.INITIALIZE_FAILED,
            parse("GROK_SMOKE_FAILED_INITIALIZE\n", exitCode = 1),
        )
    }

    @Test
    fun mapsEveryContentFreeFailureStage() {
        val values = mapOf(
            "GROK_SMOKE_FAILED_POLICY" to GrokAcpSmokeOutcome.POLICY_FAILED,
            "GROK_SMOKE_FAILED_VERSION" to GrokAcpSmokeOutcome.VERSION_FAILED,
            "GROK_SMOKE_FAILED_PROCESS" to GrokAcpSmokeOutcome.PROCESS_FAILED,
            "GROK_SMOKE_FAILED_LIFECYCLE" to GrokAcpSmokeOutcome.LIFECYCLE_FAILED,
            "GROK_SMOKE_FAILED_ACCOUNT" to GrokAcpSmokeOutcome.ACCOUNT_FAILED,
        )
        values.forEach { (marker, expected) ->
            assertEquals(expected, parse("$marker\n", exitCode = 1))
        }
    }

    @Test
    fun rejectsSuffixExtraLineWrongExitStderrTimeoutAndMalformedUtf8() {
        val invalid = listOf(
            GrokAcpSmokeParser.parse(0, false, "GROK_SMOKE_READY private".encodeToByteArray(), byteArrayOf()),
            GrokAcpSmokeParser.parse(0, false, "GROK_SMOKE_READY\nextra".encodeToByteArray(), byteArrayOf()),
            GrokAcpSmokeParser.parse(1, false, "GROK_SMOKE_READY\n".encodeToByteArray(), byteArrayOf()),
            GrokAcpSmokeParser.parse(0, false, "GROK_SMOKE_FAILED_PROCESS\n".encodeToByteArray(), byteArrayOf()),
            GrokAcpSmokeParser.parse(0, false, "GROK_SMOKE_READY\n".encodeToByteArray(), byteArrayOf(1)),
            GrokAcpSmokeParser.parse(0, true, "GROK_SMOKE_READY\n".encodeToByteArray(), byteArrayOf()),
            GrokAcpSmokeParser.parse(0, false, byteArrayOf(0xC3.toByte(), 0x28), byteArrayOf()),
        )
        invalid.forEach { assertEquals(GrokAcpSmokeOutcome.OUTPUT_INVALID, it) }
    }

    private fun parse(value: String, exitCode: Int): GrokAcpSmokeOutcome =
        GrokAcpSmokeParser.parse(
            exitCode = exitCode,
            timedOut = false,
            standardOutput = value.encodeToByteArray(),
            standardError = byteArrayOf(),
        )
}
