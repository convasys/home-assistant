"""Lytiva Fan via MQTT discovery."""
from __future__ import annotations
import json
import logging
from typing import Optional

from homeassistant.components.fan import FanEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Lytiva fan devices dynamically."""
    mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]

    def add_new_fan(device):
        entity = LytivaFan(device, mqtt)
        async_add_entities([entity], True)
        _LOGGER.info("Fan added dynamically: %s", device.get("name"))

    register_cb = hass.data[DOMAIN][entry.entry_id]["register_fan_callback"]
    register_cb(add_new_fan)

    for dev in devices.values():
        if dev.get("device_class") == "fan" or dev.get("device_class") == "fan":
            add_new_fan(dev)


class LytivaFan(FanEntity):
    """Representation of a Lytiva Fan."""

    def __init__(self, device: dict, mqtt):
        self._device = device
        self._mqtt = mqtt

        self._name = device.get("name")
        self._unique_id = str(device.get("unique_id") or device.get("address"))
        self._address = device.get("unique_id") or device.get("address")

        self._command_topic = device.get("command_topic")
        self._state_topic = device.get("state_topic")

        self._payload_on = device.get("payload_on")
        self._payload_off = device.get("payload_off")

        self._available = True
        self._percentage: Optional[int] = None

        # subscribe to state updates
        if self._state_topic:
            self._mqtt.message_callback_add(self._state_topic, self._on_state)
            self._mqtt.subscribe(self._state_topic)

    def _on_state(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            addr = payload.get("address")
            if str(addr) != str(self._address):
                return

            fan = payload.get("fan") or {}
            if "fan_speed" in fan:
                # fan_speed 0..5 -> percentage 0..100 (discovery used *20)
                try:
                    speed = int(fan.get("fan_speed", 0))
                    self._percentage = min(max(speed * 20, 0), 100)
                except Exception:
                    pass
            self._available = True
            self.schedule_update_ha_state()
        except Exception:
            _LOGGER.exception("Error parsing fan state")
            self._available = False
            self.schedule_update_ha_state()

    # ------------------------
    # DEVICE INFO
    # ------------------------
    @property
    def device_info(self):
        info = {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": self._device.get("device", {}).get("name", self._name),
            "manufacturer": self._device.get("device", {}).get("manufacturer", "Lytiva"),
            "model": self._device.get("device", {}).get("model", "Fan"),
        }
        if self._device.get("device", {}).get("suggested_area"):
            info["suggested_area"] = self._device.get("device", {}).get("suggested_area")
        return info

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def available(self):
        return self._available

    @property
    def percentage(self):
        return self._percentage

    def _publish(self, payload_obj):
        try:
            # if payload_on/payload_off provided as JSON string, send that for on/off
            if isinstance(payload_obj, str):
                self._mqtt.publish(self._command_topic, payload_obj)
            else:
                self._mqtt.publish(self._command_topic, json.dumps(payload_obj))
        except Exception:
            _LOGGER.exception("Failed to publish fan command")

    async def async_turn_on(self, percentage: int | None = None, **kwargs):
        if percentage is not None:
            # convert percentage to fan_speed (0..5 -> 0..5 where 100 -> 5)
            fan_speed = max(0, min(5, round(percentage / 20)))
            payload = {"version": "v1.0", "type": "fan", "address": int(self._address), "fan_speed": int(fan_speed)}
            self._publish(payload)
        else:
            if self._payload_on:
                # payload_on is a JSON string in discovery, publish as is
                self._publish(self._payload_on)
            else:
                payload = {"version": "v1.0", "type": "fan", "address": int(self._address), "fan_speed": 3}
                self._publish(payload)

    async def async_turn_off(self, **kwargs):
        if self._payload_off:
            self._publish(self._payload_off)
        else:
            payload = {"version": "v1.0", "type": "fan", "address": int(self._address), "fan_speed": 0}
            self._publish(payload)

    async def async_set_percentage(self, percentage: int):
        fan_speed = max(0, min(5, round(percentage / 20)))
        payload = {"version": "v1.0", "type": "fan", "address": int(self._address), "fan_speed": int(fan_speed)}
        self._publish(payload)
