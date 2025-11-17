"""Support for Lytiva switches via MQTT."""
from __future__ import annotations
import logging
import json

from homeassistant.components.switch import SwitchEntity
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
    """Set up Lytiva switches."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    mqtt = entry_data["mqtt_client"]
    discovery_prefix = entry_data["discovery_prefix"]

    def on_switch_discovery(client, userdata, message):
        """Handle switch discovery."""
        try:
            payload = json.loads(message.payload.decode())
            unique_id = payload.get("unique_id")

            if not unique_id:
                return

            # Check if already added
            if unique_id in entry_data["devices"]:
                return

            # Create switch
            switch = LytivaSwitch(hass, payload, mqtt, config_entry.entry_id)
            entry_data["devices"][unique_id] = switch

            # Add to HA
            hass.add_job(async_add_entities, [switch])
            _LOGGER.info("Discovered Lytiva switch: %s", payload.get("name"))
        except Exception as e:
            _LOGGER.error("Error discovering switch: %s", e)

    mqtt.message_callback_add(f"{discovery_prefix}/switch/+/config", on_switch_discovery)

class LytivaSwitch(SwitchEntity):
    """Representation of a Lytiva Switch."""

    def __init__(self, hass: HomeAssistant, config: dict, mqtt, entry_id: str) -> None:
        """Initialize the switch."""
        self.hass = hass
        self._config = config
        self._mqtt = mqtt
        self._attr_name = config.get("name", "Lytiva Switch")
        self._attr_unique_id = f"lytiva_{config.get('unique_id')}"
        self._attr_is_on = False
        self._value_template_str = config.get("value_template")
        self._payload_on = config.get("payload_on")
        self._payload_off = config.get("payload_off")
        self._state_on = config.get("state_on", "ON")
        self._state_off = config.get("state_off", "OFF")
        self._state_topic = config.get("state_topic")
        self._command_topic = config.get("command_topic")
        self._icon = config.get("icon", "mdi:toggle-switch")

        # Device info - EXACTLY like lights
        device_info = config.get("device", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(device_info.get("identifiers", [self._attr_unique_id])[0]))},
            "name": device_info.get("name", self._attr_name),
            "manufacturer": device_info.get("manufacturer", "Lytiva"),
            "model": device_info.get("model", "Switch"),
            "suggested_area": device_info.get("suggested_area"),
        }

    @property
    def icon(self):
        return self._icon

    @property
    def is_on(self):
        return self._attr_is_on

    async def async_added_to_hass(self) -> None:
        """Subscribe to MQTT topics."""
        def on_state_message(client, userdata, message):
            """Handle state messages."""
            try:
                payload = message.payload.decode()

                # If there's a value_template, render it
                if self._value_template_str:
                    try:
                        payload_json = json.loads(payload)
                        from homeassistant.helpers.template import Template
                        template = Template(self._value_template_str, self.hass)
                        value = template.async_render({"value_json": payload_json}, parse_result=False)
                    except Exception as e:
                        _LOGGER.error("Template error for %s: %s", self._attr_name, e)
                        value = payload
                else:
                    value = payload

                # Check state
                if str(value).strip() == self._state_on:
                    self._attr_is_on = True
                elif str(value).strip() == self._state_off:
                    self._attr_is_on = False

                self.schedule_update_ha_state()

            except Exception as e:
                _LOGGER.error("Error processing state for %s: %s", self._attr_name, e)

        state_topic = self._config.get("state_topic")
        if state_topic:
            self._mqtt.message_callback_add(state_topic, on_state_message)
            await self.hass.async_add_executor_job(self._mqtt.subscribe, state_topic)

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the switch on."""
        await self.hass.async_add_executor_job(
            self._mqtt.publish,
            self._command_topic,
            self._payload_on
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the switch off."""
        await self.hass.async_add_executor_job(
            self._mqtt.publish,
            self._command_topic,
            self._payload_off
        )
        self._attr_is_on = False
        self.async_write_ha_state()
