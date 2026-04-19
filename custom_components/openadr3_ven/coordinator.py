"""DataUpdateCoordinator for OpenADR 3 VEN integration."""

from __future__ import annotations

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


@dataclass
class ProgramData:
    """Processed data for a single program."""

    program_id: str
    program_name: str
    payload_type: str
    event_name: str | None = None
    current_value: float | None = None
    next_hour_value: float | None = None
    daily_min: float | None = None
    daily_max: float | None = None
    daily_avg: float | None = None
    schedule: list[dict[str, Any]] = field(default_factory=list)


def _process_event(event: Event) -> list[dict[str, Any]]:
    """Extract hourly schedule from an event's intervals."""
    if not event.intervals:
        return []

    schedule = []
    for interval in event.intervals:
        if not interval.payloads:
            continue
        payload = interval.payloads[0]
        raw_value = payload.values[0] if payload.values else None
        value = float(raw_value) if raw_value is not None else None
        schedule.append({
            "hour": interval.id,
            "value": value,
            "payload_type": payload.type,
        })

    return sorted(schedule, key=lambda s: s["hour"])


def _find_current_hour_value(
    schedule: list[dict[str, Any]],
) -> tuple[float | None, float | None]:
    """Find the current and next hour values from the schedule."""
    now = dt_util.now()
    current_hour = now.hour
    current_value = None
    next_hour_value = None

    for entry in schedule:
        if entry["hour"] == current_hour:
            current_value = entry["value"]
        elif entry["hour"] == (current_hour + 1) % 24:
            next_hour_value = entry["value"]

    return current_value, next_hour_value


def _compute_daily_stats(
    schedule: list[dict[str, Any]],
) -> tuple[float | None, float | None, float | None]:
    """Compute min, max, avg from a schedule."""
    values = [e["value"] for e in schedule if e["value"] is not None]
    if not values:
        return None, None, None
    return min(values), max(values), sum(values) / len(values)


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
            client_id=f"hass-oa3v-{entry_id_short}",
        )
        await self.hass.async_add_executor_job(self._mqtt.start)
        _LOGGER.info(
            "MQTT subscription started: %d topic(s) for %d program(s)",
            len(topics), len(program_ids),
        )

    async def async_stop_mqtt(self) -> None:
        """Stop MQTT subscription if running."""
        if self._mqtt is not None:
            await self.hass.async_add_executor_job(self._mqtt.stop)
            self._mqtt = None

    def _handle_mqtt_event(self, event: Event) -> None:
        """Handle an event received via MQTT (called from MQTT thread)."""
        program_id = event.program_id
        if self.data is None or program_id not in self.data:
            return

        existing = self.data[program_id]
        schedule = _process_event(event)
        current_value, next_hour_value = _find_current_hour_value(schedule)
        daily_min, daily_max, daily_avg = _compute_daily_stats(schedule)

        updated_program = ProgramData(
            program_id=program_id,
            program_name=existing.program_name,
            payload_type=existing.payload_type,
            event_name=event.event_name,
            current_value=current_value,
            next_hour_value=next_hour_value,
            daily_min=daily_min,
            daily_max=daily_max,
            daily_avg=daily_avg,
            schedule=schedule,
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
                # Keep previous data if available
                if self.data and program_id in self.data:
                    data[program_id] = self.data[program_id]
                else:
                    data[program_id] = ProgramData(
                        program_id=program_id,
                        program_name=program_name,
                        payload_type=payload_type,
                    )
                continue

            # Find the most recent event (today's event)
            best_event = _pick_current_event(events)

            if best_event is None:
                data[program_id] = ProgramData(
                    program_id=program_id,
                    program_name=program_name,
                    payload_type=payload_type,
                )
                continue

            schedule = _process_event(best_event)
            current_value, next_hour_value = _find_current_hour_value(schedule)
            daily_min, daily_max, daily_avg = _compute_daily_stats(schedule)

            data[program_id] = ProgramData(
                program_id=program_id,
                program_name=program_name,
                payload_type=payload_type,
                event_name=best_event.event_name,
                current_value=current_value,
                next_hour_value=next_hour_value,
                daily_min=daily_min,
                daily_max=daily_max,
                daily_avg=daily_avg,
                schedule=schedule,
            )

        if not data:
            raise UpdateFailed("No program data could be fetched")

        return data


def _pick_current_event(events: list[Event]) -> Event | None:
    """Pick the event most relevant to the current time.

    Events are named like RATE-CIRCUIT-YYYY-MM-DD. We pick the one
    whose date matches today, or the most recently created one.
    """
    if not events:
        return None

    today_str = dt_util.now().strftime("%Y-%m-%d")

    # Try to find today's event by name suffix
    for event in events:
        if event.event_name and event.event_name.endswith(today_str):
            return event

    # Fallback: return the most recently created event
    events_with_created = [e for e in events if e.created is not None]
    if events_with_created:
        return max(events_with_created, key=lambda e: e.created)

    return events[0]
