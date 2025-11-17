"""Lytiva integration with independent MQTT connection."""
from __future__ import annotations
import logging
import json
import asyncio
import paho.mqtt.client as mqtt_client

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__name__)

DOMAIN = "lytiva"
PLATFORMS = [
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SCENE,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.FAN,
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lytiva integration."""
    hass.data.setdefault(DOMAIN, {})

    broker = entry.data.get("broker")
    port = entry.data.get("port", 1883)
    username = entry.data.get("username")
    password = entry.data.get("password")
    discovery_prefix = "homeassistant"

    client_id = f"lytiva_{entry.entry_id}"
    mqtt = mqtt_client.Client(
        client_id=client_id,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2
    )

    if username:
        mqtt.username_pw_set(username, password)

    hass.data[DOMAIN][entry.entry_id] = {
        "mqtt_client": mqtt,
        "broker": broker,
        "port": port,
        "discovery_prefix": discovery_prefix,
        "devices": {},
        "cover_callbacks": [],
        "climate_callbacks": [],
        "fan_callbacks": [],
    }

    def register_cover_callback(callback):
        hass.data[DOMAIN][entry.entry_id]["cover_callbacks"].append(callback)
    def register_climate_callback(callback):
        hass.data[DOMAIN][entry.entry_id]["climate_callbacks"].append(callback)
    def register_fan_callback(callback):
        hass.data[DOMAIN][entry.entry_id]["fan_callbacks"].append(callback)

    hass.data[DOMAIN][entry.entry_id]["register_cover_callback"] = register_cover_callback
    hass.data[DOMAIN][entry.entry_id]["register_climate_callback"] = register_climate_callback
    hass.data[DOMAIN][entry.entry_id]["register_fan_callback"] = register_fan_callback

    # ========== MQTT CALLBACKS ==========

    def on_connect(client, userdata, flags, reason_code, *args):
        if reason_code == 0:
            _LOGGER.info("Connected to MQTT %s:%s", broker, port)
            client.publish("homeassistant/status", "online", qos=1, retain=True)
            client.subscribe(f"{discovery_prefix}/+/+/config")
        else:
            _LOGGER.error("MQTT connection failed: %s", reason_code)

    def on_message(client, userdata, msg):
        """Process MQTT discovery messages."""
        try:
            payload = json.loads(msg.payload)
        except Exception:
            _LOGGER.error("Invalid JSON on topic: %s", msg.topic)
            return

        topic_parts = msg.topic.split("/")
        if len(topic_parts) < 4:
            return

        platform = topic_parts[1]
        device_id = payload.get("unique_id") or payload.get("address")
        if not device_id:
            return

        devices = hass.data[DOMAIN][entry.entry_id]["devices"]
        first_time = device_id not in devices

        devices[device_id] = payload

        # Dynamic add for cover / climate / fan
        if first_time:
            if platform == "cover":
                for cb in hass.data[DOMAIN][entry.entry_id]["cover_callbacks"]:
                    hass.loop.call_soon_threadsafe(cb, payload)
            elif platform == "climate":
                for cb in hass.data[DOMAIN][entry.entry_id]["climate_callbacks"]:
                    hass.loop.call_soon_threadsafe(cb, payload)
            elif platform == "fan":
                for cb in hass.data[DOMAIN][entry.entry_id]["fan_callbacks"]:
                    hass.loop.call_soon_threadsafe(cb, payload)

    mqtt.on_connect = on_connect
    mqtt.on_message = on_message
    mqtt.will_set("homeassistant/status", "offline", qos=1, retain=True)

    try:
        await hass.async_add_executor_job(mqtt.connect, broker, port, 60)
        mqtt.loop_start()
    except Exception as e:
        _LOGGER.error("Could not connect to MQTT: %s", e)
        return False

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload Lytiva and disconnect MQTT."""
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload:
        mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]
        mqtt.publish("homeassistant/status", "offline", qos=1, retain=True)
        mqtt.loop_stop()
        mqtt.disconnect()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload
