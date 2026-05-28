# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the integration is in early development (0.x), breaking changes may appear between minor versions when needed to fix correctness issues or align with Home Assistant's modern patterns — see the [README stability note](README.md) for context.

> [!IMPORTANT]
> **Upgrading from 0.2.x or 0.3.x → 0.4.1:** the `async_migrate_entry` handles either starting point automatically (config-entry schema v1 → v2 covers both). Skip 0.4.0; it had a payload-type case bug that prevented sensor state from populating. Read the 0.4.0 entry below for the full list of behavioral changes you'll see (one sensor per payload type, native-granularity forecast, forecast-via-service rather than `entity.attributes.forecast`, etc.), and update Lovelace cards per [docs/dashboard.md](docs/dashboard.md) before restarting.

## [0.4.5] — 2026-05-28

### Fixed

- Spec-mandated `intervalPeriod` inheritance now applied in `coordinator._process_event`. Per OpenADR 3.1.0, `event.intervalPeriod` "sets default start time and duration of intervals" and per-interval `intervalPeriod` "may override event.intervalPeriod". The previous code only read per-interval and hardcoded `interval_minutes: 60` when absent. Now field-by-field: missing `duration` inherits from event-level; missing `start` chains sequentially from the previous resolved interval's end (or event-level `start` for the first inheriting interval). Publishers that set per-interval `intervalPeriod` explicitly on every interval — like the [California price server](https://price.grid-coordination.energy) and Mark Purcell's [AU VTN](https://openleadr-vtn-au.fly.dev) — see no behavior change ([#13](https://github.com/grid-coordination/openadr3-ven-hass/issues/13)).
- `config_flow.py:50` test-connection path constructed `VtnApiClient(...)` synchronously, triggering the same blocking-call warning we addressed in 0.4.4 for `async_setup_entry`. Now wrapped in `hass.async_add_executor_job`. Surfaced during cross-VEN testing while reproducing #11.

### Added

- First test file: `tests/test_coordinator.py` covers `_process_event` inheritance combinatorics (explicit passthrough, full inheritance with sequential start chaining, partial-field inheritance, explicit-then-inheriting chaining, legacy hour-of-day fallback, plus a regression anchor for the AU VTN's mixed PT5M + PT30M shape). Run with `python3 -m unittest discover tests/`. No CI hookup yet; runnable locally.

## [0.4.4] — 2026-05-26

### Fixed

- Three event-loop blocking-call warnings surfaced by Home Assistant 2026.5's stricter enforcement. None are user-visible failures today, but HA explicitly asks for bug reports for each. Fixed all three in one release:
  - `httpx.AsyncClient()` in `api_client.py` loaded the certifi CA bundle synchronously during `async_setup_entry`. Now constructed via `hass.async_add_executor_job`.
  - `start_local.format("YYYY-MM-DD")` in `coordinator._process_event` lazy-imported `pendulum.locales.en.locale` from inside the polling loop. Switched to stdlib `strftime("%Y-%m-%d")` — pendulum's locale machinery isn't needed for a fixed ISO date format.
  - `paho.mqtt.Client.tls_set()` in `MqttSubscriptionManager.__init__` loaded the system trust store. Moved into `start()`, which is already awaited from an executor by the coordinator.

## [0.4.3] — 2026-05-26

### Fixed

- MQTT client crash on paho-mqtt 2.x (`KeyError: 'Reason code name not found: Success'`). `_on_connect` / `_on_disconnect` constructed a `ReasonCode` from the legacy `CONNACK_ACCEPTED` int, but paho-mqtt 2.x's `ReasonCode(packetType, aName)` expects a v5 name string — the lookup failed, the MQTT thread died on the first CONNACK, and sensors registered but stayed `Unknown` because event push was gone. Now compares `rc.value == 0` (Success / Normal Disconnection in both v3.1.1 and v5). Affects any HA core on paho-mqtt 2.x with MQTT enabled on a VTN ([#12](https://github.com/grid-coordination/openadr3-ven-hass/issues/12)).

## [0.4.2] — 2026-05-25

### Added

- Adaptive coordinator polling cadence + slot-boundary-aligned refresh. The integration now derives its poll interval from each program's `min_interval_minutes` (clamped to `[60s, 3600s]`) instead of a fixed hourly cadence. A boundary-aligned trigger fires `10s` after every slot start so sub-hourly VTNs see updated values within ~10s of the upstream slot rolling over. Without this, VTNs publishing at PT5M / PT30M (e.g. Mark Purcell's AU VTN) could go up to 60 minutes stale between polls, because openleadr-rs — the only OA3 VTN with active development — doesn't implement MQTT push at all, so polling is the sole update channel for those VTNs ([#11](https://github.com/grid-coordination/openadr3-ven-hass/issues/11)).

## [0.4.1] — 2026-05-25

### Fixed

- Sensors stuck at `unknown` after upgrading to 0.4.0. The `openadr3` Python lib lowercases `payload.type` during coercion (`"PRICE"` → `"price"`), but program payload descriptors keep the OA3-spec `UPPER_SNAKE_CASE`. 0.4.0's multi-payload data model keyed everything uppercase, so event rows filled a lowercase bucket nobody read. Now normalizes `payload.type` to uppercase at the row-construction site.

## [0.4.0] — 2026-05-25 — superseded by 0.4.1

> Do not install 0.4.0. The payload-type case-mismatch bug fixed in 0.4.1 made every sensor state unreachable. Upgrade directly to 0.4.1 instead.

### Fixed

- MQTT-merge row accumulation: rows duplicated on every push for VTNs whose event names lack a `-YYYY-MM-DD` suffix. The 0.3.x dedup filter parsed a date suffix from the event name; absent it, the filter matched nothing and the forecast grew unboundedly. Now dedups on the OA3 event name itself, which works regardless of naming convention ([#9](https://github.com/grid-coordination/openadr3-ven-hass/issues/9)).
- Multiple payload types silently dropped per event. `_process_event` only read `interval.payloads[0]`, so `EXPORT_PRICE` and `RRP` payloads sitting alongside `PRICE` in the same interval never surfaced ([#10](https://github.com/grid-coordination/openadr3-ven-hass/issues/10)).

### Added

- One sensor entity per `(program, payload_type)` pair. Programs publishing `[PRICE, EXPORT_PRICE, RRP]` now create three sensors each, with payload-type-aware units and icons.
- New entity service `openadr3_ven.get_forecast` returns the full forecast on demand, with optional `start` / `end` window parameters. Matches Home Assistant's modern weather-forecast pattern (`weather.get_forecasts`).
- Sub-hourly interval support (PT5M / PT15M / PT30M). Rows carry `interval_minutes`; current-value lookup is a time-window scan against `[datetime, datetime + interval_minutes)`. Daily statistics are duration-weighted to handle mixed-granularity events (e.g. PT5M live + PT30M forecast slots in one event) correctly ([#8](https://github.com/grid-coordination/openadr3-ven-hass/issues/8)).
- Sensor attributes for current/next interval: `current_interval_start`, `current_interval_end`, `interval_minutes`, `next_interval_value`, `next_interval_datetime`. `forecast_rows` / `forecast_start` / `forecast_end` give visibility into the full forecast without downloading it.
- Config-entry migration v1 → v2 (`async_migrate_entry`) rewrites `CONF_PROGRAMS` from `payload_type: str` to `payload_types: list[str]` and updates entity-registry `unique_id`s in place. Existing 0.3.x sensors keep their history and primary entity_id; the display name gains a payload-type suffix.

### Changed

- **Breaking:** forecast is no longer exposed as an entity attribute. HA's recorder caps `extra_state_attributes` at 16 KB; sub-hourly multi-payload 7-day forecasts blow that limit and the recorder declines to persist any attribute history for the entity. Lovelace cards must switch from `data_generator: entity.attributes.forecast` to a `hass.callService(...)` recipe calling `openadr3_ven.get_forecast` — see [docs/dashboard.md](docs/dashboard.md).
- Forecast is emitted at the VTN's native interval granularity. PT5M stays PT5M, PT30M stays PT30M, PT1H stays PT1H. No more hour-bucketing.
- Daily statistics (`daily_min` / `daily_max` / `daily_avg`) are now duration-weighted (`Σ(value × minutes) / Σ(minutes)`) rather than simple arithmetic means.
- Hardcoded `PRICE` / `GHG` branches in `sensor.py` replaced with a `_PAYLOAD_UI` lookup table. Adding a payload type now requires one row in the table, not a new code branch.

### Removed

- `schedule` attribute (rolled into the service-returned forecast; today's rows derive from a `date`-filter at compute time).
- `next_hour_value` attribute (superseded by `next_interval_value`).
- `forecast_hours` attribute (superseded by `forecast_rows`).

## [0.3.0] — 2026-05-24

### Added

- MQTT push notifications. The integration subscribes to per-program event topics when the VTN advertises an MQTT notifier, so sensor state flows in near-real-time without waiting for the hourly REST poll.
- MQTT reconnect handling: on reconnect, topic subscriptions are re-fetched and a fresh REST snapshot is taken to recover any missed events.
- Operation-aware MQTT event handling. CREATE / UPDATE / READ merge new intervals into the forecast; DELETE purges intervals and drops the event name from the cache.
- Auto-release GitHub Actions workflow — pushing a manifest version bump tags and publishes a release automatically.
- Home Assistant Community Forum announcement linked from the README.
- Integration icon updated to the official OpenADR Alliance mark.

### Changed

- Each VTN interval is positioned in time by its `intervalPeriod.start` + `duration` (per OA3 spec) and expanded to per-local-hour rows in HA's timezone. TOU events with variable-duration intervals (e.g. 3 intervals covering 24 hours) normalize cleanly to 24 hourly rows per day.
- Every row carries a full ISO 8601 `datetime` with timezone offset, so downstream consumers don't need to infer the tariff's local timezone.

## [0.2.1] — 2026-04-22

### Fixed

- Timezone gap in event queries. REST event queries now pass an explicit `dateStart` / `dateEnd` window in UTC derived from HA local time, so today's event is always captured regardless of UTC offset.
- Y-axis `min: 0` clamp in the recommended ApexCharts setup that hid negative prices.

### Changed

- Dashboard guide reworked around Mushroom + ApexCharts (replacing earlier card recommendations).

## [0.2.0] — 2026-04-21

### Added

- Multi-day forecast (72-hour). Events for today plus the next two days are combined into a `forecast` attribute on each sensor.
- Dashboard setup guide (`docs/dashboard.md`) with ApexCharts forecast visualization recipes.
- User-Agent header derived from the manifest version, so VTN operators can identify the client.
- MIT license + README badges.

### Fixed

- Stale sensor values between polls. `native_value` is now computed live from the forecast rather than cached at refresh time.

## [0.1.0] — 2026-04-19

Initial release.

### Added

- Home Assistant custom integration acting as an OpenADR 3 Virtual End Node (VEN). Connects to an OA3 VTN, lists programs, and creates one sensor per subscribed program.
- Today's hourly schedule + daily min / max / average exposed as sensor attributes.
- Config flow for VTN URL entry and program selection.
- HACS-compatible packaging (custom repository install).
- Anonymous / unauthenticated VTN access (OAuth2 token support is on the roadmap).

[0.4.2]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.4.2
[0.4.1]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.4.1
[0.4.0]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.4.0
[0.3.0]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.3.0
[0.2.1]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.2.1
[0.2.0]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.2.0
[0.1.0]: https://github.com/grid-coordination/openadr3-ven-hass/releases/tag/v0.1.0
