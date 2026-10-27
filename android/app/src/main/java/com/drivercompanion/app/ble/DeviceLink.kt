package com.drivercompanion.app.ble

import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.util.Log
import com.drivercompanion.app.AppSettings
import org.json.JSONException
import org.json.JSONObject
import java.util.UUID

enum class LinkState { IDLE, BLUETOOTH_OFF, SCANNING, CONNECTING, PAIRING, SETTING_UP, READY, DISCONNECTED }

data class LinkStatus(
    val state: LinkState,
    val deviceName: String? = null,
    val firmware: String? = null,
    val message: String? = null,
)

/**
 * The phone's side of the Bluetooth link. Finds the device, pairs, keeps the
 * connection alive, and sends messages one packet at a time (Android allows
 * only one Bluetooth operation in flight).
 *
 * Everything runs on the main thread; Bluetooth callbacks are posted there.
 */
@SuppressLint("MissingPermission")
class DeviceLink(
    context: Context,
    private val settings: AppSettings,
    private val onStatus: (LinkStatus) -> Unit,
) {
    private sealed class Op(val uuid: UUID) {
        class Write(uuid: UUID, val data: ByteArray, val noResponse: Boolean) : Op(uuid)
        class EnableNotify(uuid: UUID) : Op(uuid)
    }

    private val context = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val adapter: BluetoothAdapter? =
        context.getSystemService(BluetoothManager::class.java)?.adapter

    private var gatt: BluetoothGatt? = null
    private var device: BluetoothDevice? = null
    private val chars = HashMap<UUID, BluetoothGattCharacteristic>()
    private val ops = ArrayDeque<Op>()
    private var busy = false
    private var mtu = Protocol.MIN_MTU
    private var nextMsgId = 0
    private var nextSeq = 0
    private val statusIn = Reassembler()
    private var stayConnected = false
    private var scanning = false
    private var firmware: String? = null

    var status = LinkStatus(LinkState.IDLE)
        private set
    val isReady: Boolean get() = status.state == LinkState.READY

    // ----- public controls (safe to call from any thread) -----------------

    /** Scan for a device in pairing mode, connect and pair with it. */
    fun pairNew() = onMain {
        val a = adapter
        if (a == null || !a.isEnabled) {
            update(LinkState.BLUETOOTH_OFF, "Turn on Bluetooth and try again.")
            return@onMain
        }
        val scanner = a.bluetoothLeScanner
        if (scanner == null) {
            update(LinkState.BLUETOOTH_OFF, "Turn on Bluetooth and try again.")
            return@onMain
        }
        stopScan()
        disconnectInternal()
        stayConnected = true
        val filters = listOf(ScanFilter.Builder().setServiceUuid(ParcelUuid(Protocol.SERVICE)).build())
        val scanSettings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        scanner.startScan(filters, scanSettings, scanCallback)
        scanning = true
        update(LinkState.SCANNING, "Looking for your Driver Companion…")
        main.postDelayed(scanTimeout, SCAN_TIMEOUT_MS)
    }

    /** Reconnect to the saved device; waits in the background until it is in range. */
    fun start() = onMain {
        val address = settings.deviceAddress
        if (address == null) {
            update(LinkState.IDLE, "No device paired yet.")
            return@onMain
        }
        val a = adapter
        if (a == null || !a.isEnabled) {
            update(LinkState.BLUETOOTH_OFF, "Turn on Bluetooth to connect.")
            return@onMain
        }
        if (gatt != null || scanning) return@onMain
        stayConnected = true
        val d = a.getRemoteDevice(address)
        device = d
        connect(d, autoConnect = true)
    }

    fun stop() = onMain {
        stayConnected = false
        stopScan()
        disconnectInternal()
        update(LinkState.IDLE, "Disconnected.")
    }

    fun close() = onMain {
        stop()
        try {
            context.unregisterReceiver(bondReceiver)
        } catch (e: IllegalArgumentException) {
            // already unregistered
        }
    }

    fun sendNotification(app: String, pkg: String, sender: String, text: String?, sms: Boolean) = onMain {
        if (!isReady) return@onMain
        val msg = JSONObject()
            .put("t", "notif")
            .put("app", app)
            .put("pkg", pkg)
            .put("kind", if (sms) "sms" else "msg")
            .put("from", sender)
            .put("ts", System.currentTimeMillis() / 1000)
        // Message text leaves the phone only if the user turned reading on.
        if (settings.readText && !text.isNullOrBlank()) msg.put("text", text.take(300))
        sendNow(Protocol.NOTIFICATION_IN, msg)
    }

    fun sendCall(caller: String, ringing: Boolean) = onMain {
        if (!isReady) return@onMain
        val msg = JSONObject()
            .put("t", "call")
            .put("from", caller)
            .put("state", if (ringing) "ringing" else "ended")
            .put("ts", System.currentTimeMillis() / 1000)
        sendNow(Protocol.NOTIFICATION_IN, msg)
    }

    fun sendLocation(lat: Double, lon: Double, kmh: Double, accuracyM: Double) = onMain {
        if (!isReady) return@onMain
        // Drop this fix if the last one has not gone out yet; a newer one follows in a second.
        if (ops.any { it.uuid == Protocol.LOCATION_IN }) return@onMain
        val msg = JSONObject()
            .put("t", "loc")
            .put("lat", lat)
            .put("lon", lon)
            .put("kmh", kmh)
            .put("acc", accuracyM)
            .put("ts", System.currentTimeMillis() / 1000)
        sendNow(Protocol.LOCATION_IN, msg, noResponse = true)
    }

    fun sendSettings() = onMain {
        if (!isReady) return@onMain
        sendNow(Protocol.SETTINGS, settings.toDeviceJson())
    }

    fun sendTest() = onMain {
        if (!isReady) return@onMain
        val msg = JSONObject()
            .put("t", "notif")
            .put("app", "Driver Companion")
            .put("pkg", "com.drivercompanion.app")
            .put("kind", "msg")
            .put("from", "the app")
            .put("text", "This is a test alert.")
        sendNow(Protocol.NOTIFICATION_IN, msg)
    }

    // ----- connection steps --------------------------------------------

    private fun connect(d: BluetoothDevice, autoConnect: Boolean) {
        update(LinkState.CONNECTING, if (autoConnect) "Waiting for the device…" else "Connecting…")
        gatt = d.connectGatt(context, autoConnect, callback, BluetoothDevice.TRANSPORT_LE)
    }

    private fun onFound(d: BluetoothDevice) {
        if (!scanning) return
        stopScan()
        device = d
        connect(d, autoConnect = false)
    }

    private fun onServices(g: BluetoothGatt) {
        val service = g.getService(Protocol.SERVICE)
        if (service == null) {
            stayConnected = false
            disconnectInternal()
            update(LinkState.DISCONNECTED, "That device is not a Driver Companion.")
            return
        }
        chars.clear()
        service.characteristics.forEach { chars[it.uuid] = it }
        val d = g.device
        when (d.bondState) {
            BluetoothDevice.BOND_BONDED -> afterBonded()
            BluetoothDevice.BOND_BONDING -> update(LinkState.PAIRING, PAIRING_HINT)
            else -> {
                update(LinkState.PAIRING, PAIRING_HINT)
                if (!d.createBond()) update(LinkState.PAIRING, "Could not start pairing. Try again.")
            }
        }
    }

    private fun afterBonded() {
        update(LinkState.SETTING_UP, "Paired. Checking the device…")
        enqueue(Op.EnableNotify(Protocol.STATUS))
        sendNow(
            Protocol.SETTINGS,
            JSONObject().put("t", "hello").put("proto", Protocol.VERSION).put("app", Protocol.APP_VERSION),
        )
        main.postDelayed(helloTimeout, HELLO_TIMEOUT_MS)
    }

    private fun onStatusPacket(packet: ByteArray) {
        val body = statusIn.feed(packet) ?: return
        val msg = try {
            JSONObject(String(body, Charsets.UTF_8))
        } catch (e: JSONException) {
            Log.w(TAG, "unreadable status message")
            return
        }
        when (msg.optString("t")) {
            "hello" -> {
                main.removeCallbacks(helloTimeout)
                firmware = msg.optString("fw").ifEmpty { null }
                settings.deviceAddress = gatt?.device?.address
                settings.deviceName = msg.optString("dev").ifEmpty { null }
                update(LinkState.READY, "Connected.")
                sendNow(Protocol.SETTINGS, settings.toDeviceJson())
            }
            "err" -> {
                val code = msg.optString("code")
                Log.w(TAG, "device error $code: ${msg.optString("msg")}")
                if (code == "VERSION") {
                    stayConnected = false
                    disconnectInternal()
                    update(LinkState.DISCONNECTED, "The device and app versions do not match. Update both.")
                }
            }
        }
    }

    private fun linkLost(message: String) {
        disconnectInternal()
        val d = device
        if (stayConnected && d != null && d.bondState == BluetoothDevice.BOND_BONDED) {
            update(LinkState.DISCONNECTED, "$message Reconnecting…")
            main.postDelayed(reconnect, RECONNECT_DELAY_MS)
        } else {
            stayConnected = false
            update(LinkState.DISCONNECTED, message)
        }
    }

    private fun disconnectInternal() {
        main.removeCallbacks(helloTimeout)
        main.removeCallbacks(reconnect)
        main.removeCallbacks(opTimeout)
        gatt?.let {
            it.disconnect()
            it.close()
        }
        gatt = null
        chars.clear()
        ops.clear()
        busy = false
        statusIn.reset()
        mtu = Protocol.MIN_MTU
    }

    private fun stopScan() {
        if (!scanning) return
        scanning = false
        main.removeCallbacks(scanTimeout)
        try {
            adapter?.bluetoothLeScanner?.stopScan(scanCallback)
        } catch (e: IllegalStateException) {
            // Bluetooth was turned off
        }
    }

    // ----- sending -------------------------------------------------------

    private fun sendNow(slot: UUID, msg: JSONObject, noResponse: Boolean = false) {
        if (gatt == null) return
        if (slot != Protocol.LOCATION_IN && !msg.has("seq")) {
            msg.put("seq", nextSeq)
            nextSeq = (nextSeq + 1) % 65536
        }
        val packets = try {
            Protocol.split(msg.toString().toByteArray(Charsets.UTF_8), nextMsgId, mtu)
        } catch (e: IllegalArgumentException) {
            Log.w(TAG, "message too large to send")
            return
        }
        nextMsgId = (nextMsgId + 1) % 256
        packets.forEach { enqueue(Op.Write(slot, it, noResponse)) }
    }

    private fun enqueue(op: Op) {
        ops.addLast(op)
        next()
    }

    private fun next() {
        if (busy) return
        val g = gatt ?: return
        val op = ops.removeFirstOrNull() ?: return
        val ch = chars[op.uuid]
        val started = if (ch == null) {
            false
        } else {
            when (op) {
                is Op.Write -> writeCharacteristic(
                    g, ch, op.data,
                    if (op.noResponse) BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
                    else BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT,
                )
                is Op.EnableNotify -> {
                    g.setCharacteristicNotification(ch, true)
                    val cccd = ch.getDescriptor(Protocol.CCCD)
                    cccd != null && writeDescriptor(g, cccd, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
                }
            }
        }
        if (started) {
            busy = true
            main.postDelayed(opTimeout, OP_TIMEOUT_MS)
        } else {
            Log.w(TAG, "could not start Bluetooth operation on ${op.uuid}")
            main.post { next() }
        }
    }

    private fun opDone(status: Int) {
        main.removeCallbacks(opTimeout)
        if (status != BluetoothGatt.GATT_SUCCESS) Log.w(TAG, "Bluetooth operation failed: $status")
        busy = false
        next()
    }

    @Suppress("DEPRECATION")
    private fun writeCharacteristic(
        g: BluetoothGatt,
        ch: BluetoothGattCharacteristic,
        data: ByteArray,
        type: Int,
    ): Boolean =
        if (Build.VERSION.SDK_INT >= 33) {
            g.writeCharacteristic(ch, data, type) == BluetoothStatusCodes.SUCCESS
        } else {
            ch.writeType = type
            ch.value = data
            g.writeCharacteristic(ch)
        }

    @Suppress("DEPRECATION")
    private fun writeDescriptor(g: BluetoothGatt, d: BluetoothGattDescriptor, value: ByteArray): Boolean =
        if (Build.VERSION.SDK_INT >= 33) {
            g.writeDescriptor(d, value) == BluetoothStatusCodes.SUCCESS
        } else {
            d.value = value
            g.writeDescriptor(d)
        }

    // ----- helpers -------------------------------------------------------

    private fun update(state: LinkState, message: String? = null) {
        status = LinkStatus(state, settings.deviceName, firmware, message)
        onStatus(status)
    }

    private fun onMain(block: () -> Unit) {
        if (Looper.myLooper() == Looper.getMainLooper()) block() else main.post(block)
    }

    private val scanTimeout = Runnable {
        stopScan()
        stayConnected = false
        update(
            LinkState.DISCONNECTED,
            "No device found. Check it is powered and in pairing mode (hold the button 5 seconds).",
        )
    }

    private val helloTimeout = Runnable { linkLost("The device did not answer.") }

    private val reconnect = Runnable {
        val d = device
        if (stayConnected && gatt == null && d != null) connect(d, autoConnect = true)
    }

    private val opTimeout = Runnable {
        Log.w(TAG, "Bluetooth operation timed out")
        busy = false
        next()
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            val d = result.device
            onMain { onFound(d) }
        }

        override fun onScanFailed(errorCode: Int) {
            onMain {
                stopScan()
                stayConnected = false
                update(LinkState.DISCONNECTED, "Bluetooth scan failed (code $errorCode). Try again.")
            }
        }
    }

    private val bondReceiver = object : BroadcastReceiver() {
        override fun onReceive(c: Context, intent: Intent) {
            val d = deviceExtra(intent) ?: return
            val state = intent.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.ERROR)
            onMain {
                if (d.address != device?.address || status.state != LinkState.PAIRING) return@onMain
                when (state) {
                    BluetoothDevice.BOND_BONDED -> afterBonded()
                    BluetoothDevice.BOND_NONE -> {
                        stayConnected = false
                        disconnectInternal()
                        update(
                            LinkState.DISCONNECTED,
                            "Pairing failed. Hold the device button 5 seconds for pairing mode, then try again.",
                        )
                    }
                }
            }
        }
    }

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) = onMain {
            if (g !== gatt) return@onMain
            if (newState == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                update(LinkState.CONNECTING, "Connected. Setting up…")
                if (!g.requestMtu(Protocol.WANTED_MTU)) g.discoverServices()
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED ||
                status != BluetoothGatt.GATT_SUCCESS
            ) {
                linkLost("Connection lost.")
            }
        }

        override fun onMtuChanged(g: BluetoothGatt, newMtu: Int, status: Int) = onMain {
            if (g !== gatt) return@onMain
            if (status == BluetoothGatt.GATT_SUCCESS) mtu = newMtu
            g.discoverServices()
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) = onMain {
            if (g !== gatt) return@onMain
            if (status != BluetoothGatt.GATT_SUCCESS) {
                linkLost("Could not read the device.")
                return@onMain
            }
            onServices(g)
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, ch: BluetoothGattCharacteristic, status: Int) =
            onMain { if (g === gatt) opDone(status) }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) =
            onMain { if (g === gatt) opDone(status) }

        // Android 12 and older.
        @Suppress("OVERRIDE_DEPRECATION", "DEPRECATION")
        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
            if (Build.VERSION.SDK_INT >= 33) return
            val value = ch.value?.copyOf() ?: return
            val uuid = ch.uuid
            onMain { if (g === gatt && uuid == Protocol.STATUS) onStatusPacket(value) }
        }

        // Android 13 and newer.
        override fun onCharacteristicChanged(
            g: BluetoothGatt,
            ch: BluetoothGattCharacteristic,
            value: ByteArray,
        ) {
            val copy = value.copyOf()
            val uuid = ch.uuid
            onMain { if (g === gatt && uuid == Protocol.STATUS) onStatusPacket(copy) }
        }
    }

    // Must come after bondReceiver is created (properties initialise in order).
    init {
        val filter = IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED)
        if (Build.VERSION.SDK_INT >= 33) {
            this.context.registerReceiver(bondReceiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            this.context.registerReceiver(bondReceiver, filter)
        }
    }

    companion object {
        private const val TAG = "DeviceLink"
        private const val SCAN_TIMEOUT_MS = 20_000L
        private const val HELLO_TIMEOUT_MS = 10_000L
        private const val RECONNECT_DELAY_MS = 3_000L
        private const val OP_TIMEOUT_MS = 5_000L
        private const val PAIRING_HINT = "Type the 6-digit code the device speaks into the box Android shows."

        @Suppress("DEPRECATION")
        private fun deviceExtra(intent: Intent): BluetoothDevice? =
            if (Build.VERSION.SDK_INT >= 33) {
                intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
            } else {
                intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
            }
    }
}
