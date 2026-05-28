from __future__ import annotations

import math
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import aiohttp

from .quota import DailyQuota
from .visit_deriver import Visit

log = __import__("logging").getLogger(__name__)

if TYPE_CHECKING:
    from .contact_resolver import ContactResolver


def _format_address(addr: str) -> str:
    """Title-case address segments; uppercase 2-char state codes and 3-char solo country codes."""
    segments = addr.split(", ")
    result = []
    for i, segment in enumerate(segments):
        if i == 0:
            result.append(segment.title())
        else:
            words = segment.split(" ")
            only_alpha = sum(1 for w in words if w.isalpha()) == 1
            formatted = []
            for word in words:
                if word.isdigit():
                    formatted.append(word)
                elif word.isalpha() and (len(word) == 2 or (len(word) == 3 and only_alpha)):
                    formatted.append(word.upper())
                else:
                    formatted.append(word.title())
            result.append(" ".join(formatted))
    return ", ".join(result)


_PRIORITY_PLACE_TYPES: frozenset[str] = frozenset({
    # Food & drink
    "restaurant", "fast_food_restaurant", "pizza_restaurant", "hamburger_restaurant",
    "sandwich_shop", "steak_house", "seafood_restaurant", "sushi_restaurant",
    "thai_restaurant", "chinese_restaurant", "japanese_restaurant", "korean_restaurant",
    "mexican_restaurant", "italian_restaurant", "mediterranean_restaurant",
    "indian_restaurant", "american_restaurant", "ramen_restaurant", "brunch_restaurant",
    "breakfast_restaurant", "fine_dining_restaurant", "buffet_restaurant",
    "cafe", "coffee_shop", "bakery", "bar", "wine_bar", "juice_bar", "ice_cream_shop",
    "food_store", "meal_takeaway", "meal_delivery",
    # Grocery & pharmacy
    "grocery_store", "supermarket", "convenience_store", "pharmacy", "drug_store",
    # Fuel
    "gas_station",
})

_DEPRIORITY_PLACE_TYPES: frozenset[str] = frozenset({
    "electric_vehicle_charging_station",
    "car_repair", "car_wash", "car_dealer", "auto_parts_store",
    "parking", "parking_lot", "parking_garage",
})


def _sort_place_names(places: list[dict]) -> list[str]:
    """Return display names sorted by visit-likelihood: food/gas first, EV/auto-service last."""
    def key(place: dict) -> int:
        types = set(place.get("types", []))
        if types & _PRIORITY_PLACE_TYPES:
            return 0
        if types & _DEPRIORITY_PLACE_TYPES:
            return 2
        return 1

    return [
        p["displayName"]["text"]
        for p in sorted(places, key=key)
        if p.get("displayName", {}).get("text")
    ]


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
        self._zone_locations: list[tuple[float, float, float, str]] = []  # (lat, lng, radius_m, slug)
        for zone in zones:
            attrs = zone.get("attributes", {})
            slug = zone["entity_id"].removeprefix("zone.")
            friendly = attrs.get("friendly_name", slug)
            self._zone_names[slug] = friendly
            lat = attrs.get("latitude")
            lng = attrs.get("longitude")
            radius = float(attrs.get("radius", 100.0))
            if lat is not None and lng is not None:
                self._zone_locations.append((float(lat), float(lng), radius, slug))
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

    def _zone_for_coords(self, lat: float, lng: float) -> str | None:
        """Return zone slug if coordinates are within a zone boundary, else None."""
        for zone_lat, zone_lng, radius, slug in self._zone_locations:
            dlat = math.radians(lat - zone_lat)
            dlng = math.radians(lng - zone_lng)
            a = (math.sin(dlat / 2) ** 2
                 + math.cos(math.radians(zone_lat)) * math.cos(math.radians(lat)) * math.sin(dlng / 2) ** 2)
            dist_m = 2 * math.asin(math.sqrt(a)) * 6_371_000
            if dist_m <= radius:
                return slug
        return None

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
            if visit.place_name in self._zone_names:
                friendly = self._zone_names[visit.place_name]
                zone_visit = replace(visit, place_name=friendly, source="ha_zone")
                contact = self._apply_contact_override(zone_visit)
                return contact if contact is not None else zone_visit
            # Address-like state: sensor.*_geocoded_location entity where state IS the address
            if ", " in visit.place_name:
                raw_addr = visit.geocoded_location or visit.place_name
                geocoded = replace(
                    visit,
                    place_name=_format_address(raw_addr),
                    geocoded_location=raw_addr,
                    source="geocode",
                )
                # 1. Contact resolver (free, highest confidence — e.g. home/work addresses)
                contact = self._apply_contact_override(geocoded)
                if contact is not None:
                    return contact
                # 2. Sensor fusion: if coordinates are inside a known zone, use zone name
                if visit.lat or visit.lng:
                    zone_slug = self._zone_for_coords(visit.lat, visit.lng)
                    log.debug(
                        "sensor fusion: (%.5f, %.5f) → zone=%s",
                        visit.lat, visit.lng, zone_slug or "none",
                    )
                    if zone_slug and zone_slug in self._zone_names:
                        friendly = self._zone_names[zone_slug]
                        zone_visit = replace(geocoded, place_name=friendly, source="ha_zone")
                        return zone_visit
                else:
                    log.info("sensor fusion skipped: no GPS (lat=%.5f lng=%.5f)", visit.lat, visit.lng)
                # 3. known_names cache (avoid repeat Places API calls)
                if visit.visit_id in self._known_names:
                    return replace(geocoded, place_name=self._known_names[visit.visit_id], source="places_api")
                # 4. Places API — business name + alternatives
                if not self._api_key:
                    log.info("Places API skipped: no API key configured")
                elif not (visit.lat or visit.lng):
                    log.info("Places API skipped: no GPS coords (lat=%.5f lng=%.5f)", visit.lat, visit.lng)
                elif not self._quota or not self._quota.consume(visit.start.date()):
                    log.info("Places API quota exhausted for %s", visit.start.date())
                else:
                    log.info("Places API lookup: (%.5f, %.5f) for %s", visit.lat, visit.lng, geocoded.place_name)
                    names = await self._nearby_place(visit.lat, visit.lng)
                    log.info("Places API result: %s", names or "no results")
                    if names:
                        return replace(geocoded, place_name=names[0], source="places_api", alternatives=tuple(names[1:]))
                return geocoded
            # Unknown zone slug (e.g. "gym_downtown")
            return replace(visit, place_name=visit.place_name.replace("_", " ").title(), source="ha_zone")

        # Reuse name from existing Calendar event — no API call needed
        if visit.visit_id in self._known_names:
            return replace(visit, place_name=self._known_names[visit.visit_id], source="places_api")

        if not visit.lat and not visit.lng:
            return visit

        # Contact override — free, check before spending Places API quota
        if visit.geocoded_location:
            geocoded_visit = replace(visit, place_name=_format_address(visit.geocoded_location), source="geocode")
            contact = self._apply_contact_override(geocoded_visit)
            if contact is not None:
                return contact

        # Places API lookup
        if not self._api_key:
            log.info("Places API skipped: no API key configured")
        elif not self._quota or not self._quota.consume(visit.start.date()):
            log.info("Places API quota exhausted for %s", visit.start.date())
        else:
            log.info("Places API lookup: (%.5f, %.5f)", visit.lat, visit.lng)
            names = await self._nearby_place(visit.lat, visit.lng)
            log.info("Places API result: %s", names or "no results")
            if names:
                return replace(visit, place_name=names[0], source="places_api", alternatives=tuple(names[1:]))

        # Fall back to HA companion app geocoded address
        if visit.geocoded_location:
            return replace(visit, place_name=_format_address(visit.geocoded_location), source="geocode")

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
            "maxResultCount": 20,
        }
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key,
            "X-Goog-FieldMask": "places.displayName,places.types",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.PLACES_NEARBY_URL, json=payload, headers=headers
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
            return _sort_place_names(data.get("places", []))
        except Exception:
            return []
