"""dc_phone: the Bluetooth link to the Android app.

Pairs with the phone, receives notifications, calls, settings and phone GPS,
and sends trips. It does not speak or keep trips itself: it puts alert/say,
gps/fix and settings/current on the message board, and takes trip/saved from
dc_trip into an outbox until the app confirms each trip.
"""

PROTOCOL_VERSION = 1
