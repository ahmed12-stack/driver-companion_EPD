package com.drivercompanion.app

import android.app.Notification
import android.os.Build
import android.os.Bundle
import android.os.Parcelable
import android.provider.Telephony
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification

/**
 * Reads incoming notifications (once the user grants Notification access)
 * and forwards messages, SMS and incoming calls to the device.
 */
class NotificationForwarder : NotificationListenerService() {

    /** Last thing forwarded per notification, so updates don't repeat it. */
    private val lastSent = HashMap<String, String>()

    /** Incoming calls already announced: notification key -> caller. */
    private val calls = HashMap<String, String>()

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val link = LinkHub.link ?: return
        if (sbn.packageName == packageName) return
        val n = sbn.notification
        val settings = AppSettings(this)

        if (n.category == Notification.CATEGORY_CALL) {
            if (settings.forwardCalls) handleCall(sbn)
            return
        }
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        if (n.flags and Notification.FLAG_ONGOING_EVENT != 0) return

        val pkg = sbn.packageName
        val isSms = pkg == Telephony.Sms.getDefaultSmsPackage(this)
        val allowed = if (isSms) settings.forwardSms else pkg in settings.apps
        if (!allowed) return

        val (sender, text) = extract(n.extras) ?: return
        val fingerprint = "$sender\u0000$text"
        if (lastSent[sbn.key] == fingerprint) return
        lastSent[sbn.key] = fingerprint
        link.sendNotification(KnownApps.label(pkg, isSms), pkg, sender, text, isSms)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {
        lastSent.remove(sbn.key)
        val caller = calls.remove(sbn.key) ?: return
        LinkHub.link?.sendCall(caller, ringing = false)
    }

    private fun handleCall(sbn: StatusBarNotification) {
        if (calls.containsKey(sbn.key)) return
        val n = sbn.notification
        // Android 12+ marks call notifications: 1 = incoming, 2 = ongoing, 3 = screening.
        val callType = n.extras.getInt("android.callType", 0)
        val incoming = callType == 1 || (callType == 0 && n.fullScreenIntent != null)
        if (!incoming) return
        val caller = n.extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()?.trim()
            .orEmpty().ifEmpty { "unknown caller" }
        calls[sbn.key] = caller
        LinkHub.link?.sendCall(caller, ringing = true)
    }

    /** Returns (sender, text) or null if the notification has no sender. */
    private fun extract(extras: Bundle): Pair<String, String?>? {
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
        var sender = title
        var text = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            ?: extras.getCharSequence(Notification.EXTRA_TEXT))?.toString()

        // Chat apps (WhatsApp, Telegram…) use MessagingStyle: take the newest message.
        val last = messages(extras)?.lastOrNull() as? Bundle
        if (last != null) {
            last.getCharSequence("sender")?.toString()?.let { sender = it }
            last.getCharSequence("text")?.toString()?.let { text = it }
            val conversation = extras.getCharSequence(Notification.EXTRA_CONVERSATION_TITLE)?.toString()
            val isGroup = Build.VERSION.SDK_INT >= 28 &&
                extras.getBoolean(Notification.EXTRA_IS_GROUP_CONVERSATION, false)
            if (isGroup && !conversation.isNullOrBlank() && sender != null) sender = "$sender in $conversation"
        }

        val who = sender?.trim()
        if (who.isNullOrEmpty()) return null
        return who to text?.trim()
    }

    @Suppress("DEPRECATION")
    private fun messages(extras: Bundle): Array<out Parcelable>? =
        if (Build.VERSION.SDK_INT >= 33) {
            extras.getParcelableArray(Notification.EXTRA_MESSAGES, Parcelable::class.java)
        } else {
            extras.getParcelableArray(Notification.EXTRA_MESSAGES)
        }
}
