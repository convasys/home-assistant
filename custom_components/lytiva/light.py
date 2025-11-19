"""Lytiva lights via MQTT with real-time updates (handles device + group status)."""
from __future__ import annotations
import logging
import json
import asyncio

from homeassistant.components.light import (
    LightEntity,
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_RGB_COLOR,
    ColorMode,
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
    discovery_prefix = entry_data.get("discovery_prefix", "homeassistant")
    devices = entry_data["devices"]

    #
    # -------- REAL-TIME STATUS HANDLER (NODE + GROUP) --------
    #
    async def handle_state_message(message):
        try:
            raw = message.payload
            if isinstance(raw, (bytes, bytearray)):
                text = raw.decode(errors="ignore")
            else:
                text = str(raw)

            payload = json.loads(text)

            # Address from payload MUST be present
            address = payload.get("address")
            if address is None:
                return

            # normalize address to int when possible
            try:
                address_int = int(address)
            except Exception:
                address_int = None

            # Try to update any entity in devices that matches the address
            for obj in list(devices.values()):
                # Only update if object looks like an entity (has address and update method)
                if not hasattr(obj, "address") or not hasattr(obj, "_update_from_payload"):
                    continue

                try:
                    # compare both int and str
                    if address_int is not None and isinstance(obj.address, int) and obj.address == address_int:
                        await obj._update_from_payload(payload)
                    elif str(obj.address) == str(address):
                        await obj._update_from_payload(payload)
                except Exception as e:
                    _LOGGER.exception("Error updating entity from payload: %s", e)

        except json.JSONDecodeError:
            _LOGGER.error("Received invalid JSON on state topic: %s", message.topic)
        except Exception as e:
            _LOGGER.exception("Error processing STATUS message: %s", e)

    def on_state(client, userdata, message):
        # run coroutine safely on hass loop
        try:
            asyncio.run_coroutine_threadsafe(handle_state_message(message), hass.loop)
        except Exception as e:
            _LOGGER.exception("Failed to schedule state handler: %s", e)

    # Subscribe to both per-device NODE status and group status topics.
    # These topics use single-level wildcard for project_uuid.
    try:
        mqtt.message_callback_add("LYT/+/NODE/E/STATUS", on_state)
        mqtt.subscribe("LYT/+/NODE/E/STATUS")
    except Exception as e:
        _LOGGER.debug("Could not subscribe NODE status topic: %s", e)

    try:
        mqtt.message_callback_add("LYT/+/GROUP/E/STATUS", on_state)
        mqtt.subscribe("LYT/+/GROUP/E/STATUS")
    except Exception as e:
        _LOGGER.debug("Could not subscribe GROUP status topic: %s", e)

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

            # If we already created an entity for this unique_id, skip
            if unique_id in devices:
                return

            light = LytivaLight(hass, payload, mqtt)
            # store entity object by unique_id (this will be iterated by status handler)
            devices[unique_id] = light
            hass.add_job(async_add_entities, [light])

        except Exception as e:
            _LOGGER.exception("Error discovering light: %s", e)

    # Add callback for discovery (discovery_prefix/light/+/config)
    try:
        mqtt.message_callback_add(f"{discovery_prefix}/light/+/config", on_light_discovery)
    except Exception as e:
        _LOGGER.debug("Could not add discovery callback: %s", e)


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
        # Use discovery unique_id exactly to avoid duplicates
        self._attr_unique_id = str(config.get("unique_id"))

        # Address MUST always be int if possible
        addr = config.get("address") or config.get("unique_id")
        try:
            self.address = int(addr)
        except Exception:
            self.address = str(addr)

        self.command_topic = config.get("command_topic")

        # Default state
        self._attr_is_on = False
        self._attr_brightness = 255

        # Color temp: keep your existing mireds mapping but expose kelvin in HA if needed
        self._attr_min_mireds = config.get("min_mireds", 154)
        self._attr_max_mireds = config.get("max_mireds", 370)

        # Convert mired defaults to kelvin for HA compatibility (safe fallback)
        try:
            self._attr_min_color_temp_kelvin = int(round(1_000_000 / int(self._attr_max_mireds)))
            self._attr_max_color_temp_kelvin = int(round(1_000_000 / int(self._attr_min_mireds)))
        except Exception:
            self._attr_min_color_temp_kelvin = 2700
            self._attr_max_color_temp_kelvin = 6500

        # keep a color_temp attribute in mireds (for your templates) — default to min_mireds
        self._attr_color_temp = self._attr_min_mireds

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

        # LIGHT TYPE DETECTION
        self.light_type = config.get("type", "dimmer")
        if "color_temp_command_topic" in config or self.light_type == "cct":
            self.light_type = "cct"
        elif "rgb_command_topic" in config or self.light_type == "rgb":
            self.light_type = "rgb"
        elif self.light_type == "dimmer":
            self.light_type = "dimmer"
        else:
            self.light_type = "switch"

        # COLOR MODE SETUP
        if self.light_type == "cct":
            # Use COLOR_TEMP as main mode. We include BRIGHTNESS flag for dimming support.
            self._attr_color_mode = ColorMode.COLOR_TEMP
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS}
        elif self.light_type == "rgb":
            self._attr_color_mode = ColorMode.RGB
            self._attr_supported_color_modes = {ColorMode.RGB}
        elif self.light_type == "dimmer":
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        else:
            self._attr_color_mode = ColorMode.BRIGHTNESS
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

        # optimistic: we will still update from real state messages
        self._optimistic = True

    #
    # -------- MQTT PUBLISH --------
    #
    def _publish(self, payload):
        try:
            if not self.command_topic:
                _LOGGER.warning("No command_topic for %s, can't publish", self._attr_name)
                return
            self._mqtt.publish(self.command_topic, json.dumps(payload))
        except Exception as e:
            _LOGGER.error("MQTT publish failed: %s", e)

    #
    # -------- TURN ON / OFF --------
    #
    async def async_turn_on(self, **kwargs):
        payload = {"version": "v1.0", "address": self.address}

        if self.light_type == "dimmer":
            brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)
            dim = int(brightness / 255 * 100)
            payload.update({"type": "dimmer", "dimming": dim})
            self._attr_brightness = brightness

        elif self.light_type == "cct":
            brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)
            color_temp = kwargs.get(ATTR_COLOR_TEMP, self._attr_color_temp)

            dim = int(brightness / 255 * 100)
            # scale color_temp (mireds) into percent similar to your JS (inverse)
            try:
                ct_scaled = int(
                    (color_temp - self._attr_min_mireds)
                    * 100 / (self._attr_max_mireds - self._attr_min_mireds)
                )
                ct_scaled = 100 - ct_scaled
            except Exception:
                ct_scaled = 50

            payload.update({"type": "cct", "dimming": dim, "color_temperature": ct_scaled})
            self._attr_brightness = brightness
            self._attr_color_temp = color_temp

        elif self.light_type == "rgb":
            r, g, b = kwargs.get(ATTR_RGB_COLOR, self._attr_rgb_color)
            payload.update({"type": "rgb", "r": int(r), "g": int(g), "b": int(b)})
            self._attr_rgb_color = [int(r), int(g), int(b)]

        else:
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

        elif self.light_type == "rgb":
            payload.update({"type": "rgb", "r": 0, "g": 0, "b": 0})
            self._attr_rgb_color = [0, 0, 0]

        else:
            payload.update({"type": "switch", "power": False})

        self._attr_is_on = False
        self._publish(payload)
        self.async_write_ha_state()

    #
    # -------- STATUS UPDATE FROM DEVICE / GROUP --------
    #
    async def _update_from_payload(self, payload):
        try:
            # DIMMER
            if self.light_type == "dimmer":
                dim = None
                if "dimmer" in payload:
                    dim = payload["dimmer"].get("dimming")
                elif "dimming" in payload:
                    dim = payload.get("dimming")

                if dim is not None:
                    self._attr_brightness = int(dim * 255 / 100)
                    self._attr_is_on = dim > 0

            # CCT
            elif self.light_type == "cct" and "cct" in payload:
                cct = payload["cct"]
                dim = cct.get("dimming")
                ct = cct.get("color_temperature")

                if dim is not None:
                    self._attr_brightness = int(dim * 255 / 100)
                    self._attr_is_on = dim > 0

                if ct is not None:
                    # JS mapping: device percent -> mired range
                    try:
                        self._attr_color_temp = round(
                            self._attr_max_mireds
                            - (ct * (self._attr_max_mireds - self._attr_min_mireds) / 100)
                        )
                    except Exception:
                        pass

            # RGB
            elif self.light_type == "rgb" and "rgb" in payload:
                rgb = payload["rgb"]
                self._attr_rgb_color = [
                    rgb.get("r", 0),
                    rgb.get("g", 0),
                    rgb.get("b", 0),
                ]
                self._attr_is_on = any(self._attr_rgb_color)

            # SWITCH / fallback
            else:
                power = None
                # group switch may put switch.power or top-level power
                if "switch" in payload and isinstance(payload["switch"], dict):
                    power = payload["switch"].get("power")
                if power is None:
                    power = payload.get("power")
                if power is not None:
                    self._attr_is_on = bool(power)

            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.exception("Error updating light %s from payload: %s", self._attr_name, e)
