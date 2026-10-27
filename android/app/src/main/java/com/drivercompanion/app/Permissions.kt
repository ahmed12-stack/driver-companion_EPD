package com.drivercompanion.app

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings

data class PermissionState(
    val bluetooth: Boolean,
    val location: Boolean,
    val postNotifications: Boolean,
    val notificationAccess: Boolean,
)

object Permissions {
    /** Android 12+ has its own Bluetooth permissions; older versions need location to scan. */
    fun bluetooth(): Array<String> =
        if (Build.VERSION.SDK_INT >= 31) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }

    val location = arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION)

    fun needsPostNotifications(): Boolean = Build.VERSION.SDK_INT >= 33

    fun postNotifications(): Array<String> =
        if (Build.VERSION.SDK_INT >= 33) arrayOf(Manifest.permission.POST_NOTIFICATIONS) else emptyArray()

    fun check(context: Context): PermissionState {
        fun has(p: String) = context.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
        return PermissionState(
            bluetooth = bluetooth().all(::has),
            location = has(Manifest.permission.ACCESS_FINE_LOCATION),
            postNotifications = postNotifications().all(::has),
            notificationAccess = hasNotificationAccess(context),
        )
    }

    private fun hasNotificationAccess(context: Context): Boolean {
        val enabled = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners")
            ?: return false
        val me = ComponentName(context, NotificationForwarder::class.java)
        return enabled.split(":").any { ComponentName.unflattenFromString(it) == me }
    }

    fun notificationAccessIntent(): Intent = Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
}
