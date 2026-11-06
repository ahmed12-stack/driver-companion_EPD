package com.drivercompanion.app

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.AtomicFile
import android.util.Log
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.util.concurrent.CopyOnWriteArraySet

/** One finished trip, as the device sends it on Trip Out. */
data class Trip(
    val id: String,
    val start: Long,
    val end: Long,
    val km: Double,
    val minutes: Int,
    val movingMin: Int,
    val maxKmh: Int,
    val avgKmh: Int,
    val stops: Int,
    val overspeed: Int,
    val overspeedS: Int,
    val harshBrake: Int,
    val harshAccel: Int,
    val signs: Int,
    val ended: String,
    val summary: String,
    val summaryBy: String,
    /** The path, as the device's encoded polyline; empty for trips without one. */
    val route: String = "",
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id).put("start", start).put("end", end).put("km", km)
        .put("min", minutes).put("moving_min", movingMin).put("max_kmh", maxKmh)
        .put("avg_kmh", avgKmh).put("stops", stops).put("overspeed", overspeed)
        .put("overspeed_s", overspeedS).put("harsh_brake", harshBrake)
        .put("harsh_accel", harshAccel).put("signs", signs).put("ended", ended)
        .put("summary", summary).put("summary_by", summaryBy).put("route", route)

    companion object {
        private const val MAX_ROUTE_CHARS = 16 * 1024

        /** Null if the message is not a usable trip. */
        fun fromJson(o: JSONObject): Trip? {
            val id = o.optString("id")
            val start = o.optLong("start", 0)
            if (id.isEmpty() || start <= 0) return null
            return Trip(
                id = id,
                start = start,
                end = o.optLong("end", start),
                km = o.optDouble("km", 0.0).takeIf { !it.isNaN() } ?: 0.0,
                minutes = o.optInt("min"),
                movingMin = o.optInt("moving_min"),
                maxKmh = o.optInt("max_kmh"),
                avgKmh = o.optInt("avg_kmh"),
                stops = o.optInt("stops"),
                overspeed = o.optInt("overspeed"),
                overspeedS = o.optInt("overspeed_s"),
                harshBrake = o.optInt("harsh_brake"),
                harshAccel = o.optInt("harsh_accel"),
                signs = o.optInt("signs"),
                ended = o.optString("ended", "ignition"),
                summary = o.optString("summary").take(1000),
                summaryBy = o.optString("summary_by", "template"),
                route = o.optString("route").take(MAX_ROUTE_CHARS),
            )
        }
    }
}

/** Trips kept on the phone, newest first. Saved so they survive the app closing. */
class TripStore private constructor(context: Context) {
    private val file = AtomicFile(File(context.filesDir, "trips.json"))
    private val trips = LinkedHashMap<String, Trip>()
    private val listeners = CopyOnWriteArraySet<() -> Unit>()
    private val main = Handler(Looper.getMainLooper())

    init {
        load()
    }

    @Synchronized
    fun all(): List<Trip> = trips.values.sortedByDescending { it.start }

    @Synchronized
    fun get(id: String): Trip? = trips[id]

    /** Returns true once the trip is safely written, so the device may forget it. */
    @Synchronized
    fun save(trip: Trip): Boolean {
        trips[trip.id] = trip
        while (trips.size > MAX_TRIPS) {
            val oldest = trips.values.minByOrNull { it.start } ?: break
            trips.remove(oldest.id)
        }
        val ok = write()
        main.post { listeners.forEach { it() } }
        return ok
    }

    fun addListener(listener: () -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: () -> Unit) {
        listeners.remove(listener)
    }

    private fun load() {
        try {
            val array = JSONArray(String(file.readFully(), Charsets.UTF_8))
            for (i in 0 until array.length()) {
                Trip.fromJson(array.getJSONObject(i))?.let { trips[it.id] = it }
            }
        } catch (e: java.io.FileNotFoundException) {
            // no trips yet
        } catch (e: Exception) {
            Log.w(TAG, "could not read saved trips", e)
        }
    }

    private fun write(): Boolean {
        val array = JSONArray()
        trips.values.forEach { array.put(it.toJson()) }
        val out = try {
            file.startWrite()
        } catch (e: java.io.IOException) {
            Log.w(TAG, "could not save trips", e)
            return false
        }
        return try {
            out.write(array.toString().toByteArray(Charsets.UTF_8))
            file.finishWrite(out)
            true
        } catch (e: Exception) {
            file.failWrite(out)
            Log.w(TAG, "could not save trips", e)
            false
        }
    }

    companion object {
        private const val TAG = "TripStore"
        private const val MAX_TRIPS = 200

        @Volatile
        private var instance: TripStore? = null

        fun get(context: Context): TripStore =
            instance ?: synchronized(this) {
                instance ?: TripStore(context.applicationContext).also { instance = it }
            }

        /** For the Bluetooth link: parse a "trip" message and keep it. */
        fun fromMessage(msg: JSONObject): Trip? = try {
            Trip.fromJson(msg)
        } catch (e: JSONException) {
            null
        }
    }
}
