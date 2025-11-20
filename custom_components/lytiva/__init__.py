"""Lytiva integration with independent MQTT connection (single global MQTT handler)."""
from __future__ import annotations
import logging
import json
import asyncio
from typing import Any, Callable, Dict, List, Optional

import paho.mqtt.client as mqtt_client

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DOMAIN = "lytiva"

# default platforms the integration may forward to
PLATFORMS = [
    "light",
    "cover"
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lytiva integration with a single, central MQTT handler."""
    hass.data.setdefault(DOMAIN, {})

    broker = entry.data.get("broker")
    port = entry.data.get("port", 1883)
    username = entry.data.get("username")
    password = entry.data.get("password")
    discovery_prefix = entry.options.get("discovery_prefix", "homeassistant") if entry.options else "homeassistant"

    client_id = f"lytiva_{entry.entry_id}"
    mqtt = mqtt_client.Client(
        client_id=client_id,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
    )

    if username:
        mqtt.username_pw_set(username, password)

    # Integration shared storage
    hass.data[DOMAIN][entry.entry_id] = {
        "mqtt_client": mqtt,
        "broker": broker,
        "port": port,
        "discovery_prefix": discovery_prefix,
        # discovered payloads (raw discovery payloads by unique_id/address)
        "discovered_payloads": {},  # type: Dict[str, Dict[str, Any]]
        # entity objects created by platforms (map unique_id -> entity)
        "entities_by_unique_id": {},  # type: Dict[str, Any]
        # quick lookup by address (address may be int or str) -> entity
        "entities_by_address": {},  # type: Dict[str, Any]
        # platform registration callbacks
        "cover_callbacks": [],  # type: List[Callable[[dict], None]]
        "climate_callbacks": [],
        "fan_callbacks": [],
        "light_callbacks": [],
        "other_callbacks": [],
    }

    # Helper: registration functions for platforms to register discovery callback
    def register_cover_callback(callback: Callable[[dict], None]) -> None:
        hass.data[DOMAIN][entry.entry_id]["cover_callbacks"].append(callback)

    def register_climate_callback(callback: Callable[[dict], None]) -> None:
        hass.data[DOMAIN][entry.entry_id]["climate_callbacks"].append(callback)

    def register_fan_callback(callback: Callable[[dict], None]) -> None:
        hass.data[DOMAIN][entry.entry_id]["fan_callbacks"].append(callback)

    def register_light_callback(callback: Callable[[dict], None]) -> None:
        hass.data[DOMAIN][entry.entry_id]["light_callbacks"].append(callback)

    def register_other_callback(callback: Callable[[dict], None]) -> None:
        hass.data[DOMAIN][entry.entry_id]["other_callbacks"].append(callback)

    # expose registration helpers to hass.data for platforms to call
    hass.data[DOMAIN][entry.entry_id]["register_cover_callback"] = register_cover_callback
    hass.data[DOMAIN][entry.entry_id]["register_climate_callback"] = register_climate_callback
    hass.data[DOMAIN][entry.entry_id]["register_fan_callback"] = register_fan_callback
    hass.data[DOMAIN][entry.entry_id]["register_light_callback"] = register_light_callback
    hass.data[DOMAIN][entry.entry_id]["register_other_callback"] = register_other_callback

    #
    # Central STATUS handler: updates entity objects (by address or unique_id)
    #
    def _schedule_entity_update(entity, payload):
        """Schedule entity._update_from_payload(payload). Works for async/sync methods."""
        try:
            # If entity has async _update_from_payload
            update_coro = None
            if hasattr(entity, "_update_from_payload"):
                fn = getattr(entity, "_update_from_payload")
                if asyncio.iscoroutinefunction(fn):
                    # schedule coroutine on hass loop
                    asyncio.run_coroutine_threadsafe(fn(payload), hass.loop)
                    return
                else:
                    # sync function - schedule in executor to avoid blocking
                    hass.async_add_executor_job(fn, payload)
                    return
        except Exception as e:
            _LOGGER.exception("Error scheduling update for entity %s: %s", getattr(entity, "name", "<unknown>"), e)

    async def handle_status_message(message):
        """Coroutine handling an incoming STATUS payload (called from thread callback)."""
        try:
            raw = message.payload
            if isinstance(raw, (bytes, bytearray)):
                text = raw.decode("utf-8", errors="ignore")
            else:
                text = str(raw)

            payload = json.loads(text)
        except json.JSONDecodeError:
            _LOGGER.debug("Received non-JSON status payload on %s", getattr(message, "topic", "<unknown>"))
            return
        except Exception as e:
            _LOGGER.exception("Error decoding status payload: %s", e)
            return

        # address or unique id is necessary to map to entity
        address = payload.get("address")
        unique = payload.get("unique_id") or payload.get("uniqueId") or payload.get("uniqueid")

        entities_by_address = hass.data[DOMAIN][entry.entry_id]["entities_by_address"]
        entities_by_unique_id = hass.data[DOMAIN][entry.entry_id]["entities_by_unique_id"]

        # Try address lookup (address might be int or string)
        if address is not None:
            # try both direct and stringified matching
            ent = entities_by_address.get(address) or entities_by_address.get(str(address))
            if ent:
                _schedule_entity_update(ent, payload)
                return

        # Try unique_id lookup
        if unique:
            ent = entities_by_unique_id.get(str(unique))
            if ent:
                _schedule_entity_update(ent, payload)
                return

        # If not found, try scanning all entities (fallback)
        for ent in list(entities_by_unique_id.values()):
            try:
                ent_addr = getattr(ent, "address", None)
                ent_uid = getattr(ent, "_attr_unique_id", None) or getattr(ent, "unique_id", None)
                if ent_addr is not None and str(ent_addr) == str(address):
                    _schedule_entity_update(ent, payload)
                    return
                if ent_uid is not None and str(ent_uid) == str(unique):
                    _schedule_entity_update(ent, payload)
                    return
            except Exception:
                continue

        # no entity matched — optionally we can store this status for later
        _LOGGER.debug("Status received but no matching entity found (address=%s unique=%s)", address, unique)

    # Thread callback for paho -> schedule coroutine on hass loop
    def on_status(client, userdata, message):
        try:
            # schedule the coroutine to run safely on hass loop
            asyncio.run_coroutine_threadsafe(handle_status_message(message), hass.loop)
        except Exception as e:
            _LOGGER.exception("Failed to schedule status handler: %s", e)

    #
    # Discovery (homeassistant/+/+/config) handler: store payload and call registered callbacks
    #
    def on_discovery(client, userdata, message):
        try:
            raw = message.payload
            if isinstance(raw, (bytes, bytearray)):
                text = raw.decode("utf-8", errors="ignore")
            else:
                text = str(raw)
            payload = json.loads(text)
        except Exception:
            _LOGGER.exception("Invalid discovery JSON on %s", getattr(message, "topic", "<unknown>"))
            return

        unique_id = payload.get("unique_id") or payload.get("uniqueId") or payload.get("uniqueid") or payload.get("address")
        if unique_id is None:
            _LOGGER.debug("Discovery payload without unique id: %s", payload)
            return

        unique_id = str(unique_id)
        hass.data[DOMAIN][entry.entry_id]["discovered_payloads"][unique_id] = payload
        _LOGGER.debug("Discovery payload stored for unique_id=%s", unique_id)

        # Determine platform from topic or payload
        topic_parts = message.topic.split("/") if message.topic else []
        platform = None
        # typical discovery topic: homeassistant/<platform>/<node>/<object>/config
        if len(topic_parts) >= 2:
            platform = topic_parts[1]

        # Call appropriate callbacks (safe)
        try:
            if platform == "cover":
                for cb in list(hass.data[DOMAIN][entry.entry_id]["cover_callbacks"]):
                    hass.loop.call_soon_threadsafe(cb, payload)
            elif platform == "climate":
                for cb in list(hass.data[DOMAIN][entry.entry_id]["climate_callbacks"]):
                    hass.loop.call_soon_threadsafe(cb, payload)
            elif platform == "fan":
                for cb in list(hass.data[DOMAIN][entry.entry_id]["fan_callbacks"]):
                    hass.loop.call_soon_threadsafe(cb, payload)
            elif platform == "light":
                for cb in list(hass.data[DOMAIN][entry.entry_id]["light_callbacks"]):
                    hass.loop.call_soon_threadsafe(cb, payload)
            else:
                # call any other registered callbacks
                for cb in list(hass.data[DOMAIN][entry.entry_id]["other_callbacks"]):
                    hass.loop.call_soon_threadsafe(cb, payload)
        except Exception as e:
            _LOGGER.exception("Error calling discovery callbacks: %s", e)

    #
    # Paho MQTT connect/message handlers
    #
    def on_connect(client, userdata, flags, reason_code, *args):
        if reason_code == 0:
            _LOGGER.info("Connected to MQTT %s:%s", broker, port)
            try:
                client.publish("homeassistant/status", "online", qos=1, retain=True)
            except Exception:
                pass
            # subscribe discovery + status topics via message_callback_add (single owner)
            try:
                client.subscribe(f"{discovery_prefix}/+/+/config")
                client.message_callback_add(f"{discovery_prefix}/+/+/config", on_discovery)
            except Exception as e:
                _LOGGER.exception("Failed to subscribe discovery topic: %s", e)

            try:
                client.subscribe("LYT/+/NODE/E/STATUS")
                client.message_callback_add("LYT/+/NODE/E/STATUS", on_status)
                client.subscribe("LYT/+/GROUP/E/STATUS")
                client.message_callback_add("LYT/+/GROUP/E/STATUS", on_status)
            except Exception as e:
                _LOGGER.exception("Failed to subscribe STATUS topics: %s", e)
        else:
            _LOGGER.error("MQTT connection failed: %s", reason_code)

    def on_message_fallback(client, userdata, msg):
        # fallback - we mainly rely on message_callback_add handlers above
        _LOGGER.debug("Fallback on_message for topic %s", msg.topic)

    mqtt.on_connect = on_connect
    mqtt.on_message = on_message_fallback
    mqtt.will_set("homeassistant/status", "offline", qos=1, retain=True)

    # Connect (in executor) and start loop
    try:
        await hass.async_add_executor_job(mqtt.connect, broker, port, 60)
        await hass.async_add_executor_job(mqtt.loop_start)
    except Exception as e:
        _LOGGER.error("Could not connect/start MQTT: %s", e)
        return False

    # Forward platforms (load platform modules)
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as e:
        _LOGGER.exception("Error forwarding platforms: %s", e)

    # After platforms are loaded, force-call callbacks for any already discovered payloads
    try:
        discovered = list(hass.data[DOMAIN][entry.entry_id]["discovered_payloads"].values())
        for payload in discovered:
            topic_like = payload.get("discovery_topic") or ""
            # attempt to infer platform from payload keys
            # safe default: call light callbacks if it looks like a light
            called = False
            if "device_class" in payload and "cover" in str(payload.get("device_class","")).lower():
                for cb in hass.data[DOMAIN][entry.entry_id]["cover_callbacks"]:
                    hass.loop.call_soon_threadsafe(cb, payload)
                    called = True
            if not called and ("state_topic" in payload or "command_topic" in payload or "unique_id" in payload):
                # heuristics - call light callbacks (platforms should validate)
                for cb in hass.data[DOMAIN][entry.entry_id]["light_callbacks"]:
                    hass.loop.call_soon_threadsafe(cb, payload)
    except Exception as e:
        _LOGGER.exception("Error during initial dispatch of discovered payloads: %s", e)

    _LOGGER.info("Lytiva integration setup complete for entry %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration and stop MQTT client."""
    unload = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload:
        mqtt = hass.data[DOMAIN][entry.entry_id]["mqtt_client"]
        try:
            mqtt.publish("homeassistant/status", "offline", qos=1, retain=True)
        except Exception:
            pass
        try:
            await hass.async_add_executor_job(mqtt.loop_stop)
        except Exception:
            pass
        try:
            await hass.async_add_executor_job(mqtt.disconnect)
        except Exception:
            pass

        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload
