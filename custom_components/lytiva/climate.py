"""Lytiva IR AC (climate) via MQTT discovery."""
from __future__ import annotations
import json
import logging
from typing import Optional

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    PRESET_NONE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Lytiva climate devices dynamically."""
    mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]
    devices = hass.data[DOMAIN][entry.entry_id]["devices"]

    def add_new_climate(device):
        entity = LytivaIRAC(device, mqtt)
        async_add_entities([entity], True)
        _LOGGER.info("IR AC added dynamically: %s", device.get("name"))

    # Register callback
    register_cb = hass.data[DOMAIN][entry.entry_id]["register_climate_callback"]
    register_cb(add_new_climate)

    # Add already discovered devices
    for dev in devices.values():
        if "mode_state_topic" in dev or "temperature_command_topic" in dev:
            add_new_climate(dev)


class LytivaIRAC(ClimateEntity):
    """Representation of an IR AC as a climate entity."""

    def __init__(self, device: dict, mqtt):
        self._device = device
        self._mqtt = mqtt

        self._name = device.get("name")
        self._unique_id = str(device.get("unique_id") or device.get("address"))
        self._address = device.get("unique_id") or device.get("address")

        # Topics (from discovery payload)
        self._state_topic = device.get("mode_state_topic") or device.get("current_temperature_topic") or device.get("target_temperature_topic") or device.get("fan_mode_state_topic")
        self._command_topic = device.get("mode_command_topic") or device.get("temperature_command_topic") or device.get("fan_mode_command_topic") or device.get("preset_mode_command_topic")

        # Ranges and supported modes from payload (fallbacks)
        self._hvac_modes = device.get("modes", ["auto", "cool", "dry", "fan_only", "heat"])
        self._preset_modes = device.get("preset_modes", ["On", "Off"])
        self._fan_modes = device.get("fan_modes", ["Vlow", "Low", "Med", "High", "Top", "Auto"])
        self._min_temp = device.get("min_temp", 18)
        self._max_temp = device.get("max_temp", 30)
        self._temp_step = device.get("temp_step", 1)

        # metadata
        self._manufacturer = device.get("device", {}).get("manufacturer", "Lytiva")
        self._model = device.get("device", {}).get("model", "IR Blaster AC")
        self._area = device.get("device", {}).get("suggested_area")

        # internal state
        self._hvac_mode: Optional[str] = None
        self._preset_mode: Optional[str] = None
        self._current_temp: Optional[float] = None
        self._target_temp: Optional[float] = None
        self._fan_mode: Optional[str] = None
        self._available = True

        # Subscribe to status topic (if provided)
        status_topic = device.get("mode_state_topic") or device.get("current_temperature_topic") or device.get("target_temperature_topic") or device.get("fan_mode_state_topic")
        if status_topic:
            self._mqtt.message_callback_add(status_topic, self._on_state_message)
            self._mqtt.subscribe(status_topic)

    def _on_state_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            # match by numeric address if present in payload
            addr = payload.get("address")
            if str(addr) != str(self._address):
                return

            ir = payload.get("ir_ac") or {}
            # update hvac mode mapping for fan -> fan_only
            mode = ir.get("mode")
            if mode:
                if mode == "fan":
                    self._hvac_mode = "fan_only"
                else:
                    self._hvac_mode = mode

            # preset power (On/Off)
            power = ir.get("power")
            if power is not None:
                self._preset_mode = "On" if power else "Off"

            # temps
            if "current_temperature" in ir:
                try:
                    self._current_temp = float(ir.get("current_temperature"))
                except Exception:
                    pass
            if "temperature" in ir:
                try:
                    self._target_temp = float(ir.get("temperature"))
                except Exception:
                    pass

            # fan speed mapping -> fan_mode string
            fan_speed = ir.get("fan_speed")
            if fan_speed is not None:
                mapping = {1: "Vlow", 2: "Low", 3: "Med", 4: "High", 5: "Top", 6: "Auto"}
                self._fan_mode = mapping.get(int(fan_speed), self._fan_modes[0])

            self._available = True
            self.schedule_update_ha_state()

        except Exception as e:
            _LOGGER.exception("Error parsing IR AC state: %s", e)
            self._available = False
            self.schedule_update_ha_state()

    # ------------------------
    # DEVICE INFO
    # ------------------------
    @property
    def device_info(self):
        info = {
            "identifiers": {(DOMAIN, self._unique_id)},
            "name": self._name,
            "manufacturer": self._manufacturer,
            "model": self._model,
        }
        if self._area:
            info["suggested_area"] = self._area
        return info

    # ------------------------
    # PROPERTIES
    # ------------------------
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
    def hvac_modes(self):
        return self._hvac_modes

    @property
    def hvac_mode(self):
        return self._hvac_mode

    @property
    def preset_modes(self):
        return self._preset_modes

    @property
    def preset_mode(self):
        return self._preset_mode or PRESET_NONE

    @property
    def current_temperature(self):
        return self._current_temp

    @property
    def target_temperature(self):
        return self._target_temp

    @property
    def min_temp(self):
        return self._min_temp

    @property
    def max_temp(self):
        return self._max_temp

    @property
    def temperature_step(self):
        return self._temp_step

    @property
    def fan_modes(self):
        return self._fan_modes

    @property
    def fan_mode(self):
        return self._fan_mode

    # ------------------------
    # CONTROL METHODS
    # ------------------------
    def _publish(self, topic: str, payload_obj: dict):
        try:
            self._mqtt.publish(topic, json.dumps(payload_obj))
        except Exception:
            _LOGGER.exception("Failed to publish to %s", topic)

    async def async_set_hvac_mode(self, hvac_mode: str):
        # mapping back 'fan_only' to 'fan' for the device
        val = "fan" if hvac_mode == "fan_only" else hvac_mode
        topic = self._device.get("mode_command_topic") or self._command_topic
        payload = {"address": int(self._address), "type": "ir_ac", "action": "mode", "value": val, "version": "v1.0"}
        self._publish(topic, payload)

    async def async_set_temperature(self, **kwargs):
        temp = kwargs.get("temperature")
        if temp is None:
            return
        topic = self._device.get("temperature_command_topic") or self._command_topic
        payload = {"address": int(self._address), "type": "ir_ac", "action": "temperature", "value": int(temp), "version": "v1.0"}
        self._publish(topic, payload)

    async def async_set_fan_mode(self, fan_mode: str):
        mapping = {"Vlow": 1, "Low": 2, "Med": 3, "High": 4, "Top": 5, "Auto": 6}
        val = mapping.get(fan_mode, 3)
        topic = self._device.get("fan_mode_command_topic") or self._command_topic
        payload = {"address": int(self._address), "type": "ir_ac", "action": "fan_speed", "value": int(val), "version": "v1.0"}
        self._publish(topic, payload)

    async def async_set_preset_mode(self, preset_mode: str):
        topic = self._device.get("preset_mode_command_topic") or self._command_topic
        if preset_mode == "On":
            payload = {"address": int(self._address), "type": "ir_ac", "action": "power", "value": True, "version": "v1.0"}
        else:
            payload = {"address": int(self._address), "type": "ir_ac", "action": "power", "value": False, "version": "v1.0"}
        self._publish(topic, payload)
