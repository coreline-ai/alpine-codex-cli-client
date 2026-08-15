package dev.alpine.codexclient

import android.content.Context
import dev.alpine.runtime.api.RuntimeLifecycleState

/** Persists only the user's desired on/off state; Runtime and OAuth data remain authoritative. */
internal class RuntimeRestorePreference(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences(
        PREFERENCES,
        Context.MODE_PRIVATE,
    )

    @Synchronized
    fun shouldRestore(lifecycle: RuntimeLifecycleState): Boolean {
        if (preferences.contains(KEY_RESTORE_REQUESTED)) {
            return preferences.getBoolean(KEY_RESTORE_REQUESTED, false)
        }
        val migrated = defaultRestoreRequested(lifecycle)
        preferences.edit().putBoolean(KEY_RESTORE_REQUESTED, migrated).commit()
        return migrated
    }

    @Synchronized
    fun setRestoreRequested(requested: Boolean) {
        preferences.edit().putBoolean(KEY_RESTORE_REQUESTED, requested).commit()
    }

    private companion object {
        const val PREFERENCES = "alpine-configured-runtime"
        // V2 makes the app self-restoring even when a previous update found no active session.
        const val KEY_RESTORE_REQUESTED = "restore-requested-v2"
    }
}

/** The client is operational by default; only an explicit Runtime stop persists opt-out. */
internal fun defaultRestoreRequested(lifecycle: RuntimeLifecycleState): Boolean =
    lifecycle != RuntimeLifecycleState.INSTALLING
