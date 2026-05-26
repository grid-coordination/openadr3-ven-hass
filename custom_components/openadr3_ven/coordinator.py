"""DataUpdateCoordinator for OpenADR 3 VEN integration."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from openadr3 import Event

from .api_client import VtnApiClient
from .const import CONF_PROGRAMS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .mqtt_client import MqttSubscriptionManager, pick_broker_uri

_LOGGER = logging.getLogger(__name__)

# Pattern to extract date from event names like EELEC-012041131-2026-04-21
_DATE_SUFFIX_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")

# Boundary-aligned refreshes fire this many seconds *after* the slot start, so
# the VTN has a moment to publish the new slot's data before we poll.
_BOUNDARY_OFFSET_SECONDS = 10


@dataclass
class PayloadData:
    """Per-payload-type forecast for a single program.

    `forecast` holds rows at the VTN's native interval granularity — each row
    represents one interval and carries enough information to be located in
    time (datetime, interval_minutes). No hour-bucketing is applied.
    """

    payload_type: str
    forecast: list[dict[str, Any]] = field(default_factory=list)
    daily_min: float | None = None
    daily_max: float | None = None
    daily_avg: float | None = None


@dataclass
class ProgramData:
    """Processed data for a single program; may carry multiple payload types."""

    program_id: str
    program_name: str
    event_names: list[str] = field(default_factory=list)
    payloads: dict[str, PayloadData] = field(default_factory=dict)


def _extract_date(event_name: str | None) -> str | None:
    """Extract YYYY-MM-DD from an event name suffix."""
    if not event_name:
        return None
    m = _DATE_SUFFIX_RE.search(event_name)
    return m.group(1) if m else None


def _process_event(event: Event) -> dict[str, list[dict[str, Any]]]:
    """Expand an event into per-payload-type row streams at native granularity.

    Each interval becomes one row per payload it carries. The row is positioned
    in time by intervalPeriod.start (in HA local timezone) and tagged with
    interval_minutes so consumers can do time-window scans. Multi-payload
    intervals (e.g. PRICE + EXPORT_PRICE + RRP) fan out into separate streams,
    one per payload type.

    Falls back to interval.id as hour-of-day at PT1H when intervalPeriod is
    absent — legacy path for VTNs that don't populate per-interval timing.
    """
    if not event.intervals:
        return {}

    fallback_date = _extract_date(event.event_name)
    source_event = event.event_name
    local_tz_key = dt_util.DEFAULT_TIME_ZONE.key
    by_type: dict[str, list[dict[str, Any]]] = {}

    for interval in event.intervals:
        if not interval.payloads:
            continue

        ip = interval.interval_period
        if ip is not None and ip.start is not None and ip.duration is not None:
            duration_minutes = max(
                1, int(round(ip.duration.total_seconds() / 60))
            )
            start_local = ip.start.in_timezone(local_tz_key)
            row_base = {
                # strftime, not pendulum's format(), to avoid lazy-importing
                # pendulum.locales.<locale> inside the HA event loop.
                "date": start_local.strftime("%Y-%m-%d"),
                "hour": start_local.hour,
                "minute": start_local.minute,
                "interval_minutes": duration_minutes,
                "datetime": start_local.to_iso8601_string(),
            }
        else:
            row_base = {
                "hour": interval.id,
                "minute": 0,
                "interval_minutes": 60,
            }
            if fallback_date:
                row_base["date"] = fallback_date
                row_base["datetime"] = f"{fallback_date}T{interval.id:02d}:00:00"

        for payload in interval.payloads:
            # The openadr3 lib's coerce_payload lowercases payload.type. The OA3
            # spec uses UPPER_SNAKE_CASE ("PRICE", "EXPORT_PRICE", "GHG"), and
            # program payload descriptors also surface it that way. Normalize
            # back to UPPER here so descriptor/CONF_PROGRAMS/sensor-lookup keys
            # all match. Tracked upstream as a python-oa3 bug.
            ptype = (payload.type or "").upper()
            raw_value = payload.values[0] if payload.values else None
            value = float(raw_value) if raw_value is not None else None
            rows = by_type.setdefault(ptype, [])
            rows.append({
                **row_base,
                "value": value,
                "payload_type": ptype,
                "source_event": source_event,
            })

    for rows in by_type.values():
        rows.sort(key=lambda r: r.get("datetime", ""))

    return by_type


def _compute_daily_stats(
    rows: list[dict[str, Any]],
    today_str: str,
) -> tuple[float | None, float | None, float | None]:
    """Duration-weighted daily stats for rate-shaped payloads.

    All currently-supported payload types (PRICE, EXPORT_PRICE, GHG, RRP) are
    rate-shaped: the value is a $/kWh- or g/kWh-style intensity that applies
    uniformly across the interval. The mean is therefore a duration-weighted
    average: sum(value * minutes) / sum(minutes).

    If a flow-shaped event payload is ever added (e.g. an event-side USAGE in
    kWh per interval), this site needs a payload-kind dispatch — flow
    aggregation is sum(value), not weighted-mean.
    """
    todays = [
        r for r in rows
        if r.get("date") == today_str and r.get("value") is not None
    ]
    if not todays:
        return None, None, None
    values = [r["value"] for r in todays]
    weights = [r.get("interval_minutes", 60) for r in todays]
    total_weight = sum(weights)
    avg = (
        sum(v * w for v, w in zip(values, weights)) / total_weight
        if total_weight > 0 else None
    )
    return min(values), max(values), avg


def _build_program_data(
    program_id: str,
    program_name: str,
    payload_types: list[str],
    events: list[Event],
) -> ProgramData:
    """Process all events for a program into a ProgramData."""
    today_str = dt_util.now().strftime("%Y-%m-%d")

    dated_events = sorted(
        [e for e in events if e.event_name],
        key=lambda e: e.event_name,
    )

    # Seed with the configured payload types so an entity always exists even
    # if the VTN hasn't published any matching events yet.
    by_type_forecast: dict[str, list[dict[str, Any]]] = {
        pt: [] for pt in payload_types
    }
    event_names: list[str] = []

    for event in dated_events:
        for ptype, rows in _process_event(event).items():
            by_type_forecast.setdefault(ptype, []).extend(rows)
        event_names.append(event.event_name or "")

    payloads: dict[str, PayloadData] = {}
    for ptype, forecast in by_type_forecast.items():
        forecast.sort(key=lambda r: r.get("datetime", ""))
        daily_min, daily_max, daily_avg = _compute_daily_stats(forecast, today_str)
        payloads[ptype] = PayloadData(
            payload_type=ptype,
            forecast=forecast,
            daily_min=daily_min,
            daily_max=daily_max,
            daily_avg=daily_avg,
        )

    return ProgramData(
        program_id=program_id,
        program_name=program_name,
        event_names=event_names,
        payloads=payloads,
    )


class OpenADR3Coordinator(DataUpdateCoordinator[dict[str, ProgramData]]):
    """Coordinator that fetches event data for all subscribed programs."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: VtnApiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self._mqtt: MqttSubscriptionManager | None = None
        self._mqtt_connect_count = 0
        self._boundary_cancel: Callable[[], None] | None = None
        # Cadence we last applied; lets _reconfigure_polling skip work when
        # nothing changed.
        self._applied_cadence_minutes: int | None = None

    @staticmethod
    def _boundary_minutes(interval_min: int) -> list[int]:
        """Clock minutes within the hour to fire boundary-aligned refreshes at.

        For divisor cadences (PT5M / PT15M / PT30M / PT60M) the list aligns
        naturally. For non-divisor cadences (e.g. PT7M) we fire at every minute
        within the hour that's a multiple of the cadence — the alignment drifts
        across hour boundaries but stays close enough that staleness never
        exceeds one interval.
        """
        if interval_min >= 60 or interval_min <= 0:
            return [0]
        if 60 % interval_min == 0:
            return list(range(0, 60, interval_min))
        return [m for m in range(0, 60) if m % interval_min == 0]

    @staticmethod
    def _min_interval_minutes(data: dict[str, ProgramData]) -> int:
        """Smallest non-zero interval_minutes across all program forecasts.

        Defaults to 60 when no data is available yet (matches the legacy hourly
        cadence assumption).
        """
        minutes: list[int] = []
        for program in data.values():
            for payload in program.payloads.values():
                for row in payload.forecast:
                    m = row.get("interval_minutes")
                    if isinstance(m, int) and m > 0:
                        minutes.append(m)
        return min(minutes) if minutes else 60

    @callback
    def _reconfigure_polling(self, data: dict[str, ProgramData]) -> None:
        """Match the polling cadence to the data's native interval granularity.

        Sets `update_interval` to the program's `min_interval_minutes` (clamped
        to [60s, 3600s]) and installs a slot-boundary-aligned trigger that
        fires `_BOUNDARY_OFFSET_SECONDS` after each slot start. Boundary
        alignment is what keeps a PT30M VTN from drifting up to 30 minutes
        stale between polls — most tariff dynamics happen *at* the slot
        boundary, not midway through.

        Idempotent: if the cadence hasn't changed since the last call, this
        is a no-op.
        """
        interval_min = self._min_interval_minutes(data)
        if (
            interval_min == self._applied_cadence_minutes
            and self._boundary_cancel is not None
        ):
            return

        self._applied_cadence_minutes = interval_min
        safety_seconds = max(60, min(interval_min * 60, 3600))
        self.update_interval = timedelta(seconds=safety_seconds)

        if self._boundary_cancel is not None:
            self._boundary_cancel()
            self._boundary_cancel = None

        boundaries = self._boundary_minutes(interval_min)
        self._boundary_cancel = async_track_time_change(
            self.hass,
            self._on_boundary,
            minute=boundaries,
            second=_BOUNDARY_OFFSET_SECONDS,
        )
        _LOGGER.info(
            "Adaptive polling reconfigured: native=%dmin safety_cadence=%s "
            "boundary_minutes=%s offset=%ds",
            interval_min, self.update_interval, boundaries,
            _BOUNDARY_OFFSET_SECONDS,
        )

    async def _on_boundary(self, now: datetime) -> None:
        """Refresh on a slot-boundary tick. Debounced by DataUpdateCoordinator."""
        _LOGGER.debug("Slot-boundary refresh fired at %s", now.isoformat())
        await self.async_request_refresh()

    @callback
    def _stop_boundary(self) -> None:
        if self._boundary_cancel is not None:
            self._boundary_cancel()
            self._boundary_cancel = None

    async def async_start_mqtt(self) -> None:
        """Detect MQTT support and start subscription if available."""
        notifiers = await self.client.get_notifiers()
        mqtt_config = notifiers.get("MQTT") if isinstance(notifiers, dict) else None
        if not mqtt_config:
            _LOGGER.info("VTN does not support MQTT notifications, polling only")
            return

        uris = mqtt_config.get("URIS", [])
        broker_uri = pick_broker_uri(uris)
        if not broker_uri:
            _LOGGER.warning("VTN reports MQTT support but no broker URIs")
            return

        programs_config = self.config_entry.data[CONF_PROGRAMS]
        program_ids = {p["id"] for p in programs_config}

        topics = await self.client.get_all_program_event_topics(program_ids)
        if not topics:
            _LOGGER.warning("No MQTT event topics found for subscribed programs")
            return

        entry_id_short = self.config_entry.entry_id[:8]
        self._mqtt = MqttSubscriptionManager(
            broker_uri=broker_uri,
            topics=topics,
            on_event=self._handle_mqtt_event,
            on_connected=self._on_mqtt_connected,
            client_id=f"hass-oa3v-{entry_id_short}",
        )
        await self.hass.async_add_executor_job(self._mqtt.start)
        _LOGGER.info(
            "MQTT subscription started: %d topic(s) for %d program(s)",
            len(topics), len(program_ids),
        )

    def _on_mqtt_connected(self) -> None:
        """Fired from the MQTT thread on every (re)connect.

        First call (initial connect) is a no-op: async_start_mqtt fetched topics
        and async_config_entry_first_refresh just snapshotted state, so there is
        nothing to redo. Subsequent calls are reconnects — schedule a re-fetch
        of topics and a fresh REST snapshot on the HA loop.
        """
        self._mqtt_connect_count += 1
        if self._mqtt_connect_count == 1:
            return
        self.hass.loop.call_soon_threadsafe(
            lambda: self.hass.async_create_task(self._async_refresh_on_reconnect())
        )

    async def _async_refresh_on_reconnect(self) -> None:
        """Re-fetch topic list and trigger a fresh REST snapshot."""
        _LOGGER.info(
            "MQTT reconnected (count=%d); re-fetching topics and re-snapshotting",
            self._mqtt_connect_count,
        )
        programs_config = self.config_entry.data[CONF_PROGRAMS]
        program_ids = {p["id"] for p in programs_config}
        try:
            fresh_topics = await self.client.get_all_program_event_topics(program_ids)
        except Exception:
            _LOGGER.exception("Failed to re-fetch MQTT topics after reconnect")
        else:
            if self._mqtt is not None and fresh_topics:
                await self.hass.async_add_executor_job(
                    self._mqtt.update_topics, fresh_topics
                )
        await self.async_request_refresh()

    async def async_stop_mqtt(self) -> None:
        """Stop MQTT subscription if running."""
        if self._mqtt is not None:
            await self.hass.async_add_executor_job(self._mqtt.stop)
            self._mqtt = None

    def _handle_mqtt_event(self, event: Event, operation: str) -> None:
        """Handle an event received via MQTT (called from MQTT thread).

        Branches on the notification's operation:
          CREATE/UPDATE/READ → drop prior rows from this event and re-emit from the new payload.
          DELETE             → drop prior rows from this event and forget its name.

        Dedup is keyed on the OA3 event name (`source_event`), not on a parsed
        date suffix. The previous date-suffix approach silently failed for VTNs
        whose event names don't carry a date — rows accumulated forever on
        repeated MQTT updates because the filter matched nothing to remove.
        """
        program_id = event.program_id
        if self.data is None or program_id not in self.data:
            return

        existing = self.data[program_id]
        event_name = event.event_name
        new_streams = (
            _process_event(event) if operation != "DELETE" else {}
        )
        today_str = dt_util.now().strftime("%Y-%m-%d")

        # Touch every payload type known to the existing program plus any new
        # ones from the incoming event (the VTN may have added a descriptor).
        all_types = set(existing.payloads.keys()) | set(new_streams.keys())
        new_payloads: dict[str, PayloadData] = {}

        for ptype in all_types:
            old = existing.payloads.get(ptype)
            old_forecast = old.forecast if old is not None else []
            forecast = [
                r for r in old_forecast if r.get("source_event") != event_name
            ]
            if operation != "DELETE":
                forecast.extend(new_streams.get(ptype, []))
                forecast.sort(key=lambda r: r.get("datetime", ""))

            daily_min, daily_max, daily_avg = _compute_daily_stats(forecast, today_str)
            new_payloads[ptype] = PayloadData(
                payload_type=ptype,
                forecast=forecast,
                daily_min=daily_min,
                daily_max=daily_max,
                daily_avg=daily_avg,
            )

        event_names = list(existing.event_names)
        if operation == "DELETE":
            event_names = [n for n in event_names if n != event_name]
        elif event_name and event_name not in event_names:
            event_names.append(event_name)
            event_names.sort()

        updated_program = ProgramData(
            program_id=program_id,
            program_name=existing.program_name,
            event_names=event_names,
            payloads=new_payloads,
        )

        new_data = {**self.data, program_id: updated_program}
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, new_data)
        self.hass.loop.call_soon_threadsafe(self._reconfigure_polling, new_data)

    async def _async_update_data(self) -> dict[str, ProgramData]:
        """Fetch events for all subscribed programs."""
        programs_config = self.config_entry.data[CONF_PROGRAMS]
        data: dict[str, ProgramData] = {}

        for prog in programs_config:
            program_id = prog["id"]
            program_name = prog["name"]
            payload_types = list(prog["payload_types"])

            try:
                events = await self.client.get_events(program_id)
            except Exception as err:
                _LOGGER.warning(
                    "Failed to fetch events for program %s: %s",
                    program_name,
                    err,
                )
                if self.data and program_id in self.data:
                    data[program_id] = self.data[program_id]
                else:
                    data[program_id] = ProgramData(
                        program_id=program_id,
                        program_name=program_name,
                        payloads={
                            pt: PayloadData(payload_type=pt)
                            for pt in payload_types
                        },
                    )
                continue

            data[program_id] = _build_program_data(
                program_id, program_name, payload_types, events
            )

        if not data:
            raise UpdateFailed("No program data could be fetched")

        self._reconfigure_polling(data)
        return data
