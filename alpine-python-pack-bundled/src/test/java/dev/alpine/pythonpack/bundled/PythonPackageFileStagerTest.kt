package dev.alpine.pythonpack.bundled

import java.io.ByteArrayInputStream
import java.io.File
import java.nio.file.Files
import java.security.MessageDigest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PythonPackageFileStagerTest {
    @Test
    fun `stages exact package atomically and returns absolute guest path`() {
        val fixture = fixture()
        val result = fixture.stager.stage(
            fixture.host,
            "/workspace/.alpine-codex/staging",
            "alpine-python3",
            listOf(fixture.entry),
        )

        assertEquals(
            listOf(
                "/workspace/.alpine-codex/staging/python-pack/alpine-python3/packages/python3.apk",
            ),
            result,
        )
        assertArrayEquals(
            fixture.bytes,
            File(fixture.host, "python-pack/alpine-python3/packages/python3.apk").readBytes(),
        )
        assertTrue(
            File(fixture.host, "python-pack").listFiles().orEmpty().none { it.name.contains(".partial-") },
        )
    }

    @Test
    fun `repairs a mutated staged file before reuse`() {
        val fixture = fixture()
        fixture.stager.stage(
            fixture.host,
            "/workspace/.alpine-codex/staging",
            "alpine-python3",
            listOf(fixture.entry),
        )
        val staged = File(fixture.host, "python-pack/alpine-python3/packages/python3.apk")
        staged.writeBytes(byteArrayOf(9, 9, 9))

        fixture.stager.stage(
            fixture.host,
            "/workspace/.alpine-codex/staging",
            "alpine-python3",
            listOf(fixture.entry),
        )

        assertArrayEquals(fixture.bytes, staged.readBytes())
    }

    @Test
    fun `copy hash failure removes partial directory and does not activate pack`() {
        val fixture = fixture()
        val invalid = fixture.entry.copy(sha256 = "f".repeat(64))

        runCatching {
            fixture.stager.stage(
                fixture.host,
                "/workspace/.alpine-codex/staging",
                "alpine-python3",
                listOf(invalid),
            )
        }.onSuccess { error("invalid hash unexpectedly staged") }

        assertFalse(File(fixture.host, "python-pack/alpine-python3").exists())
        assertTrue(
            File(fixture.host, "python-pack").listFiles().orEmpty().none { it.name.contains(".partial-") },
        )
    }

    @Test(expected = IllegalArgumentException::class)
    fun `unsafe package file name is rejected`() {
        VerifiedPythonPackageEntry("../python3.apk", 1, "0".repeat(64))
    }

    private fun fixture(): Fixture {
        val host = Files.createTempDirectory("python-pack-stager").toFile().also { it.deleteOnExit() }
        val bytes = "locked-python-package".toByteArray()
        val entry = VerifiedPythonPackageEntry("python3.apk", bytes.size.toLong(), sha256(bytes))
        return Fixture(
            host = host,
            bytes = bytes,
            entry = entry,
            stager = PythonPackageFileStager { ByteArrayInputStream(bytes) },
        )
    }

    private fun sha256(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { byte ->
            (byte.toInt() and 0xff).toString(16).padStart(2, '0')
        }

    private data class Fixture(
        val host: File,
        val bytes: ByteArray,
        val entry: VerifiedPythonPackageEntry,
        val stager: PythonPackageFileStager,
    )
}
