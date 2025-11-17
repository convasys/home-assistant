# Lytiva Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)

Lytiva MQTT integration for Home Assistant.

## Installation via HACS

### Option 1: Add Custom Repository
1. Make sure you have [HACS](https://hacs.xyz/) installed
2. Go to HACS → Integrations
3. Click the 3 dots menu (top right) → Custom repositories
4. Add this repository URL: `https://github.com/convasys/home-assistant`
5. Select category: **Integration**
6. Click **Add**
7. Find "Lytiva" in the list and click **Download**
8. Restart Home Assistant
9. Go to Settings → Devices & Services → Add Integration
10. Search for "Lytiva" and configure

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for **Lytiva**
4. Enter your MQTT broker details:
   - MQTT Broker IP
   - Port (default: 1883)
   - Username (optional)
   - Password (optional)
   - Discovery Prefix (default: homeassistant)

## Supported Platforms

- Lights
- Switches
- Sensors
- Binary Sensors
- Covers (Blinds/Curtains)
- Climate (HVAC)
- Fans
- Scenes

## Requirements

- Home Assistant 2024.1.0 or newer
- MQTT Broker (Mosquitto recommended)
- Lytiva devices connected to MQTT

## Support

For issues and feature requests, please use [GitHub Issues](https://github.com/convasys/home-assistant/issues).