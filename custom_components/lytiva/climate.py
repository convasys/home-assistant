
"""Lytiva IR AC Climate via MQTT with enhanced debugging."""

from __future__ import annotations

import logging

import json

from jinja2 import Template

from homeassistant.components.climate import (

    ClimateEntity,

    ClimateEntityFeature,

    HVACMode,

)

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

def _parse_template(template_str, msg_payload):

    """Parse Jinja2 template with payload."""

    if not template_str:

        return None

    try:

        try:

            payload_json = json.loads(msg_payload)

        except:

            payload_json = {}

        

        t = Template(template_str)

        result = t.render(value_json=payload_json, value=msg_payload.decode() if isinstance(msg_payload, bytes) else msg_payload)

        return result.strip()

    except Exception as e:

        _LOGGER.error("Template render error: %s | Template: %s", e, template_str)

        return None

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):

    """Set up climate entities dynamically from MQTT discovery."""

    integration = hass.data[DOMAIN][entry.entry_id]

    mqtt = integration["mqtt_client"]

    devices = integration["devices"]

    

    def add_new_climate(payload):

        """Add newly discovered climate device."""

        _LOGGER.info("Attempting to add climate device: %s", payload.get("name"))

        _LOGGER.debug("Full climate payload: %s", json.dumps(payload, indent=2))

        

        if payload.get("mode_state_topic") or payload.get("device_class") == "climate":

            entity = LytivaClimateEntity(hass, entry, payload, mqtt)

            async_add_entities([entity], True)

            _LOGGER.info("✅ Lytiva climate added: %s (ID: %s)", payload.get("name"), payload.get("unique_id"))

        else:

            _LOGGER.warning("❌ Climate device missing mode_state_topic: %s", payload.get("name"))

    

    register = integration.get("register_climate_callback")

    if register:

        register(add_new_climate)

    

    # Add existing climate devices

    for device_id, payload in devices.items():

        if isinstance(payload, dict):

            # Check multiple conditions for climate devices

            is_climate = (

                payload.get("mode_state_topic") or 

                payload.get("device_class") == "climate" or

                "climate" in payload.get("name", "").lower() or

                payload.get("modes") or

                payload.get("temperature_command_topic")

            )

            

            if is_climate:

                add_new_climate(payload)

class LytivaClimateEntity(ClimateEntity, RestoreEntity):

    """Representation of IR AC (Air Conditioner)."""

    

    def __init__(self, hass, entry, payload, mqtt_client):

        self.hass = hass

        self.entry = entry

        self.payload = payload

        self._mqtt = mqtt_client

        

        self._name = payload.get("name", "Lytiva Climate")

        self._unique_id = payload.get("unique_id") or payload.get("address") or f"lytiva_climate_{id(payload)}"

        self._address = payload.get("address")

        

        # Topics

        self._topic_mode_state = payload.get("mode_state_topic")

        self._topic_mode_cmd = payload.get("mode_command_topic")

        self._topic_temp_cmd = payload.get("temperature_command_topic")

        self._topic_target_temp_state = payload.get("target_temperature_topic")

        self._topic_curr_temp_state = payload.get("current_temperature_topic")

        self._topic_fan_mode_state = payload.get("fan_mode_state_topic")

        self._topic_fan_mode_cmd = payload.get("fan_mode_command_topic")

        self._topic_preset_state = payload.get("preset_mode_state_topic")

        self._topic_preset_cmd = payload.get("preset_mode_command_topic")

        

        # Templates

        self._mode_state_template = payload.get("mode_state_template")

        self._target_temp_template = payload.get("target_temperature_template")

        self._current_temp_template = payload.get("current_temperature_template")

        self._fan_mode_state_template = payload.get("fan_mode_state_template")

        self._preset_mode_state_template = payload.get("preset_mode_state_template")

        

        self._mode_command_template = payload.get("mode_command_template")

        self._temp_command_template = payload.get("temperature_command_template")

        self._fan_mode_command_template = payload.get("fan_mode_command_template")

        self._preset_mode_command_template = payload.get("preset_mode_command_template")

        

        # Supported values

        modes = payload.get("modes", ["cool", "heat", "dry", "fan_only", "auto", "off"])

        self._hvac_modes = [HVAC_MAP.get(m, HVACMode.OFF) for m in modes if m in HVAC_MAP]

        if HVACMode.OFF not in self._hvac_modes:

            self._hvac_modes.append(HVACMode.OFF)

            

        self._fan_modes = payload.get("fan_modes", ["low", "medium", "high", "auto"])

        self._preset_modes = payload.get("preset_modes", ["On", "Off"])

        

        # Device info

        dev_meta = payload.get("device", {})

        self._manufacturer = dev_meta.get("manufacturer", "Lytiva")

        self._model = dev_meta.get("model", "IR AC")

        self._area = dev_meta.get("suggested_area")

        

        # State - START AS AVAILABLE for IR devices (they don't send state updates)

        self._available = True  # Changed to True by default for IR devices

        self._target_temp = 24

        self._current_temp = None

        self._fan_mode = self._fan_modes[0] if self._fan_modes else None

        self._hvac_mode = HVACMode.OFF

        self._preset = "Off"

        

        # Temperature limits

        self._min_temp = payload.get("min_temp", 16)

        self._max_temp = payload.get("max_temp", 30)

        self._temp_step = payload.get("temp_step", 1)

        

        _LOGGER.info("🔧 Climate entity initialized: %s", self._name)

        _LOGGER.info("   Topics - State: %s | Command: %s", self._topic_mode_state, self._topic_mode_cmd)

        _LOGGER.info("   Address: %s | Unique ID: %s", self._address, self._unique_id)

        

        # Subscribe to topics

        self._subscribe_topics()

    

    def _subscribe_topics(self):

        """Subscribe to all relevant MQTT topics."""

        topics = {

            "mode_state": self._topic_mode_state,

            "target_temp": self._topic_target_temp_state,

            "current_temp": self._topic_curr_temp_state,

            "fan_mode": self._topic_fan_mode_state,

            "preset": self._topic_preset_state,

        }

        

        subscribed_count = 0

        for topic_name, topic in topics.items():

            if topic:

                try:

                    self._mqtt.message_callback_add(topic, self._receive_update)

                    self._mqtt.subscribe(topic)

                    _LOGGER.info("✅ Subscribed to %s: %s", topic_name, topic)

                    subscribed_count += 1

                except Exception as e:

                    _LOGGER.error("❌ Failed to subscribe to %s (%s): %s", topic_name, topic, e)

        

        if subscribed_count == 0:

            _LOGGER.warning("⚠️  No topics subscribed for %s - IR device may be send-only", self._name)

    

    def _receive_update(self, client, userdata, msg):

        """Handle MQTT payloads via templates."""

        try:

            payload_str = msg.payload.decode() if isinstance(msg.payload, bytes) else str(msg.payload)

            _LOGGER.info("📥 [%s] Received on %s: %s", self._name, msg.topic, payload_str)

            

            try:

                payload_json = json.loads(payload_str)

            except:

                payload_json = {}

                _LOGGER.debug("Payload is not JSON, treating as plain text")

            

            # Check if message is for this device

            if self._address and payload_json.get("address"):

                if str(payload_json.get("address")) != str(self._address):

                    _LOGGER.debug("Message for different address, ignoring")

                    return

            

            updated = False

            

            # Mode state

            if msg.topic == self._topic_mode_state:

                if self._mode_state_template:

                    val = _parse_template(self._mode_state_template, msg.payload)

                    _LOGGER.info("   Mode parsed: %s", val)

                    if val and val in HVAC_MAP:

                        self._hvac_mode = HVAC_MAP[val]

                        updated = True

                else:

                    # Try direct value

                    val = payload_json.get("mode") or payload_str

                    if val in HVAC_MAP:

                        self._hvac_mode = HVAC_MAP[val]

                        updated = True

            

            # Target temperature

            if msg.topic == self._topic_target_temp_state:

                if self._target_temp_template:

                    val = _parse_template(self._target_temp_template, msg.payload)

                else:

                    val = payload_json.get("temperature") or payload_str

                

                try:

                    self._target_temp = float(val)

                    _LOGGER.info("   Target temp: %s°C", self._target_temp)

                    updated = True

                except:

                    pass

            

            # Current temperature

            if msg.topic == self._topic_curr_temp_state:

                if self._current_temp_template:

                    val = _parse_template(self._current_temp_template, msg.payload)

                else:

                    val = payload_json.get("current_temperature") or payload_str

                

                try:

                    self._current_temp = float(val)

                    _LOGGER.info("   Current temp: %s°C", self._current_temp)

                    updated = True

                except:

                    pass

            

            # Fan mode

            if msg.topic == self._topic_fan_mode_state:

                if self._fan_mode_state_template:

                    val = _parse_template(self._fan_mode_state_template, msg.payload)

                else:

                    val = payload_json.get("fan_mode") or payload_str

                

                if val and val in self._fan_modes:

                    self._fan_mode = val

                    _LOGGER.info("   Fan mode: %s", self._fan_mode)

                    updated = True

            

            # Preset mode

            if msg.topic == self._topic_preset_state:

                if self._preset_mode_state_template:

                    val = _parse_template(self._preset_mode_state_template, msg.payload)

                else:

                    val = payload_json.get("preset") or payload_str

                

                if val and val in self._preset_modes:

                    self._preset = val

                    _LOGGER.info("   Preset: %s", self._preset)

                    updated = True

            

            if updated or payload_json:

                self._available = True

                self.schedule_update_ha_state()

                _LOGGER.info("✅ State updated for %s", self._name)

            

        except Exception as e:

            _LOGGER.error("❌ Error processing message for %s: %s", self._name, e, exc_info=True)

    

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

            "name": self.payload.get("device", {}).get("name", self._name),

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

    def temperature_unit(self):

        return UnitOfTemperature.CELSIUS

    

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

    

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):

        """Set HVAC mode."""

        _LOGGER.info("🎛️  [%s] Setting HVAC mode to: %s", self._name, hvac_mode)

        

        if not self._mode_command_template or not self._topic_mode_cmd:

            _LOGGER.error("❌ No mode command template or topic configured")

            return

        

        mode_str = REVERSE_HVAC.get(hvac_mode)

        if not mode_str:

            _LOGGER.error("❌ Unknown HVAC mode: %s", hvac_mode)

            return

        

        try:

            template = Template(self._mode_command_template)

            payload = template.render(value=mode_str, mapping=REVERSE_HVAC)

            

            _LOGGER.info("📤 Publishing to %s: %s", self._topic_mode_cmd, payload)

            self._mqtt.publish(self._topic_mode_cmd, payload)

            

            self._hvac_mode = hvac_mode

            self.schedule_update_ha_state()

            _LOGGER.info("✅ HVAC mode set successfully")

        except Exception as e:

            _LOGGER.error("❌ Failed to set HVAC mode: %s", e, exc_info=True)

    

    async def async_set_temperature(self, **kwargs):

        """Set target temperature."""

        temp = kwargs.get("temperature")

        _LOGGER.info("🌡️  [%s] Setting temperature to: %s°C", self._name, temp)

        

        if temp is None or not self._temp_command_template or not self._topic_temp_cmd:

            _LOGGER.error("❌ Missing temperature or command config")

            return

        

        try:

            temp = max(self._min_temp, min(self._max_temp, temp))

            

            template = Template(self._temp_command_template)

            payload = template.render(value=int(temp))

            

            _LOGGER.info("📤 Publishing to %s: %s", self._topic_temp_cmd, payload)

            self._mqtt.publish(self._topic_temp_cmd, payload)

            

            self._target_temp = temp

            self.schedule_update_ha_state()

            _LOGGER.info("✅ Temperature set successfully")

        except Exception as e:

            _LOGGER.error("❌ Failed to set temperature: %s", e, exc_info=True)

    

    async def async_set_fan_mode(self, fan_mode: str):

        """Set fan mode."""

        _LOGGER.info("💨 [%s] Setting fan mode to: %s", self._name, fan_mode)

        

        if not self._fan_mode_command_template or not self._topic_fan_mode_cmd:

            _LOGGER.error("❌ No fan mode command config")

            return

        

        if fan_mode not in self._fan_modes:

            _LOGGER.error("❌ Invalid fan mode: %s", fan_mode)

            return

        

        try:

            fan_index = self._fan_modes.index(fan_mode) + 1

            

            template = Template(self._fan_mode_command_template)

            payload = template.render(value=fan_mode, mapping={fm: i+1 for i, fm in enumerate(self._fan_modes)})

            

            _LOGGER.info("📤 Publishing to %s: %s", self._topic_fan_mode_cmd, payload)

            self._mqtt.publish(self._topic_fan_mode_cmd, payload)

            

            self._fan_mode = fan_mode

            self.schedule_update_ha_state()

            _LOGGER.info("✅ Fan mode set successfully")

        except Exception as e:

            _LOGGER.error("❌ Failed to set fan mode: %s", e, exc_info=True)

    

    async def async_set_preset_mode(self, preset_mode: str):

        """Set preset mode."""

        _LOGGER.info("🔘 [%s] Setting preset to: %s", self._name, preset_mode)

        

        if not self._preset_mode_command_template or not self._topic_preset_cmd:

            _LOGGER.error("❌ No preset command config")

            return

        

        if preset_mode not in self._preset_modes:

            _LOGGER.error("❌ Invalid preset: %s", preset_mode)

            return

        

        try:

            template = Template(self._preset_mode_command_template)

            payload = template.render(value=preset_mode)

            

            _LOGGER.info("📤 Publishing to %s: %s", self._topic_preset_cmd, payload)

            self._mqtt.publish(self._topic_preset_cmd, payload)

            

            self._preset = preset_mode

            self.schedule_update_ha_state()

            _LOGGER.info("✅ Preset set successfully")

        except Exception as e:

            _LOGGER.error("❌ Failed to set preset: %s", e, exc_info=True)

    

    async def async_added_to_hass(self):

        """Restore previous state when added to hass."""

        await super().async_added_to_hass()

        

        old_state = await self.async_get_last_state()

        if old_state is not None:

            _LOGGER.info("🔄 Restoring previous state for %s", self._name)

            

            if old_state.state in [mode.value for mode in HVACMode]:

                self._hvac_mode = HVACMode(old_state.state)

            

            if old_state.attributes.get("temperature"):

                self._target_temp = float(old_state.attributes["temperature"])

            

            if old_state.attributes.get("fan_mode"):

                self._fan_mode = old_state.attributes["fan_mode"]

            

            if old_state.attributes.get("preset_mode"):

                self._preset = old_state.attributes["preset_mode"]

            

            _LOGGER.info("✅ Previous state restored")

