"""BlueZ glue: GATT server, advertising and the pairing agent over D-Bus.

The Pi is a Bluetooth Low Energy peripheral. Every characteristic needs an
encrypted, authenticated (passkey-paired) link, so an unpaired phone can
neither read nor write anything.
"""

import asyncio
import logging

from dbus_fast import BusType, DBusError, Message, MessageType, PropertyAccess
from dbus_fast.aio import MessageBus
from dbus_fast.service import ServiceInterface, dbus_property, method

from . import uuids
from .speaker import Priority

log = logging.getLogger(__name__)

BLUEZ = "org.bluez"
ADAPTER_PATH = "/org/bluez/hci0"
APP_PATH = "/com/drivercompanion"
SERVICE_PATH = APP_PATH + "/service0"
ADVERT_PATH = APP_PATH + "/advert0"
AGENT_PATH = APP_PATH + "/agent"
PAIRING_WINDOW_S = 120

REJECTED = "org.bluez.Error.Rejected"
NOT_PERMITTED = "org.bluez.Error.NotPermitted"
FAILED = "org.bluez.Error.Failed"


def _option(options, key, default=None):
    value = options.get(key)
    return value.value if value is not None else default


class GattService(ServiceInterface):
    def __init__(self, uuid: str):
        super().__init__("org.bluez.GattService1")
        self._uuid = uuid

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Primary(self) -> "b":
        return True


class GattCharacteristic(ServiceInterface):
    def __init__(self, uuid, flags, service_path, on_write=None, on_read=None):
        super().__init__("org.bluez.GattCharacteristic1")
        self._uuid = uuid
        self._flags = flags
        self._service_path = service_path
        self._on_write = on_write
        self._on_read = on_read
        self._value = b""
        self.notifying = False

    @method()
    def ReadValue(self, options: "a{sv}") -> "ay":
        if self._on_read is None:
            raise DBusError(NOT_PERMITTED, "not readable")
        return self._on_read()[_option(options, "offset", 0):]

    @method()
    def WriteValue(self, value: "ay", options: "a{sv}"):
        if self._on_write is None:
            raise DBusError(NOT_PERMITTED, "not writable")
        try:
            self._on_write(bytes(value), _option(options, "mtu"))
        except Exception:
            log.exception("write handler failed")
            raise DBusError(FAILED, "device error")

    @method()
    def StartNotify(self):
        self.notifying = True

    @method()
    def StopNotify(self):
        self.notifying = False

    @dbus_property(access=PropertyAccess.READ)
    def UUID(self) -> "s":
        return self._uuid

    @dbus_property(access=PropertyAccess.READ)
    def Service(self) -> "o":
        return self._service_path

    @dbus_property(access=PropertyAccess.READ)
    def Flags(self) -> "as":
        return self._flags

    @dbus_property(access=PropertyAccess.READ)
    def Value(self) -> "ay":
        return self._value

    def push(self, data: bytes):
        """Send one packet to the phone as a notification."""
        self._value = data
        if self.notifying:
            self.emit_properties_changed({"Value": data})


class Advertisement(ServiceInterface):
    def __init__(self, name: str, service_uuid: str):
        super().__init__("org.bluez.LEAdvertisement1")
        self._name = name
        self._uuid = service_uuid

    @method()
    def Release(self):
        log.info("advertisement released by BlueZ")

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "s":
        return "peripheral"

    @dbus_property(access=PropertyAccess.READ)
    def ServiceUUIDs(self) -> "as":
        return [self._uuid]

    @dbus_property(access=PropertyAccess.READ)
    def LocalName(self) -> "s":
        return self._name


class PairingAgent(ServiceInterface):
    """Display-only agent: the device speaks the passkey, the phone types it."""

    def __init__(self, gateway):
        super().__init__("org.bluez.Agent1")
        self._gateway = gateway
        self._last_passkey = None

    @method()
    def Release(self):
        pass

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        raise DBusError(REJECTED, "PIN codes are not used")

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        pass

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        raise DBusError(REJECTED, "the device has no keypad")

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        if passkey != self._last_passkey:
            self._last_passkey = passkey
            self._gateway.on_passkey(passkey)

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        if not self._gateway.pairing:
            raise DBusError(REJECTED, "not in pairing mode")

    @method()
    def RequestAuthorization(self, device: "o"):
        if not self._gateway.pairing:
            raise DBusError(REJECTED, "not in pairing mode")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        pass

    @method()
    def Cancel(self):
        log.info("pairing cancelled")


class BluetoothService:
    def __init__(self, pairing_window_s: float = PAIRING_WINDOW_S):
        self.gateway = None
        self._window = pairing_window_s
        self._pairing_task = None
        self.status = None
        self.trip_out = None
        self.bus = None
        self.adapter = None
        self.adverts = None
        self._advert_lock = asyncio.Lock()

    def attach(self, gateway):
        self.gateway = gateway

    def push_status(self, packet: bytes):
        if self.status is not None:
            self.status.push(packet)

    def push_trip(self, packet: bytes):
        if self.trip_out is not None:
            self.trip_out.push(packet)

    def _writer(self, slot):
        def write(data, mtu):
            self.gateway.note_mtu(mtu)
            self.gateway.receive(slot, data)
        return write

    async def start(self):
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        adapter_obj = self.bus.get_proxy_object(
            BLUEZ, ADAPTER_PATH, await self.bus.introspect(BLUEZ, ADAPTER_PATH))
        self.adapter = adapter_obj.get_interface("org.bluez.Adapter1")
        await self.adapter.set_powered(True)
        await self.adapter.set_pairable(False)
        address = await self.adapter.get_address()
        name = "DC-" + address.replace(":", "")[-4:].upper()
        await self.adapter.set_alias(name)
        self.gateway.device_name = name

        g = self.gateway
        read_w = ["encrypt-authenticated-read"]
        write_w = ["encrypt-authenticated-write"]
        notify_w = ["encrypt-authenticated-notify"]
        chars = [
            GattCharacteristic(uuids.NOTIFICATION_IN, ["write"] + write_w, SERVICE_PATH,
                               on_write=self._writer("notification")),
            GattCharacteristic(uuids.LOCATION_IN, ["write-without-response"] + write_w,
                               SERVICE_PATH, on_write=self._writer("location")),
            GattCharacteristic(uuids.SETTINGS, ["read", "write"] + read_w + write_w,
                               SERVICE_PATH, on_write=self._writer("settings"),
                               on_read=g.settings_snapshot),
            GattCharacteristic(uuids.TRIP_OUT, ["notify"] + notify_w, SERVICE_PATH),
            GattCharacteristic(uuids.STATUS, ["read", "notify"] + read_w + notify_w,
                               SERVICE_PATH, on_read=g.status_snapshot),
            GattCharacteristic(uuids.UPDATE_IN, ["write"] + write_w, SERVICE_PATH,
                               on_write=self._writer("update")),
        ]
        self.trip_out, self.status = chars[3], chars[4]

        self.bus.export(SERVICE_PATH, GattService(uuids.SERVICE))
        for i, char in enumerate(chars):
            self.bus.export(f"{SERVICE_PATH}/char{i}", char)
        self.bus.export(ADVERT_PATH, Advertisement(name, uuids.SERVICE))
        self.bus.export(AGENT_PATH, PairingAgent(g))

        gatt = adapter_obj.get_interface("org.bluez.GattManager1")
        await gatt.call_register_application(APP_PATH, {})
        self.adverts = adapter_obj.get_interface("org.bluez.LEAdvertisingManager1")
        await self.adverts.call_register_advertisement(ADVERT_PATH, {})

        root = self.bus.get_proxy_object(BLUEZ, "/org/bluez",
                                         await self.bus.introspect(BLUEZ, "/org/bluez"))
        agents = root.get_interface("org.bluez.AgentManager1")
        await agents.call_register_agent(AGENT_PATH, "DisplayOnly")
        await agents.call_request_default_agent(AGENT_PATH)

        await self._watch_devices()
        log.info("advertising as %s", name)
        # Only after our service is registered, so a reconnecting phone finds it.
        await self._drop_stale_connections()

    async def _drop_stale_connections(self):
        """A phone still connected from before this program started has no
        session with us. Disconnect it so it reconnects and says hello again."""
        root = self.bus.get_proxy_object(BLUEZ, "/", await self.bus.introspect(BLUEZ, "/"))
        objects = await root.get_interface("org.freedesktop.DBus.ObjectManager").call_get_managed_objects()
        for path, interfaces in objects.items():
            device = interfaces.get("org.bluez.Device1")
            if not path.startswith(ADAPTER_PATH + "/") or device is None:
                continue
            connected = device.get("Connected")
            if connected is None or not connected.value:
                continue
            try:
                proxy = self.bus.get_proxy_object(BLUEZ, path, await self.bus.introspect(BLUEZ, path))
                await proxy.get_interface("org.bluez.Device1").call_disconnect()
                log.info("dropped a connection left over from before the restart")
            except DBusError as e:
                log.warning("could not drop old connection: %s", e)

    async def _readvertise(self):
        """The Pi pauses advertising while a phone is connected and, after some
        disconnects, never resumes, so phones can no longer find it. Registering
        the advertisement again after every disconnect makes it findable."""
        async with self._advert_lock:
            await asyncio.sleep(0.5)  # let BlueZ finish tearing down the link
            try:
                await self.adverts.call_unregister_advertisement(ADVERT_PATH)
            except DBusError:
                pass  # already gone
            try:
                await self.adverts.call_register_advertisement(ADVERT_PATH, {})
                log.info("advertising again")
            except DBusError as e:
                log.warning("could not restart advertising: %s", e)

    async def _watch_devices(self):
        rule = ("type='signal',sender='org.bluez',"
                "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',"
                "arg0='org.bluez.Device1'")
        await self.bus.call(Message(
            destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
            interface="org.freedesktop.DBus", member="AddMatch", signature="s", body=[rule]))
        self.bus.add_message_handler(self._on_bus_message)

    def _on_bus_message(self, msg):
        if msg.message_type != MessageType.SIGNAL or msg.member != "PropertiesChanged":
            return None
        if not msg.body or msg.body[0] != "org.bluez.Device1":
            return None
        changed = msg.body[1]
        if "Connected" in changed:
            if changed["Connected"].value:
                log.info("phone connected")
            else:
                log.info("phone disconnected")
                self.gateway.connection_lost()
                asyncio.get_running_loop().create_task(self._readvertise())
        if "Paired" in changed and changed["Paired"].value and self.gateway.pairing:
            self.gateway.speaker.say("Phone paired.", Priority.SYSTEM)
            asyncio.get_running_loop().create_task(self.stop_pairing(announce=False))
        return None

    async def start_pairing(self):
        if self.gateway.pairing:
            return
        self.gateway.pairing = True
        await self.adapter.set_pairable(True)
        self.gateway.speaker.say("Pairing mode on.", Priority.SYSTEM)
        self._pairing_task = asyncio.get_running_loop().create_task(self._pairing_timeout())
        log.info("pairing mode on for %d s", self._window)

    async def _pairing_timeout(self):
        await asyncio.sleep(self._window)
        await self.stop_pairing(announce=True)

    async def stop_pairing(self, announce: bool):
        if not self.gateway.pairing:
            return
        self.gateway.pairing = False
        task = self._pairing_task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self._pairing_task = None
        await self.adapter.set_pairable(False)
        if announce:
            self.gateway.speaker.say("Pairing mode off.", Priority.SYSTEM)
        log.info("pairing mode off")
