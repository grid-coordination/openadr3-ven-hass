"""Sensor platform for OpenADR 3 VEN integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.util import dt as dt_util
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_PROGRAMS, CONF_VTN_NAME, DOMAIN
from .coordinator import OpenADR3Coordinator, ProgramData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up OpenADR 3 VEN sensor entities from a config entry."""
    coordinator: OpenADR3Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        OpenADR3ProgramSensor(coordinator, prog)
        for prog in entry.data[CONF_PROGRAMS]
    ]
    async_add_entities(entities)


class OpenADR3ProgramSensor(CoordinatorEntity[OpenADR3Coordinator], SensorEntity):
    """Sensor for an OpenADR 3 program (price or GHG level)."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: OpenADR3Coordinator,
        program_config: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._program_id: str = program_config["id"]
        self._program_name: str = program_config["name"]
        payload_type: str = program_config["payload_type"]

        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{self._program_id}"
        )
        self._attr_name = self._program_name

        if payload_type == "PRICE":
            self._attr_native_unit_of_measurement = "$/kWh"
            self._attr_icon = "mdi:currency-usd"
            self._attr_suggested_display_precision = 5
        elif payload_type == "GHG":
            self._attr_native_unit_of_measurement = "g CO\u2082/kWh"
            self._attr_icon = "mdi:molecule-co2"
            self._attr_suggested_display_precision = 1
        else:
            self._attr_native_unit_of_measurement = payload_type
            self._attr_icon = "mdi:flash"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info to group sensors under the VTN."""
        vtn_name = self.coordinator.config_entry.data.get(CONF_VTN_NAME, "OpenADR3 VTN")
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.config_entry.entry_id)},
            name=f"OpenADR3 VTN ({vtn_name})",
            manufacturer="OpenADR Alliance",
            model="OpenADR 3 VTN",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def _program_data(self) -> ProgramData | None:
        """Get the current program data from the coordinator."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._program_id)

    def _value_for_hour(self, hour: int) -> float | None:
        """Look up a value by hour from the schedule."""
        data = self._program_data
        if data is None:
            return None
        for entry in data.schedule:
            if entry["hour"] == hour:
                return entry["value"]
        return None

    @property
    def native_value(self) -> float | None:
        """Return the current hour's value, computed live from the schedule."""
        return self._value_for_hour(dt_util.now().hour)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the full schedule and stats as attributes."""
        data = self._program_data
        if data is None:
            return {}
        return {
            "event_name": data.event_name,
            "payload_type": data.payload_type,
            "next_hour_value": self._value_for_hour((dt_util.now().hour + 1) % 24),
            "daily_min": data.daily_min,
            "daily_max": data.daily_max,
            "daily_avg": data.daily_avg,
            "schedule": data.schedule,
        }
