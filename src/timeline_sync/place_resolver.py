from __future__ import annotations

from typing import Any, Literal

import aiohttp

from .quota import DailyQuota
from .visit_deriver import Visit


class PlaceResolver:
    """
    Resolves place names for visits.

    HA zone visits use the zone's friendly_name. For "not_home" visits:
    1. If visit_id is in known_names (already enriched in Calendar), reuse it.
    2. Otherwise call Places API, gated by per-visit-date DailyQuota.
    3. Fall back to reverse geocode, then leave as-is.
    """

    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

    def __init__(
        self,
        zones: list[dict[str, Any]],
        places_api_key: str | None = None,
        quota: DailyQuota | None = None,
        known_names: dict[str, str] | None = None,
    ) -> None:
        self._zone_names: dict[str, str] = {}
        for zone in zones:
            slug = zone["entity_id"].removeprefix("zone.")
            friendly = zone.get("attributes", {}).get("friendly_name", slug)
            self._zone_names[slug] = friendly
        self._api_key = places_api_key
        self._quota = quota or (DailyQuota() if places_api_key else None)
        # visit_id → already-enriched place name from existing Calendar events
        self._known_names: dict[str, str] = known_names or {}

    def resolve_place_name(self, visit: Visit) -> str:
        if visit.place_name != "not_home":
            return self._zone_names.get(
                visit.place_name, visit.place_name.replace("_", " ").title()
            )
        return visit.place_name

    async def enrich_visit(self, visit: Visit) -> Visit:
        """Return a new Visit with enriched place_name and source."""
        if visit.place_name != "not_home":
            friendly = self._zone_names.get(
                visit.place_name,
                visit.place_name.replace("_", " ").title(),
            )
            return Visit(
                visit_id=visit.visit_id,
                place_name=friendly,
                start=visit.start,
                end=visit.end,
                lat=visit.lat,
                lng=visit.lng,
                source="ha_zone",
            )

        # Reuse name from existing Calendar event — no API call needed
        if visit.visit_id in self._known_names:
            return Visit(
                visit_id=visit.visit_id,
                place_name=self._known_names[visit.visit_id],
                start=visit.start,
                end=visit.end,
                lat=visit.lat,
                lng=visit.lng,
                source="places_api",
            )

        if not visit.lat and not visit.lng:
            return visit

        if self._api_key and self._quota and self._quota.consume(visit.start.date()):
            result = await self._places_lookup(visit.lat, visit.lng)
            if result:
                name, source = result
                return Visit(
                    visit_id=visit.visit_id,
                    place_name=name,
                    start=visit.start,
                    end=visit.end,
                    lat=visit.lat,
                    lng=visit.lng,
                    source=source,
                )

        return visit

    async def _places_lookup(
        self, lat: float, lng: float
    ) -> tuple[str, Literal["places_api", "geocode"]] | None:
        name = await self._nearby_place(lat, lng)
        if name:
            return name, "places_api"
        name = await self._reverse_geocode(lat, lng)
        if name:
            return name, "geocode"
        return None

    async def _nearby_place(self, lat: float, lng: float) -> str | None:
        assert self._api_key is not None
        payload = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": 50.0,
                }
            },
            "maxResultCount": 1,
        }
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": "places.displayName",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.PLACES_NEARBY_URL, json=payload, headers=headers
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            places = data.get("places", [])
            if places:
                return places[0].get("displayName", {}).get("text")
        except Exception:
            return None
        return None

    async def _reverse_geocode(self, lat: float, lng: float) -> str | None:
        assert self._api_key is not None
        params: dict[str, str] = {
            "latlng": f"{lat},{lng}",
            "key": self._api_key,
            "result_type": "establishment|point_of_interest|neighborhood",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.GEOCODE_URL, params=params) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
            results = data.get("results", [])
            if results:
                return results[0].get("formatted_address")
        except Exception:
            return None
        return None
