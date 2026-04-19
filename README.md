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

Any OpenADR 3.x compliant VTN.

### Grid Coordination Energy Price Server (California, USA)

If you live in California, you can connect to the **Grid Coordination Energy Price Server**, a free, no-authentication VTN providing real-time marginal electricity pricing and GHG emissions data:

- **VTN URL:** `https://price.grid-coordination.energy/openadr3/3.1.0`
- **Coverage:** PG&E and SCE service territories (492 pricing programs by circuit/substation) plus 11 MOER GHG emissions programs
- **Data sources:** CAISO Day-Ahead Market pricing via GridX, marginal emissions from [SGIP Signal](https://sgipsignal.com)

See the [Price Server User Guide](https://github.com/grid-coordination/price-server-user-guide) for full details on available programs, data format, MQTT support, and example API usage.

## Limitations

- **No authentication support yet** — currently only VTNs that allow anonymous/unauthenticated access are supported. OAuth2 and token-based authentication are planned for a future release.

## Requirements

- Home Assistant 2024.12.0 or later
- Network access to an OpenADR 3 VTN (anonymous access)
