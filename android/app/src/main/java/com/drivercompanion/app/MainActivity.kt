package com.drivercompanion.app

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.SystemBarStyle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import com.drivercompanion.app.ble.LinkState
import com.drivercompanion.app.ble.LinkStatus
import com.drivercompanion.app.ui.ControlShape
import com.drivercompanion.app.ui.Dash
import com.drivercompanion.app.ui.DashEase
import com.drivercompanion.app.ui.DashSwitch
import com.drivercompanion.app.ui.DriverTheme
import com.drivercompanion.app.ui.Figures
import com.drivercompanion.app.ui.Glyph
import com.drivercompanion.app.ui.LineIcon
import com.drivercompanion.app.ui.Panel
import com.drivercompanion.app.ui.PanelShape
import com.drivercompanion.app.ui.PrimaryButton
import com.drivercompanion.app.ui.RouteMap
import com.drivercompanion.app.ui.RouteSketch
import com.drivercompanion.app.ui.SecondaryButton
import com.drivercompanion.app.ui.SectionTitle
import com.drivercompanion.app.ui.Segmented
import com.drivercompanion.app.ui.SpeedRoundel
import com.drivercompanion.app.ui.StatusDot
import com.drivercompanion.app.ui.TextAction
import com.drivercompanion.app.ui.ToggleChip
import com.drivercompanion.app.ui.decodeRoute
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt
import kotlinx.coroutines.delay

private sealed interface Screen {
    /** How deep the screen is, so moving deeper slides one way and going back the other. */
    val depth: Int

    data object Home : Screen {
        override val depth = 0
    }

    data object Trips : Screen {
        override val depth = 1
    }

    data class Detail(val id: String) : Screen {
        override val depth = 2
    }
}

class MainActivity : ComponentActivity() {
    /** Bumped on every resume so permission checks refresh after returning from Settings. */
    private var resumeCount by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        setContent {
            DriverTheme {
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(Dash.Background)
                        .systemBarsPadding(),
                ) {
                    var screen by remember { mutableStateOf<Screen>(Screen.Home) }
                    BackHandler(enabled = screen != Screen.Home) {
                        screen = if (screen is Screen.Detail) Screen.Trips else Screen.Home
                    }
                    AnimatedContent(
                        targetState = screen,
                        transitionSpec = {
                            // A short fade and a small shift: enough to show direction, nothing flies
                            val dir = if (targetState.depth >= initialState.depth) 1 else -1
                            val enter = tween<IntOffset>(260, easing = DashEase)
                            (slideInHorizontally(enter) { dir * 36 } + fadeIn(tween(260, easing = DashEase))) togetherWith
                                (slideOutHorizontally(tween(180, easing = DashEase)) { -dir * 36 } + fadeOut(tween(160, easing = DashEase)))
                        },
                        label = "screens",
                    ) { s ->
                        when (s) {
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
    }

    override fun onResume() {
        super.onResume()
        resumeCount++
    }
}

private val PagePadding = PaddingValues(horizontal = 20.dp, vertical = 16.dp)

@Composable
private fun BackRow(label: String, onBack: () -> Unit) {
    Row(
        Modifier
            .clip(ControlShape)
            .clickable(onClick = onBack)
            .padding(start = 4.dp, end = 12.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        LineIcon(Glyph.Back, Dash.Secondary)
        Spacer(Modifier.width(4.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = Dash.Secondary)
    }
}

// ----- home ------------------------------------------------------------------

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
            .padding(PagePadding),
        verticalArrangement = Arrangement.spacedBy(24.dp),
    ) {
        Text(
            "Driver Companion",
            style = MaterialTheme.typography.titleMedium,
            color = Dash.Text,
            modifier = Modifier.padding(top = 8.dp, start = 4.dp),
        )
        StatusPanel(context, settings, status, perms.bluetooth)
        TripsSection(trips, onOpenTrips, onOpenTrip)
        PermissionsPanel(context, perms) { askPermissions.launch(it) }
        SettingsPanel(settings)
        Spacer(Modifier.height(8.dp))
    }
}

private fun dotOf(state: LinkState): Color = when (state) {
    LinkState.READY -> Dash.Good
    LinkState.SCANNING, LinkState.CONNECTING, LinkState.PAIRING, LinkState.SETTING_UP -> Dash.Amber
    LinkState.BLUETOOTH_OFF, LinkState.DISCONNECTED -> Dash.Bad
    LinkState.IDLE -> Dash.Muted
}

@Composable
private fun StatusPanel(context: Context, settings: AppSettings, status: LinkStatus, bluetoothAllowed: Boolean) {
    val state = status.state
    val hasDevice = settings.deviceAddress != null
    val searching = LinkHub.link != null && state != LinkState.READY && state != LinkState.IDLE

    Panel {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(dotOf(state), 10.dp)
                Spacer(Modifier.width(12.dp))
                Text(stateLabel(state), style = MaterialTheme.typography.titleLarge, color = Dash.Text)
            }
            val name = status.deviceName ?: settings.deviceName
            val meta = listOfNotNull(
                name,
                status.firmware?.takeIf { state == LinkState.READY }?.let { "software $it" },
            ).joinToString("  ·  ")
            if (meta.isNotEmpty()) {
                Text(meta, style = Figures.small, color = Dash.Muted, modifier = Modifier.padding(start = 22.dp))
            }
        }
        if (state == LinkState.READY) GpsLine()
        if (status.message != null && state != LinkState.READY) {
            Text(status.message, style = MaterialTheme.typography.bodyMedium, color = Dash.Secondary)
        }
        if (!bluetoothAllowed) {
            Text("Allow Bluetooth under Permissions first", style = MaterialTheme.typography.bodyMedium, color = Dash.Secondary)
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (state == LinkState.READY) {
                PrimaryButton("Send test alert", { LinkHub.link?.sendTest() })
                SecondaryButton("Disconnect", { LinkService.send(context, LinkService.ACTION_STOP) })
            } else {
                if (searching) {
                    SecondaryButton("Stop", { LinkService.send(context, LinkService.ACTION_STOP) })
                }
                if (hasDevice && !searching) {
                    PrimaryButton("Reconnect", { LinkService.send(context, LinkService.ACTION_START) }, enabled = bluetoothAllowed)
                }
            }
        }

        HorizontalDivider(color = Dash.Line)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                "Hold the device button for 5 s until it says “Pairing mode on”, then tap Pair and type the code it speaks",
                style = MaterialTheme.typography.bodySmall,
                color = Dash.Muted,
                modifier = Modifier.weight(1f),
            )
            Spacer(Modifier.width(12.dp))
            TextAction(
                if (hasDevice) "Pair new" else "Pair",
                { LinkService.send(context, LinkService.ACTION_PAIR) },
                enabled = bluetoothAllowed,
            )
        }
    }
}

/** Whether the phone's GPS is reaching the device, refreshed every second. */
@Composable
private fun GpsLine() {
    var fix by remember { mutableStateOf(LinkHub.lastFix) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            fix = LinkHub.lastFix
            now = System.currentTimeMillis()
            delay(1000)
        }
    }
    val f = fix
    val (text, color) = when {
        f == null -> "Phone GPS  waiting for a fix" to Dash.Amber
        now - f.atMs > 10_000 -> "Phone GPS  no fix for ${(now - f.atMs) / 1000} s" to Dash.Amber
        f.accuracyM > 30 -> "Phone GPS  weak, ±${f.accuracyM.roundToInt()} m" to Dash.Amber
        else -> "Phone GPS  ±${f.accuracyM.roundToInt()} m  ·  ${f.kmh.roundToInt()} km/h" to Dash.Good
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        StatusDot(color, 6.dp)
        Spacer(Modifier.width(8.dp))
        Text(text, style = Figures.small, color = Dash.Secondary)
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

// ----- permissions and settings -------------------------------------------

@Composable
private fun PermissionsPanel(context: Context, perms: PermissionState, request: (Array<String>) -> Unit) {
    val allGranted = perms.bluetooth && perms.notificationAccess && perms.location &&
        (!Permissions.needsPostNotifications() || perms.postNotifications)
    if (allGranted) {
        Row(Modifier.padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            LineIcon(Glyph.Check, Dash.Good, 16.dp)
            Spacer(Modifier.width(8.dp))
            Text("All permissions allowed", style = MaterialTheme.typography.bodyMedium, color = Dash.Muted)
        }
        return
    }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionTitle("Permissions needed", Modifier.padding(start = 4.dp))
        Panel {
            PermissionRow("Bluetooth", "Find and connect to the device", perms.bluetooth) {
                request(Permissions.bluetooth())
            }
            PermissionRow("Notification access", "Announce messages, SMS and calls", perms.notificationAccess) {
                context.startActivity(Permissions.notificationAccessIntent())
            }
            PermissionRow("Location", "Speed and the route of each trip", perms.location) {
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
            Text(title, style = MaterialTheme.typography.bodyLarge, color = Dash.Text)
            Text(why, style = MaterialTheme.typography.bodySmall, color = Dash.Muted)
        }
        Spacer(Modifier.width(12.dp))
        if (granted) {
            LineIcon(Glyph.Check, Dash.Good)
        } else {
            SecondaryButton("Allow", onAllow)
        }
    }
}

@Composable
private fun SettingsPanel(settings: AppSettings) {
    var volume by remember { mutableIntStateOf(settings.volume) }
    var readText by remember { mutableStateOf(settings.readText) }
    var quiet by remember { mutableStateOf(settings.quiet) }
    var calls by remember { mutableStateOf(settings.forwardCalls) }
    var sms by remember { mutableStateOf(settings.forwardSms) }
    var apps by remember { mutableStateOf(settings.apps) }

    fun pushToDevice() {
        LinkHub.link?.sendSettings()
    }

    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionTitle("Announcements", Modifier.padding(start = 4.dp))
        Panel {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Volume", style = MaterialTheme.typography.bodyLarge, color = Dash.Text)
                Segmented(listOf("Low", "Medium", "High"), volume) { level ->
                    volume = level
                    settings.volume = level
                    pushToDevice()
                }
            }
            SwitchRow("Read message text", "Off: only the app and sender are spoken", readText) {
                readText = it
                settings.readText = it
                pushToDevice()
            }
            SwitchRow("Quiet mode", "Road alerts only, no messages or calls", quiet) {
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

            HorizontalDivider(color = Dash.Line)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Apps to announce", style = MaterialTheme.typography.bodyLarge, color = Dash.Text)
                KnownApps.list.chunked(2).forEach { pair ->
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        pair.forEach { app ->
                            ToggleChip(app.label, app.pkg in apps, Modifier.weight(1f)) { on ->
                                apps = if (on) apps + app.pkg else apps - app.pkg
                                settings.apps = apps
                                pushToDevice()
                            }
                        }
                        if (pair.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
            }
        }
    }
}

@Composable
private fun SwitchRow(title: String, subtitle: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge, color = Dash.Text)
            if (subtitle != null) {
                Text(subtitle, style = MaterialTheme.typography.bodySmall, color = Dash.Muted)
            }
        }
        Spacer(Modifier.width(12.dp))
        DashSwitch(checked, onChange)
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

private fun format(pattern: String, epochSeconds: Long): String =
    DateTimeFormatter.ofPattern(pattern, Locale.getDefault())
        .format(Instant.ofEpochSecond(epochSeconds).atZone(ZoneId.systemDefault()))

private fun formatKm(km: Double): String = String.format(Locale.getDefault(), "%.1f", km)

private fun plural(n: Int, word: String): String = if (n == 1) "$n $word" else "$n ${word}s"

@Composable
private fun TripsSection(trips: List<Trip>, onOpenTrips: () -> Unit, onOpenTrip: (String) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.padding(start = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            SectionTitle("Recent trips", Modifier.weight(1f))
            if (trips.size > 3) TextAction("See all ${trips.size}", onOpenTrips)
        }
        if (trips.isEmpty()) {
            Panel {
                Text("No trips yet", style = MaterialTheme.typography.titleMedium, color = Dash.Text)
                Text(
                    "Each trip appears here with its route after the car is switched off",
                    style = MaterialTheme.typography.bodySmall,
                    color = Dash.Muted,
                )
            }
        }
        trips.take(3).forEach { trip -> TripCard(trip) { onOpenTrip(trip.id) } }
    }
}

@Composable
private fun TripCard(trip: Trip, onClick: () -> Unit) {
    val points = remember(trip.route) { decodeRoute(trip.route) }
    Row(
        Modifier
            .fillMaxWidth()
            .clip(PanelShape)
            .background(Dash.Panel)
            .border(1.dp, Dash.Line, PanelShape)
            .clickable(onClick = onClick)
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (points.size >= 2) {
            Box(
                Modifier
                    .size(56.dp)
                    .clip(ControlShape)
                    .background(Dash.Deep),
            ) {
                RouteSketch(points, Modifier.fillMaxSize())
            }
            Spacer(Modifier.width(16.dp))
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(format("EEE d MMM, h:mm a", trip.start), style = MaterialTheme.typography.titleSmall, color = Dash.Text)
            Text(
                "${formatKm(trip.km)} km  ·  ${trip.minutes} min",
                style = Figures.small,
                color = Dash.Secondary,
            )
            if (trip.overspeed > 0) {
                Text(plural(trip.overspeed, "overspeed warning"), style = MaterialTheme.typography.bodySmall, color = Dash.Bad)
            }
        }
        Spacer(Modifier.width(12.dp))
        SpeedRoundel(trip.maxKmh, 36.dp)
        Spacer(Modifier.width(8.dp))
        LineIcon(Glyph.Forward, Dash.Muted, 16.dp)
    }
}

@Composable
private fun TripsScreen(onBack: () -> Unit, onOpenTrip: (String) -> Unit) {
    val trips = rememberTrips()
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PagePadding,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item { BackRow("Home", onBack) }
        item {
            Text(
                "Trips",
                style = MaterialTheme.typography.headlineMedium,
                color = Dash.Text,
                modifier = Modifier.padding(start = 4.dp, top = 8.dp, bottom = 16.dp),
            )
        }
        if (trips.isEmpty()) {
            item { Text("No trips yet", style = MaterialTheme.typography.bodyMedium, color = Dash.Muted, modifier = Modifier.padding(start = 4.dp)) }
        }
        items(trips, key = { it.id }) { trip -> TripCard(trip) { onOpenTrip(trip.id) } }
    }
}

@Composable
private fun TripDetailScreen(id: String, onBack: () -> Unit) {
    val trip = rememberTrips().firstOrNull { it.id == id }
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(PagePadding),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        BackRow("Trips", onBack)
        if (trip == null) {
            Text("This trip is no longer saved", style = MaterialTheme.typography.bodyMedium, color = Dash.Muted)
            return@Column
        }
        Column(Modifier.padding(horizontal = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(format("EEEE d MMMM yyyy", trip.start), style = MaterialTheme.typography.bodyMedium, color = Dash.Secondary)
            Text(
                "${format("h:mm a", trip.start)} – ${format("h:mm a", trip.end)}",
                style = MaterialTheme.typography.headlineMedium,
                color = Dash.Text,
            )
        }

        // Moment 1: the road travelled
        val points = remember(trip.route) { decodeRoute(trip.route) }
        if (points.size >= 2) {
            RouteMap(
                points,
                Modifier
                    .fillMaxWidth()
                    .height(260.dp)
                    .clip(PanelShape)
                    .border(1.dp, Dash.Line, PanelShape),
            )
        }

        // Moment 2: the trip read like an instrument cluster
        Panel {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Figure(formatKm(trip.km), "km", "Distance", Modifier.weight(1f))
                Figure("${trip.minutes}", "min", "Trip time", Modifier.weight(1f))
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    SpeedRoundel(trip.maxKmh, 56.dp)
                    Text("Top speed", style = MaterialTheme.typography.bodySmall, color = Dash.Muted)
                }
            }
            HorizontalDivider(color = Dash.Line)
            Text(
                listOfNotNull(
                    trip.avgKmh.takeIf { it > 0 }?.let { "average $it km/h" },
                    "driving ${trip.movingMin} min",
                    plural(trip.stops, "stop"),
                ).joinToString("  ·  "),
                style = Figures.small,
                color = Dash.Secondary,
            )
        }

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SectionTitle("Summary", Modifier.padding(start = 4.dp))
            Panel {
                Text(trip.summary, style = MaterialTheme.typography.bodyLarge, color = Dash.Text)
                if (trip.summaryBy == "llm") {
                    Text("Last sentence by the on-device AI; the numbers come from the trip log", style = Figures.meta, color = Dash.Muted)
                }
                if (trip.ended == "power_loss") {
                    Text(
                        "This trip ended with a power cut, so its last minute may be missing",
                        style = MaterialTheme.typography.bodySmall,
                        color = Dash.Secondary,
                    )
                }
            }
        }

        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            SectionTitle("Details", Modifier.padding(start = 4.dp))
            Panel {
                StatRow("Overspeed warnings", "${trip.overspeed}", warn = trip.overspeed > 0)
                if (trip.overspeedS > 0) StatRow("Time over the limit", "${trip.overspeedS} s", warn = true)
                StatRow("Road signs read", "${trip.signs}")
                StatRow("Stops along the way", "${trip.stops}")
                // No motion sensor yet: say so instead of showing a reassuring zero
                StatRow("Harsh braking", if (trip.harshBrake > 0) "${trip.harshBrake}" else "Not measured yet", warn = trip.harshBrake > 0, quietValue = trip.harshBrake == 0)
                StatRow("Harsh acceleration", if (trip.harshAccel > 0) "${trip.harshAccel}" else "Not measured yet", warn = trip.harshAccel > 0, quietValue = trip.harshAccel == 0)
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun Figure(value: String, unit: String, label: String, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.Bottom) {
            Text(value, style = Figures.big, color = Dash.Text)
            Spacer(Modifier.width(4.dp))
            Text(unit, style = Figures.small, color = Dash.Muted, modifier = Modifier.padding(bottom = 4.dp))
        }
        Text(label, style = MaterialTheme.typography.bodySmall, color = Dash.Muted)
    }
}

@Composable
private fun StatRow(label: String, value: String, warn: Boolean = false, quietValue: Boolean = false) {
    val color by animateColorAsState(
        when {
            warn -> Dash.Bad
            quietValue -> Dash.Muted
            else -> Dash.Text
        },
        tween(160, easing = DashEase),
        label = "stat",
    )
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = Dash.Secondary, modifier = Modifier.weight(1f))
        Text(value, style = if (quietValue) MaterialTheme.typography.bodySmall else Figures.small.copy(fontSize = MaterialTheme.typography.bodyMedium.fontSize), color = color)
    }
}
