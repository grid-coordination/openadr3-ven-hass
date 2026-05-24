"""DataUpdateCoordinator for OpenADR 3 VEN integration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from openadr3 import Event

from .api_client import VtnApiClient
from .const import CONF_PROGRAMS, DEFAULT_SCAN_INTERVAL, DOMAIN
from .mqtt_client import MqttSubscriptionManager, pick_broker_uri

_LOGGER = logging.getLogger(__name__)

# Pattern to extract date from event names like EELEC-012041131-2026-04-21
_DATE_SUFFIX_RE = re.compile(r"(\d{4}-\d{2}-\d{2})$")


@dataclass
class ProgramData:
    """Processed data for a single program."""

    program_id: str
    program_name: str
    payload_type: str
    # Today's schedule (24 hourly entries for the current day's event)
    schedule: list[dict[str, Any]] = field(default_factory=list)
    # Multi-day forecast (all available hourly entries with ISO datetime keys)
    forecast: list[dict[str, Any]] = field(default_factory=list)
    # Per-day event names
    event_names: list[str] = field(default_factory=list)
    # Today's stats
    daily_min: float | None = None
    daily_max: float | None = None
    daily_avg: float | None = None


def _extract_date(event_name: str | None) -> str | None:
    """Extract YYYY-MM-DD from an event name suffix."""
    if not event_name:
        return None
    m = _DATE_SUFFIX_RE.search(event_name)
    return m.group(1) if m else None


def _process_event(event: Event) -> list[dict[str, Any]]:
    """Expand an event's intervals into per-local-hour schedule rows.

    Each interval is positioned in time by intervalPeriod.start +
    intervalPeriod.duration and expanded to one row per hour it covers in the
    HA local timezone. This handles TOU events whose intervals span multiple
    hours (e.g. 3 intervals × variable duration = 24 hourly rows). Falls back
    to interval.id as hour-of-day only when intervalPeriod is absent.
    """
    if not event.intervals:
        return []

    fallback_date = _extract_date(event.event_name)
    local_tz_key = dt_util.DEFAULT_TIME_ZONE.key
    schedule: list[dict[str, Any]] = []

    for interval in event.intervals:
        if not interval.payloads:
            continue
        payload = interval.payloads[0]
        raw_value = payload.values[0] if payload.values else None
        value = float(raw_value) if raw_value is not None else None

        ip = interval.interval_period
        if ip is not None and ip.start is not None and ip.duration is not None:
            duration_hours = max(
                1, int(round(ip.duration.total_seconds() / 3600))
            )
            start_local = ip.start.in_timezone(local_tz_key).start_of("hour")
            for offset in range(duration_hours):
                slot = start_local.add(hours=offset)
                schedule.append({
                    "hour": slot.hour,
                    "value": value,
                    "payload_type": payload.type,
                    "date": slot.format("YYYY-MM-DD"),
                    "datetime": slot.to_iso8601_string(),
                })
        else:
            entry: dict[str, Any] = {
                "hour": interval.id,
                "value": value,
                "payload_type": payload.type,
            }
            if fallback_date:
                entry["date"] = fallback_date
                entry["datetime"] = f"{fallback_date}T{interval.id:02d}:00:00"
            schedule.append(entry)

    return sorted(schedule, key=lambda s: (s.get("date", ""), s["hour"]))


def _compute_daily_stats(
    schedule: list[dict[str, Any]],
) -> tuple[float | None, float | None, float | None]:
    """Compute min, max, avg from a schedule."""
    values = [e["value"] for e in schedule if e["value"] is not None]
    if not values:
        return None, None, None
    return min(values), max(values), sum(values) / len(values)


def _build_program_data(
    program_id: str,
    program_name: str,
    payload_type: str,
    events: list[Event],
) -> ProgramData:
    """Process all events for a program into a ProgramData."""
    today_str = dt_util.now().strftime("%Y-%m-%d")

    # Sort events chronologically by date in event name
    dated_events = sorted(
        [e for e in events if e.event_name],
        key=lambda e: e.event_name,
    )

    # Build combined forecast from all events
    forecast: list[dict[str, Any]] = []
    event_names: list[str] = []

    for event in dated_events:
        forecast.extend(_process_event(event))
        event_names.append(event.event_name or "")

    # Today's schedule is whatever forecast rows fall on today's local date.
    today_schedule = [e for e in forecast if e.get("date") == today_str]

    # Last resort: if nothing matches today (e.g. no event for today), use the
    # earliest dated event's rows so consumers see *something* rather than nothing.
    if not today_schedule and dated_events:
        today_schedule = _process_event(dated_events[0])

    daily_min, daily_max, daily_avg = _compute_daily_stats(today_schedule)

    return ProgramData(
        program_id=program_id,
        program_name=program_name,
        payload_type=payload_type,
        schedule=today_schedule,
        forecast=forecast,
        event_names=event_names,
        daily_min=daily_min,
        daily_max=daily_max,
        daily_avg=daily_avg,
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

        # Fetch per-program event topics from the VTN
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
          CREATE/UPDATE/READ → merge intervals for the event's date into the forecast.
          DELETE             → purge intervals for the event's date and drop its name.
        """
        program_id = event.program_id
        if self.data is None or program_id not in self.data:
            return

        existing = self.data[program_id]
        event_date = _extract_date(event.event_name)

        # Every op first removes this event's prior contribution from the forecast.
        forecast = [
            e for e in existing.forecast
            if e.get("date") != event_date
        ]
        event_names = list(existing.event_names)

        if operation == "DELETE":
            event_names = [n for n in event_names if n != event.event_name]
        else:
            forecast.extend(_process_event(event))
            forecast.sort(key=lambda e: (e.get("date", ""), e["hour"]))
            if event.event_name and event.event_name not in event_names:
                event_names.append(event.event_name)
                event_names.sort()

        # Recompute today's schedule from the updated forecast.
        today_str = dt_util.now().strftime("%Y-%m-%d")
        today_schedule = [e for e in forecast if e.get("date") == today_str]
        # For non-DELETE, preserve the previous schedule if the update happened to
        # be for a non-today date and we have no today rows otherwise.
        if not today_schedule and operation != "DELETE":
            today_schedule = existing.schedule

        daily_min, daily_max, daily_avg = _compute_daily_stats(today_schedule)

        updated_program = ProgramData(
            program_id=program_id,
            program_name=existing.program_name,
            payload_type=existing.payload_type,
            schedule=today_schedule,
            forecast=forecast,
            event_names=event_names,
            daily_min=daily_min,
            daily_max=daily_max,
            daily_avg=daily_avg,
        )

        new_data = {**self.data, program_id: updated_program}
        self.hass.loop.call_soon_threadsafe(self.async_set_updated_data, new_data)

    async def _async_update_data(self) -> dict[str, ProgramData]:
        """Fetch events for all subscribed programs."""
        programs_config = self.config_entry.data[CONF_PROGRAMS]
        data: dict[str, ProgramData] = {}

        for prog in programs_config:
            program_id = prog["id"]
            program_name = prog["name"]
            payload_type = prog["payload_type"]

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
                        payload_type=payload_type,
                    )
                continue

            data[program_id] = _build_program_data(
                program_id, program_name, payload_type, events
            )

        if not data:
            raise UpdateFailed("No program data could be fetched")

        return data
