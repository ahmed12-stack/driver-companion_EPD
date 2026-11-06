"""dc_speech: the only module that uses the speaker.

Listens on alert/say and speaks one message at a time, most urgent first
(overspeed, sign, call, message, system). Messages wait for a 5-second gap
after the last alert (REQ-17) and are dropped if they get too old. Volume
follows settings/current.
"""
