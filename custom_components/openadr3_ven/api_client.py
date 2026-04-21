"""Async HTTP client for OpenADR 3 VTN REST API."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
from openadr3 import Event, Program

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_SIZE = 50

_MANIFEST = json.loads((Path(__file__).parent / "manifest.json").read_text())
USER_AGENT = f"{_MANIFEST['domain']}/{_MANIFEST['version']}"


class VtnApiClient:
    """Async client for an OpenADR 3 VTN."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )

    async def test_connection(self) -> bool:
        """Test connectivity by fetching the first page of programs."""
        resp = await self._client.get("/programs", params={"limit": 1})
        resp.raise_for_status()
        return True

    async def get_programs(
        self, skip: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> list[Program]:
        """Fetch a page of programs."""
        resp = await self._client.get(
            "/programs", params={"skip": skip, "limit": limit}
        )
        resp.raise_for_status()
        return [Program.from_raw(p) for p in resp.json()]

    async def get_all_programs(self) -> list[Program]:
        """Fetch all programs, handling pagination."""
        programs: list[Program] = []
        skip = 0
        while True:
            page = await self.get_programs(skip=skip, limit=DEFAULT_PAGE_SIZE)
            programs.extend(page)
            if len(page) < DEFAULT_PAGE_SIZE:
                break
            skip += DEFAULT_PAGE_SIZE
        return programs

    async def get_events(
        self, program_id: str, skip: int = 0, limit: int = DEFAULT_PAGE_SIZE
    ) -> list[Event]:
        """Fetch events for a program."""
        resp = await self._client.get(
            "/events", params={"programID": program_id, "skip": skip, "limit": limit}
        )
        resp.raise_for_status()
        return [Event.from_raw(e) for e in resp.json()]

    async def get_notifiers(self) -> dict[str, Any]:
        """Fetch notifier configuration (MQTT broker details, etc)."""
        try:
            resp = await self._client.get("/notifiers")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            return {}

    async def get_program_event_topics(self, program_id: str) -> list[str]:
        """Fetch MQTT topics for a program's events from the VTN."""
        try:
            resp = await self._client.get(
                f"/notifiers/mqtt/topics/programs/{program_id}/events"
            )
            resp.raise_for_status()
            data = resp.json()
            topics = data.get("topics", {})
            # Subscribe to the wildcard topic covering all operations
            all_topic = topics.get("ALL")
            if all_topic:
                return [all_topic]
            # Fallback: subscribe to individual operation topics
            return [t for t in topics.values() if isinstance(t, str)]
        except httpx.HTTPStatusError:
            return []

    async def get_all_program_event_topics(
        self, program_ids: set[str]
    ) -> list[str]:
        """Fetch MQTT event topics for all given programs."""
        topics: list[str] = []
        for pid in program_ids:
            pid_topics = await self.get_program_event_topics(pid)
            topics.extend(pid_topics)
        return topics

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
