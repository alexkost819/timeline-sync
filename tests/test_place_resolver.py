from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from timeline_sync.place_resolver import PlaceResolver
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


class TestPlaceResolver:
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
    async def test_quota_exhausted_falls_back_to_geocoded_location(self, tmp_path):
        from timeline_sync.quota import DailyQuota

        exhausted_quota = DailyQuota(limit=0, path=tmp_path / "q.json")
        resolver = PlaceResolver(ZONES, places_api_key="test-key", quota=exhausted_quota)
        visit = make_visit("not_home", lat=37.8, lng=-122.5, geocoded_location="456 Oak Ave")

        enriched = await resolver.enrich_visit(visit)

        assert enriched.place_name == "456 Oak Ave"
        assert enriched.source == "geocode"
