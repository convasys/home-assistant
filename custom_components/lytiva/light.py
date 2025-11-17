"""Support for Lytiva lights via MQTT."""
from __future__ import annotations
import logging
import json

from homeassistant.components.light import (
    LightEntity,
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_RGB_COLOR,
    ColorMode
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)
DOMAIN = "lytiva"

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    mqtt = entry_data["mqtt_client"]
    discovery_prefix = entry_data["discovery_prefix"]

    def on_light_discovery(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode())
            unique_id = payload.get("unique_id")
            if not unique_id:
                return

            if unique_id in entry_data["devices"]:
                return

            light = LytivaLight(hass, payload, mqtt)
            entry_data["devices"][unique_id] = light
            hass.add_job(async_add_entities, [light])

        except Exception as e:
            _LOGGER.error("Error discovering light: %s", e)

    mqtt.message_callback_add(f"{discovery_prefix}/light/+/config", on_light_discovery)


class LytivaLight(LightEntity):
    """Representation of a Lytiva Light."""

    def __init__(self, hass: HomeAssistant, config: dict, mqtt) -> None:
        self.hass = hass
        self._config = config
        self._mqtt = mqtt

        self._attr_name = config.get("name", "Lytiva Light")
        self._attr_unique_id = f"lytiva_{config.get('unique_id')}"
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_color_temp = 154
        self._attr_rgb_color = [255, 255, 255]

        # Device info
        device_info = config.get("device", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._attr_unique_id)},
            "name": device_info.get("name", self._attr_name),
            "manufacturer": device_info.get("manufacturer", "Lytiva"),
            "model": device_info.get("model", "Smart Light"),
            "suggested_area": device_info.get("suggested_area"),
        }

        # Determine type
        self.light_type = config.get("type", "dimmer")
        if "color_temp_command_topic" in config or self.light_type == "cct":
            self.light_type = "cct"
        elif self.light_type == "rgb" or "rgb_command_topic" in config:
            self.light_type = "rgb"
        elif self.light_type == "dimmer":
            self.light_type = "dimmer"
        else:
            self.light_type = "switch"

        self.address = config.get("address")
        self.command_topic = config.get("command_topic")

        # Color mode
        if self.light_type == "cct":
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS, ColorMode.COLOR_TEMP}
            self._attr_min_mireds = config.get("min_mireds", 154)
            self._attr_max_mireds = config.get("max_mireds", 370)
        elif self.light_type == "rgb":
            self._attr_color_mode = ColorMode.RGB
            self._attr_supported_color_modes = {ColorMode.RGB}
        elif self.light_type == "dimmer":
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.ONOFF
            self._attr_supported_color_modes = {ColorMode.ONOFF}

        self._optimistic = True  # immediate HA update

    def _publish(self, payload):
        try:
            self._mqtt.publish(self.command_topic, json.dumps(payload))
        except Exception as e:
            _LOGGER.error("MQTT publish failed: %s", e)

    async def async_turn_on(self, **kwargs):
        brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
        color_temp = kwargs.get(ATTR_COLOR_TEMP, self._attr_max_mireds if self.light_type == "cct" else None)
        rgb = kwargs.get(ATTR_RGB_COLOR, self._attr_rgb_color if self.light_type == "rgb" else None)

        payload = {"version": "v1.0", "address": self.address}

        if self.light_type == "dimmer":
            dimming_value = int((brightness / 255) * 100)
            payload.update({"type": "dimmer", "dimming": dimming_value})
            self._attr_brightness = brightness

        elif self.light_type == "cct":
            dimming_value = int((brightness / 255) * 100)
            ct_value = 100
            if color_temp is not None:
                ct_value = int((color_temp - self._attr_min_mireds) * 100 / (self._attr_max_mireds - self._attr_min_mireds))
                ct_value = 100 - ct_value  # reverse for device
            payload.update({"type": "cct", "dimming": dimming_value, "color_temperature": ct_value})
            self._attr_brightness = brightness
            self._attr_color_temp = color_temp

        elif self.light_type == "rgb":
            r, g, b = rgb
            payload.update({
                "type": "rgb",
                "r": r,
                "g": g,
                "b": b
            })
            self._attr_rgb_color = [r, g, b]

        else:  # switch
            payload.update({"type": "switch", "power": True})

        self._attr_is_on = True
        self._publish(payload)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        payload = {"version": "v1.0", "address": self.address}
        if self.light_type == "dimmer":
            payload.update({"type": "dimmer", "dimming": 0})
            self._attr_brightness = 0
        elif self.light_type == "cct":
            payload.update({"type": "cct", "dimming": 0, "color_temperature": 0})
            self._attr_brightness = 0
            self._attr_color_temp = self._attr_min_mireds
        elif self.light_type == "rgb":
            payload.update({"type": "rgb", "r": 0, "g": 0, "b": 0})
            self._attr_rgb_color = [0, 0, 0]
        else:
            payload.update({"type": "switch", "power": False})
        self._attr_is_on = False
        self._publish(payload)
        self.async_write_ha_state()
