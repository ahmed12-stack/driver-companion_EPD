package com.drivercompanion.app

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.Looper
import android.util.Log
import com.drivercompanion.app.ble.DeviceLink
import com.drivercompanion.app.ble.LinkState
import com.drivercompanion.app.ble.LinkStatus

/**
 * Foreground service that owns the Bluetooth link, so it stays connected while
 * the app is in the background, and sends phone GPS while connected.
 */
@SuppressLint("MissingPermission")
class LinkService : Service(), LocationListener {

    companion object {
        const val ACTION_START = "com.drivercompanion.app.action.START"
        const val ACTION_PAIR = "com.drivercompanion.app.action.PAIR"
        const val ACTION_STOP = "com.drivercompanion.app.action.STOP"
        private const val CHANNEL = "link"
        private const val NOTIFICATION_ID = 1
        private const val TAG = "LinkService"

        fun send(context: Context, action: String) {
            context.startForegroundService(Intent(context, LinkService::class.java).setAction(action))
        }
    }

    private lateinit var settings: AppSettings
    private lateinit var link: DeviceLink
    private var locationOn = false

    override fun onCreate() {
        super.onCreate()
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Device connection", NotificationManager.IMPORTANCE_LOW),
        )
        if (!goForeground("Starting…")) return
        settings = AppSettings(this)
        link = DeviceLink(this, settings, TripStore.get(this)) { s ->
            LinkHub.publish(s)
            onLinkStatus(s)
        }
        LinkHub.link = link
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!::link.isInitialized) {
            stopSelf()
            return START_NOT_STICKY
        }
        // Called again so the location type is added once that permission is granted.
        if (!goForeground(describe(link.status))) return START_NOT_STICKY
        when (intent?.action) {
            ACTION_PAIR -> {
                settings.autoConnect = true
                link.pairNew()
            }
            ACTION_STOP -> {
                // Remembered, so opening the app does not start searching again.
                settings.autoConnect = false
                link.stop()
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                settings.autoConnect = true
                link.start()
            }
        }
        // Location may have been allowed after the link came up.
        if (link.isReady) startLocation()
        return START_STICKY
    }

    override fun onDestroy() {
        stopLocation()
        if (::link.isInitialized) {
            link.close()
            if (LinkHub.link === link) LinkHub.link = null
        }
        // close() posts a final status; make sure no notification is left behind.
        getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun onLinkStatus(s: LinkStatus) {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(describe(s)))
        if (s.state == LinkState.READY) startLocation() else stopLocation()
    }

    private fun describe(s: LinkStatus): String = when (s.state) {
        LinkState.READY -> "Connected to ${s.deviceName ?: "your device"}"
        else -> s.message ?: "Not connected"
    }

    private fun hasLocation(): Boolean =
        checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED

    /** Returns false (and stops) if Android refuses to run the service. */
    private fun goForeground(text: String): Boolean {
        val notification = buildNotification(text)
        return try {
            if (Build.VERSION.SDK_INT >= 29) {
                var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE
                if (hasLocation()) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
                startForeground(NOTIFICATION_ID, notification, type)
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            true
        } catch (e: SecurityException) {
            Log.w(TAG, "missing permission for the foreground service", e)
            stopSelf()
            false
        }
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("Driver Companion")
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(open)
            .build()
    }

    // ----- phone GPS -------------------------------------------------------

    private fun startLocation() {
        if (locationOn || !hasLocation()) return
        val lm = getSystemService(LocationManager::class.java) ?: return
        // Registered even if Location is switched off right now: fixes start
        // arriving by themselves when the user turns it on.
        try {
            lm.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, this, Looper.getMainLooper())
            locationOn = true
        } catch (e: SecurityException) {
            Log.w(TAG, "location permission missing", e)
        }
    }

    private fun stopLocation() {
        if (!locationOn) return
        getSystemService(LocationManager::class.java)?.removeUpdates(this)
        locationOn = false
    }

    override fun onLocationChanged(location: Location) {
        val kmh = if (location.hasSpeed()) location.speed * 3.6 else 0.0
        link.sendLocation(location.latitude, location.longitude, kmh, location.accuracy.toDouble(), location.time)
    }

    // Android 10 and older require these; newer versions provide defaults.
    @Deprecated("Deprecated in Java")
    override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
    override fun onProviderEnabled(provider: String) {}
    override fun onProviderDisabled(provider: String) {}
}
