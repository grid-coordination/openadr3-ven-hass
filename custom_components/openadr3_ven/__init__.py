"""The OpenADR 3 VEN integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api_client import VtnApiClient
from .const import CONF_VTN_URL, DOMAIN
from .coordinator import OpenADR3Coordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenADR 3 VEN from a config entry."""
    client = VtnApiClient(entry.data[CONF_VTN_URL], time_zone=hass.config.time_zone)
    coordinator = OpenADR3Coordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start MQTT push notifications if the VTN supports it
    await coordinator.async_start_mqtt()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: OpenADR3Coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop_mqtt()
        await coordinator.client.close()
    return unload_ok
