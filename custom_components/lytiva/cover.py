"""Lytiva curtain (cover) via MQTT - improved logging and robust subscriptions."""
from __future__ import annotations
import logging
import json
from homeassistant.components.cover import CoverEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

SUPPORT_OPEN = 1
SUPPORT_CLOSE = 2
SUPPORT_STOP = 4
SUPPORT_SET_POSITION = 8


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up curtains dynamically."""
    mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]

    def add_new_cover(device):
        entity = LytivaCurtain(device, mqtt)
        async_add_entities([entity], True)
        _LOGGER.debug("add_new_cover called for device: %s (address=%s)", device.get("name"), device.get("address"))

    # register callback if __init__ exposed it
    register_cb = hass.data[DOMAIN][entry.entry_id].get("register_cover_callback")
    if register_cb:
        register_cb(add_new_cover)
        _LOGGER.debug("Registered cover callback for dynamic discovery")

    # Add already discovered curtains
    for dev in devices.values():
        if dev.get("device_class") == "curtain" or dev.get("platform") == "cover":
            _LOGGER.debug("Adding existing discovered curtain: %s (address=%s)", dev.get("name"), dev.get("address"))
            add_new_cover(dev)


class LytivaCurtain(CoverEntity):
    """Representation of a Lytiva Curtain."""

    def __init__(self, device, mqtt):
        self._device = device or {}
        self._mqtt = mqtt

        self._name = self._device.get("name", "Lytiva Curtain")
        # prefer unique_id if present, otherwise use address
        self._unique_id = str(self._device.get("unique_id") or self._device.get("address"))
        self._address = self._device.get("address")

        self._command_topic = self._device.get("command_topic")
        self._state_topic = self._device.get("state_topic")
        self._position_topic = self._device.get("position_topic") or self._state_topic
        self._set_position_topic = self._device.get("set_position_topic") or self._command_topic
        self._set_position_template = self._device.get("set_position_template")

        self._payload_open = self._device.get("payload_open")
        self._payload_close = self._device.get("payload_close")
        self._payload_stop = self._device.get("payload_stop")

        self._position = None
        self._attr_available = True

        # device metadata (if present in discovery payload under "device")
        dev_meta = self._device.get("device", {})
        self._manufacturer = dev_meta.get("manufacturer") or self._device.get("manufacturer") or "Lytiva"
        self._model = dev_meta.get("model") or self._device.get("model") or "Curtain"
        self._sw_version = dev_meta.get("sw_version") or self._device.get("sw_version")
        self._hw_version = dev_meta.get("hw_version") or self._device.get("hw_version")
        self._area = dev_meta.get("suggested_area") or self._device.get("suggested_area")

        _LOGGER.debug(
            "Initialized LytivaCurtain name=%s unique_id=%s address=%s state_topic=%s position_topic=%s",
            self._name, self._unique_id, self._address, self._state_topic, self._position_topic
        )

        # Subscribe to topics (use message_callback_add to keep parsing local)
        self._subscribe_state()

    def _subscribe_state(self):
        """Set up MQTT subscriptions for both state and position topics."""
        if not self._mqtt:
            _LOGGER.error("MQTT client not available for curtain %s", self._name)
            return

        if not self._state_topic and not self._position_topic:
            _LOGGER.warning("No state/position topic for curtain %s (address=%s)", self._name, self._address)
            return

        def _on_message(client, userdata, msg):
            topic = msg.topic if hasattr(msg, "topic") else "<unknown>"
            payload_raw = msg.payload
            _LOGGER.debug("Curtain %s received MQTT on %s: %s", self._name, topic, payload_raw)

            try:
                payload = json.loads(payload_raw)
            except Exception as e:
                _LOGGER.error("JSON decode error for curtain %s on topic %s: %s", self._name, topic, e)
                return

            # ensure message is for this device address
            msg_address = payload.get("address")
            if msg_address is None:
                _LOGGER.debug("Dropped message for curtain %s: no address in payload", self._name)
                return

            # Accept both int and string addresses
            try:
                if str(msg_address) != str(self._address):
                    _LOGGER.debug("Message address %s does not match this curtain address %s", msg_address, self._address)
                    return
            except Exception:
                return

            # parse curtain level
            curtain_obj = payload.get("curtain") or {}
            # also support top-level curtain_level for some payloads
            level = None
            if "curtain_level" in curtain_obj:
                level = curtain_obj.get("curtain_level")
            elif "curtain_level" in payload:
                level = payload.get("curtain_level")

            if level is not None:
                try:
                    new_pos = int(level)
                    if new_pos != self._position:
                        _LOGGER.info("Curtain %s (addr=%s) position updated: %s", self._name, self._address, new_pos)
                        self._position = new_pos
                        self._attr_available = True
                        # schedule HA update
                        try:
                            self.schedule_update_ha_state()
                        except Exception:
                            try:
                                self.async_write_ha_state()
                            except Exception:
                                pass
                except Exception as e:
                    _LOGGER.error("Failed to parse curtain level for %s: %s", self._name, e)

        # add callbacks and subscribe to both topics if present
        try:
            if self._state_topic:
                self._mqtt.message_callback_add(self._state_topic, _on_message)
                self._mqtt.subscribe(self._state_topic)
                _LOGGER.debug("Subscribed to state_topic %s for curtain %s", self._state_topic, self._name)

            if self._position_topic and self._position_topic != self._state_topic:
                self._mqtt.message_callback_add(self._position_topic, _on_message)
                self._mqtt.subscribe(self._position_topic)
                _LOGGER.debug("Subscribed to position_topic %s for curtain %s", self._position_topic, self._name)
        except Exception as e:
            _LOGGER.error("Exception subscribing MQTT topics for curtain %s: %s", self._name, e)

    # ------------------------
    # DEVICE INFO
    # ------------------------
    @property
    def device_info(self):
        identifiers = None
        # Prefer identifiers from the discovery payload device block, fallback to unique_id
        dev_block = self._device.get("device", {})
        if dev_block and dev_block.get("identifiers"):
            # Ensure identifiers are tuples (DOMAIN, id)
            ids = dev_block.get("identifiers")
            if isinstance(ids, (list, tuple)) and ids:
                # If identifiers are strings like "10603_60590", keep them as device id
                identifiers = {(DOMAIN, ids[0])}
        if not identifiers:
            identifiers = {(DOMAIN, self._unique_id)}

        info = {
            "identifiers": identifiers,
            "name": dev_block.get("name") or self._name,
            "manufacturer": self._manufacturer,
            "model": self._model,
        }
        if self._sw_version:
            info["sw_version"] = self._sw_version
        if self._hw_version:
            info["hw_version"] = self._hw_version
        if self._area:
            info["suggested_area"] = self._area

        return info

    # BASIC ENTITY PROPERTIES
    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def supported_features(self):
        return SUPPORT_OPEN | SUPPORT_CLOSE | SUPPORT_STOP | SUPPORT_SET_POSITION

    @property
    def current_cover_position(self):
        return self._position

    @property
    def is_closed(self):
        if self._position is not None:
            return self._position == 0
        return None

    def open_cover(self, **_):
        if self._payload_open and self._command_topic:
            self._mqtt.publish(self._command_topic, self._payload_open)
            _LOGGER.debug("Published open payload for %s to %s", self._name, self._command_topic)

    def close_cover(self, **_):
        if self._payload_close and self._command_topic:
            self._mqtt.publish(self._command_topic, self._payload_close)
            _LOGGER.debug("Published close payload for %s to %s", self._name, self._command_topic)

    def stop_cover(self, **_):
        if self._payload_stop and self._command_topic:
            self._mqtt.publish(self._command_topic, self._payload_stop)
            _LOGGER.debug("Published stop payload for %s to %s", self._name, self._command_topic)

    def set_cover_position(self, **kwargs):
        pos = kwargs.get("position")
        if pos is not None and self._set_position_topic:
            try:
                payload = (self._set_position_template or self._device.get("set_position_template") or "").replace("{{ position }}", str(pos))
                self._mqtt.publish(self._set_position_topic, payload)
                _LOGGER.debug("Published set_position for %s to %s: %s", self._name, self._set_position_topic, payload)
            except Exception as e:
                _LOGGER.error("Error publishing set_position for %s: %s", self._name, e)
