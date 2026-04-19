"""Config flow for OpenADR 3 VEN integration."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
import homeassistant.helpers.config_validation as cv

from .api_client import VtnApiClient
from .const import (
    CONF_PROGRAMS,
    CONF_VEN_NAME,
    CONF_VTN_NAME,
    CONF_VTN_URL,
    DEFAULT_VEN_NAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class OpenADR3VENConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for OpenADR 3 VEN."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._vtn_url: str = ""
        self._ven_name: str = DEFAULT_VEN_NAME
        self._programs: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — VTN URL entry and connection test."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._vtn_url = user_input[CONF_VTN_URL].rstrip("/")
            self._ven_name = user_input.get(CONF_VEN_NAME, DEFAULT_VEN_NAME)

            # Check if already configured with this URL
            self._async_abort_entries_match({CONF_VTN_URL: self._vtn_url})

            # Test connection and fetch programs
            client = VtnApiClient(self._vtn_url)
            try:
                programs = await client.get_all_programs()
            except (httpx.HTTPError, httpx.TimeoutException):
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error during VTN connection test")
                errors["base"] = "unknown"
            else:
                self._programs = [
                    {
                        "id": p.id,
                        "name": p.program_name,
                        "payload_type": (
                            p.payload_descriptors[0].payload_type
                            if p.payload_descriptors
                            else "UNKNOWN"
                        ),
                    }
                    for p in programs
                ]
                return await self.async_step_select_programs()
            finally:
                await client.close()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VTN_URL): str,
                    vol.Optional(CONF_VEN_NAME, default=DEFAULT_VEN_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_select_programs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle program selection step."""
        if user_input is not None:
            selected_ids = user_input[CONF_PROGRAMS]
            selected_programs = [
                p for p in self._programs if p["id"] in selected_ids
            ]

            # Derive a friendly VTN name from the URL
            vtn_name = self._vtn_url.split("//")[-1].split("/")[0]

            return self.async_create_entry(
                title=f"OpenADR3 VEN ({vtn_name})",
                data={
                    CONF_VTN_URL: self._vtn_url,
                    CONF_VTN_NAME: vtn_name,
                    CONF_VEN_NAME: self._ven_name,
                    CONF_PROGRAMS: selected_programs,
                },
            )

        # Build multi-select options: {program_id: "program_name (PAYLOAD_TYPE)"}
        program_options = {
            p["id"]: f"{p['name']} ({p['payload_type']})"
            for p in sorted(self._programs, key=lambda p: p["name"])
        }

        return self.async_show_form(
            step_id="select_programs",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PROGRAMS): cv.multi_select(program_options),
                }
            ),
            description_placeholders={"program_count": str(len(self._programs))},
        )
