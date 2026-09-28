# Driver Companion

Group 7, Electronic Product Development, KIIT.

An offline in-car assistant on a Raspberry Pi that speaks road-sign and speed
alerts, and gives a spoken trip summary when parked. No screen, no wireless, advisory only.

| Folder | Module | Owner |
|---|---|---|
| `vision/` | Camera → sign recognition → confirmed sign events | Asir Ahmed (2430268), group lead |
| `speed/` | GPS speed, IMU, overspeed logic | Amrit (2430078) |
| `speech/` | Spoken alerts | Abhay (2430071) |
| `llm/` | On-device trip summary, documentation | Anirudha (2430211) |

Modules talk over local Unix sockets. The vision interface is documented in
[`vision/README.md`](vision/README.md).
