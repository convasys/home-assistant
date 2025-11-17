"""Lytiva sensors via MQTT discovery."""
from __future__ import annotations
import logging, json
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template

_LOGGER = logging.getLogger(__name__)
DOMAIN = "lytiva"

DEFAULT_ICONS = {
    "temperature": "mdi:thermometer",
    "humidity": "mdi:water-percent",
    "illuminance": "mdi:brightness-5",
    "default": "mdi:circle-outline",
}

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    mqtt = entry_data["mqtt_client"]

    def on_sensor_discovery(client, userdata, message):
        try:
            payload = json.loads(message.payload.decode())
            unique_id = str(payload.get("unique_id"))
            if not unique_id or unique_id in entry_data["devices"]:
                return
            sensor = LytivaSensor(hass, payload, mqtt, entry.entry_id)
            entry_data["devices"][unique_id] = sensor
            hass.add_job(async_add_entities, [sensor])
            _LOGGER.info("Discovered Lytiva sensor: %s", payload.get("name"))
        except Exception as e:
            _LOGGER.error("Error discovering sensor: %s", e)

    mqtt.message_callback_add("homeassistant/sensor/+/config", on_sensor_discovery)

class LytivaSensor(SensorEntity):
    def __init__(self, hass, config: dict, mqtt, entry_id: str):
        self.hass = hass
        self._config = config
        self._mqtt = mqtt
        self._attr_name = config.get("name", "Lytiva Sensor")
        self._attr_unique_id = f"lytiva_sensor_{config.get('unique_id')}"
        self._state = None
        self._attributes = {}
        self._device_class = config.get("device_class")
        self._unit_of_measurement = config.get("unit_of_measurement")
        self._value_template = config.get("value_template")
        self._json_attributes_template = config.get("json_attributes_template")
        self._json_attributes_topic = config.get("json_attributes_topic")
        self._state_topic = config.get("state_topic")
        self._icon = config.get("icon") or DEFAULT_ICONS.get(self._device_class, DEFAULT_ICONS["default"])

        device_info = config.get("device", {})
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(device_info.get("identifiers", [self._attr_unique_id])[0]))},
            "name": device_info.get("name", self._attr_name),
            "manufacturer": device_info.get("manufacturer", "Lytiva"),
            "model": device_info.get("model", "Sensor"),
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
                attributes = {}

                if self._value_template:
                    template = Template(self._value_template, self.hass)
                    value = template.async_render({"value_json": json.loads(payload)})

                if self._json_attributes_template:
                    template = Template(self._json_attributes_template, self.hass)
                    attributes = template.async_render({"value_json": json.loads(payload)}, parse_result=False)
                    if isinstance(attributes, str):
                        try:
                            attributes = json.loads(attributes)
                        except:
                            attributes = {"raw": attributes}

                self._state = value
                self._attributes = attributes
                self.async_write_ha_state()
            except Exception as e:
                _LOGGER.error("Error updating sensor state: %s", e)

        if self._state_topic:
            self._mqtt.message_callback_add(self._state_topic, on_state_message)
            self._mqtt.subscribe(self._state_topic)

    @property
    def native_value(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attributes

    @property
    def native_unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def device_class(self):
        return self._device_class
