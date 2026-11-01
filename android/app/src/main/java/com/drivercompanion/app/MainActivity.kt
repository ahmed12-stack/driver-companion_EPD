package com.drivercompanion.app

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.drivercompanion.app.ble.LinkState
import com.drivercompanion.app.ble.LinkStatus
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private sealed interface Screen {
    data object Home : Screen
    data object Trips : Screen
    data class Detail(val id: String) : Screen
}

class MainActivity : ComponentActivity() {
    /** Bumped on every resume so permission checks refresh after returning from Settings. */
    private var resumeCount by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
                Surface(Modifier.fillMaxSize()) {
                    var screen by remember { mutableStateOf<Screen>(Screen.Home) }
                    BackHandler(enabled = screen != Screen.Home) {
                        screen = if (screen is Screen.Detail) Screen.Trips else Screen.Home
                    }
                    when (val s = screen) {
                        Screen.Home -> HomeScreen(
                            resumeCount,
                            onOpenTrips = { screen = Screen.Trips },
                            onOpenTrip = { screen = Screen.Detail(it) },
                        )
                        Screen.Trips -> TripsScreen(
                            onBack = { screen = Screen.Home },
                            onOpenTrip = { screen = Screen.Detail(it) },
                        )
                        is Screen.Detail -> TripDetailScreen(s.id, onBack = { screen = Screen.Trips })
                    }
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        resumeCount++
    }
}

@Composable
private fun HomeScreen(resumeCount: Int, onOpenTrips: () -> Unit, onOpenTrip: (String) -> Unit) {
    val context = LocalContext.current
    val settings = remember { AppSettings(context) }
    val trips = rememberTrips()
    var status by remember { mutableStateOf(LinkHub.status) }
    var permissionResults by remember { mutableIntStateOf(0) }
    val perms = remember(resumeCount, permissionResults) { Permissions.check(context) }
    val askPermissions = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions(),
    ) { permissionResults++ }

    DisposableEffect(Unit) {
        val listener: (LinkStatus) -> Unit = { status = it }
        LinkHub.addListener(listener)
        status = LinkHub.status
        onDispose { LinkHub.removeListener(listener) }
    }

    // Reconnect automatically to a device paired earlier, unless the user tapped Stop.
    LaunchedEffect(perms.bluetooth) {
        if (perms.bluetooth && settings.deviceAddress != null && settings.autoConnect &&
            LinkHub.link == null
        ) {
            LinkService.send(context, LinkService.ACTION_START)
        }
    }

    // Location allowed while already connected: tell the service to start phone GPS.
    LaunchedEffect(perms.location) {
        if (perms.location && LinkHub.link?.isReady == true) {
            LinkService.send(context, LinkService.ACTION_START)
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            "Driver Companion",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.SemiBold,
        )
        ConnectionCard(context, settings, status, perms.bluetooth)
        TripsCard(trips, onOpenTrips, onOpenTrip)
        PermissionsCard(context, perms) { askPermissions.launch(it) }
        SettingsCard(settings)
    }
}

@Composable
private fun ConnectionCard(context: Context, settings: AppSettings, status: LinkStatus, bluetoothAllowed: Boolean) {
    val hasDevice = settings.deviceAddress != null
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Device", style = MaterialTheme.typography.titleMedium)
            Text(stateLabel(status.state), style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.SemiBold)
            val name = status.deviceName ?: settings.deviceName
            if (name != null) Text(name)
            if (status.firmware != null && status.state == LinkState.READY) {
                Text("Device software ${status.firmware}", style = MaterialTheme.typography.bodySmall)
            }
            if (status.message != null && status.state != LinkState.READY) {
                Text(status.message, style = MaterialTheme.typography.bodyMedium)
            }
            if (!bluetoothAllowed) {
                Text("Allow Bluetooth in Permissions below first.", color = MaterialTheme.colorScheme.error)
            }

            val state = status.state
            val searching = LinkHub.link != null && state != LinkState.READY && state != LinkState.IDLE
            if (state == LinkState.READY) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { LinkHub.link?.sendTest() }) { Text("Send test alert") }
                    OutlinedButton(onClick = { LinkService.send(context, LinkService.ACTION_STOP) }) {
                        Text("Disconnect")
                    }
                }
            } else {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    if (searching) {
                        OutlinedButton(onClick = { LinkService.send(context, LinkService.ACTION_STOP) }) {
                            Text("Stop")
                        }
                    }
                    if (hasDevice && !searching) {
                        Button(
                            onClick = { LinkService.send(context, LinkService.ACTION_START) },
                            enabled = bluetoothAllowed,
                        ) { Text("Reconnect") }
                    }
                }
            }

            OutlinedButton(
                onClick = { LinkService.send(context, LinkService.ACTION_PAIR) },
                enabled = bluetoothAllowed,
            ) { Text(if (hasDevice) "Pair a different device" else "Pair a device") }

            Text(
                "To pair: hold the device button for 5 seconds until it says \"Pairing mode on\", " +
                    "then tap Pair. Type the 6-digit code it speaks into the box Android shows.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun stateLabel(state: LinkState): String = when (state) {
    LinkState.IDLE -> "Not connected"
    LinkState.BLUETOOTH_OFF -> "Bluetooth is off"
    LinkState.SCANNING -> "Searching…"
    LinkState.CONNECTING -> "Connecting…"
    LinkState.PAIRING -> "Pairing…"
    LinkState.SETTING_UP -> "Setting up…"
    LinkState.READY -> "Connected"
    LinkState.DISCONNECTED -> "Disconnected"
}

@Composable
private fun PermissionsCard(context: Context, perms: PermissionState, request: (Array<String>) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Permissions", style = MaterialTheme.typography.titleMedium)
            PermissionRow("Bluetooth", "Find and connect to the device", perms.bluetooth) {
                request(Permissions.bluetooth())
            }
            PermissionRow("Notification access", "Announce messages, SMS and calls", perms.notificationAccess) {
                context.startActivity(Permissions.notificationAccessIntent())
            }
            PermissionRow("Location", "Phone GPS as a backup for the device", perms.location) {
                request(Permissions.location)
            }
            if (Permissions.needsPostNotifications()) {
                PermissionRow("Notifications", "Show the connection status", perms.postNotifications) {
                    request(Permissions.postNotifications())
                }
            }
        }
    }
}

@Composable
private fun PermissionRow(title: String, why: String, granted: Boolean, onAllow: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title)
            Text(why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        if (granted) {
            Text("Allowed", color = MaterialTheme.colorScheme.primary)
        } else {
            Button(onClick = onAllow) { Text("Allow") }
        }
    }
}

@Composable
private fun SettingsCard(settings: AppSettings) {
    var volume by remember { mutableIntStateOf(settings.volume) }
    var readText by remember { mutableStateOf(settings.readText) }
    var quiet by remember { mutableStateOf(settings.quiet) }
    var calls by remember { mutableStateOf(settings.forwardCalls) }
    var sms by remember { mutableStateOf(settings.forwardSms) }
    var apps by remember { mutableStateOf(settings.apps) }

    fun pushToDevice() {
        LinkHub.link?.sendSettings()
    }

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Announcements", style = MaterialTheme.typography.titleMedium)

            Text("Volume")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("Low", "Medium", "High").forEachIndexed { level, label ->
                    if (level == volume) {
                        Button(onClick = {}) { Text(label) }
                    } else {
                        OutlinedButton(onClick = {
                            volume = level
                            settings.volume = level
                            pushToDevice()
                        }) { Text(label) }
                    }
                }
            }

            SwitchRow("Read message text", "Off: only the app and sender are spoken", readText) {
                readText = it
                settings.readText = it
                pushToDevice()
            }
            SwitchRow("Quiet mode", "Road alerts only; no messages or calls", quiet) {
                quiet = it
                settings.quiet = it
                pushToDevice()
            }
            SwitchRow("Incoming calls", null, calls) {
                calls = it
                settings.forwardCalls = it
            }
            SwitchRow("SMS", "From your default messages app", sms) {
                sms = it
                settings.forwardSms = it
            }

            HorizontalDivider()
            Text("Apps to announce")
            KnownApps.list.forEach { app ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(
                        checked = app.pkg in apps,
                        onCheckedChange = { on ->
                            apps = if (on) apps + app.pkg else apps - app.pkg
                            settings.apps = apps
                            pushToDevice()
                        },
                    )
                    Text(app.label)
                }
            }
        }
    }
}

@Composable
private fun SwitchRow(title: String, subtitle: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title)
            if (subtitle != null) {
                Text(
                    subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

// ----- trips -------------------------------------------------------------

/** The saved trips, newest first; updates when the device sends a new one. */
@Composable
private fun rememberTrips(): List<Trip> {
    val context = LocalContext.current
    val store = remember { TripStore.get(context) }
    var trips by remember { mutableStateOf(store.all()) }
    DisposableEffect(store) {
        val listener: () -> Unit = { trips = store.all() }
        store.addListener(listener)
        trips = store.all()
        onDispose { store.removeListener(listener) }
    }
    return trips
}

private val dateFormat = DateTimeFormatter.ofPattern("EEE d MMM yyyy, h:mm a", Locale.getDefault())

private fun formatDate(epochSeconds: Long): String =
    dateFormat.format(Instant.ofEpochSecond(epochSeconds).atZone(ZoneId.systemDefault()))

private fun formatKm(km: Double): String = String.format(Locale.getDefault(), "%.1f", km)

private fun plural(n: Int, word: String): String = if (n == 1) "$n $word" else "$n ${word}s"

@Composable
private fun TripsCard(trips: List<Trip>, onOpenTrips: () -> Unit, onOpenTrip: (String) -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Trips", style = MaterialTheme.typography.titleMedium)
            if (trips.isEmpty()) {
                Text(
                    "No trips yet. Each trip appears here after the car is switched off.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            trips.take(3).forEach { trip -> TripRow(trip) { onOpenTrip(trip.id) } }
            if (trips.size > 3) {
                TextButton(onClick = onOpenTrips) { Text("See all ${trips.size} trips") }
            }
        }
    }
}

@Composable
private fun TripRow(trip: Trip, onClick: () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(vertical = 8.dp),
    ) {
        Text(formatDate(trip.start), fontWeight = FontWeight.SemiBold)
        Text(
            "${formatKm(trip.km)} km · ${trip.minutes} min · top ${trip.maxKmh} km/h",
            style = MaterialTheme.typography.bodyMedium,
        )
        if (trip.overspeed > 0) {
            Text(
                plural(trip.overspeed, "overspeed warning"),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun TripsScreen(onBack: () -> Unit, onOpenTrip: (String) -> Unit) {
    val trips = rememberTrips()
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        TextButton(onClick = onBack) { Text("Back") }
        Text("All trips", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
        if (trips.isEmpty()) {
            Text("No trips yet.", modifier = Modifier.padding(top = 16.dp))
        }
        trips.forEachIndexed { i, trip ->
            if (i > 0) HorizontalDivider()
            TripRow(trip) { onOpenTrip(trip.id) }
        }
    }
}

@Composable
private fun TripDetailScreen(id: String, onBack: () -> Unit) {
    val trip = rememberTrips().firstOrNull { it.id == id }
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        TextButton(onClick = onBack) { Text("Back") }
        if (trip == null) {
            Text("This trip is no longer saved.")
            return@Column
        }
        Text(formatDate(trip.start), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Summary", style = MaterialTheme.typography.titleMedium)
                Text(trip.summary, style = MaterialTheme.typography.bodyLarge)
                Text(
                    if (trip.summaryBy == "llm") "Last sentence written by the on-device AI"
                    else "Standard summary",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (trip.ended == "power_loss") {
                    Text(
                        "This trip ended with a power cut, so its last minute may be missing.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Details", style = MaterialTheme.typography.titleMedium)
                StatRow("Distance", "${formatKm(trip.km)} km")
                StatRow("Trip time", plural(trip.minutes, "minute"))
                StatRow("Driving time", plural(trip.movingMin, "minute"))
                StatRow("Top speed", "${trip.maxKmh} km/h")
                if (trip.avgKmh > 0) StatRow("Average speed while moving", "${trip.avgKmh} km/h")
                StatRow("Stops along the way", "${trip.stops}")
                StatRow("Overspeed warnings", "${trip.overspeed}", warn = trip.overspeed > 0)
                if (trip.overspeedS > 0) StatRow("Time over the limit", "${trip.overspeedS} s", warn = true)
                StatRow("Harsh braking", "${trip.harshBrake}", warn = trip.harshBrake > 0)
                StatRow("Harsh acceleration", "${trip.harshAccel}", warn = trip.harshAccel > 0)
                StatRow("Road signs announced", "${trip.signs}")
            }
        }
    }
}

@Composable
private fun StatRow(label: String, value: String, warn: Boolean = false) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, modifier = Modifier.weight(1f))
        Text(
            value,
            fontWeight = FontWeight.SemiBold,
            color = if (warn) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface,
        )
    }
}
