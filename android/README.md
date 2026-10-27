# Driver Companion — Android app

Pairs with the Driver Companion device over Bluetooth, announces WhatsApp messages, SMS and incoming calls through it, and sends phone GPS as a backup. Android 8.0 or newer; Android only.

## Install on your phone

1. **Phone:** Settings → About phone → tap *Build number* 7 times. Then Settings → Developer options → turn on *USB debugging*.
2. **Laptop:** Android Studio → *Open* → select this `android` folder. Wait for "Gradle sync" to finish (first time downloads a few hundred MB). If it offers to upgrade the Android Gradle Plugin, you can say *Don't ask again*; the pinned versions work.
3. Plug the phone in by USB and allow debugging on the phone when asked.
4. Pick your phone in the device list at the top and press the green **Run** ▶ button. The app installs and opens.

To share it with teammates without a cable: *Build → Build App Bundle(s) / APK(s) → Build APK(s)*, then send `app/build/outputs/apk/debug/app-debug.apk`.

## First use

1. In the app, under **Permissions**, allow all four: Bluetooth, Notification access (opens Android settings; switch on *Driver Companion*), Location, Notifications.
   - If Android says *Restricted setting* for Notification access (happens with APKs sent as files): Settings → Apps → Driver Companion → ⋮ menu → *Allow restricted settings*, then try again.
2. Start the device service on the Pi (see `bluetooth/README.md`).
3. Hold the device button 5 seconds until it says "Pairing mode on". (Or run it with `--pair`.)
4. In the app tap **Pair a device**. Android shows a pairing box; type the 6-digit code the Pi speaks.
5. The card turns to **Connected**. Tap **Send test alert**: the Pi says "Driver Companion message from the app".
6. Send yourself a WhatsApp message or SMS from another phone: the Pi announces it.

After the first pairing, the app reconnects on its own whenever the device is powered and in range, while the status notification is showing.

## What's in it

| File | Job |
| --- | --- |
| `ble/Protocol.kt` | UUIDs and packet framing; must match `bluetooth/dc_ble` |
| `ble/DeviceLink.kt` | Scan, connect, pair, reconnect, one-at-a-time Bluetooth writes, hello handshake |
| `LinkService.kt` | Background service holding the link; sends phone GPS once a second while connected |
| `NotificationForwarder.kt` | Reads notifications; forwards chosen apps, SMS (default messages app) and incoming calls |
| `AppSettings.kt` | Volume, read-text, quiet, calls, SMS, app list; copied to the device on change |
| `MainActivity.kt` | The single screen |

Message text is sent to the device only when *Read message text* is on; otherwise only the app name and sender leave the phone.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| "No device found" | Pi service running? Pairing mode on? Phone Bluetooth on? On Android 11 or older, phone Location must also be on to scan |
| Pairing box never appears / "Pairing failed" | Pairing mode times out after 2 minutes; hold the button again. Remove any old *DC-XXXX* entry in Android Bluetooth settings |
| Connected but nothing is spoken | Check Notification access is allowed, the app is ticked under *Apps to announce*, and Quiet mode is off |
| "Device did not answer" | Restart the Pi service; check `journalctl -u driver-companion-ble -f` |
| Stops after the phone sleeps | Settings → Apps → Driver Companion → Battery → *Unrestricted* |
