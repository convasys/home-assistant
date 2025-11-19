"""Lytiva curtain (cover) via MQTT - optimized for multiple entities on shared topics."""
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
    """Set up curtains dynamically with centralized MQTT handling."""
    mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]

    # Dictionary to store entities by their address for quick lookup
    covers_by_address = {}
    hass.data[DOMAIN][entry.entry_id]["covers_by_address"] = covers_by_address

    def add_new_cover(device):
        entity = LytivaCurtain(device, mqtt, hass, entry)
        async_add_entities([entity], True)
        covers_by_address[entity._address] = entity
        _LOGGER.debug(
            "add_new_cover called for device: %s (address=%s)", device.get("name"), device.get("address")
        )

    # register callback for dynamic discovery if available
    register_cb = hass.data[DOMAIN][entry.entry_id].get("register_cover_callback")
    if register_cb:
        register_cb(add_new_cover)
        _LOGGER.debug("Registered cover callback for dynamic discovery")

    # Add already discovered curtains
    for dev in devices.values():
        if dev.get("device_class") == "curtain" or dev.get("platform") == "cover":
            _LOGGER.debug("Adding existing discovered curtain: %s (address=%s)", dev.get("name"), dev.get("address"))
            add_new_cover(dev)

    # Central MQTT subscription for NODE and GROUP topics
    def on_mqtt_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            addr = payload.get("address")
            if addr is None:
                return
            entity = covers_by_address.get(addr)
            if entity:
                level = payload.get("curtain", {}).get("curtain_level", payload.get("curtain_level"))
                if level is not None:
                    new_pos = int(level)
                    if new_pos != entity._position:
                        entity._position = new_pos
                        _LOGGER.info("Curtain %s updated position: %s", entity._name, entity._position)
                        try:
                            entity.schedule_update_ha_state()
                        except Exception:
                            try:
                                entity.async_write_ha_state()
                            except Exception:
                                pass
        except Exception as e:
            _LOGGER.exception("Error handling curtain STATUS message: %s", e)

    try:
        mqtt.subscribe("LYT/+/NODE/E/STATUS")
        mqtt.message_callback_add("LYT/+/NODE/E/STATUS", on_mqtt_message)
        mqtt.subscribe("LYT/+/GROUP/E/STATUS")
        mqtt.message_callback_add("LYT/+/GROUP/E/STATUS", on_mqtt_message)
        _LOGGER.debug("Central MQTT subscription added for curtain STATUS topics")
    except Exception as e:
        _LOGGER.error("Failed to subscribe curtain MQTT topics: %s", e)


class LytivaCurtain(CoverEntity):
    """Representation of a Lytiva Curtain."""

    def __init__(self, device, mqtt, hass, entry):
        self._device = device or {}
        self._mqtt = mqtt
        self._hass = hass
        self._entry = entry

        self._name = self._device.get("name", "Lytiva Curtain")
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

    @property
    def device_info(self):
        identifiers = None
        dev_block = self._device.get("device", {})
        if dev_block and dev_block.get("identifiers"):
            ids = dev_block.get("identifiers")
            if isinstance(ids, (list, tuple)) and ids:
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
                payload = (self._set_position_template or self._device.get("set_position_template") or "").replace(
                    "{{ position }}", str(pos)
                )
                self._mqtt.publish(self._set_position_topic, payload)
                _LOGGER.debug("Published set_position for %s to %s: %s", self._name, self._set_position_topic, payload)
            except Exception as e:
                _LOGGER.error("Error publishing set_position for %s: %s", self._name, e)
