from __future__ import annotations

from typing import Any, Literal

import aiohttp

from .visit_deriver import Visit


class PlaceResolver:
    """
    Resolves place names for visits.

    HA zone visits already have a friendly name in the state value (e.g. "home",
    "work"). For "not_home" visits, we query Google Places API (if configured)
    or fall back to reverse geocoding.
    """

    GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
    PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

    def __init__(
        self,
        zones: list[dict[str, Any]],
        places_api_key: str | None = None,
    ) -> None:
        # Build zone friendly_name lookup keyed by HA state value (zone slug)
        self._zone_names: dict[str, str] = {}
        for zone in zones:
            slug = zone["entity_id"].removeprefix("zone.")
            friendly = zone.get("attributes", {}).get("friendly_name", slug)
            self._zone_names[slug] = friendly
        self._api_key = places_api_key

    def resolve_place_name(self, visit: Visit) -> str:
        """Return human-readable place name for the visit's zone slug."""
        if visit.place_name != "not_home":
            return self._zone_names.get(visit.place_name, visit.place_name.replace("_", " ").title())
        return visit.place_name  # resolved async in resolve_unknown

    async def enrich_visit(self, visit: Visit) -> Visit:
        """
        Return a new Visit with enriched place_name and source for not_home visits.
        Zone visits are returned unchanged (already named).
        """
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

        if not visit.lat and not visit.lng:
            return visit  # no coords, can't resolve

        if self._api_key:
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

        return visit  # leave as "not_home" / unknown if no API key or lookup failed

    async def _places_lookup(
        self, lat: float, lng: float
    ) -> tuple[str, Literal["places_api", "geocode"]] | None:
        """Try Places API first, fall back to geocode."""
        name = await self._nearby_place(lat, lng)
        if name:
            return name, "places_api"
        name = await self._reverse_geocode(lat, lng)
        if name:
            return name, "geocode"
        return None

    async def _nearby_place(self, lat: float, lng: float) -> str | None:
        payload = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": 50.0,
                }
            },
            "maxResultCount": 1,
        }
        headers = {
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
        params = {
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
