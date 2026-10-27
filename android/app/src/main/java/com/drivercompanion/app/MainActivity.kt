package com.drivercompanion.app

import android.content.Context
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
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

class MainActivity : ComponentActivity() {
    /** Bumped on every resume so permission checks refresh after returning from Settings. */
    private var resumeCount by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
                Surface(Modifier.fillMaxSize()) {
                    HomeScreen(resumeCount)
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
private fun HomeScreen(resumeCount: Int) {
    val context = LocalContext.current
    val settings = remember { AppSettings(context) }
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

    // Reconnect automatically to a device paired earlier.
    LaunchedEffect(perms.bluetooth) {
        if (perms.bluetooth && settings.deviceAddress != null && LinkHub.link == null) {
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

            if (status.state == LinkState.READY) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { LinkHub.link?.sendTest() }) { Text("Send test alert") }
                    OutlinedButton(onClick = { LinkService.send(context, LinkService.ACTION_STOP) }) {
                        Text("Disconnect")
                    }
                }
            } else if (hasDevice) {
                Button(
                    onClick = { LinkService.send(context, LinkService.ACTION_START) },
                    enabled = bluetoothAllowed,
                ) { Text("Reconnect") }
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
