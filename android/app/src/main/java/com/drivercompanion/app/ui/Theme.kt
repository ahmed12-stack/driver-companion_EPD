package com.drivercompanion.app.ui

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.drivercompanion.app.R

/**
 * Direction: steady, attentive, plain-spoken. The world is a car's instrument
 * cluster at night: neutral graphite surfaces, and one accent, the amber of
 * road markings and gauge needles, used rarely (the route, the selection).
 */
object Dash {
    // Graphite ramp (dark)
    val Deep = Color(0xFF0F0F10)
    val Background = Color(0xFF141415)
    val Panel = Color(0xFF19191B)
    val Raised = Color(0xFF232325)
    val Text = Color(0xFFEDEDED)
    val Secondary = Color(0xFFA1A1A6)
    val Muted = Color(0xFF85858B)
    val Line = Text.copy(alpha = 0.08f)          // hairline
    val LineStrong = Text.copy(alpha = 0.14f)

    // The one accent: road-marking amber
    val Amber = Color(0xFFF2B33D)
    val OnAmber = Color(0xFF141415)

    // Meaning only
    val Good = Color(0xFF4CC38A)
    val Bad = Color(0xFFE5484D)

    // Signature: the speed-limit roundel
    val SignWhite = Color(0xFFF4F4F2)
    val SignRed = Color(0xFFD7373F)
    val SignInk = Color(0xFF141415)
}

/** One easing curve for every motion. */
val DashEase = CubicBezierEasing(0.22f, 1f, 0.36f, 1f)

val PlexSans = FontFamily(
    Font(R.font.ibm_plex_sans_regular, FontWeight.Normal),
    Font(R.font.ibm_plex_sans_medium, FontWeight.Medium),
    Font(R.font.ibm_plex_sans_semibold, FontWeight.SemiBold),
)

val PlexMono = FontFamily(
    Font(R.font.ibm_plex_mono_regular, FontWeight.Normal),
    Font(R.font.ibm_plex_mono_medium, FontWeight.Medium),
)

private val colors = darkColorScheme(
    primary = Dash.Text,
    onPrimary = Dash.Background,
    primaryContainer = Dash.Raised,
    onPrimaryContainer = Dash.Text,
    secondary = Dash.Amber,
    onSecondary = Dash.OnAmber,
    background = Dash.Background,
    onBackground = Dash.Text,
    surface = Dash.Panel,
    onSurface = Dash.Text,
    surfaceVariant = Dash.Raised,
    onSurfaceVariant = Dash.Secondary,
    surfaceContainer = Dash.Panel,
    surfaceContainerHigh = Dash.Raised,
    surfaceContainerHighest = Dash.Raised,
    outline = Dash.LineStrong,
    outlineVariant = Dash.Line,
    error = Dash.Bad,
    onError = Dash.Background,
)

private fun sans(size: Int, line: Int, weight: FontWeight = FontWeight.Normal) =
    TextStyle(fontFamily = PlexSans, fontSize = size.sp, lineHeight = line.sp, fontWeight = weight)

private val type = Typography(
    headlineMedium = sans(28, 34, FontWeight.Medium),   // page title
    headlineSmall = sans(22, 28, FontWeight.Medium),
    titleLarge = sans(20, 26, FontWeight.Medium),
    titleMedium = sans(16, 22, FontWeight.Medium),
    titleSmall = sans(14, 20, FontWeight.Medium),
    bodyLarge = sans(16, 24),
    bodyMedium = sans(14, 20),
    bodySmall = sans(13, 18),
    labelLarge = sans(15, 20, FontWeight.Medium),       // buttons
    labelMedium = sans(13, 18, FontWeight.Medium),
    labelSmall = sans(12, 16),
)

/** Figures: mono, fixed-width digits, like an instrument cluster. */
object Figures {
    val big = TextStyle(fontFamily = PlexMono, fontSize = 32.sp, lineHeight = 36.sp,
        fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum")
    val medium = TextStyle(fontFamily = PlexMono, fontSize = 20.sp, lineHeight = 24.sp,
        fontWeight = FontWeight.Medium, fontFeatureSettings = "tnum")
    val small = TextStyle(fontFamily = PlexMono, fontSize = 13.sp, lineHeight = 18.sp,
        fontFeatureSettings = "tnum")
    val meta = TextStyle(fontFamily = PlexMono, fontSize = 12.sp, lineHeight = 16.sp,
        fontFeatureSettings = "tnum")
}

@Composable
fun DriverTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = colors, typography = type, content = content)
}
