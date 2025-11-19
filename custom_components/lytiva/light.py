"""Lytiva lights via MQTT with real-time updates."""
from __future__ import annotations
import logging
import json
import asyncio

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
    devices = entry_data["devices"]

    #
    # -------- REAL-TIME STATUS HANDLER --------
    #
    async def handle_state_message(message):
        try:
            payload = json.loads(message.payload.decode())

            # Address from payload MUST be int
            address = payload.get("address")
            if address is None:
                return

            address = int(address)

            # Match using integer address
            for light in devices.values():
                if light.address == address:
                    await light._update_from_payload(payload)

        except Exception as e:
            _LOGGER.error("Error processing STATUS: %s", e)

    def on_state(client, userdata, message):
        asyncio.run_coroutine_threadsafe(
            handle_state_message(message), hass.loop
        )

    mqtt.message_callback_add("LYT/+/NODE/E/STATUS", on_state)
    mqtt.subscribe("LYT/+/NODE/E/STATUS")

    #
    # -------- AUTO DISCOVERY HANDLER --------
    #
    def on_light_discovery(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode())
            unique_id = payload.get("unique_id")
            if unique_id is None:
                return

            unique_id = str(unique_id)

            if unique_id in devices:
                return

            light = LytivaLight(hass, payload, mqtt)
            devices[unique_id] = light
            hass.add_job(async_add_entities, [light])

        except Exception as e:
            _LOGGER.error("Error discovering light: %s", e)

    mqtt.message_callback_add(f"{discovery_prefix}/light/+/config", on_light_discovery)


#
# -------- LIGHT ENTITY CLASS --------
#
class LytivaLight(LightEntity):

    def __init__(self, hass: HomeAssistant, config: dict, mqtt) -> None:
        self.hass = hass
        self._mqtt = mqtt
        self._config = config

        # Basic fields
        self._attr_name = config.get("name", "Lytiva Light")
        self._attr_unique_id = str(config.get("unique_id"))

        # Address MUST always be int
        addr = config.get("address") or config.get("unique_id")
        self.address = int(addr)

        self.command_topic = config.get("command_topic")

        # Default state
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_color_temp = 200
        self._attr_rgb_color = [255, 255, 255]

        # Device Info
        dev = config.get("device", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self._attr_unique_id)},
            "manufacturer": dev.get("manufacturer", "Lytiva"),
            "model": dev.get("model", "Light"),
            "name": dev.get("name", self._attr_name),
            "suggested_area": dev.get("suggested_area"),
        }

        #
        # -------- LIGHT TYPE DETECTION --------
        #
        self.light_type = config.get("type", "dimmer")

        if "color_temp_command_topic" in config or self.light_type == "cct":
            self.light_type = "cct"
        elif "rgb_command_topic" in config or self.light_type == "rgb":
            self.light_type = "rgb"
        elif self.light_type == "dimmer":
            self.light_type = "dimmer"
        else:
            self.light_type = "switch"

        #
        # -------- COLOR MODE SETUP --------
        #
        if self.light_type == "cct":
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS, ColorMode.COLOR_TEMP}
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_min_mireds = config.get("min_mireds", 154)
            self._attr_max_mireds = config.get("max_mireds", 370)

        elif self.light_type == "rgb":
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB

        elif self.light_type == "dimmer":
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS

        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

    #
    # -------- MQTT PUBLISH --------
    #
    def _publish(self, payload):
        try:
            self._mqtt.publish(self.command_topic, json.dumps(payload))
        except Exception as e:
            _LOGGER.error("MQTT publish failed: %s", e)

    #
    # -------- TURN ON --------
    #
    async def async_turn_on(self, **kwargs):
        payload = {"version": "v1.0", "address": self.address}

        if self.light_type == "dimmer":
            brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
            dim = int(brightness / 255 * 100)
            payload.update({"type": "dimmer", "dimming": dim})
            self._attr_brightness = brightness

        elif self.light_type == "cct":
            brightness = kwargs.get(ATTR_BRIGHTNESS, 255)
            color_temp = kwargs.get(ATTR_COLOR_TEMP, self._attr_color_temp)
            dim = int(brightness / 255 * 100)
            ct_scaled = int(
                (color_temp - self._attr_min_mireds)
                * 100 / (self._attr_max_mireds - self._attr_min_mireds)
            )
            ct_scaled = 100 - ct_scaled

            payload.update({
                "type": "cct",
                "dimming": dim,
                "color_temperature": ct_scaled
            })

            self._attr_brightness = brightness
            self._attr_color_temp = color_temp

        elif self.light_type == "rgb":
            r, g, b = kwargs.get(ATTR_RGB_COLOR, self._attr_rgb_color)
            payload.update({"type": "rgb", "r": r, "g": g, "b": b})
            self._attr_rgb_color = [r, g, b]

        else:
            payload.update({"type": "switch", "power": True})

        self._attr_is_on = True
        self._publish(payload)
        self.async_write_ha_state()

    #
    # -------- TURN OFF --------
    #
    async def async_turn_off(self, **kwargs):
        payload = {"version": "v1.0", "address": self.address}

        if self.light_type == "dimmer":
            payload.update({"type": "dimmer", "dimming": 0})
            self._attr_brightness = 0

        elif self.light_type == "cct":
            payload.update({"type": "cct", "dimming": 0, "color_temperature": 0})
            self._attr_brightness = 0

        elif self.light_type == "rgb":
            payload.update({"type": "rgb", "r": 0, "g": 0, "b": 0})
            self._attr_rgb_color = [0, 0, 0]

        else:
            payload.update({"type": "switch", "power": False})

        self._attr_is_on = False
        self._publish(payload)
        self.async_write_ha_state()

    #
    # -------- STATUS UPDATE FROM DEVICE --------
    #
    async def _update_from_payload(self, payload):
        try:
            #
            # DIMMER
            #
            if self.light_type == "dimmer":
                dim = None

                if "dimmer" in payload:
                    dim = payload["dimmer"].get("dimming")
                elif "dimming" in payload:
                    dim = payload.get("dimming")

                if dim is not None:
                    self._attr_brightness = int(dim * 255 / 100)
                    self._attr_is_on = dim > 0

            #
            # CCT
            #
            elif self.light_type == "cct" and "cct" in payload:
                cct = payload["cct"]

                dim = cct.get("dimming")
                ct = cct.get("color_temperature")

                if dim is not None:
                    self._attr_brightness = int(dim * 255 / 100)
                    self._attr_is_on = dim > 0

                if ct is not None:
                    self._attr_color_temp = round(
                        self._attr_max_mireds
                        - (ct * (self._attr_max_mireds - self._attr_min_mireds) / 100)
                    )

            #
            # RGB
            #
            elif self.light_type == "rgb" and "rgb" in payload:
                rgb = payload["rgb"]
                self._attr_rgb_color = [
                    rgb.get("r", 0),
                    rgb.get("g", 0),
                    rgb.get("b", 0),
                ]
                self._attr_is_on = any(self._attr_rgb_color)

            #
            # SWITCH
            #
            else:
                power = payload.get("power")
                if power is not None:
                    self._attr_is_on = bool(power)

            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.error(
                "Error updating light %s from payload: %s",
                self._attr_name,
                e,
            )
