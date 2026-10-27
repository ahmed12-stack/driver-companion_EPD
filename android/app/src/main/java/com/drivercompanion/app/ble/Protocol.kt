package com.drivercompanion.app.ble

import android.os.SystemClock
import java.io.ByteArrayOutputStream
import java.util.UUID

/**
 * Driver Companion Bluetooth Protocol v1. Must match the device's
 * bluetooth/dc_ble/uuids.py and framing.py exactly.
 */
object Protocol {
    const val VERSION = 1
    const val APP_VERSION = "0.1.0"

    private fun uuid(n: Int): UUID =
        UUID.fromString(String.format("70b6%04x-fb6b-4539-82b8-75e1dfffb8c2", n))

    val SERVICE: UUID = uuid(0x0000)
    val NOTIFICATION_IN: UUID = uuid(0x0001)
    val LOCATION_IN: UUID = uuid(0x0002)
    val SETTINGS: UUID = uuid(0x0003)
    val TRIP_OUT: UUID = uuid(0x0004)
    val STATUS: UUID = uuid(0x0005)
    val UPDATE_IN: UUID = uuid(0x0006)

    /** Standard descriptor that turns notifications on. */
    val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    const val HEADER = 3
    private const val ATT_OVERHEAD = 3
    const val MIN_MTU = 23
    const val WANTED_MTU = 247
    const val MAX_MESSAGE = 32 * 1024

    fun bodySize(mtu: Int): Int = maxOf(mtu, MIN_MTU) - ATT_OVERHEAD - HEADER

    /** Cut a message into packets, each starting with [id, index, count]. */
    fun split(body: ByteArray, msgId: Int, mtu: Int): List<ByteArray> {
        val size = bodySize(mtu)
        val count = maxOf(1, (body.size + size - 1) / size)
        require(count <= 255 && body.size <= MAX_MESSAGE) { "message too large" }
        return List(count) { i ->
            val start = i * size
            val end = minOf(body.size, start + size)
            val packet = ByteArray(HEADER + (end - start))
            packet[0] = msgId.toByte()
            packet[1] = i.toByte()
            packet[2] = count.toByte()
            System.arraycopy(body, start, packet, HEADER, end - start)
            packet
        }
    }
}

/** Puts the packets of one incoming message back together. */
class Reassembler(
    private val timeoutMs: Long = 2000,
    private val clock: () -> Long = { SystemClock.elapsedRealtime() },
) {
    private var msgId = -1
    private var count = 0
    private var expected = 0
    private var last = 0L
    private val buffer = ByteArrayOutputStream()

    fun reset() {
        msgId = -1
        count = 0
        expected = 0
        buffer.reset()
    }

    /** Returns the whole message when its last packet arrives, else null. */
    fun feed(packet: ByteArray): ByteArray? {
        val now = clock()
        if (msgId >= 0 && now - last > timeoutMs) reset()
        if (packet.size < Protocol.HEADER) {
            reset()
            return null
        }
        val id = packet[0].toInt() and 0xFF
        val index = packet[1].toInt() and 0xFF
        val total = packet[2].toInt() and 0xFF
        if (total == 0 || index >= total) {
            reset()
            return null
        }
        if (index == 0) {
            reset()
            msgId = id
            count = total
        } else if (msgId != id || index != expected || total != count) {
            reset()
            return null
        }
        buffer.write(packet, Protocol.HEADER, packet.size - Protocol.HEADER)
        if (buffer.size() > Protocol.MAX_MESSAGE) {
            reset()
            return null
        }
        expected++
        last = now
        if (expected == count) {
            val message = buffer.toByteArray()
            reset()
            return message
        }
        return null
    }
}
