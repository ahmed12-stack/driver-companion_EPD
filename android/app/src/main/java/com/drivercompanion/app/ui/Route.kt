package com.drivercompanion.app.ui

import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.unit.dp
import kotlin.math.cos
import kotlin.math.max

data class RoutePoint(val lat: Double, val lon: Double)

/** Decodes the trip path the device sends (encoded polyline, precision 5). Bad input gives what was readable. */
fun decodeRoute(text: String): List<RoutePoint> {
    val points = ArrayList<RoutePoint>()
    var index = 0
    var lat = 0
    var lon = 0
    while (index < text.length) {
        val deltas = IntArray(2)
        for (k in 0..1) {
            var shift = 0
            var result = 0
            while (true) {
                if (index >= text.length) return points
                val b = text[index++].code - 63
                result = result or ((b and 0x1F) shl shift)
                shift += 5
                if (b < 0x20) break
            }
            deltas[k] = if (result and 1 != 0) (result shr 1).inv() else result shr 1
        }
        lat += deltas[0]
        lon += deltas[1]
        points.add(RoutePoint(lat / 1e5, lon / 1e5))
    }
    return points
}

/** A small drawing of the trip's path, without a map: for trip cards. */
@Composable
fun RouteSketch(points: List<RoutePoint>, modifier: Modifier = Modifier) {
    val shape = remember(points) { normalise(points) }
    Canvas(modifier) {
        if (shape.size < 2) return@Canvas  // no route recorded: the card shows no drawing
        val pad = 8.dp.toPx()
        val w = size.width - 2 * pad
        val h = size.height - 2 * pad
        val scale = minOf(w, h)
        // centre the drawing in the box
        val spanX = shape.maxOf { it.x } * scale
        val spanY = shape.maxOf { it.y } * scale
        val ox = pad + (w - spanX) / 2
        val oy = pad + (h - spanY) / 2
        val pts = shape.map { Offset(ox + it.x * scale, oy + it.y * scale) }
        val path = Path().apply {
            moveTo(pts[0].x, pts[0].y)
            for (i in 1 until pts.size) lineTo(pts[i].x, pts[i].y)
        }
        // the moment: the road travelled, in road-marking amber; hollow start, solid end
        drawPath(path, Dash.Amber, style = Stroke(2.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        drawCircle(Dash.Secondary, radius = 3.dp.toPx(), center = pts.first(), style = Stroke(1.5.dp.toPx()))
        drawCircle(Dash.Amber, radius = 3.5.dp.toPx(), center = pts.last())
    }
}

/** Points scaled into a unit box (0..1 on the longer side), north up. */
private fun normalise(points: List<RoutePoint>): List<Offset> {
    if (points.size < 2) return emptyList()
    val k = cos(Math.toRadians(points.sumOf { it.lat } / points.size))
    val xs = points.map { it.lon * k }
    val ys = points.map { -it.lat }
    val minX = xs.min()
    val minY = ys.min()
    val span = max(xs.max() - minX, ys.max() - minY)
    if (span < 1e-6) return emptyList()
    return xs.indices.map { Offset(((xs[it] - minX) / span).toFloat(), ((ys[it] - minY) / span).toFloat()) }
}
