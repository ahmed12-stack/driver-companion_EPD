"""dc_health: the power-on self-test (REQ-09) and a watch on the other modules.

About 20 s after start it checks that every expected module is online and
which hardware is present, speaks the result ("System ready." or what is
wrong) and publishes it on health/selftest. After that it warns once if a
module crashes (its status turns "offline", the board's last will) and stays
down past the restart grace period, or stops sending heartbeats.
"""
