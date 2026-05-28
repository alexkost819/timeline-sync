from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import aiohttp

from .quota import DailyQuota
from .visit_deriver import Visit

if TYPE_CHECKING:
    from .contact_resolver import ContactResolver


class PlaceResolver:
    """
    Resolves place names for visits.

    Enrichment chain for "not_home" visits:
    1. known_names cache (already enriched in Calendar) — no API call
    2. Places API top-3 results (gated by per-visit-date DailyQuota)
    3. visit.geocoded_location from HA companion app
    4. Contact override: if geocoded_location matches a contact address, use contact label

    HA zone visits use the zone's friendly_name directly.
    """

    PLACES_NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"

    def __init__(
        self,
        zones: list[dict[str, Any]],
        places_api_key: str | None = None,
        quota: DailyQuota | None = None,
        known_names: dict[str, str] | None = None,
        contact_resolver: ContactResolver | None = None,
    ) -> None:
        self._zone_names: dict[str, str] = {}
        for zone in zones:
            slug = zone["entity_id"].removeprefix("zone.")
            friendly = zone.get("attributes", {}).get("friendly_name", slug)
            self._zone_names[slug] = friendly
        self._api_key = places_api_key
        self._quota = quota or (DailyQuota() if places_api_key else None)
        self._known_names: dict[str, str] = known_names or {}
        self._contact_resolver = contact_resolver

    def resolve_place_name(self, visit: Visit) -> str:
        if visit.place_name != "not_home":
            return self._zone_names.get(
                visit.place_name, visit.place_name.replace("_", " ").title()
            )
        return visit.place_name

    def _apply_contact_override(self, visit: Visit) -> Visit | None:
        """If geocoded_location matches a contact, return overridden Visit, else None."""
        if self._contact_resolver and visit.geocoded_location:
            name = self._contact_resolver.resolve(visit.geocoded_location)
            if name:
                return replace(visit, place_name=name, source="contact", alternatives=())
        return None

    async def enrich_visit(self, visit: Visit) -> Visit:
        """Return a new Visit with enriched place_name and source."""
        if visit.place_name != "not_home":
            friendly = self._zone_names.get(
                visit.place_name,
                visit.place_name.replace("_", " ").title(),
            )
            return replace(visit, place_name=friendly, source="ha_zone")

        # Reuse name from existing Calendar event — no API call needed
        if visit.visit_id in self._known_names:
            return replace(visit, place_name=self._known_names[visit.visit_id], source="places_api")

        if not visit.lat and not visit.lng:
            return visit

        # Places API lookup — returns up to 3 results
        if self._api_key and self._quota and self._quota.consume(visit.start.date()):
            names = await self._nearby_place(visit.lat, visit.lng)
            if names:
                enriched = replace(
                    visit,
                    place_name=names[0],
                    source="places_api",
                    alternatives=tuple(names[1:]),
                )
                contact = self._apply_contact_override(enriched)
                return contact if contact is not None else enriched

        # Fall back to HA companion app geocoded address
        if visit.geocoded_location:
            geocoded = replace(visit, place_name=visit.geocoded_location, source="geocode")
            contact = self._apply_contact_override(geocoded)
            return contact if contact is not None else geocoded

        return visit

    async def _nearby_place(self, lat: float, lng: float) -> list[str]:
        assert self._api_key is not None
        payload = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": 50.0,
                }
            },
            "maxResultCount": 3,
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
                        return []
                    data = await resp.json()
            return [
                p["displayName"]["text"]
                for p in data.get("places", [])
                if p.get("displayName", {}).get("text")
            ]
        except Exception:
            return []
