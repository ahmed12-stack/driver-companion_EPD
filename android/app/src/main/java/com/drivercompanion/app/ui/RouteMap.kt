package com.drivercompanion.app.ui

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.ColorMatrix
import android.graphics.ColorMatrixColorFilter
import android.graphics.Paint
import android.graphics.drawable.GradientDrawable
import android.view.MotionEvent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import org.osmdroid.config.Configuration
import org.osmdroid.tileprovider.tilesource.TileSourceFactory
import org.osmdroid.util.BoundingBox
import org.osmdroid.util.GeoPoint
import org.osmdroid.views.CustomZoomButtonsController
import org.osmdroid.views.MapView
import org.osmdroid.views.overlay.Marker
import org.osmdroid.views.overlay.Polyline
import java.io.File

/**
 * The trip's path on an OpenStreetMap map. Map tiles need the internet; without
 * it the path is still drawn on a blank background.
 */
@Composable
fun RouteMap(points: List<RoutePoint>, modifier: Modifier = Modifier) {
    Box(modifier.background(Dash.Background)) {
        AndroidView(
            factory = { context -> buildMap(context, points) },
            modifier = Modifier.fillMaxSize(),
            onRelease = { it.onDetach() },
        )
        // Required credit for OpenStreetMap data.
        Text(
            "© OpenStreetMap contributors",
            style = Figures.meta.copy(fontSize = 11.sp),
            color = Dash.Muted,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .background(Dash.Background.copy(alpha = 0.8f))
                .padding(horizontal = 8.dp, vertical = 4.dp),
        )
    }
}

@SuppressLint("ClickableViewAccessibility")
private fun buildMap(context: Context, points: List<RoutePoint>): MapView {
    // Must be set before the first MapView: who we are (OSM's tile rules) and
    // where tiles are cached (app storage, so no storage permission is needed).
    Configuration.getInstance().apply {
        userAgentValue = context.packageName
        osmdroidBasePath = File(context.cacheDir, "osmdroid")
        osmdroidTileCache = File(context.cacheDir, "osmdroid/tiles")
    }

    val geo = points.map { GeoPoint(it.lat, it.lon) }
    val map = MapView(context)
    map.setTileSource(TileSourceFactory.MAPNIK)
    map.setMultiTouchControls(true)
    map.zoomController.setVisibility(CustomZoomButtonsController.Visibility.NEVER)
    map.isTilesScaledToDpi = true
    map.setMaxZoomLevel(19.0)
    map.setMinZoomLevel(3.0)
    map.overlayManager.tilesOverlay.apply {
        setColorFilter(darkTiles())
        setLoadingBackgroundColor(Dash.Background.toArgb())
        setLoadingLineColor(Dash.Panel.toArgb())
    }

    // A soft glow under a sharp line.
    // The moment: the road travelled in road-marking amber, a hollow start and a solid end.
    map.overlays.add(routeLine(map, geo, Dash.Amber.toArgb(), 8f))
    map.overlays.add(dot(map, geo.first(), Dash.Background.toArgb(), Dash.Secondary.toArgb()))
    map.overlays.add(dot(map, geo.last(), Dash.Amber.toArgb(), Dash.Background.toArgb()))

    map.addOnFirstLayoutListener { _, _, _, _, _ ->
        val box = BoundingBox.fromGeoPoints(geo)
        if (box.latitudeSpan < 0.0005 && box.longitudeSpan < 0.0005) {
            map.controller.setZoom(17.0)
            map.controller.setCenter(GeoPoint(box.centerLatitude, box.centerLongitude))
        } else {
            map.zoomToBoundingBox(box, false, (48 * context.resources.displayMetrics.density).toInt())
        }
    }

    // Let the map take drags instead of the page scrolling under it.
    map.setOnTouchListener { v, event ->
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> v.parent?.requestDisallowInterceptTouchEvent(true)
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL ->
                v.parent?.requestDisallowInterceptTouchEvent(false)
        }
        false
    }
    return map
}

private fun routeLine(map: MapView, geo: List<GeoPoint>, color: Int, width: Float) = Polyline(map).apply {
    setPoints(geo)
    outlinePaint.color = color
    outlinePaint.strokeWidth = width
    outlinePaint.strokeCap = Paint.Cap.ROUND
    outlinePaint.strokeJoin = Paint.Join.ROUND
    outlinePaint.isAntiAlias = true
    setOnClickListener { _, _, _ -> true } // no pop-up bubble on tap
}

private fun dot(map: MapView, at: GeoPoint, color: Int, ring: Int) = Marker(map).apply {
    position = at
    icon = GradientDrawable().apply {
        shape = GradientDrawable.OVAL
        setColor(color)
        setStroke(6, ring)
        setSize(40, 40)
    }
    setAnchor(Marker.ANCHOR_CENTER, Marker.ANCHOR_CENTER)
    setOnMarkerClickListener { _, _ -> true }
}

/** Turns the light OpenStreetMap tiles into a dim graphite night map, so only the route carries colour. */
private fun darkTiles(): ColorMatrixColorFilter {
    val m = ColorMatrix().apply { setSaturation(0f) }
    m.postConcat(
        ColorMatrix(
            floatArrayOf(
                -1f, 0f, 0f, 0f, 255f,
                0f, -1f, 0f, 0f, 255f,
                0f, 0f, -1f, 0f, 255f,
                0f, 0f, 0f, 1f, 0f,
            ),
        ),
    )
    m.postConcat(ColorMatrix().apply { setScale(0.55f, 0.55f, 0.56f, 1f) })
    return ColorMatrixColorFilter(m)
}
