package com.drivercompanion.app

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

data class KnownApp(val pkg: String, val label: String)

/** Apps the user can choose to have announced. SMS is handled separately. */
object KnownApps {
    val list = listOf(
        KnownApp("com.whatsapp", "WhatsApp"),
        KnownApp("com.whatsapp.w4b", "WhatsApp Business"),
        KnownApp("org.telegram.messenger", "Telegram"),
        KnownApp("com.instagram.android", "Instagram"),
        KnownApp("com.facebook.orca", "Messenger"),
        KnownApp("org.thoughtcrime.securesms", "Signal"),
        KnownApp("com.google.android.gm", "Gmail"),
    )
    val defaults = setOf("com.whatsapp", "com.whatsapp.w4b")

    fun label(pkg: String, isSms: Boolean): String =
        list.firstOrNull { it.pkg == pkg }?.label
            ?: if (isSms) "Messages" else pkg.substringAfterLast('.').replaceFirstChar { it.uppercase() }
}

/** Settings kept on the phone; the device gets a copy after each change. */
class AppSettings(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("driver_companion", Context.MODE_PRIVATE)

    var deviceAddress: String?
        get() = prefs.getString("device_address", null)
        set(value) { prefs.edit().putString("device_address", value).apply() }

    var deviceName: String?
        get() = prefs.getString("device_name", null)
        set(value) { prefs.edit().putString("device_name", value).apply() }

    /** 0 = low, 1 = medium, 2 = high. */
    var volume: Int
        get() = prefs.getInt("vol", 1).coerceIn(0, 2)
        set(value) { prefs.edit().putInt("vol", value.coerceIn(0, 2)).apply() }

    var readText: Boolean
        get() = prefs.getBoolean("read_text", false)
        set(value) { prefs.edit().putBoolean("read_text", value).apply() }

    var quiet: Boolean
        get() = prefs.getBoolean("quiet", false)
        set(value) { prefs.edit().putBoolean("quiet", value).apply() }

    /** False after the user taps Stop, until they tap Reconnect or Pair. */
    var autoConnect: Boolean
        get() = prefs.getBoolean("auto_connect", true)
        set(value) { prefs.edit().putBoolean("auto_connect", value).apply() }

    var forwardCalls: Boolean
        get() = prefs.getBoolean("forward_calls", true)
        set(value) { prefs.edit().putBoolean("forward_calls", value).apply() }

    var forwardSms: Boolean
        get() = prefs.getBoolean("forward_sms", true)
        set(value) { prefs.edit().putBoolean("forward_sms", value).apply() }

    var apps: Set<String>
        get() = prefs.getStringSet("apps", null)?.toSet() ?: KnownApps.defaults
        set(value) { prefs.edit().putStringSet("apps", HashSet(value)).apply() }

    fun toDeviceJson(): JSONObject = JSONObject()
        .put("t", "settings")
        .put("vol", volume)
        .put("apps", JSONArray(apps.sorted()))
        .put("read_text", readText)
        .put("quiet", quiet)
}
