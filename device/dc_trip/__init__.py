"""dc_trip: trips, their summary, and the trip history.

One trip per ignition cycle (vehicle/ignition). Distance, time and speeds come
from gps/fix; overspeed, harsh driving and signs from trip/event. When the
ignition goes off it speaks the facts and the LLM's remark (alert/say), keeps
the trip in its history and publishes it on trip/saved for the phone.
"""
