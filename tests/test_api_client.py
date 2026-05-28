"""Tests for openadr3_ven.api_client.

Run with:
    python3 -m unittest discover tests/ -v

api_client.py itself doesn't import HA, but the parent package's __init__.py
does — so we load api_client.py directly via importlib to avoid pulling in
the package's HA dependencies during test collection.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
from unittest.mock import AsyncMock, MagicMock

import httpx

_API_PATH = (
    pathlib.Path(__file__).parent.parent
    / "custom_components"
    / "openadr3_ven"
    / "api_client.py"
)
_spec = importlib.util.spec_from_file_location("openadr3_ven_api_client", _API_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
VtnApiClient = _mod.VtnApiClient


class GetNotifiersTests(unittest.TestCase):
    """`get_notifiers` must return {} on any HTTP error — including network
    failures (ConnectError, ReadTimeout, etc.), not just HTTP 4xx/5xx.

    MQTT discovery is non-critical: a transient network blip at startup
    must not propagate up and crash `async_setup_entry` (GH#15).
    """

    def _client_with_mocked_get(self, *, get_returns=None, get_raises=None):
        client = VtnApiClient.__new__(VtnApiClient)  # bypass __init__
        client._client = MagicMock()
        if get_raises is not None:
            client._client.get = AsyncMock(side_effect=get_raises)
        else:
            client._client.get = AsyncMock(return_value=get_returns)
        return client

    def test_returns_json_on_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "MQTT": {"URIS": ["mqtt://broker:1883"]},
        })
        client = self._client_with_mocked_get(get_returns=mock_resp)
        self.assertEqual(
            asyncio.run(client.get_notifiers()),
            {"MQTT": {"URIS": ["mqtt://broker:1883"]}},
        )

    def test_returns_empty_on_http_status_error(self):
        """HTTPStatusError (404 etc) — VTN doesn't advertise notifiers."""
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(status_code=404),
        ))
        client = self._client_with_mocked_get(get_returns=mock_resp)
        self.assertEqual(asyncio.run(client.get_notifiers()), {})

    def test_returns_empty_on_connect_error(self):
        """ConnectError — VTN unreachable at startup; must not brick setup."""
        client = self._client_with_mocked_get(
            get_raises=httpx.ConnectError("connection refused"),
        )
        self.assertEqual(asyncio.run(client.get_notifiers()), {})

    def test_returns_empty_on_read_timeout(self):
        """ReadTimeout — slow VTN response; must not brick setup."""
        client = self._client_with_mocked_get(
            get_raises=httpx.ReadTimeout("timeout reading /notifiers"),
        )
        self.assertEqual(asyncio.run(client.get_notifiers()), {})

    def test_returns_empty_on_remote_protocol_error(self):
        """RemoteProtocolError — VTN sent malformed response; must not brick setup."""
        client = self._client_with_mocked_get(
            get_raises=httpx.RemoteProtocolError("bad protocol"),
        )
        self.assertEqual(asyncio.run(client.get_notifiers()), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
