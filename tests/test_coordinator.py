"""Tests for openadr3_ven.coordinator._process_event.

Run with:
    python3 -m unittest tests/test_coordinator.py -v

Stubs Home Assistant modules so the coordinator imports cleanly without a
running HA instance. Targets the pure `_process_event` function and its
intervalPeriod-inheritance behavior (GH#13 / OA3V-195).
"""

from __future__ import annotations

import datetime
import importlib.util
import pathlib
import sys
import types
import unittest
import zoneinfo


def _install_ha_stubs() -> None:
    """Populate sys.modules with the HA imports coordinator.py needs."""
    for full in (
        "homeassistant",
        "homeassistant.core",
        "homeassistant.config_entries",
        "homeassistant.helpers",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.helpers.event",
        "homeassistant.util",
        "homeassistant.util.dt",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.exceptions",
    ):
        sys.modules.setdefault(full, types.ModuleType(full))

    tz_brisbane = zoneinfo.ZoneInfo("Australia/Brisbane")
    sys.modules["homeassistant.util.dt"].DEFAULT_TIME_ZONE = tz_brisbane
    sys.modules["homeassistant.util.dt"].now = lambda: datetime.datetime.now(tz_brisbane)
    sys.modules["homeassistant.util.dt"].parse_datetime = (
        lambda s: datetime.datetime.fromisoformat(s)
    )
    sys.modules["homeassistant.util"].dt = sys.modules["homeassistant.util.dt"]

    class _DataUpdateCoordinator:
        def __class_getitem__(cls, item):
            return cls

    sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _DataUpdateCoordinator
    sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = Exception
    sys.modules["homeassistant.helpers.event"].async_track_time_change = lambda *a, **k: None
    sys.modules["homeassistant.core"].callback = lambda f: f
    sys.modules["homeassistant.core"].HomeAssistant = object
    sys.modules["homeassistant.config_entries"].ConfigEntry = object
    sys.modules["homeassistant.exceptions"].HomeAssistantError = Exception


def _load_coordinator():
    """Load coordinator.py as a real module after stubbing its imports."""
    _install_ha_stubs()

    pkg_path = (
        pathlib.Path(__file__).parent.parent
        / "custom_components"
        / "openadr3_ven"
    )

    pkg = types.ModuleType("openadr3_ven")
    pkg.__path__ = [str(pkg_path)]
    sys.modules["openadr3_ven"] = pkg

    api_stub = types.ModuleType("openadr3_ven.api_client")
    api_stub.VtnApiClient = object
    sys.modules["openadr3_ven.api_client"] = api_stub

    mqtt_stub = types.ModuleType("openadr3_ven.mqtt_client")
    mqtt_stub.MqttSubscriptionManager = object
    mqtt_stub.pick_broker_uri = lambda x: None
    sys.modules["openadr3_ven.mqtt_client"] = mqtt_stub

    for name in ("const", "coordinator"):
        spec = importlib.util.spec_from_file_location(
            f"openadr3_ven.{name}", pkg_path / f"{name}.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"openadr3_ven.{name}"] = mod
        spec.loader.exec_module(mod)

    return sys.modules["openadr3_ven.coordinator"]


_coord = _load_coordinator()
_process_event = _coord._process_event

from openadr3 import Event  # noqa: E402  (must follow HA stubs)


def _build_event(
    *,
    event_intervalperiod: dict | None = None,
    intervals: list[dict] | None = None,
    event_name: str = "test-event",
) -> Event:
    raw = {
        "id": "test-id",
        "createdDateTime": "2026-05-28T11:00:00+00:00",
        "modificationDateTime": "2026-05-28T11:00:00+00:00",
        "objectType": "EVENT",
        "programID": "test-program",
        "eventName": event_name,
        "intervals": intervals or [],
    }
    if event_intervalperiod is not None:
        raw["intervalPeriod"] = event_intervalperiod
    return Event.from_raw(raw)


def _interval(id_: int, value: float, *, intervalperiod: dict | None = None) -> dict:
    iv = {"id": id_, "payloads": [{"type": "PRICE", "values": [value]}]}
    if intervalperiod is not None:
        iv["intervalPeriod"] = intervalperiod
    return iv


class ProcessEventTests(unittest.TestCase):
    """Cases anchored to OpenADR 3.1.0 intervalPeriod inheritance semantics."""

    def test_explicit_intervalperiod_passthrough(self):
        """Per-interval intervalPeriod fully populated — current behavior unchanged."""
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:00:00+00:00", "duration": "PT1H"},
            intervals=[
                _interval(0, 0.10, intervalperiod={
                    "start": "2026-05-28T11:05:00+00:00", "duration": "PT5M",
                }),
                _interval(1, 0.20, intervalperiod={
                    "start": "2026-05-28T11:30:00+00:00", "duration": "PT30M",
                }),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["interval_minutes"], 5)
        self.assertEqual(rows[0]["value"], 0.10)
        self.assertEqual(rows[0]["datetime"], "2026-05-28T21:05:00+10:00")
        self.assertEqual(rows[1]["interval_minutes"], 30)
        self.assertEqual(rows[1]["datetime"], "2026-05-28T21:30:00+10:00")

    def test_full_inheritance_sequential_starts(self):
        """All intervals omit intervalPeriod → inherit from event-level with sequential start chaining."""
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:00:00+00:00", "duration": "PT5M"},
            intervals=[
                _interval(0, 0.10),
                _interval(1, 0.20),
                _interval(2, 0.30),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual([r["interval_minutes"] for r in rows], [5, 5, 5])
        self.assertEqual(rows[0]["datetime"], "2026-05-28T21:00:00+10:00")
        self.assertEqual(rows[1]["datetime"], "2026-05-28T21:05:00+10:00")
        self.assertEqual(rows[2]["datetime"], "2026-05-28T21:10:00+10:00")

    def test_inherit_duration_only(self):
        """Per-interval has start, no duration → duration inherits, start preserved."""
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:00:00+00:00", "duration": "PT15M"},
            intervals=[
                _interval(0, 0.10, intervalperiod={"start": "2026-05-28T11:30:00+00:00"}),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual(rows[0]["interval_minutes"], 15)
        self.assertEqual(rows[0]["datetime"], "2026-05-28T21:30:00+10:00")

    def test_inherit_start_only(self):
        """Per-interval has duration, no start → start inherits (sequential chain), duration preserved."""
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:00:00+00:00", "duration": "PT15M"},
            intervals=[
                _interval(0, 0.10, intervalperiod={"duration": "PT45M"}),
                _interval(1, 0.20, intervalperiod={"duration": "PT15M"}),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual(rows[0]["interval_minutes"], 45)
        self.assertEqual(rows[0]["datetime"], "2026-05-28T21:00:00+10:00")
        self.assertEqual(rows[1]["interval_minutes"], 15)
        self.assertEqual(rows[1]["datetime"], "2026-05-28T21:45:00+10:00")

    def test_explicit_then_inheriting_chains_from_explicit_end(self):
        """An inheriting interval following an explicit one chains from the explicit interval's end."""
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:00:00+00:00", "duration": "PT5M"},
            intervals=[
                _interval(0, 0.10),
                _interval(1, 0.20, intervalperiod={
                    "start": "2026-05-28T12:00:00+00:00", "duration": "PT30M",
                }),
                _interval(2, 0.30),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual(rows[0]["datetime"], "2026-05-28T21:00:00+10:00")
        self.assertEqual(rows[0]["interval_minutes"], 5)
        self.assertEqual(rows[1]["datetime"], "2026-05-28T22:00:00+10:00")
        self.assertEqual(rows[1]["interval_minutes"], 30)
        self.assertEqual(rows[2]["datetime"], "2026-05-28T22:30:00+10:00")
        self.assertEqual(rows[2]["interval_minutes"], 5)

    def test_both_absent_falls_through_to_legacy_hour_of_day(self):
        """Neither per-interval nor event-level intervalPeriod → legacy fallback path."""
        ev = _build_event(
            event_name="legacy-event-2026-05-28",
            intervals=[
                _interval(0, 0.10),
                _interval(3, 0.40),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual(rows[0]["hour"], 0)
        self.assertEqual(rows[0]["interval_minutes"], 60)
        self.assertEqual(rows[0]["date"], "2026-05-28")
        self.assertEqual(rows[1]["hour"], 3)
        self.assertEqual(rows[1]["interval_minutes"], 60)

    def test_empty_intervals_returns_empty_dict(self):
        ev = _build_event(intervals=[])
        self.assertEqual(_process_event(ev), {})

    def test_intervals_without_payloads_are_skipped(self):
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:00:00+00:00", "duration": "PT5M"},
            intervals=[
                {
                    "id": 0,
                    "intervalPeriod": {"start": "2026-05-28T11:00:00+00:00", "duration": "PT5M"},
                    "payloads": [],
                },
                _interval(1, 0.20, intervalperiod={
                    "start": "2026-05-28T11:05:00+00:00", "duration": "PT5M",
                }),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 0.20)

    def test_marks_au_vtn_shape_mixed_pt5m_and_pt30m(self):
        """Regression anchor for Mark Purcell's AU VTN shape (GH#11):
        1× PT5M live interval at the head, followed by PT30M forecast slots,
        every interval has explicit intervalPeriod.
        """
        ev = _build_event(
            event_intervalperiod={"start": "2026-05-28T11:06:16+00:00", "duration": "P7D"},
            intervals=[
                _interval(0, 0.219234, intervalperiod={
                    "start": "2026-05-28T11:05:00+00:00", "duration": "PT5M",
                }),
                _interval(1, 0.249057, intervalperiod={
                    "start": "2026-05-28T11:30:00+00:00", "duration": "PT30M",
                }),
                _interval(2, 0.229169, intervalperiod={
                    "start": "2026-05-28T12:00:00+00:00", "duration": "PT30M",
                }),
            ],
        )
        rows = _process_event(ev)["PRICE"]
        self.assertEqual([r["interval_minutes"] for r in rows], [5, 30, 30])
        self.assertEqual([r["value"] for r in rows], [0.219234, 0.249057, 0.229169])


if __name__ == "__main__":
    unittest.main(verbosity=2)
