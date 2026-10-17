"""Sensor units derived from OA3 payload descriptors."""

from __future__ import annotations

from dataclasses import dataclass

# OA3 payload descriptor `units` values → HA energy unit spelling.
_ENERGY_UNITS = {"WH": "Wh", "KWH": "kWh", "MWH": "MWh"}

# Payload types that are monetary even when the VTN omits `currency`.
_PRICE_TYPES = {"PRICE", "EXPORT_PRICE"}

_GHG_UNIT = "g CO₂/kWh"


@dataclass(frozen=True)
class PayloadDescriptor:
    """The `units` and `currency` of one payload type, from the VTN."""

    units: str | None = None
    currency: str | None = None


def is_price(payload_type: str, descriptor: PayloadDescriptor | None) -> bool:
    """True if the payload is a price per unit of energy."""
    return payload_type in _PRICE_TYPES or bool(
        descriptor is not None and descriptor.currency
    )


def unit_of_measurement(
    payload_type: str,
    descriptor: PayloadDescriptor | None,
    default_currency: str,
) -> str:
    """Unit string for a payload type, e.g. "EUR/kWh".

    Prices use the descriptor's ISO 4217 currency, falling back to the HA
    instance currency, over the descriptor's energy unit (default kWh). This
    is the "<currency>/kWh" form HA's energy dashboard accepts for prices.
    """
    if is_price(payload_type, descriptor):
        currency = (descriptor.currency if descriptor else None) or default_currency
        units = (descriptor.units or "").upper() if descriptor else ""
        return f"{currency}/{_ENERGY_UNITS.get(units, 'kWh')}"
    if payload_type == "GHG":
        return _GHG_UNIT
    return payload_type
