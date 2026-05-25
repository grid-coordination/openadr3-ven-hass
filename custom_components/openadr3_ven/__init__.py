"""The OpenADR 3 VEN integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .api_client import VtnApiClient
from .const import CONF_PROGRAMS, CONF_VTN_URL, DOMAIN
from .coordinator import OpenADR3Coordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up OpenADR 3 VEN from a config entry."""
    client = VtnApiClient(entry.data[CONF_VTN_URL], time_zone=hass.config.time_zone)
    coordinator = OpenADR3Coordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry from older schema versions.

    v1 → v2 (0.3.x → 0.4.0): rewrite CONF_PROGRAMS to store payload_types as a
    list (every descriptor the VTN publishes) instead of a single payload_type.
    Existing sensor entities have their unique_id rewritten in-place so their
    history and dashboard wiring survive the upgrade.

    v1 unique_id format: f"{entry_id}_{program_id}"
    v2 unique_id format: f"{entry_id}_{program_id}_{payload_type.lower()}"
    """
    if entry.version == 1:
        new_programs: list[dict[str, Any]] = []
        unique_id_map: dict[str, str] = {}

        for prog in entry.data.get(CONF_PROGRAMS, []):
            if "payload_types" in prog:
                new_programs.append(prog)
                continue

            old_pt = prog.get("payload_type", "UNKNOWN")
            new_programs.append({
                "id": prog["id"],
                "name": prog["name"],
                "payload_types": [old_pt],
            })

            old_uid = f"{entry.entry_id}_{prog['id']}"
            new_uid = f"{old_uid}_{old_pt.lower()}"
            unique_id_map[old_uid] = new_uid

        ent_reg = er.async_get(hass)
        for ent in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            new_uid = unique_id_map.get(ent.unique_id)
            if new_uid is not None and new_uid != ent.unique_id:
                ent_reg.async_update_entity(ent.entity_id, new_unique_id=new_uid)

        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_PROGRAMS: new_programs},
            version=2,
        )
        _LOGGER.info(
            "Migrated config entry %s from v1 to v2 (%d program(s))",
            entry.entry_id, len(new_programs),
        )

    return True
