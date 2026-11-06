package com.drivercompanion.app.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

// Radius by size: controls 10, cards and panels 16; inner = outer - padding.
val PanelShape = RoundedCornerShape(16.dp)
val ControlShape = RoundedCornerShape(10.dp)
val SmallShape = RoundedCornerShape(6.dp)

/** A raised panel with a hairline: every section of the app sits on one. */
@Composable
fun Panel(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = PanelShape,
        color = Dash.Panel,
        border = BorderStroke(1.dp, Dash.Line),
    ) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp), content = content)
    }
}

/** Section title in sentence case, sitting just above its content. */
@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    Text(text, style = MaterialTheme.typography.titleSmall, color = Dash.Secondary, modifier = modifier)
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    Button(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(44.dp),
        shape = ControlShape,
        contentPadding = PaddingValues(horizontal = 20.dp),
        colors = ButtonDefaults.buttonColors(
            containerColor = Dash.Text, contentColor = Dash.Background,
            disabledContainerColor = Dash.Raised, disabledContentColor = Dash.Muted,
        ),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

@Composable
fun SecondaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true) {
    OutlinedButton(
        onClick = onClick,
        enabled = enabled,
        modifier = modifier.height(44.dp),
        shape = ControlShape,
        contentPadding = PaddingValues(horizontal = 20.dp),
        border = BorderStroke(1.dp, Dash.LineStrong),
        colors = ButtonDefaults.outlinedButtonColors(containerColor = Dash.Raised, contentColor = Dash.Text),
    ) { Text(text, style = MaterialTheme.typography.labelLarge) }
}

/** A quiet text action, e.g. "Pair new" or "See all". */
@Composable
fun TextAction(text: String, onClick: () -> Unit, enabled: Boolean = true) {
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = if (enabled) Dash.Text else Dash.Muted,
        modifier = Modifier
            .clip(SmallShape)
            .clickable(enabled = enabled, onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 8.dp),
    )
}

@Composable
fun DashSwitch(checked: Boolean, onChange: (Boolean) -> Unit) {
    Switch(
        checked = checked,
        onCheckedChange = onChange,
        colors = SwitchDefaults.colors(
            checkedThumbColor = Dash.Background,
            checkedTrackColor = Dash.Text,
            checkedBorderColor = Dash.Text,
            uncheckedThumbColor = Dash.Muted,
            uncheckedTrackColor = Dash.Raised,
            uncheckedBorderColor = Dash.LineStrong,
        ),
    )
}

/** A small flat status dot. No glow, no pulse. */
@Composable
fun StatusDot(color: Color, size: Dp = 8.dp) {
    val c by animateColorAsState(color, tween(180, easing = DashEase), label = "dot")
    Box(
        Modifier
            .size(size)
            .clip(CircleShape)
            .background(c),
    )
}

/** Line icons drawn in one style: 18 dp box, 1.5 dp stroke, text-role colour. */
enum class Glyph { Back, Forward, Check }

@Composable
fun LineIcon(glyph: Glyph, color: Color = Dash.Secondary, size: Dp = 18.dp) {
    Canvas(Modifier.size(size)) {
        val s = this.size.minDimension / 24f   // lucide's 24-unit grid
        val p = Path()
        when (glyph) {
            Glyph.Back -> { p.moveTo(15 * s, 18 * s); p.lineTo(9 * s, 12 * s); p.lineTo(15 * s, 6 * s) }
            Glyph.Forward -> { p.moveTo(9 * s, 18 * s); p.lineTo(15 * s, 12 * s); p.lineTo(9 * s, 6 * s) }
            Glyph.Check -> { p.moveTo(20 * s, 6 * s); p.lineTo(9 * s, 17 * s); p.lineTo(4 * s, 12 * s) }
        }
        drawPath(p, color, style = Stroke(1.5.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
    }
}

/**
 * Signature: speed shown the way the road shows it, a white disc with a red
 * ring and the number in the middle. Used for speeds only, nowhere else.
 */
@Composable
fun SpeedRoundel(kmh: Int, size: Dp = 40.dp) {
    val ring = size * 0.12f
    val digits = kmh.toString().length
    val textSize = with(LocalDensity.current) { (size * if (digits >= 3) 0.30f else 0.38f).toSp() }
    Box(Modifier.size(size), contentAlignment = Alignment.Center) {
        Canvas(Modifier.size(size)) {
            val r = this.size.minDimension / 2
            drawCircle(Dash.SignWhite, radius = r)
            drawCircle(Dash.SignRed, radius = r - ring.toPx() / 2, style = Stroke(ring.toPx()))
        }
        Text(
            "$kmh",
            style = Figures.medium.copy(fontSize = textSize, lineHeight = textSize),
            color = Dash.SignInk,
        )
    }
}

/** A row of choices where exactly one is selected, like a car's drive-mode switch. */
@Composable
fun Segmented(options: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(ControlShape)
            .background(Dash.Deep)
            .border(1.dp, Dash.Line, ControlShape)
            .padding(4.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        options.forEachIndexed { i, label ->
            val on = i == selected
            val bg by animateColorAsState(if (on) Dash.Raised else Color.Transparent, tween(160, easing = DashEase), label = "seg")
            Box(
                Modifier
                    .weight(1f)
                    .clip(RoundedCornerShape(6.dp))
                    .background(bg)
                    .border(1.dp, if (on) Dash.LineStrong else Color.Transparent, RoundedCornerShape(6.dp))
                    .clickable { onSelect(i) }
                    .padding(vertical = 8.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    label,
                    style = MaterialTheme.typography.labelMedium,
                    color = if (on) Dash.Text else Dash.Muted,
                )
            }
        }
    }
}

/** An app the device may announce. On = raised surface, amber selection dot. */
@Composable
fun ToggleChip(label: String, on: Boolean, modifier: Modifier = Modifier, onChange: (Boolean) -> Unit) {
    val bg by animateColorAsState(if (on) Dash.Raised else Dash.Deep, tween(160, easing = DashEase), label = "chip")
    Row(
        modifier
            .height(40.dp)
            .clip(ControlShape)
            .background(bg)
            .border(1.dp, if (on) Dash.LineStrong else Dash.Line, ControlShape)
            .clickable { onChange(!on) }
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        StatusDot(if (on) Dash.Amber else Dash.Raised)
        Spacer8()
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            fontWeight = if (on) FontWeight.Medium else FontWeight.Normal,
            color = if (on) Dash.Text else Dash.Muted,
            maxLines = 1,
        )
    }
}

@Composable
fun Spacer8() {
    Box(Modifier.width(8.dp))
}
