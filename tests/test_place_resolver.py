from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from timeline_sync.place_resolver import PlaceResolver, _format_address
from timeline_sync.visit_deriver import Visit

ZONES = [
    {
        "entity_id": "zone.home",
        "attributes": {"friendly_name": "Home", "latitude": 37.7, "longitude": -122.4},
    },
    {
        "entity_id": "zone.work",
        "attributes": {"friendly_name": "Office", "latitude": 37.78, "longitude": -122.39},
    },
]


def make_visit(
    place_name: str,
    lat: float = 0.0,
    lng: float = 0.0,
    geocoded_location: str | None = None,
) -> Visit:
    return Visit(
        visit_id="test123",
        place_name=place_name,
        start=datetime(2024, 1, 15, 8, tzinfo=UTC),
        end=datetime(2024, 1, 15, 9, tzinfo=UTC),
        lat=lat,
        lng=lng,
        source="ha_zone",
        geocoded_location=geocoded_location,
    )


class TestFormatAddress:
    def test_lowercases_state_country_uppercased(self):
        assert _format_address("123 main st, city, ca, usa") == "123 Main St, City, CA, USA"

    def test_preserves_existing_caps(self):
        assert _format_address("123 Oak St, San Francisco, CA 94110, USA") == "123 Oak St, San Francisco, CA 94110, USA"

    def test_full_state_name_not_uppercased(self):
        assert _format_address("123 main st, boston, massachusetts, us") == "123 Main St, Boston, Massachusetts, US"

    def test_zip_code_preserved(self):
        assert _format_address("123 main st, san francisco, ca 94110, usa") == "123 Main St, San Francisco, CA 94110, USA"


def _make_contact_resolver(entries: dict[str, str]):
    from timeline_sync.contact_resolver import ContactResolver, _normalize
    cr = ContactResolver.__new__(ContactResolver)
    cr._map = {_normalize(addr): label for addr, label in entries.items()}
    return cr


class TestPlaceResolver:
    @pytest.mark.asyncio
    async def test_zone_visit_with_geocoded_location_uses_contact_label(self):
        cr = _make_contact_resolver({"123 Oak St, San Francisco, CA 94110, USA": "Dan Smith's Home"})
        resolver = PlaceResolver(ZONES, contact_resolver=cr)
        visit = make_visit("home", lat=37.7, lng=-122.4, geocoded_location="123 Oak St, San Francisco, CA 94110, USA")
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "Dan Smith's Home"
        assert enriched.source == "contact"

    @pytest.mark.asyncio
    async def test_zone_visit_without_geocoded_uses_friendly_name(self):
        resolver = PlaceResolver(ZONES)
        visit = make_visit("home", lat=37.7, lng=-122.4)
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "Home"
        assert enriched.source == "ha_zone"

    def test_zone_slug_mapped_to_friendly_name(self):
        resolver = PlaceResolver(ZONES)
        assert resolver.resolve_place_name(make_visit("home")) == "Home"
        assert resolver.resolve_place_name(make_visit("work")) == "Office"

    def test_unknown_zone_slug_title_cased(self):
        resolver = PlaceResolver(ZONES)
        assert resolver.resolve_place_name(make_visit("gym_downtown")) == "Gym Downtown"

    @pytest.mark.asyncio
    async def test_zone_visit_enriched_with_friendly_name(self):
        resolver = PlaceResolver(ZONES)
        visit = make_visit("home", lat=37.7, lng=-122.4)
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "Home"
        assert enriched.source == "ha_zone"

    @pytest.mark.asyncio
    async def test_not_home_no_api_key_returns_unchanged(self):
        resolver = PlaceResolver(ZONES, places_api_key=None)
        visit = make_visit("not_home", lat=37.8, lng=-122.5)
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "not_home"

    @pytest.mark.asyncio
    async def test_not_home_places_api_lookup(self):
        resolver = PlaceResolver(ZONES, places_api_key="test-key")
        visit = make_visit("not_home", lat=37.8, lng=-122.5)

        with patch.object(
            resolver, "_nearby_place", new=AsyncMock(return_value=["Starbucks", "Peet's Coffee"])
        ):
            enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "Starbucks"
        assert enriched.alternatives == ("Peet's Coffee",)
        assert enriched.source == "places_api"

    @pytest.mark.asyncio
    async def test_not_home_falls_back_to_ha_geocoded_location(self):
        resolver = PlaceResolver(ZONES, places_api_key="test-key")
        visit = make_visit("not_home", lat=37.8, lng=-122.5, geocoded_location="123 Main St")

        with patch.object(resolver, "_nearby_place", new=AsyncMock(return_value=None)):
            enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "123 Main St"
        assert enriched.source == "geocode"

    @pytest.mark.asyncio
    async def test_not_home_no_geocoded_location_unchanged(self):
        resolver = PlaceResolver(ZONES, places_api_key="test-key")
        visit = make_visit("not_home", lat=37.8, lng=-122.5, geocoded_location=None)

        with patch.object(resolver, "_nearby_place", new=AsyncMock(return_value=None)):
            enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "not_home"

    @pytest.mark.asyncio
    async def test_known_names_skips_api_call(self):
        resolver = PlaceResolver(
            ZONES,
            places_api_key="test-key",
            known_names={"test123": "Starbucks"},
        )
        visit = make_visit("not_home", lat=37.8, lng=-122.5)

        with patch.object(resolver, "_nearby_place", new=AsyncMock()) as mock_nearby:
            enriched = await resolver.enrich_visit(visit)

        mock_nearby.assert_not_called()
        assert enriched.place_name == "Starbucks"
        assert enriched.source == "places_api"

    @pytest.mark.asyncio
    async def test_sensor_address_state_formatted_as_geocode(self):
        # lat/lng far from test zones (zone.home is at 37.7, -122.4)
        resolver = PlaceResolver(ZONES, places_api_key=None)
        visit = make_visit("1103 fairwood ave, sunnyvale, ca 94089, usa", lat=37.37, lng=-122.01)
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "1103 Fairwood Ave, Sunnyvale, CA 94089, USA"
        assert enriched.source == "geocode"
        assert enriched.geocoded_location == "1103 fairwood ave, sunnyvale, ca 94089, usa"

    @pytest.mark.asyncio
    async def test_sensor_address_location_field_set_in_event_body(self):
        from timeline_sync.calendar_sync import _event_body

        resolver = PlaceResolver(ZONES, places_api_key=None)
        visit = make_visit("1103 fairwood ave, sunnyvale, ca 94089, usa", lat=37.37, lng=-122.01)
        enriched = await resolver.enrich_visit(visit)
        body = _event_body(enriched, datetime(2024, 1, 15, 9, tzinfo=UTC))
        assert body.get("location") == "1103 Fairwood Ave, Sunnyvale, CA 94089, USA"

    @pytest.mark.asyncio
    async def test_sensor_address_at_home_zone_coords_uses_zone_name(self):
        # Sensor fusion: coordinates match home zone → treat as zone visit
        resolver = PlaceResolver(ZONES, places_api_key=None)
        visit = make_visit("123 main st, san francisco, ca, usa", lat=37.7, lng=-122.4)
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "Home"
        assert enriched.source == "ha_zone"

    @pytest.mark.asyncio
    async def test_sensor_address_at_home_zone_uses_contact_if_available(self):
        cr = _make_contact_resolver({"123 main st, san francisco, ca, usa": "Alex Smith's Home"})
        resolver = PlaceResolver(ZONES, places_api_key=None, contact_resolver=cr)
        visit = make_visit("123 main st, san francisco, ca, usa", lat=37.7, lng=-122.4)
        enriched = await resolver.enrich_visit(visit)
        assert enriched.place_name == "Alex Smith's Home"
        assert enriched.source == "contact"

    @pytest.mark.asyncio
    async def test_sensor_address_outside_zone_gets_places_api_alternatives(self):
        resolver = PlaceResolver(ZONES, places_api_key="test-key")
        visit = make_visit("584 n rengstorff ave, mountain view, ca 94043, usa", lat=37.37, lng=-122.01)

        with patch.object(
            resolver, "_nearby_place", new=AsyncMock(return_value=["Chipotle", "Subway"])
        ):
            enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "Chipotle"
        assert enriched.alternatives == ("Subway",)
        assert enriched.source == "places_api"

    @pytest.mark.asyncio
    async def test_geocode_fallback_applies_formatting(self):
        resolver = PlaceResolver(ZONES, places_api_key=None)
        visit = make_visit("not_home", lat=37.8, lng=-122.5, geocoded_location="123 main st, san francisco, ca 94110, usa")

        enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "123 Main St, San Francisco, CA 94110, USA"
        assert enriched.source == "geocode"

    @pytest.mark.asyncio
    async def test_not_home_contact_match_skips_places_api(self):
        cr = _make_contact_resolver({"500 Castro St, Mountain View, CA": "Friend's Home"})
        resolver = PlaceResolver(ZONES, places_api_key="test-key", contact_resolver=cr)
        visit = make_visit("not_home", lat=37.38, lng=-122.08, geocoded_location="500 Castro St, Mountain View, CA")

        with patch.object(resolver, "_nearby_place", new=AsyncMock()) as mock_nearby:
            enriched = await resolver.enrich_visit(visit)

        mock_nearby.assert_not_called()
        assert enriched.place_name == "Friend's Home"
        assert enriched.source == "contact"

    @pytest.mark.asyncio
    async def test_all_places_api_results_in_alternatives(self):
        resolver = PlaceResolver(ZONES, places_api_key="test-key")
        visit = make_visit("not_home", lat=37.8, lng=-122.5)
        many_names = [f"Place {i}" for i in range(10)]

        with patch.object(resolver, "_nearby_place", new=AsyncMock(return_value=many_names)):
            enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "Place 0"
        assert len(enriched.alternatives) == 9

    @pytest.mark.asyncio
    async def test_quota_exhausted_falls_back_to_geocoded_location(self, tmp_path):
        from timeline_sync.quota import DailyQuota

        exhausted_quota = DailyQuota(limit=0, path=tmp_path / "q.json")
        resolver = PlaceResolver(ZONES, places_api_key="test-key", quota=exhausted_quota)
        visit = make_visit("not_home", lat=37.8, lng=-122.5, geocoded_location="456 Oak Ave")

        enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "456 Oak Ave"
        assert enriched.source == "geocode"


class TestSortPlaceNames:
    def _place(self, name: str, types: list[str]) -> dict:
        return {"displayName": {"text": name}, "types": types}

    def test_restaurant_before_ev_charging(self):
        from timeline_sync.place_resolver import _sort_place_names
        places = [
            self._place("Tesla Supercharger", ["electric_vehicle_charging_station"]),
            self._place("Chipotle", ["fast_food_restaurant", "restaurant"]),
        ]
        assert _sort_place_names(places) == ["Chipotle", "Tesla Supercharger"]

    def test_gas_station_before_car_repair(self):
        from timeline_sync.place_resolver import _sort_place_names
        places = [
            self._place("Jiffy Lube", ["car_repair"]),
            self._place("Shell", ["gas_station"]),
        ]
        assert _sort_place_names(places) == ["Shell", "Jiffy Lube"]

    def test_cafe_before_parking(self):
        from timeline_sync.place_resolver import _sort_place_names
        places = [
            self._place("Lot 7 Parking", ["parking"]),
            self._place("Blue Bottle Coffee", ["cafe"]),
        ]
        assert _sort_place_names(places) == ["Blue Bottle Coffee", "Lot 7 Parking"]

    def test_all_results_returned(self):
        from timeline_sync.place_resolver import _sort_place_names
        places = [self._place(f"Place {i}", ["store"]) for i in range(15)]
        assert len(_sort_place_names(places)) == 15

    def test_place_without_display_name_skipped(self):
        from timeline_sync.place_resolver import _sort_place_names
        places = [
            self._place("Starbucks", ["cafe"]),
            {"types": ["store"]},
        ]
        assert _sort_place_names(places) == ["Starbucks"]

    def test_stable_sort_within_same_priority(self):
        from timeline_sync.place_resolver import _sort_place_names
        places = [
            self._place("A Restaurant", ["restaurant"]),
            self._place("B Cafe", ["cafe"]),
        ]
        result = _sort_place_names(places)
        assert result == ["A Restaurant", "B Cafe"]
