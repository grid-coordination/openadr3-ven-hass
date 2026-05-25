"""Sensor platform for OpenADR 3 VEN integration."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import CONF_PROGRAMS, CONF_VTN_NAME, DOMAIN
from .coordinator import OpenADR3Coordinator, PayloadData, ProgramData

SERVICE_GET_FORECAST = "get_forecast"

# Per-payload-type display config. Unknown types fall through to a generic icon
# with the payload type as the unit string.
_PAYLOAD_UI: dict[str, dict[str, Any]] = {
    "PRICE": {
        "unit": "$/kWh",
        "icon": "mdi:currency-usd",
        "precision": 5,
    },
    "EXPORT_PRICE": {
        "unit": "$/kWh",
        "icon": "mdi:transmission-tower-export",
        "precision": 5,
    },
    "GHG": {
        "unit": "g CO₂/kWh",
        "icon": "mdi:molecule-co2",
        "precision": 1,
    },
}


def _pretty_payload_type(payload_type: str) -> str:
    """Render a payload-type identifier as a Title Case label."""
    return payload_type.replace("_", " ").title()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenADR 3 VEN sensor entities from a config entry."""
    coordinator: OpenADR3Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[OpenADR3ProgramSensor] = []
    for prog in entry.data[CONF_PROGRAMS]:
        for payload_type in prog["payload_types"]:
            entities.append(
                OpenADR3ProgramSensor(coordinator, prog, payload_type)
            )
    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_GET_FORECAST,
        {
            vol.Optional("start"): cv.datetime,
            vol.Optional("end"): cv.datetime,
        },
        "async_get_forecast",
        supports_response=SupportsResponse.ONLY,
    )


class OpenADR3ProgramSensor(CoordinatorEntity[OpenADR3Coordinator], SensorEntity):
    """Sensor for one (program, payload_type) pair.

    State is the value of the interval covering wall-clock now. Forecast data
    is not exposed as an entity attribute (HA's 16 KB recorder cap rules that
    out at sub-hourly granularity); use the `openadr3_ven.get_forecast` service
    to read the full forecast.
    """

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OpenADR3Coordinator,
        program_config: dict[str, Any],
        payload_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._program_id: str = program_config["id"]
        self._program_name: str = program_config["name"]
        self._payload_type: str = payload_type

        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}"
            f"_{self._program_id}"
            f"_{payload_type.lower()}"
        )
        self._attr_name = (
            f"{self._program_name} {_pretty_payload_type(payload_type)}"
        )

        ui = _PAYLOAD_UI.get(payload_type)
        if ui is not None:
            self._attr_native_unit_of_measurement = ui["unit"]
            self._attr_icon = ui["icon"]
            if "precision" in ui:
                self._attr_suggested_display_precision = ui["precision"]
        else:
            self._attr_native_unit_of_measurement = payload_type
            self._attr_icon = "mdi:flash"

    @property
    def device_info(self) -> DeviceInfo:
        """Group sensors under the VTN device."""
        vtn_name = self.coordinator.config_entry.data.get(CONF_VTN_NAME, "OpenADR3 VTN")
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name=f"OpenADR3 VTN ({vtn_name})",
            manufacturer="OpenADR Alliance",
            model="OpenADR 3 VTN",
            entry_type=DeviceEntryType.SERVICE,
        )

    def _program(self) -> ProgramData | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._program_id)

    def _payload(self) -> PayloadData | None:
        program = self._program()
        if program is None:
            return None
        return program.payloads.get(self._payload_type)

    def _row_covering(
        self, when: datetime, rows: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Return the row whose [start, start+duration) interval contains `when`."""
        for row in rows:
            iso = row.get("datetime")
            if not iso:
                continue
            try:
                start = dt_util.parse_datetime(iso)
            except (TypeError, ValueError):
                continue
            if start is None:
                continue
            minutes = row.get("interval_minutes") or 60
            end = start + timedelta(minutes=minutes)
            if start <= when < end:
                return row
        return None

    def _row_after(
        self, when: datetime, rows: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Return the first row whose start is strictly after `when`."""
        for row in rows:
            iso = row.get("datetime")
            if not iso:
                continue
            start = dt_util.parse_datetime(iso)
            if start is not None and start > when:
                return row
        return None

    @property
    def native_value(self) -> float | None:
        """Return the value of the interval covering wall-clock now."""
        data = self._payload()
        if data is None:
            return None
        row = self._row_covering(dt_util.now(), data.forecast)
        return row["value"] if row is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return small, recorder-friendly metadata. No forecast/schedule arrays."""
        program = self._program()
        data = self._payload()
        if data is None:
            return {}

        now = dt_util.now()
        current = self._row_covering(now, data.forecast)
        upcoming = self._row_after(now, data.forecast)

        attrs: dict[str, Any] = {
            "payload_type": data.payload_type,
            "event_names": program.event_names if program else [],
            "daily_min": data.daily_min,
            "daily_max": data.daily_max,
            "daily_avg": data.daily_avg,
        }

        if current is not None:
            start = dt_util.parse_datetime(current["datetime"])
            minutes = current.get("interval_minutes") or 60
            attrs["current_interval_start"] = current["datetime"]
            attrs["current_interval_end"] = (
                (start + timedelta(minutes=minutes)).isoformat()
                if start is not None else None
            )
            attrs["interval_minutes"] = minutes

        if upcoming is not None:
            attrs["next_interval_value"] = upcoming["value"]
            attrs["next_interval_datetime"] = upcoming.get("datetime")

        if data.forecast:
            attrs["forecast_rows"] = len(data.forecast)
            attrs["forecast_start"] = data.forecast[0].get("datetime")
            attrs["forecast_end"] = data.forecast[-1].get("datetime")
        else:
            attrs["forecast_rows"] = 0

        return attrs

    async def async_get_forecast(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        """Return the full forecast, optionally filtered to [start, end)."""
        data = self._payload()
        if data is None:
            return {
                "payload_type": self._payload_type,
                "unit": self._attr_native_unit_of_measurement,
                "forecast": [],
            }

        rows = data.forecast
        if start is not None or end is not None:
            filtered: list[dict[str, Any]] = []
            for row in rows:
                iso = row.get("datetime")
                if not iso:
                    continue
                row_start = dt_util.parse_datetime(iso)
                if row_start is None:
                    continue
                if start is not None and row_start < start:
                    continue
                if end is not None and row_start >= end:
                    continue
                filtered.append(row)
            rows = filtered

        return {
            "payload_type": data.payload_type,
            "unit": self._attr_native_unit_of_measurement,
            "forecast": [
                {
                    "datetime": r.get("datetime"),
                    "value": r.get("value"),
                    "interval_minutes": r.get("interval_minutes"),
                }
                for r in rows
            ],
        }
