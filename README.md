# OpenADR 3 VEN for Home Assistant

[![HACS Validation](https://github.com/grid-coordination/openadr3-ven-hass/actions/workflows/hacs.yml/badge.svg)](https://github.com/grid-coordination/openadr3-ven-hass/actions/workflows/hacs.yml)
[![Hassfest Validation](https://github.com/grid-coordination/openadr3-ven-hass/actions/workflows/hassfest.yml/badge.svg)](https://github.com/grid-coordination/openadr3-ven-hass/actions/workflows/hassfest.yml)
[![GitHub Release](https://img.shields.io/github/v/release/grid-coordination/openadr3-ven-hass)](https://github.com/grid-coordination/openadr3-ven-hass/releases)
[![License](https://img.shields.io/github/license/grid-coordination/openadr3-ven-hass)](LICENSE)

A Home Assistant custom integration that acts as an [OpenADR 3](https://www.openadr.org/) Virtual End Node (VEN). It connects to an OpenADR 3 VTN, subscribes to programs (energy pricing, GHG emissions), and surfaces real-time data as Home Assistant sensors.

> [!IMPORTANT]
> **This integration is in active early development.** We're learning from user feedback as real-world VTNs come online — multi-payload programs, sub-hourly intervals, and two-way (import/export) tariffs have each driven structural changes. Expect occasional breaking changes between minor versions when they're needed to fix correctness issues or to align with Home Assistant's modern patterns. Read the release notes before updating, and pin to a specific version if you need stability between upgrades.

## Features

- **One sensor per OpenADR 3 payload type** — `PRICE`, `EXPORT_PRICE`, `GHG`, and any other payload type a program publishes are each exposed as their own Home Assistant sensor
- **Native interval granularity** — sub-hourly intervals (PT5M / PT15M / PT30M) are preserved; the integration does not down-sample to hourly
- **Forecast via service call** — full multi-day forecast available on demand via the `openadr3_ven.get_forecast` service, matching Home Assistant's modern weather-forecast pattern
- **MQTT push updates** — near-real-time sensor updates when the VTN supports MQTT notifications
- **Multi-program support** — subscribe to any combination of programs from the VTN
- **Daily statistics** — duration-weighted min, max, and average values as sensor attributes

## Installation

### HACS (recommended)

> [!NOTE]
> **Early access — not yet in the HACS default repository.**
> Until this integration is listed in HACS, add it as a custom repository first.
>
> **One-click via [My Home Assistant](https://my.home-assistant.io/):**
>
> [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=grid-coordination&repository=openadr3-ven-hass&category=integration)
>
> **Or add manually:**
> 1. In HACS, go to **Integrations** → three-dot menu (⋮) → **Custom repositories**
> 2. Enter `https://github.com/grid-coordination/openadr3-ven-hass` and select category **Integration**
> 3. Click **Add**
>
> After that, proceed with the standard install below.

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

Each program creates one sensor per OpenADR 3 payload type the program publishes. A program publishing `[PRICE, EXPORT_PRICE, RRP]` produces three sensors; a program publishing only `[PRICE]` produces one. Sensor names are derived from the program name plus the payload type (e.g. `AU-NSW1-AUSGRID-EA025 Export Price`).

### Sensor state and attributes

| Property | Description |
|----------|-------------|
| State (`native_value`) | Value of the interval covering wall-clock now, at the VTN's native granularity |
| `payload_type` | The OpenADR 3 payload type identifier (`PRICE`, `EXPORT_PRICE`, `GHG`, `RRP`, …) |
| `current_interval_start` / `current_interval_end` | ISO 8601 timestamps bounding the slot whose value drives the state |
| `interval_minutes` | Duration of the current slot, in minutes (5, 15, 30, 60, …) |
| `next_interval_value` / `next_interval_datetime` | Value and start of the next interval after the current one |
| `daily_min` / `daily_max` / `daily_avg` | Duration-weighted daily statistics for today |
| `event_names` | List of OpenADR 3 event names contributing to the forecast |
| `forecast_rows` / `forecast_start` / `forecast_end` | Size and range of the full forecast (read it via the `openadr3_ven.get_forecast` service) |

The full forecast is **not** exposed as an entity attribute. Home Assistant's recorder has a 16 KB cap on `extra_state_attributes`; a 7-day forecast at PT30M is comfortably over that and a PT5M live + PT30M rolling forecast more so. Instead, the forecast is returned by a service call on demand:

```yaml
service: openadr3_ven.get_forecast
target:
  entity_id: sensor.openadr3_vtn_au_nsw1_ausgrid_ea025_price
data:
  start: "2026-05-25T00:00:00+10:00"   # optional
  end:   "2026-06-01T00:00:00+10:00"   # optional
```

The response (per entity) is:

```yaml
payload_type: PRICE
unit: $/kWh
forecast:
  - datetime: "2026-05-25T17:05:00+10:00"
    value: 0.171915
    interval_minutes: 5
  - datetime: "2026-05-25T17:10:00+10:00"
    value: 0.234
    interval_minutes: 30
  ...
```

This matches the pattern Home Assistant uses for its own weather entities (`weather.get_forecasts`). Lovelace cards read the forecast via a `data_generator` that calls the service — see the [Dashboard Setup Guide](docs/dashboard.md) for the recipe.

### Time and timezone

The integration positions each VTN interval in time using its `intervalPeriod.start` + `duration` (per the OpenADR 3 spec) and emits it as a row in HA local timezone with no down-sampling. A PT5M interval stays PT5M; a PT30M interval stays PT30M; an hourly interval stays hourly. The `datetime` field on every row is a full ISO 8601 timestamp with timezone offset (e.g. `2026-05-23T01:00:00-07:00`).

### Dashboard

Current values can be displayed with [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom) and the forecast as charts using [ApexCharts Card](https://github.com/RomRider/apexcharts-card). See the [Dashboard Setup Guide](docs/dashboard.md) for full configuration, including the `data_generator` recipe for reading the forecast via the service call.

## Upgrading from 0.2.x or 0.3.x

Upgrade directly to **0.4.1 or later** (0.4.0 had a payload-type case-mismatch bug that left sensors stuck at "unknown"; 0.4.1 fixes it). The config-entry migration handles either 0.2.x or 0.3.x starting points — schema v1 → v2 is the same hop.

0.4.x is a **breaking change** for existing dashboards. Two things change:

1. **Forecast moved from entity attribute to service call.** Lovelace cards using `data_generator: entity.attributes.forecast` will return empty arrays. Replace them with the service-call recipe in [docs/dashboard.md](docs/dashboard.md).
2. **One sensor per payload type per program.** A program that previously created a single `sensor.openadr3_vtn_<program>` sensor for its first payload type now creates one sensor per type (e.g. `..._price`, `..._export_price`). The existing entity is migrated in place — its history and unique_id survive — but additional payload types appear as new entities, and the display name gains a payload-type suffix (e.g. `EELEC-024131103` → `EELEC-024131103 Price`).

Sensor states and the small attribute surface (`daily_min`, `daily_max`, `current_interval_start`, etc.) keep working through the upgrade without intervention. The config-entry migration runs automatically on first restart.

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

## Community

- [**Announcement on the Home Assistant Community Forum**](https://community.home-assistant.io/t/announcing-openadr-3-ven-for-home-assistant/1005701)

## Contributing

- **Questions and ideas** — please start a [Discussion](https://github.com/grid-coordination/openadr3-ven-hass/discussions)
- **Bug reports** — file an [Issue](https://github.com/grid-coordination/openadr3-ven-hass/issues) with steps to reproduce
- **Pull requests** — should reference an existing Issue that describes the motivation and expected behavior

## Trademarks

OpenADR® and the OpenADR logo are trademarks of the OpenADR Alliance. This is a community-developed integration and is not affiliated with, endorsed by, or certified by the OpenADR Alliance.
