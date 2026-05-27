from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp


class HAReader:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def get_state_history(
        self,
        entity_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Return flat list of state-change dicts for entity_id in [start, end]."""
        start_str = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        end_str = end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        url = f"{self._url}/api/history/period/{start_str}"
        params = {
            "filter_entity_id": entity_id,
            "end_time": end_str,
            "minimal_response": "true",
            "no_attributes": "false",
        }
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data: list[list[dict]] = await resp.json()

        # HA returns a list of lists (one per entity); we requested one entity
        if not data:
            return []
        return data[0]

    async def get_zones(self) -> list[dict[str, Any]]:
        """Return all zone entities from HA."""
        url = f"{self._url}/api/states"
        async with aiohttp.ClientSession(headers=self._headers) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                states: list[dict] = await resp.json()

        return [s for s in states if s.get("entity_id", "").startswith("zone.")]
