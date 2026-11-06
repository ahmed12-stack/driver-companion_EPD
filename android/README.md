# Driver Companion — Android app

Pairs with the Driver Companion device over Bluetooth, announces WhatsApp messages, SMS and incoming calls through it, sends the phone's GPS (speed and route) to it, and shows every trip with its route on a map. Night instrument-cluster look: graphite with one amber accent. Android 8.0 or newer; Android only.

## Install on your phone

1. **Phone:** Settings → About phone → tap *Build number* 7 times. Then Settings → Developer options → turn on *USB debugging*.
2. **Laptop:** Android Studio → *Open* → select this `android` folder. Wait for "Gradle sync" to finish (first time downloads a few hundred MB). If it offers to upgrade the Android Gradle Plugin, you can say *Don't ask again*; the pinned versions work.
3. Plug the phone in by USB and allow debugging on the phone when asked.
4. Pick your phone in the device list at the top and press the green **Run** ▶ button. The app installs and opens.

To share it with teammates without a cable: *Build → Build App Bundle(s) / APK(s) → Build APK(s)*, then send `app/build/outputs/apk/debug/app-debug.apk`.

To give it to someone outside the group (e.g. the teacher): *Build → Generate Signed App Bundle / APK → APK*, create a key store **outside the repo** (keep it and its password safe: every later version must be signed with the same key), choose *release*, and share `app/release/app-release.apk` by a Drive or GitHub Release link. On the phone: open the link, allow installing from that source, and choose *Install anyway* if Play Protect asks.

## First use

1. In the app, under **Permissions**, allow all four: Bluetooth, Notification access (opens Android settings; switch on *Driver Companion*), Location, Notifications.
   - If Android says *Restricted setting* for Notification access (happens with APKs sent as files): Settings → Apps → Driver Companion → ⋮ menu → *Allow restricted settings*, then try again.
2. Install and start the device software on the Pi (see `device/README.md`).
3. Hold the device button 5 seconds until it says "Pairing mode on". No button fitted: join pins 11 and 9 with a jumper wire for 5 seconds, then take it off (or on the Pi: `sudo systemctl kill -s USR1 dc@phone`).
4. In the app tap **Pair** (or **Pair new**). Android shows a pairing box; type the 6-digit code the Pi speaks.
5. The card turns to **Connected**. Tap **Send test alert**: the Pi says "Driver Companion message from the app".
6. Send yourself a WhatsApp message or SMS from another phone: the Pi announces it.

After the first pairing, the app reconnects on its own whenever the device is powered and in range, while the status notification is showing. **Stop** (while searching) or **Disconnect** turns that off until you tap **Reconnect**.

## The home screen

- **Device:** a small dot shows the link: green when connected, amber while searching or connecting, red when Bluetooth is off or the link dropped, grey when idle. Under it, the device name and software version. While connected, a line shows the phone GPS being sent: green **Phone GPS ±8 m · 42 km/h**, or amber when there is no fix yet or it is too weak (worse than ±30 m) to be used for trips.
- **Recent trips:** the last three, each with a drawing of its route, distance and time, and the top speed in a speed-limit sign.
- **Permissions:** one line once everything is allowed; otherwise the ones still needed, with *Allow* buttons.
- **Announcements:** volume (Low, Medium, High), read message text, quiet mode, calls, SMS, and the apps to announce (tap an app to switch it on; it gets an amber dot).

## Phone GPS

While connected, the app sends the phone's location and speed to the device every second, using Google's fused location (GPS plus Wi-Fi and mobile towers, as Maps uses), or plain GPS on phones without Google Play services. The speed comes from GPS, so it is accurate outdoors; indoors there may be only rough fixes, which the device's trip log ignores (worse than ±30 m). Each fix carries its own time, because the Pi has no reliable clock.

## Trips

When the car is switched off, the device sends the finished trip. It appears on the home screen under **Recent trips** (latest three, with a small drawing of the route, or *See all*). Tapping one shows the route on an OpenStreetMap map (drag and pinch to explore; the map tiles need mobile data or Wi-Fi, the route line does not), the trip read like an instrument cluster (distance, time, top speed), the summary and the details: distance, trip and driving time, top and average speed, stops, overspeed warnings, harsh braking and acceleration (shown as *Not measured yet*, since there is no motion sensor), and signs announced. Trips are kept on the phone (last 200). The app confirms each one to the device only after saving it, so a trip lost on the way is sent again.

## What's in it

| File | Job |
| --- | --- |
| `ble/Protocol.kt` | UUIDs and packet framing; must match `device/dc_phone` |
| `ble/DeviceLink.kt` | Scan, connect, pair, reconnect, one-at-a-time Bluetooth writes, hello handshake |
| `LinkService.kt` | Background service holding the link; sends phone GPS (fused location) once a second while connected |
| `LinkHub.kt` | Shares the link, its status and the last GPS fix between the service and the screen |
| `NotificationForwarder.kt` | Reads notifications; forwards chosen apps, SMS (default messages app) and incoming calls |
| `AppSettings.kt` | Volume, read-text, quiet, calls, SMS, app list; copied to the device on change |
| `TripStore.kt` | Trips received from the device, saved on the phone |
| `MainActivity.kt` | Home screen, trip list and trip detail |
| `ui/Theme.kt` | Colours, fonts (IBM Plex Sans and Mono), text styles, the one easing curve |
| `ui/Parts.kt` | Shared pieces: panels, buttons, switch, status dot, line icons, speed-limit sign, segmented control, app chips |
| `ui/Route.kt` | Decodes the trip path the device sends; small route drawing for trip cards |
| `ui/RouteMap.kt` | The OpenStreetMap map (osmdroid) with the route, start and end |

Message text is sent to the device only when *Read message text* is on; otherwise only the app name and sender leave the phone.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| "No device found" | Pi service running? Pairing mode on? Phone Bluetooth on? On Android 11 or older, phone Location must also be on to scan |
| Pairing box never appears / "Pairing failed" | Pairing mode times out after 2 minutes; hold the button (or the jumper on pins 11 and 9) again. Remove any old *DC-XXXX* entry in Android Bluetooth settings |
| Connected but nothing is spoken | Check Notification access is allowed, the app is ticked under *Apps to announce*, and Quiet mode is off |
| "Device did not answer" | Restart the phone module on the Pi: `sudo systemctl restart dc@phone`; check `journalctl -u dc@phone -f` |
| Stops after the phone sleeps | Settings → Apps → Driver Companion → Battery → *Unrestricted* |
| No phone GPS on the device | Location allowed (with *precise location*) and switched on in quick settings; the home screen's *Phone GPS* line shows what is sent. Watch it arrive on the Pi: `sudo /opt/driver-companion/venv/bin/python /opt/driver-companion/device/tools/watch_board.py gps/fix` |
| WhatsApp not announced | Under *Apps to announce*, WhatsApp must have its amber dot |
| Map is blank but the route line shows | The map tiles need mobile data or Wi-Fi; the route itself is stored on the phone |

## Design

The look follows the *unslop* method: one direction (a car's instrument cluster at night), a graphite colour ramp with a single amber accent, a 4 dp spacing grid, one line-icon style, sentence-case copy, mono fixed-width figures, and one easing curve with no looping animation. The speed-limit sign (white disc, red ring) is used for speeds only.

- Method: [unslop](https://github.com/heydamir/unslop) by Damir Sokolovsky (@heydamir), CC BY 4.0.
- Fonts: IBM Plex Sans and IBM Plex Mono, SIL Open Font License 1.1 (see `FONTS-LICENSE.txt`).
