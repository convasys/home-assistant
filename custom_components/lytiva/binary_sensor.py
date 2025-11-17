"""Lytiva binary sensors via MQTT discovery."""
from __future__ import annotations
import logging, json
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template

_LOGGER = logging.getLogger(__name__)
DOMAIN = "lytiva"

DEFAULT_ICONS = {
    "motion": "mdi:motion-sensor",
    "occupancy": "mdi:account-multiple",
    "default": "mdi:circle-outline",
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    mqtt = entry_data["mqtt_client"]

    def on_bs_discovery(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode())
            unique_id = str(payload.get("unique_id"))
            if not unique_id or unique_id in entry_data["devices"]:
                return
            bs = LytivaBinarySensor(hass, payload, mqtt, entry.entry_id)
            entry_data["devices"][unique_id] = bs
            hass.add_job(async_add_entities, [bs])
            _LOGGER.info("Discovered Lytiva binary sensor: %s", payload.get("name"))
        except Exception as e:
            _LOGGER.error("Error discovering binary sensor: %s", e)

    mqtt.message_callback_add("homeassistant/binary_sensor/+/config", on_bs_discovery)

class LytivaBinarySensor(BinarySensorEntity):
    def __init__(self, hass, config: dict, mqtt, entry_id: str):
        self.hass = hass
        self._config = config
        self._mqtt = mqtt
        self._attr_name = config.get("name", "Lytiva Binary Sensor")
        self._attr_unique_id = f"lytiva_bs_{config.get('unique_id')}"
        self._state = None
        self._device_class = config.get("device_class")
        self._value_template = config.get("value_template")
        self._payload_on = config.get("payload_on", "ON")
        self._payload_off = config.get("payload_off", "OFF")
        self._state_topic = config.get("state_topic")
        self._icon = config.get("icon") or DEFAULT_ICONS.get(self._device_class, DEFAULT_ICONS["default"])

        device_info = config.get("device", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(device_info.get("identifiers", [self._attr_unique_id])[0]))},
            "name": device_info.get("name", self._attr_name),
            "manufacturer": device_info.get("manufacturer", "Lytiva"),
            "model": device_info.get("model", "Binary Sensor"),
            "suggested_area": device_info.get("suggested_area", "Unknown"),
        }

    @property
    def icon(self):
        return self._icon

    async def async_added_to_hass(self):
        def on_state_message(client, userdata, message):
            try:
                payload = message.payload.decode()
                value = payload
                if self._value_template:
                    template = Template(self._value_template, self.hass)
                    value = template.async_render({"value_json": json.loads(payload)})
                self._state = value == self._payload_on
                self.async_write_ha_state()
            except Exception as e:
                _LOGGER.error("Error updating binary sensor state: %s", e)

        if self._state_topic:
            self._mqtt.message_callback_add(self._state_topic, on_state_message)
            self._mqtt.subscribe(self._state_topic)

    @property
    def is_on(self):
        return self._state

    @property
    def device_class(self):
        return self._device_class
