"""Lytiva Fan integration with live updates and HA-compatible control."""
from __future__ import annotations
import json
import logging
import math
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.fan import FanEntity, FanEntityFeature

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Lytiva fans from discovery or stored devices."""
    data = hass.data[DOMAIN][entry.entry_id]
    mqtt = data["mqtt_client"]
    devices = data.get("devices", {})

    def add_fan(device):
        ent = LytivaFan(hass, entry, device)
        async_add_entities([ent])
        _LOGGER.info("✅ Lytiva fan added: %s (Address: %s)", device.get("name"), device.get("unique_id"))

    register_cb = data.get("register_fan_callback")
    if register_cb:
        register_cb(add_fan)

    # Add already known devices
    for dev in devices.values():
        if isinstance(dev, dict) and ("fan" in dev.get("name", "").lower() or dev.get("device_class") == "fan"):
            add_fan(dev)


class LytivaFan(FanEntity):
    """Lytiva fan entity with live updates."""

    _attr_supported_features = FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    def __init__(self, hass, entry, device: dict[str, Any]):
        self.hass = hass
        self._entry = entry
        self._device = device
        self._mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]

        self._name = device.get("name")
        self._unique_id = str(device.get("unique_id") or device.get("address"))
        self._address = int(device.get("unique_id") or device.get("address"))

        self._state_topic = device.get("state_topic")
        self._command_topic = device.get("command_topic")

        dev_meta = device.get("device", {})
        self._manufacturer = dev_meta.get("manufacturer", "Lytiva")
        self._model = dev_meta.get("model", "Fan")
        self._area = dev_meta.get("suggested_area")

        # Internal state
        self._available = True
        self._speed = 0
        self._percentage = 0
        self._last_speed = 3
        self._is_on = False

        self._subscribe_state()

    # ---------------------------------------------------------
    #  MQTT STATUS SUBSCRIPTION
    # ---------------------------------------------------------
    def _subscribe_state(self):
        if not self._state_topic:
            return

        def _cb(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode())
                if payload.get("address") != self._address:
                    return

                fan_data = payload.get("fan", {})
                speed = fan_data.get("fan_speed", payload.get("fan_speed"))
                if speed is not None:
                    speed = max(0, min(4, int(speed)))
                    self._speed = speed
                    self._percentage = speed * 25
                    if speed > 0:
                        self._last_speed = speed
                    self._is_on = speed > 0
                    self._available = True
                    self.schedule_update_ha_state()
            except Exception as e:
                _LOGGER.error("Error processing fan state: %s", e)

        self._mqtt.message_callback_add(self._state_topic, _cb)
        self._mqtt.subscribe(self._state_topic)
        _LOGGER.debug("Subscribed to fan state topic: %s", self._state_topic)

    # ---------------------------------------------------------
    #  HA PROPERTIES
    # ---------------------------------------------------------
    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return self._unique_id

    @property
    def device_info(self):
        info = {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": self._device.get("device", {}).get("name", self._name),
            "manufacturer": self._manufacturer,
            "model": self._model,
        }
        if self._area:
            info["suggested_area"] = self._area
        return info

    @property
    def available(self):
        return self._available

    @property
    def is_on(self):
        return self._is_on

    @property
    def percentage(self):
        return self._percentage

    @property
    def speed_count(self):
        return 4

    # ---------------------------------------------------------
    #  CONTROL METHODS
    # ---------------------------------------------------------
    async def async_turn_on(self, percentage: int | None = None, **kwargs):
        """Turn on fan using last speed or percentage."""
        speed = self._last_speed if self._last_speed > 0 else 3
        if percentage is not None:
            speed = max(1, math.ceil(percentage / 25))
        await self._set_speed(speed)

    async def async_turn_off(self, **kwargs):
        """Turn off fan via 0% speed."""
        await self._set_speed(0)

    async def async_set_percentage(self, percentage: int, **kwargs):
        """Set fan speed; 0 turns off."""
        percentage = max(0, min(100, percentage))
        speed = 0 if percentage == 0 else max(1, math.ceil(percentage / 25))
        await self._set_speed(speed)

    async def _set_speed(self, speed: int):
        """Publish MQTT payload and update internal state."""
        payload = {
            "version": "v1.0",
            "type": "fan",
            "address": self._address,
            "fan_speed": speed
        }
        if self._command_topic:
            self._mqtt.publish(self._command_topic, json.dumps(payload))

        self._speed = speed
        self._percentage = speed * 25
        self._last_speed = speed if speed > 0 else self._last_speed
        self._is_on = speed > 0
        self.schedule_update_ha_state()

    # ---------------------------------------------------------
    #  RESTORE OLD STATE
    # ---------------------------------------------------------
    async def async_added_to_hass(self):
        old_state = self.hass.states.get(f"fan.{self._unique_id}")
        if old_state:
            self._speed = math.ceil(int(old_state.attributes.get("percentage", 0)) / 25)
            self._percentage = int(old_state.attributes.get("percentage", 0))
            self._last_speed = self._speed if self._speed > 0 else 3
            self._is_on = self._speed > 0
