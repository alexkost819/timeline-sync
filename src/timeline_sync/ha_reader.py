from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import aiohttp

log = logging.getLogger(__name__)

_RECONNECT_BASE = 5  # seconds
_RECONNECT_MAX = 300  # seconds


class HAReader:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
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

    async def listen_state_changes(
        self,
        entity_id: str,
        callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Subscribe to HA WebSocket state_changed events for entity_id.
        Calls callback() each time the entity changes state.
        Reconnects with exponential backoff on disconnect.
        Runs indefinitely — cancel the task to stop.
        """
        ws_url = self._url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = f"{ws_url}/api/websocket"
        delay = _RECONNECT_BASE

        while True:
            try:
                await self._ws_session(ws_url, entity_id, callback)
                delay = _RECONNECT_BASE  # reset on clean disconnect
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("WebSocket disconnected (%s). Reconnecting in %ds.", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, _RECONNECT_MAX)

    async def _ws_session(
        self,
        ws_url: str,
        entity_id: str,
        callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        sub_id = 1
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                # HA sends auth_required first
                msg = await ws.receive_json()
                if msg.get("type") != "auth_required":
                    raise RuntimeError(f"Unexpected initial message: {msg}")

                await ws.send_json({"type": "auth", "access_token": self._token})
                msg = await ws.receive_json()
                if msg.get("type") != "auth_ok":
                    raise RuntimeError(f"Auth failed: {msg}")

                await ws.send_json(
                    {"id": sub_id, "type": "subscribe_events", "event_type": "state_changed"}
                )
                msg = await ws.receive_json()
                if msg.get("type") != "result" or not msg.get("success"):
                    raise RuntimeError(f"Subscription failed: {msg}")

                log.info("WebSocket connected. Listening for %s changes.", entity_id)

                async for raw in ws:
                    if raw.type == aiohttp.WSMsgType.TEXT:
                        data = raw.json()
                        if data.get("type") != "event":
                            continue
                        event_data = data.get("event", {}).get("data", {})
                        if event_data.get("entity_id") == entity_id:
                            new_state = event_data.get("new_state", {}).get("state", "")
                            log.info("State changed → %s. Triggering sync.", new_state)
                            await callback()
                    elif raw.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        raise RuntimeError(f"WebSocket closed: {raw.type}")
