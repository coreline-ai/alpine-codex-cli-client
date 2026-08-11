package dev.alpine.codexclient

import android.app.Application
import org.junit.Assert.assertNotNull
import org.junit.Test

class CodexChatViewModelFactoryTest {
    @Test
    fun exposesApplicationOnlyConstructorForDefaultAndroidViewModelFactory() {
        val constructor = CodexChatViewModel::class.java.getConstructor(Application::class.java)
        assertNotNull(constructor)
    }
}
