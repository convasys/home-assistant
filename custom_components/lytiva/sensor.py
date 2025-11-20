"""Lytiva IR AC Climate via central MQTT STATUS updates."""
from __future__ import annotations
import logging
import json

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACMode
from homeassistant.const import UnitOfTemperature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

HVAC_MAP = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "dry": HVACMode.DRY,
    "fan_only": HVACMode.FAN_ONLY,
    "auto": HVACMode.AUTO,
    "off": HVACMode.OFF,
}
REVERSE_HVAC = {v: k for k, v in HVAC_MAP.items()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    """Set up Lytiva climate devices via central integration."""
    integration = hass.data[DOMAIN][entry.entry_id]

    # Add existing devices
    entities = []
    for device_id, payload in integration.get("devices", {}).items():
        if payload.get("type") == "ir_ac" or payload.get("mode_state_topic"):
            entities.append(LytivaClimateEntity(hass, entry, payload, integration))
    if entities:
        async_add_entities(entities, True)

    # Register callback to handle new devices dynamically
    def register_callback(new_payload):
        entity = LytivaClimateEntity(hass, entry, new_payload, integration)
        async_add_entities([entity], True)

    register_fn = integration.get("register_climate_callback")
    if register_fn:
        register_fn(register_callback)


class LytivaClimateEntity(ClimateEntity, RestoreEntity):
    """IR AC climate device that listens to central STATUS updates."""

    def __init__(self, hass, entry, payload, integration):
        self.hass = hass
        self.entry = entry
        self.payload = payload
        self._integration = integration

        self._name = payload.get("name", "Lytiva Climate")
        self._unique_id = payload.get("unique_id") or f"lytiva_climate_{payload.get('address')}"
        self._address = payload.get("address")

        # Supported modes
        modes = payload.get("modes", ["cool", "heat", "dry", "fan_only", "auto", "off"])
        self._hvac_modes = [HVAC_MAP.get(m, HVACMode.OFF) for m in modes]
        if HVACMode.OFF not in self._hvac_modes:
            self._hvac_modes.append(HVACMode.OFF)

        self._fan_modes = payload.get("fan_modes", ["Vlow", "Low", "Med", "High", "Top", "Auto"])
        self._preset_modes = payload.get("preset_modes", ["On", "Off"])

        self._manufacturer = payload.get("device", {}).get("manufacturer", "Lytiva")
        self._model = payload.get("device", {}).get("model", "IR AC")

        # Initial state
        self._hvac_mode = HVACMode.OFF
        self._target_temp = 24
        self._current_temp = None
        self._fan_mode = self._fan_modes[0]
        self._preset = "Off"
        self._available = True

        self._min_temp = payload.get("min_temp", 16)
        self._max_temp = payload.get("max_temp", 30)
        self._temp_step = payload.get("temp_step", 1)

        # Subscribe to central handler
        self._integration.setdefault("climate_entities", []).append(self)

        _LOGGER.info("✅ Climate initialized: %s (ID: %s)", self._name, self._unique_id)

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
    def fan_modes(self):
        return self._fan_modes

    @property
    def fan_mode(self):
        return self._fan_mode

    @property
    def preset_modes(self):
        return self._preset_modes

    @property
    def preset_mode(self):
        return self._preset

    @property
    def temperature_unit(self):
        return UnitOfTemperature.CELSIUS

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
    def target_temperature_step(self):
        return self._temp_step

    @property
    def supported_features(self):
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self._preset_modes:
            features |= ClimateEntityFeature.PRESET_MODE
        return features

    async def _update_from_status(self, status_payload: dict):
        """Update climate state from central STATUS payload."""
        try:
            if status_payload.get("address") != self._address:
                return

            ir = status_payload.get("ir_ac", {})
            updated = False

            # Power/preset
            power = ir.get("power")
            if power is not None:
                self._preset = "On" if power else "Off"
                updated = True

            # Mode
            mode = ir.get("mode")
            if mode == "fan":
                mode = "fan_only"
            if mode in HVAC_MAP:
                self._hvac_mode = HVAC_MAP[mode]
                updated = True

            # Target temperature
            temp = ir.get("temperature")
            if temp is not None:
                self._target_temp = float(temp)
                updated = True

            # Current temperature
            current_temp = ir.get("current_temperature")
            if current_temp is not None:
                self._current_temp = float(current_temp)
                updated = True

            # Fan speed mapping
            fan_speed = ir.get("fan_speed")
            if fan_speed is not None:
                mapping = {0: "Vlow", 1: "Low", 2: "Med", 3: "High", 4: "Top", 5: "Auto"}
                self._fan_mode = mapping.get(fan_speed, self._fan_modes[0])
                updated = True

            if updated:
                self._available = True
                self.schedule_update_ha_state()
        except Exception as e:
            _LOGGER.error("❌ Failed to update climate entity: %s", e, exc_info=True)

    async def async_added_to_hass(self):
        """Restore previous state when added to hass."""
        await super().async_added_to_hass()
        old_state = await self.async_get_last_state()
        if old_state:
            self._hvac_mode = HVACMode(old_state.state) if old_state.state in [m.value for m in HVACMode] else HVACMode.OFF
            self._target_temp = float(old_state.attributes.get("temperature", self._target_temp))
            self._fan_mode = old_state.attributes.get("fan_mode", self._fan_mode)
            self._preset = old_state.attributes.get("preset_mode", self._preset)
