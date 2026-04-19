# OpenADR 3 VEN for Home Assistant

A Home Assistant custom integration that acts as an [OpenADR 3](https://www.openadr.org/) Virtual End Node (VEN). It connects to an OpenADR 3 VTN, subscribes to programs (energy pricing, GHG emissions), and surfaces real-time data as Home Assistant sensors.

## Features

- **Energy pricing sensors** — current-hour electricity price ($/kWh) with full 24-hour schedule
- **GHG emissions sensors** — marginal operating emissions rate (g CO₂/kWh)
- **MQTT push updates** — near-real-time sensor updates when the VTN supports MQTT notifications
- **Multi-program support** — subscribe to any combination of programs from the VTN
- **Daily statistics** — min, max, and average values as sensor attributes

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Go to **Integrations** → search for **OpenADR 3 VEN**
3. Click **Install**
4. **Restart Home Assistant**

### Manual

1. Copy the `custom_components/openadr3_ven` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **OpenADR 3 VEN**
3. Enter the VTN base URL
4. Select the programs you want to subscribe to

## Sensor Data

Each subscribed program creates a sensor with:

| Property | Description |
|----------|-------------|
| State | Current hour's value (price or emissions) |
| `event_name` | Name of the current event |
| `next_hour_value` | Next hour's value |
| `daily_min` / `daily_max` / `daily_avg` | Daily statistics |
| `schedule` | Full hourly schedule as a list |
| `payload_type` | `PRICE` or `GHG` |

## Compatible VTNs

Any OpenADR 3.x compliant VTN. Tested with the [Grid Coordination Energy Price Server](https://price.grid-coordination.energy/api).

## Requirements

- Home Assistant 2024.12.0 or later
- Network access to an OpenADR 3 VTN
