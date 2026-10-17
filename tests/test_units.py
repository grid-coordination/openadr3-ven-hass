"""Tests for units.py (no Home Assistant dependency)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

_spec = importlib.util.spec_from_file_location(
    "units",
    pathlib.Path(__file__).parent.parent / "custom_components" / "openadr3_ven" / "units.py",
)
units = importlib.util.module_from_spec(_spec)
sys.modules["units"] = units  # dataclasses resolves the module by name
_spec.loader.exec_module(units)
PD = units.PayloadDescriptor


class UnitOfMeasurementTests(unittest.TestCase):
    def test_price_uses_descriptor_currency_and_units(self):
        self.assertEqual(
            units.unit_of_measurement("PRICE", PD(units="KWH", currency="AUD"), "USD"),
            "AUD/kWh",
        )

    def test_price_without_currency_falls_back_to_ha_currency(self):
        self.assertEqual(units.unit_of_measurement("PRICE", PD(units="KWH"), "EUR"), "EUR/kWh")
        self.assertEqual(units.unit_of_measurement("EXPORT_PRICE", None, "EUR"), "EUR/kWh")

    def test_mwh_units(self):
        self.assertEqual(
            units.unit_of_measurement("PRICE", PD(units="MWH", currency="EUR"), "USD"),
            "EUR/MWh",
        )

    def test_unlisted_type_with_currency_is_a_price(self):
        self.assertEqual(
            units.unit_of_measurement("RRP", PD(units="KWH", currency="AUD"), "USD"),
            "AUD/kWh",
        )

    def test_ghg_and_unknown_types(self):
        self.assertEqual(units.unit_of_measurement("GHG", None, "USD"), "g CO₂/kWh")
        self.assertEqual(units.unit_of_measurement("FOO", None, "USD"), "FOO")


if __name__ == "__main__":
    unittest.main(verbosity=2)
