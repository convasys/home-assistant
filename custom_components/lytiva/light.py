"""Lytiva lights via MQTT (stable + HA compatible + area support)."""
from __future__ import annotations
import logging
import json
from typing import Any, Dict

from homeassistant.components.light import (
    LightEntity,
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP,
    ATTR_RGB_COLOR,
    ColorMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------
#  REGISTER DISCOVERY CALLBACK
# ---------------------------------------------------------
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    data = hass.data[DOMAIN][entry.entry_id]
    register_cb = data.get("register_light_callback")

    if register_cb:
        register_cb(lambda payload: _handle_discovery(hass, entry, payload, async_add_entities))
        _LOGGER.debug("Lytiva Light: discovery callback registered.")


# ---------------------------------------------------------
#  DISCOVERY HANDLER
# ---------------------------------------------------------
def _handle_discovery(hass, entry, payload, async_add_entities):
    try:
        uid = payload.get("unique_id") or payload.get("address")
        if uid is None:
            return

        uid = str(uid)

        # FIX: If address missing, use unique_id as address
        if "address" not in payload or payload.get("address") is None:
            payload["address"] = uid

        store = hass.data[DOMAIN][entry.entry_id]
        by_uid = store["entities_by_unique_id"]
        by_addr = store["entities_by_address"]

        # Duplicate check
        if uid in by_uid:
            return

        if str(payload["address"]) in by_addr:
            return

        ent = LytivaLight(hass, entry, payload)
        by_uid[uid] = ent
        by_addr[str(ent.address)] = ent

        hass.add_job(async_add_entities, [ent])

    except Exception as e:
        _LOGGER.exception("Lytiva Light discovery failed: %s", e)


# ---------------------------------------------------------
#  LIGHT ENTITY
# ---------------------------------------------------------
class LytivaLight(LightEntity):
    """Representation of a Lytiva Light."""

    def __init__(self, hass, entry, cfg):
        self.hass = hass
        self._entry = entry
        self._cfg = cfg or {}

        # Identity
        self._attr_name = cfg.get("name", "Lytiva Light")
        self._attr_unique_id = str(cfg.get("unique_id") or cfg.get("address"))

        # FIX: If address missing — use unique_id as address
        addr = cfg.get("address") if cfg.get("address") not in (None, "") else self._attr_unique_id

        try:
            self.address = int(addr)
        except:
            self.address = addr

        self.command_topic = cfg.get("command_topic")

        # Default internal state
        self._attr_is_on = False
        self._attr_brightness = 255
        self._attr_rgb_color = [255, 255, 255]

        # CCT temperature
        self._attr_min_mireds = cfg.get("min_mireds", 154)
        self._attr_max_mireds = cfg.get("max_mireds", 370)
        self._attr_color_temp = self._attr_min_mireds

        # Type detect
        typ = cfg.get("type", "")

        if "color_temp_command_topic" in cfg or typ == "cct":
            self.light_type = "cct"
        elif "rgb_command_topic" in cfg or typ == "rgb":
            self.light_type = "rgb"
        else:
            self.light_type = "dimmer"

        # Supported modes
        if self.light_type == "cct":
            self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif self.light_type == "rgb":
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        else:
            self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
            self._attr_color_mode = ColorMode.BRIGHTNESS

    # ---------------------------------------------------------
    #  DEVICE INFO (area support)
    # ---------------------------------------------------------
    @property
    def device_info(self):
        dev = self._cfg.get("device", {}) or {}

        identifiers = dev.get("identifiers")
        if isinstance(identifiers, list) and identifiers:
            identifiers = {(DOMAIN, identifiers[0])}
        else:
            identifiers = {(DOMAIN, self._attr_unique_id)}

        info = {
            "identifiers": identifiers,
            "name": dev.get("name", self._attr_name),
            "manufacturer": dev.get("manufacturer", "Lytiva"),
            "model": dev.get("model", "Light"),
        }

        if dev.get("suggested_area"):
            info["suggested_area"] = dev["suggested_area"]

        return info

    # ---------------------------------------------------------
    #  MQTT PUBLISH
    # ---------------------------------------------------------
    def _publish(self, payload):
        try:
            mqtt = self.hass.data[DOMAIN][self._entry.entry_id]["mqtt_client"]
            mqtt.publish(self.command_topic, json.dumps(payload))
        except Exception as e:
            _LOGGER.error("Light MQTT publish error: %s", e)

    # ---------------------------------------------------------
    #  TURN ON
    # ---------------------------------------------------------
    async def async_turn_on(self, **kwargs):
        payload = {"version": "v1.0", "address": self.address}

        if self.light_type == "dimmer":
            b = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)
            self._attr_brightness = b
            payload.update({"type": "dimmer", "dimming": int(b * 100 / 255)})

        elif self.light_type == "cct":
            b = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness)
            t = kwargs.get(ATTR_COLOR_TEMP, self._attr_color_temp)
            self._attr_brightness = b
            self._attr_color_temp = t

            dim = int(b * 100 / 255)
            ct_scaled = int((t - self._attr_min_mireds) * 100 /
                            (self._attr_max_mireds - self._attr_min_mireds))
            ct_scaled = 100 - ct_scaled

            payload.update({
                "type": "cct",
                "dimming": dim,
                "color_temperature": ct_scaled
            })

        elif self.light_type == "rgb":
            r, g, b = kwargs.get(ATTR_RGB_COLOR, self._attr_rgb_color)
            self._attr_rgb_color = [r, g, b]
            payload.update({"type": "rgb", "r": r, "g": g, "b": b})

        self._attr_is_on = True
        self._publish(payload)
        self.async_write_ha_state()

    # ---------------------------------------------------------
    #  TURN OFF
    # ---------------------------------------------------------
    async def async_turn_off(self, **kwargs):
        payload = {"version": "v1.0", "address": self.address}

        if self.light_type == "dimmer":
            payload.update({"type": "dimmer", "dimming": 0})

        elif self.light_type == "cct":
            payload.update({"type": "cct", "dimming": 0, "color_temperature": 0})

        elif self.light_type == "rgb":
            payload.update({"type": "rgb", "r": 0, "g": 0, "b": 0})

        self._attr_is_on = False
        self._publish(payload)
        self.async_write_ha_state()

    # ---------------------------------------------------------
    #  UPDATE FROM DEVICE PAYLOAD
    # ---------------------------------------------------------
    async def _update_from_payload(self, payload):
        try:
            if payload.get("address") != self.address:
                return

            if self.light_type == "dimmer":
                d = payload.get("dimmer", {}).get("dimming") or payload.get("dimming")
                if d is not None:
                    self._attr_brightness = int(d * 255 / 100)
                    self._attr_is_on = d > 0

            elif self.light_type == "cct":
                c = payload.get("cct")
                if isinstance(c, dict):
                    d = c.get("dimming")
                    t = c.get("color_temperature")

                    if d is not None:
                        self._attr_brightness = int(d * 255 / 100)
                        self._attr_is_on = d > 0

                    if t is not None:
                        self._attr_color_temp = round(
                            self._attr_max_mireds -
                            (t * (self._attr_max_mireds - self._attr_min_mireds) / 100)
                        )

            elif self.light_type == "rgb":
                rgb = payload.get("rgb")
                if isinstance(rgb, dict):
                    r = rgb.get("r", 0)
                    g = rgb.get("g", 0)
                    b = rgb.get("b", 0)
                    self._attr_rgb_color = [r, g, b]
                    self._attr_is_on = any([r, g, b])

            self.async_write_ha_state()

        except Exception as e:
            _LOGGER.exception("Light update error: %s", e)
