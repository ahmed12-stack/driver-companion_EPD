package com.drivercompanion.app

import com.drivercompanion.app.ble.DeviceLink
import com.drivercompanion.app.ble.LinkState
import com.drivercompanion.app.ble.LinkStatus
import java.util.concurrent.CopyOnWriteArraySet

/** Shares the running link between the service, the notification reader and the screen. */
object LinkHub {
    @Volatile
    var link: DeviceLink? = null

    @Volatile
    var status: LinkStatus = LinkStatus(LinkState.IDLE)
        private set

    private val listeners = CopyOnWriteArraySet<(LinkStatus) -> Unit>()

    /** Called on the main thread by the link. */
    fun publish(newStatus: LinkStatus) {
        status = newStatus
        listeners.forEach { it(newStatus) }
    }

    fun addListener(listener: (LinkStatus) -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: (LinkStatus) -> Unit) {
        listeners.remove(listener)
    }
}
