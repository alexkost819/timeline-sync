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
    3. Fall back to visit.geocoded_location (from HA companion app).
    4. Leave unchanged if nothing is available.
    """

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
                geocoded_location=visit.geocoded_location,
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
                geocoded_location=visit.geocoded_location,
            )

        if not visit.lat and not visit.lng:
            return visit

        # Places API lookup
        if self._api_key and self._quota and self._quota.consume(visit.start.date()):
            name = await self._nearby_place(visit.lat, visit.lng)
            if name:
                return Visit(
                    visit_id=visit.visit_id,
                    place_name=name,
                    start=visit.start,
                    end=visit.end,
                    lat=visit.lat,
                    lng=visit.lng,
                    source="places_api",
                    geocoded_location=visit.geocoded_location,
                )

        # Fall back to HA companion app geocoded address
        if visit.geocoded_location:
            return Visit(
                visit_id=visit.visit_id,
                place_name=visit.geocoded_location,
                start=visit.start,
                end=visit.end,
                lat=visit.lat,
                lng=visit.lng,
                source="geocode",
                geocoded_location=visit.geocoded_location,
            )

        return visit

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
